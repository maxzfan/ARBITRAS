"""Trust states and the confidence thresholds that map onto them.

THIS FILE IS THE SINGLE SWAP POINT FOR THRESHOLDS (design.md §8).
At 21:00 Saturday, tracks A and C replace the placeholder values with measured
separation points. Nothing else in the codebase should contain a threshold.
"""
from enum import IntEnum


class TrustState(IntEnum):
    """Ordered so that authority comparisons are integer comparisons.

    Higher = more authority. 'Monotone non-increasing' (invariant 1) is
    `min()`, and 'cap at DEGRADED' (credential override) is also `min()`.
    """
    SURRENDERED = 0
    RESTRICTED = 1
    DEGRADED = 2
    NOMINAL = 3


# Lower bound of confidence required to be IN each state.
# MEASURED (design.md §10 step 4), 2026-09-05, from the regenerated streams
# with the cross-constellation feature and live geometry blocks wired in
# (out/clean.jsonl, out/carryoff.jsonl; python -m backend.demo, top6 subset):
#
#   clean day, 2880 epochs:  min 0.6591  p1 0.7315  p50 0.8679
#   attack window, 90 epochs: p95 0.6268  p75 0.5478  p50 0.5338  p25 0.5182
#
#   NOMINAL     0.643 = midpoint of the zero-overlap band [attack p95 0.6268,
#               clean min 0.6591]: 0 of 2880 clean epochs fall below it
#               (FSR contribution 0 on this day) and >=95% of attack epochs
#               fall under it. The only attack epochs above it are the first
#               ~4 of the onset ramp (range offset <= 110 m, displacement
#               <= ~7 m) — they set time-to-alert, not the threshold.
#   DEGRADED    0.548 = attack p75  } raised INTO the attack distribution so
#   RESTRICTED  0.518 = attack p25  } the state machine traverses the full
#               staircase on signal alone (team decision 2026-09-05); the
#               deep-attack quartile (conf < p25) reads SURRENDERED.
#
# Separation clean-vs-attack: d' = 7.18. Not round numbers, on purpose.
THRESHOLDS = {
    TrustState.NOMINAL: 0.643,
    TrustState.DEGRADED: 0.548,
    TrustState.RESTRICTED: 0.518,
    # SURRENDERED is everything below RESTRICTED's bound.
}

# Surfaced in the console and stamped into every emitted decision, so a
# placeholder threshold cannot reach the submission video unnoticed.
THRESHOLD_PROVENANCE = (
    "measured 2026-09-05: NOMINAL 0.643 = mid of zero-overlap band "
    "[attack p95 0.6268, clean min 0.6591] (0/2880 clean epochs below); "
    "DEGRADED 0.548 = attack p75; RESTRICTED 0.518 = attack p25; d' 7.18"
)

# Hysteresis (invariant 2). Also placeholders, but of a different kind: these
# are policy, not fitted to data, and are defensible as stated in §8.
RECOVERY_EPOCHS = 10   # consecutive epochs above the higher threshold
MIN_DWELL_EPOCHS = 5   # minimum epochs in a state before any upgrade

# Stale-epoch policy. design.md §5: "Silence is not consent."
STALE_GRACE_TICKS = 3  # consecutive missing/malformed epochs before stepping down


def state_from_confidence(confidence: float) -> TrustState:
    """Confidence -> the state it implies, before any credential override."""
    if confidence >= THRESHOLDS[TrustState.NOMINAL]:
        return TrustState.NOMINAL
    if confidence >= THRESHOLDS[TrustState.DEGRADED]:
        return TrustState.DEGRADED
    if confidence >= THRESHOLDS[TrustState.RESTRICTED]:
        return TrustState.RESTRICTED
    return TrustState.SURRENDERED


# Credential override (design.md §8). Two mechanisms, deliberately distinct:
# FORCE pins the state regardless of confidence; CAP bounds it from above.
CREDENTIAL_FORCE = {
    "REVOKED": TrustState.SURRENDERED,
    "EXPIRED": TrustState.SURRENDERED,
}
CREDENTIAL_CAP = {
    "UNVERIFIED": TrustState.DEGRADED,
}
# PENDING: no override — the previously verified credential is still inside its
# window, and PENDING lasts exactly the TESLA disclosure lag (§9).
# VALID: no override.
CREDENTIAL_STATUSES = frozenset(
    {"VALID", "PENDING", "EXPIRED", "UNVERIFIED", "REVOKED"}
)
