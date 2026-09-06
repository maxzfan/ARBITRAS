"""Correction gate: five checks + hysteresis -> correction_ok (TRACK_D.md D3).

Decides whether the vehicle may drive on the corrected fix. Pure and
deterministic — no I/O, no wall clock — so Track C's Dirichlet sweep replays
it per weight draw and Track B's arbitras rule holds: no time inside the gate
(TRACK_B.md "Decisions made"). This module never decides state; the console
consumes `correction_ok` and must never recompute it (design.md §5 extension,
TRACK_D.md "CONTRACT EXTENSION REQUESTED").

Check semantics (TRACK_D.md "The gate — five checks, then hysteresis"):
each check returns True, False, or None (not evaluated). An epoch passes iff
every non-null check is True AND at least one check was evaluated. Hysteresis
matches the arbitras's asymmetry (design.md §8 invariant 2): correction_ok
turns ON only after GRANT_EPOCHS consecutive passing epochs, OFF on the first
non-passing epoch. One epoch can revoke, no single epoch can grant.
"""
from dataclasses import dataclass
from typing import Optional

import math

# Alert limit — corridor half-width (design.md §10: "error beyond which the
# situation is hazardous"). Must equal console/mission.py ALERT_LIMIT_M; the
# §5 boundary forbids importing console from backend, so backend/correction/
# test_gate.py asserts the two values are equal instead.
ALERT_LIMIT_M = 15.0
ALERT_LIMIT_PROVENANCE = "PLACEHOLDER"  # -> "agreed" once the team signs off

# Hysteresis grant window. Matches the arbitras's RECOVERY_EPOCHS asymmetry
# (console/arbitras/states.py). TRACK_D.md: "One epoch can revoke, no single
# epoch can grant." Policy, not fitted to data — defensible as stated in §8.
GRANT_EPOCHS = 10

# Chi-square cutoff for the weighted residual test and per-satellite residual
# threshold tau — FIT on the clean day (design.md §10 "Threshold procedure —
# by hand"; TRACK_D.md 21:00 session). Fit 2026-09-05 by
# `python -m backend.correction.validate` on the USN8 clean day (2026-08-20,
# data/USN800USA_R_20262320000_01D_30S_MO.crx.gz, 2,880 epochs, all solved),
# corrector run per epoch with the demo distrust rule; the fit never sees
# injected data (backend/replay.py header). Numbers + full distributions in
# out/correction_thresholds.json.
#
# CHI2_CUTOFF: p99.9 of the clean-day per-epoch statistic r^T W r / dof
# (2,880 samples; p50 2.79, p95 5.22, p99 6.32, max 7.96 m^2). Each clean
# exceedance is a false revoke costing a 10-epoch re-grant; p99.9 predicts
# ~3/day — measured clean-day correction_ok availability 0.9816.
CHI2_CUTOFF: Optional[float] = 7.703
CHI2_PROVENANCE = ("p99.9 of clean-day r'Wr/dof, USN8 2026-08-20, fit "
                   "2026-09-05 by backend.correction.validate")
# TAU_M: p99.9 of the clean-day per-SV |post-fit residual| pool over w>0
# rows (49,287 samples; p50 0.96, p95 2.82, p99 3.85, max 6.36 m). PL
# assumes an undetected single fault keeps its residual under tau; tau is
# the clean day's own p99.9 residual — below it a bias is indistinguishable
# from the clean floor (unmodelled iono/tropo leakage included: the fit
# absorbs it rather than assuming it away). Clean-day PL with this tau:
# p50 3.51 m, p95 4.95 m, max 6.67 m — under the 15 m alert limit.
TAU_M: Optional[float] = 5.187
TAU_PROVENANCE = ("p99.9 of clean-day per-SV |post-fit residual|, USN8 "
                  "2026-08-20, fit 2026-09-05 by backend.correction.validate")

# DR drift bound for the continuity check (check 4). On the replay, dead
# reckoning is "still at the antenna" with drift 0 + noise floor
# (TRACK_D.md), so the bound IS the measured clean-day noise floor of the
# corrected fix: p99.9 of |corrected − antenna| (3D — emit.py's continuity
# delta is 3D, and the 3D error carries the unmodelled single-frequency
# atmospheric bias, which the fit absorbs; clean p50 15.18 m, p95 18.92 m,
# max 21.36 m). Same fit run and dataset as above. On the RC car / a real
# UGV this must be re-measured against wheel-speed / IMU dead reckoning.
DRIFT_BOUND_M: Optional[float] = 20.672
DRIFT_BOUND_PROVENANCE = ("p99.9 of clean-day 3D |corrected − antenna|, "
                          "USN8 2026-08-20, fit 2026-09-05 by "
                          "backend.correction.validate")

# Redundancy floor (TRACK_D.md check 2): with no redundancy P is zero, the
# slope is infinite, and the residual test means nothing.
MIN_TRUSTED_SV = 5
TRUST_WEIGHT_FLOOR = 0.5


def pl_under_al(pl_m, alert_limit_m: float = ALERT_LIMIT_M):
    """Check 1: protection level strictly inside the alert limit.

    inf PL (no redundancy, unbounded slope) is False. None PL -> None.
    """
    if pl_m is None:
        return None
    if math.isinf(pl_m) or math.isnan(pl_m):
        return False
    return float(pl_m) < float(alert_limit_m)


def redundancy(w, k):
    """Check 2: >= MIN_TRUSTED_SV satellites with w > 0.5 and n_eff >= 3+k+1.

    w is the per-satellite trust weight vector, k the constellation count
    (H is n x (3+k), CLAUDE.md geometry correction).
    """
    weights = [float(x) for x in w]
    trusted = sum(1 for x in weights if x > TRUST_WEIGHT_FLOOR)
    n_eff = sum(weights)
    return trusted >= MIN_TRUSTED_SV and n_eff >= 3 + int(k) + 1


def residual_test(r, w, k, chi2_cutoff=CHI2_CUTOFF):
    """Check 3: r^T W r / (n_eff - (3+k)) below the clean-day cutoff.

    None cutoff -> None (not yet fit). Non-positive dof -> False: no
    redundancy means the test means nothing — fail closed (TRACK_D.md).
    """
    if chi2_cutoff is None:
        return None
    weights = [float(x) for x in w]
    residuals = [float(x) for x in r]
    dof = sum(weights) - (3 + int(k))
    if dof <= 0.0:
        return False
    stat = sum(wi * ri * ri for wi, ri in zip(weights, residuals)) / dof
    return stat < float(chi2_cutoff)


def continuity(corrected_dr_delta_m, drift_bound_m):
    """Check 4: |corrected - dead-reckoned| within the DR drift bound.

    The defence against a self-consistent majority spoof (TRACK_D.md check 4).
    Takes a precomputed scalar distance in metres; on the replay DR is "still
    at the antenna" with bound = noise floor. None delta -> None.
    """
    if corrected_dr_delta_m is None:
        return None
    return float(corrected_dr_delta_m) <= float(drift_bound_m)


def cross_constellation(*_args, **_kwargs):
    """Check 5: single-constellation fixes overlap the corrected fix.

    None until Track A's fourth feature is wired per-solution (TRACK_D.md
    check 5). The seam is kept so evaluate_checks emits the key today.
    """
    return None


def evaluate_checks(
    pl_m=None,
    w=None,
    k=None,
    r=None,
    corrected_dr_delta_m=None,
    drift_bound_m=None,
    alert_limit_m: float = ALERT_LIMIT_M,
    chi2_cutoff=CHI2_CUTOFF,
) -> dict:
    """Assemble the five checks under the exact §5-extension key names.

    Missing inputs yield None (not evaluated) for the dependent check, never
    a silent pass. Values are True | False | None, matching the contract's
    true/false/null.
    """
    return {
        "pl_under_al": pl_under_al(pl_m, alert_limit_m),
        "redundancy": (None if w is None or k is None else redundancy(w, k)),
        "residual_test": (
            None
            if r is None or w is None or k is None
            else residual_test(r, w, k, chi2_cutoff)
        ),
        "continuity": (
            None
            if corrected_dr_delta_m is None or drift_bound_m is None
            else continuity(corrected_dr_delta_m, drift_bound_m)
        ),
        "cross_constellation": cross_constellation(),
    }


def speed_scale(pl_m, alert_limit_m: float = ALERT_LIMIT_M) -> float:
    """Advisory speed factor: clamp((AL - PL) / AL, 0, 1). Emit, never act
    on it — same rule as the lateral offset (TRACK_D.md, design.md §8)."""
    if pl_m is None:
        return 0.0
    pl = float(pl_m)
    if math.isinf(pl) or math.isnan(pl):
        return 0.0
    al = float(alert_limit_m)
    return min(1.0, max(0.0, (al - pl) / al))


@dataclass(frozen=True)
class GateDecision:
    """One gate result per epoch. `checks` uses the §5-extension key names."""
    checks: dict
    correction_ok: bool
    speed_scale: float


class Gate:
    """Epoch-history hysteresis over the check dict. Stateful across epochs,
    one instance per replay — same shape as console/arbitras Arbitras.

    Pass: every non-null check True and at least one check non-null. An
    all-None dict is NOT a pass — nothing was evaluated, no grant progress —
    and, being a non-pass, it also revokes (fail closed). correction_ok is
    False through the first GRANT_EPOCHS-1 consecutive passes, True on the
    GRANT_EPOCHS-th, and drops to False on the first non-passing epoch with
    the counter reset to zero.
    """

    def __init__(self):
        self._run = 0            # consecutive passing epochs
        self._ok = False

    def reset(self) -> None:
        """Restart for a fresh replay (browser-reload rule, TRACK_B.md)."""
        self._run = 0
        self._ok = False

    def step(self, checks: dict, pl_m: float,
             alert_limit_m: float = ALERT_LIMIT_M) -> GateDecision:
        """Advance one epoch. Deterministic in (history, arguments) only."""
        evaluated = [v for v in checks.values() if v is not None]
        passing = bool(evaluated) and all(evaluated)
        if passing:
            self._run += 1
            if self._run >= GRANT_EPOCHS:
                self._ok = True
        else:
            # One epoch revokes; the grant run restarts from zero.
            self._run = 0
            self._ok = False
        return GateDecision(
            checks=dict(checks),
            correction_ok=self._ok,
            speed_scale=speed_scale(pl_m, alert_limit_m),
        )
