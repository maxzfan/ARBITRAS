"""GeometryEngine — produces the `geometry` block of the design.md §5
contract. This module is Track A's only entry point into the track.

    from backend.geometry.engine import compute_geometry_block
    block = compute_geometry_block(t, excluded_sv=["G07", "G13"],
                                   tracked_sv=tracked, freeze=below_nominal)

Freeze-at-last-NOMINAL (design.md §9): line-of-sight vectors derive from
the receiver's own position estimate, which under attack is the spoofed
one. While `freeze` is False the engine snapshots the LOS set; while True
it evaluates geometry against the snapshot held at the last unfrozen
epoch, and the frozen-vs-live divergence is logged as evidence.

`displacement_bound_m` is None until `sigma_uere_m` is set from the
measured clean-day residual RMS (Track A, 21:00 threshold session) —
thresholds derive from observed data, never a guessed number.
"""
from __future__ import annotations

from datetime import datetime

import numpy as np

from backend.geometry.ephemeris import load_records, sv_positions_at
from backend.geometry.hmatrix import azimuth_deg, build_H, elevation_deg
from backend.geometry.information import (displacement_bound_m,
                                          information_ratio,
                                          next_best_observation)

USN8_ECEF = np.array([1112161.8802, -4842854.4026, 3985497.3830])


class GeometryEngine:
    def __init__(self, nav_path=None, rx_ecef=USN8_ECEF,
                 el_mask_deg: float = 10.0,
                 sigma_uere_m: float | None = None,
                 alpha: float = 1e-3):
        from backend.geometry import ephemeris
        self.records = load_records(nav_path or ephemeris.NAV_PATH)
        self.rx_ecef = np.asarray(rx_ecef, dtype=float)
        self.el_mask_deg = el_mask_deg
        self.sigma_uere_m = sigma_uere_m
        self.alpha = alpha
        self._frozen: dict[str, np.ndarray] | None = None
        self.divergence_log: list[tuple[datetime, float, float]] = []
        self._last_context: dict | None = None

    def solve_context(self) -> dict | None:
        """Per-epoch solve context for Track D's corrector (TRACK_D.md).

        Returns the state of the most recent compute() call:
          t, H_trusted (n x (3+k)), sv_order, const_order (row/column
          orderings of H_trusted), trusted_sat_pos ({sv: ECEF}),
          frozen_basis (the LOS set the epoch was evaluated against —
          the last-NOMINAL snapshot under freeze), frozen (bool),
          rx_ecef (the linearisation point).

        Track D builds its weighted-RAIM S-matrix and slope-based PL on
        this; it must not re-derive any of it. None before first compute.
        """
        return self._last_context

    def visible_svs(self, t: datetime) -> dict[str, np.ndarray]:
        pos = sv_positions_at(self.records, t)
        return {sv: p for sv, p in pos.items()
                if elevation_deg(p, self.rx_ecef) > self.el_mask_deg}

    def compute(self, t: datetime, excluded_sv: list[str],
                tracked_sv: list[str] | None = None,
                freeze: bool = False) -> dict:
        """The contract `geometry` block for one epoch."""
        live = self.visible_svs(t)
        if tracked_sv is not None:
            live = {sv: p for sv, p in live.items() if sv in tracked_sv}

        if not freeze:
            self._frozen = live          # snapshot at every trusted epoch
            basis = live
        else:
            basis = self._frozen if self._frozen is not None else live

        excluded = set(excluded_sv)
        trusted = {sv: p for sv, p in basis.items() if sv not in excluded}

        h_full, _, _ = build_H(basis, self.rx_ecef) if basis else (
            np.zeros((0, 3)), [], [])
        h_trusted, sv_order, const_order = build_H(trusted, self.rx_ecef) \
            if trusted else (np.zeros((0, 3)), [], [])

        # Solve context for Track D (backend/correction/, TRACK_D.md): the
        # weighted-RAIM corrector consumes exactly this epoch state — it must
        # NOT rebuild H, LOS, or the trusted set. Read via solve_context().
        self._last_context = {
            "t": t,
            "H_trusted": h_trusted,
            "sv_order": sv_order,
            "const_order": const_order,
            "trusted_sat_pos": trusted,
            "frozen_basis": basis,
            "frozen": freeze,
            "rx_ecef": self.rx_ecef,
        }

        ratio = information_ratio(h_trusted, h_full) if len(basis) else 0.0

        if freeze and self._frozen is not None and live:
            # frozen-vs-live divergence is itself evidence (design.md §9)
            live_trusted = {sv: p for sv, p in live.items()
                            if sv not in excluded}
            h_lt, _, _ = build_H(live_trusted, self.rx_ecef)
            h_lf, _, _ = build_H(live, self.rx_ecef)
            self.divergence_log.append(
                (t, ratio, information_ratio(h_lt, h_lf)))

        bound = None
        if self.sigma_uere_m is not None and len(trusted):
            bound = displacement_bound_m(h_trusted, self.sigma_uere_m,
                                         self.alpha)

        # Sky view (Track B contract extension): every SV in the epoch's
        # basis — the frozen set below NOMINAL, consistent with the ratio —
        # at its az/el, dark exactly where excluded_sv says so.
        sky = sorted(
            ({"sv": sv,
              "az": round(azimuth_deg(p, self.rx_ecef), 1),
              "el": round(elevation_deg(p, self.rx_ecef), 1),
              "trusted": sv not in excluded}
             for sv, p in basis.items()),
            key=lambda e: -e["el"])

        return {
            "information_ratio": round(ratio, 4),
            "excluded_sv": sorted(excluded),
            "displacement_bound_m": None if bound is None else round(bound, 1),
            "next_best_observation": next_best_observation(
                basis, sorted(trusted), self.rx_ecef),
            "sky": sky,
        }


_ENGINE: GeometryEngine | None = None


def compute_geometry_block(t: datetime, excluded_sv: list[str],
                           tracked_sv: list[str] | None = None,
                           freeze: bool = False) -> dict:
    """Module-level convenience with a lazily built singleton engine."""
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = GeometryEngine()
    return _ENGINE.compute(t, excluded_sv, tracked_sv, freeze)


def last_solve_context() -> dict | None:
    """Module-level accessor for Track D's emitter (TRACK_D.md D4): the
    solve context left by the singleton engine's most recent compute().
    None before any compute — the emitter fails closed on it."""
    return _ENGINE.solve_context() if _ENGINE is not None else None


def geometry_for(ep, excluded_sv: list[str] | None = None,
                 freeze: bool = False) -> dict:
    """Adapter for the `geometry_for(epoch)` seam in backend.replay.run.

    Takes Track A's Epoch (ep.time, ep.df indexed by SV) and returns the
    contract geometry block. `excluded_sv` stays empty until the 21:00
    threshold session fixes the per-SV exclusion rule — the block flows
    end to end either way, which is the 18:30 deliverable.
    """
    return compute_geometry_block(ep.time, excluded_sv or [],
                                  tracked_sv=list(ep.df.index),
                                  freeze=freeze)


def set_el_mask_deg(el_mask_deg: float) -> None:
    """Set the shared elevation mask on the singleton engine.

    Added for Track A coordination (ruling of 2026-09-05, item 1). The mask
    angle is ruled at 5 degrees and defined once, in
    `backend.rinex.solve.EL_MASK_DEG`; backend.replay pushes it here so the
    position solution and the information-ratio denominator stand on the same
    satellite set. The engine's own default (10 degrees) is stricter, and
    leaving the two unequal would move the ratio for reasons unrelated to any
    attack.
    """
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = GeometryEngine()
    _ENGINE.el_mask_deg = el_mask_deg


def set_sigma_uere(sigma_uere_m: float, alpha: float | None = None) -> None:
    """Called once the clean-day residual RMS is measured (21:00 session)."""
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = GeometryEngine()
    _ENGINE.sigma_uere_m = sigma_uere_m
    if alpha is not None:
        _ENGINE.alpha = alpha
