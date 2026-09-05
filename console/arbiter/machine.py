"""The trust arbiter: confidence + credential state -> positional authority.

Consumes the §5 interface contract. Touches no observable, ever.
Pure and synchronous, so Track C's Dirichlet sweep (§10) can replay a whole
2,880-epoch day through it per weight draw with no I/O.

The five invariants of design.md §8 are implemented here and asserted in
console/tests/test_machine.py. (design.md says "four invariants" and then lists
five; there are five.)
"""
from dataclasses import dataclass, field, asdict
from typing import Any, Optional

from .states import (
    CREDENTIAL_CAP,
    CREDENTIAL_FORCE,
    CREDENTIAL_STATUSES,
    MIN_DWELL_EPOCHS,
    RECOVERY_EPOCHS,
    STALE_GRACE_TICKS,
    THRESHOLD_PROVENANCE,
    TrustState,
    state_from_confidence,
)

# design.md §8 / cut-order item 3. The one place the design edges toward action
# under uncertainty; §16 mentor question 3 asks whether an operator accepts it.
# Flip to False to cut the motion advisory and keep only the reweighting.
EMIT_LATERAL_ADVISORY = True

REQUIRED_KEYS = ("timestamp", "confidence", "credential_status")


@dataclass
class Decision:
    """One arbitration result. Everything the console needs to render an epoch."""
    epoch_index: int
    timestamp: Optional[str]
    state: TrustState
    previous_state: TrustState
    changed: bool
    reason: str                       # confidence | credential_force |
                                      # credential_cap | recovery_gated |
                                      # stale | hold
    confidence: Optional[float]
    credential_status: Optional[str]
    implied_state: Optional[TrustState]   # from confidence alone
    # Invariant 4 — clock coupling.
    clock_discipline: bool
    accepting_credentials: bool
    # Invariant 5 — DEGRADED is active.
    pursuing: Optional[str] = None
    advisory: Optional[str] = None
    # Console-side half of the freeze rule (design.md §9).
    geometry_divergence: Optional[dict] = None
    stale_ticks: int = 0
    threshold_provenance: str = THRESHOLD_PROVENANCE
    features: dict = field(default_factory=dict)
    geometry: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["state"] = self.state.name
        d["previous_state"] = self.previous_state.name
        d["implied_state"] = (
            self.implied_state.name if self.implied_state is not None else None
        )
        return d


def _valid(epoch: Any) -> bool:
    if not isinstance(epoch, dict):
        return False
    if any(k not in epoch for k in REQUIRED_KEYS):
        return False
    c = epoch.get("confidence")
    if not isinstance(c, (int, float)) or not (0.0 <= float(c) <= 1.0):
        return False
    return epoch.get("credential_status") in CREDENTIAL_STATUSES


class Arbiter:
    """Stateful across epochs. One instance per replay."""

    def __init__(self, initial: TrustState = TrustState.NOMINAL):
        self.state = initial
        self.epoch_index = -1
        self._dwell = 0          # epochs held in the current state
        self._recovery_run = 0   # consecutive epochs implying a higher state
        self._stale = 0
        self._last_confidence: Optional[float] = None
        self._last_credential: Optional[str] = None
        # Snapshot of the geometry block at the last NOMINAL epoch. Divergence
        # between this and live geometry is itself evidence (design.md §9).
        self._frozen_geometry: Optional[dict] = None

    # ---------------------------------------------------------------- helpers

    def _target(self, confidence: float, credential: str):
        """Compose confidence and credential state into a target authority."""
        implied = state_from_confidence(confidence)
        forced = CREDENTIAL_FORCE.get(credential)
        if forced is not None:
            return forced, implied, "credential_force"
        cap = CREDENTIAL_CAP.get(credential)
        if cap is not None and implied > cap:
            return cap, implied, "credential_cap"
        return implied, implied, "confidence"

    def _transition(self, target: TrustState, reason: str) -> str:
        """Apply invariants 1 and 2. Returns the (possibly amended) reason."""
        if target < self.state:
            # Invariant 1: downgrades fire immediately and may skip states.
            self.state = target
            self._dwell = 0
            self._recovery_run = 0
            return reason
        if target > self.state:
            # Invariant 2: up one state at a time, gated on a sustained run
            # plus a minimum dwell. No single epoch can restore authority.
            self._recovery_run += 1
            if (
                self._recovery_run >= RECOVERY_EPOCHS
                and self._dwell >= MIN_DWELL_EPOCHS
            ):
                self.state = TrustState(self.state + 1)
                self._dwell = 0
                self._recovery_run = 0
                return "recovery"
            self._dwell += 1
            return "recovery_gated"
        self._recovery_run = 0
        self._dwell += 1
        return "hold"

    def _divergence(self, live: dict) -> Optional[dict]:
        """Live vs last-NOMINAL geometry. Console-side; touches no observable.

        Track C owns recomputing the information ratio against the frozen
        line-of-sight set. This reports the divergence the operator sees and
        the explanation layer names. Reconcile at the 18:30 checkpoint so the
        two halves are not built twice.
        """
        frozen = self._frozen_geometry
        if not frozen or not live:
            return None
        fr = frozen.get("information_ratio")
        lr = live.get("information_ratio")
        newly = sorted(
            set(live.get("excluded_sv") or []) - set(frozen.get("excluded_sv") or [])
        )
        return {
            "frozen_information_ratio": fr,
            "live_information_ratio": lr,
            "ratio_delta": (None if fr is None or lr is None else round(lr - fr, 4)),
            "newly_excluded_sv": newly,
            "frozen_at": frozen.get("_frozen_at"),
        }

    # ------------------------------------------------------------------- step

    def step(self, epoch: Any) -> Decision:
        """Advance one tick. Pass None (or a malformed object) for a missing epoch."""
        self.epoch_index += 1
        previous = self.state

        if not _valid(epoch):
            # design.md §5: a missing or malformed epoch is evidence of
            # degradation, not a no-op. Silence is not consent.
            self._stale += 1
            reason = "hold"
            if self._stale >= STALE_GRACE_TICKS and self.state > TrustState.SURRENDERED:
                self.state = TrustState(self.state - 1)
                self._dwell = 0
                self._recovery_run = 0
                self._stale = 0
                reason = "stale"
            return self._emit(
                previous, reason, None, None, self._last_confidence,
                self._last_credential, None, {}, {},
            )

        self._stale = 0
        confidence = float(epoch["confidence"])
        credential = epoch["credential_status"]
        self._last_confidence = confidence
        self._last_credential = credential

        target, implied, cause = self._target(confidence, credential)
        kind = self._transition(target, cause)
        # A credential that is still the binding constraint stays the stated
        # reason on hold epochs. Video beat 4 holds SURRENDERED for ~25 s at
        # confidence ~0.93; without this every epoch after the transition read
        # "confidence is below the trusted range" beside a confidence of 0.92.
        if kind == "hold" and cause in ("credential_force", "credential_cap") \
                and self.state == target:
            reason = cause
        else:
            reason = kind

        geometry = epoch.get("geometry") or {}
        divergence = None
        if self.state is TrustState.NOMINAL:
            # Refresh the frozen snapshot only while fully trusted.
            if geometry:
                self._frozen_geometry = dict(geometry)
                self._frozen_geometry["_frozen_at"] = epoch.get("timestamp")
        else:
            divergence = self._divergence(geometry)

        return self._emit(
            previous, reason, epoch.get("timestamp"), implied, confidence,
            credential, divergence, epoch.get("features") or {}, geometry,
        )

    def _emit(self, previous, reason, timestamp, implied, confidence,
              credential, divergence, features, geometry) -> Decision:
        nominal = self.state is TrustState.NOMINAL
        pursuing = advisory = None
        if self.state is TrustState.DEGRADED:
            # Invariant 5: losing authority is not the same as doing nothing.
            pursuing = (geometry or {}).get("next_best_observation")
            if pursuing:
                advisory = f"Reweight toward {pursuing}."
                if EMIT_LATERAL_ADVISORY:
                    advisory += (
                        " Lateral offset would break a fixed-position spoofer's"
                        " geometry — advisory only, not a motion command."
                    )
        return Decision(
            epoch_index=self.epoch_index,
            timestamp=timestamp,
            state=self.state,
            previous_state=previous,
            changed=self.state != previous,
            reason=reason,
            confidence=confidence,
            credential_status=credential,
            implied_state=implied,
            # Invariant 4: below NOMINAL the clock free-runs and no new
            # credential is accepted. Both gates are the same predicate.
            clock_discipline=nominal,
            accepting_credentials=nominal,
            pursuing=pursuing,
            advisory=advisory,
            geometry_divergence=divergence,
            stale_ticks=self._stale,
            features=features,
            geometry=geometry,
        )
