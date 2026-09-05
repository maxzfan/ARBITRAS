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
from backend.geometry.hmatrix import build_H, elevation_deg
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
        h_trusted, _, _ = build_H(trusted, self.rx_ecef) if trusted else (
            np.zeros((0, 3)), [], [])

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

        return {
            "information_ratio": round(ratio, 4),
            "excluded_sv": sorted(excluded),
            "displacement_bound_m": None if bound is None else round(bound, 1),
            "next_best_observation": next_best_observation(
                basis, sorted(trusted), self.rx_ecef),
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


def set_sigma_uere(sigma_uere_m: float, alpha: float | None = None) -> None:
    """Called once the clean-day residual RMS is measured (21:00 session)."""
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = GeometryEngine()
    _ENGINE.sigma_uere_m = sigma_uere_m
    if alpha is not None:
        _ENGINE.alpha = alpha
