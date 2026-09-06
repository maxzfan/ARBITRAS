"""Contract + emitter: the `geometry.correction` block — TRACK_D.md D4.

Wires D1 (backend/correction/solve.py), D2 (protection.py) and D3 (gate.py)
into the per-epoch pipeline. Consumes Track C's solve context exactly as
GeometryEngine.solve_context() hands it over — H, sv/constellation order,
satellite positions and the linearisation point are NOT re-derived here
(TRACK_C.md: "it must not re-derive any of it"). Emits the §5-extension
block with the exact key names of TRACK_D.md "CONTRACT EXTENSION REQUESTED":

    corrected_position, protection_level_m, alert_limit_m, weights,
    trusted_count, checks, correction_ok

plus `speed_scale`, which TRACK_D.md mandates emitting ("Emit it; do not act
on it") without naming its slot; it rides in this block, additive, the same
way `score_detail` rides on the §5 record.

## Two different absences, treated differently

- **Distrusted** (`excluded_sv`, the detector's list): weight 0 inside the
  solve — TRACK_D.md's binary fallback (theta_low == theta_high). The
  invariant `{sv : w == 0} == excluded_sv` is asserted on the emitted
  weights, same as the sky.trusted assertion. Excluding every SV of a
  constellation makes its clock state unobservable and D1 raises
  RankDeficientError — caught here, emitted as a fail-closed block
  (corrected_position null, correction_ok false), never garbage.
- **Uncorrectable** (no clock-corrected pseudorange: outside the nav tables'
  G+E coverage, or unhealthy at this epoch): outside the corrector's domain.
  Its H row is dropped, and a clock column left with no supporting row is
  dropped with it — row/column selection of Track C's H, identical by
  construction to build_H over the subset (rows sorted by sv, one-hot clock
  columns sorted; CLAUDE.md: drop the column, never carry rank deficiency).
  These SVs carry no weight entry: there is no measurement to weight.

## d_rho — reused machinery, not reimplemented

d_rho_i = observed pseudorange + c*dt_sv − predicted range at x_lin, via the
validated fix machinery in backend/geometry/solve.py: `epoch_inputs` for the
broadcast clock polynomial (relativistic + band-1 group delay included) and
`geometric_range` for the Sagnac-rotated model range. x_lin is the context's
rx_ecef — the position frozen at the last NOMINAL epoch, not the live
believed one (TRACK_D.md: "Under attack the live one is the attacker's
frame"). Iono/tropo are ignored as near common-mode (TRACK_D.md README note).

## Purity and state

`correction_block` is pure in (arguments, gate history): no wall clock, no
I/O — Track C's Dirichlet sweep can replay it per draw. The Gate instance is
owned by the caller, one per replay (CorrectionEmitter bundles one and is
reset per replay, mirroring CrossConstellation.reset()).

tau_m / chi2_cutoff default to gate.py's TAU_M / CHI2_CUTOFF, read at call
time (PLACEHOLDER None until the clean-day fit). While tau_m is None no PL
can exist: protection_level_m is null and pl_under_al is None (not
evaluated), the same mechanism as the console's PLACEHOLDER banner.
"""
from __future__ import annotations

import math

import numpy as np

from backend.correction import gate as gate_mod
from backend.correction.gate import (ALERT_LIMIT_M, Gate, TRUST_WEIGHT_FLOOR,
                                     evaluate_checks)
from backend.correction.protection import protection_level
from backend.correction.solve import RankDeficientError, weighted_solve
from backend.detection.emit import ecef_to_lla
from backend.geometry.solve import (C_LIGHT, epoch_inputs, geometric_range)

# "Use gate.py's current module value" sentinel: D5 sets TAU_M / CHI2_CUTOFF
# at the 21:00 fit, and reading them per call (not at import) means a replay
# started after the fit sees the fitted numbers without re-importing.
_MODULE = object()


def d_rho_from_epoch(ep, nav, x_lin, svs=None) -> dict:
    """{sv: observed pseudorange − predicted range at x_lin}, SV clock removed.

    Reuses backend/geometry/solve.py end to end: `epoch_inputs` supplies the
    band-1 pseudorange, the transmit-time satellite ECEF and the broadcast
    clock (polynomial + relativistic + group delay); `geometric_range` the
    Sagnac-rotated model range. Nothing about the satellite clock is
    re-derived here (TRACK_D.md: "gnss-lib-py does this; use it" — this repo
    validated its own equivalent in the differential WLS instead).

    Only SVs the nav tables cover appear (G+E); `svs` restricts and orders
    nothing — the result is a lookup map, keyed by SV.
    """
    P, S, dt = epoch_inputs(ep, nav)
    want = svs if svs is not None else sorted(P)
    return {sv: float(P[sv] + C_LIGHT * dt[sv] - geometric_range(S[sv], x_lin))
            for sv in want if sv in P}


def _emit(gate: Gate, checks: dict, pl_m, corrected_lla, weights: dict,
          alert_limit_m: float) -> dict:
    """Step the gate and assemble the contract block. One exit for all paths,
    so the key set cannot drift between the success and failure branches."""
    decision = gate.step(checks, pl_m, alert_limit_m)
    # JSON has no Infinity: an unbounded PL (unmonitorable satellite) is
    # emitted as null; pl_under_al already reads False for it, so the gate,
    # not the display, carries the bad news.
    pl_out = (None if pl_m is None or not math.isfinite(pl_m)
              else round(float(pl_m), 2))
    return {
        "corrected_position": corrected_lla,
        "protection_level_m": pl_out,
        "alert_limit_m": float(alert_limit_m),
        "weights": {sv: float(weights[sv]) for sv in sorted(weights)},
        "trusted_count": sum(1 for v in weights.values()
                             if v > TRUST_WEIGHT_FLOOR),
        "checks": decision.checks,
        "correction_ok": decision.correction_ok,
        "speed_scale": round(decision.speed_scale, 4),
    }


def correction_block(context, d_rho_by_sv: dict, excluded_sv, gate: Gate,
                     tau_m=_MODULE, chi2_cutoff=_MODULE,
                     alert_limit_m: float = ALERT_LIMIT_M,
                     drift_bound_m=None, dr_ecef=None) -> dict:
    """One epoch's `geometry.correction` block. See module docstring.

    context      GeometryEngine.solve_context() dict (or None: fail closed).
    d_rho_by_sv  {sv: d_rho at context rx_ecef} — d_rho_from_epoch().
    excluded_sv  the detector's distrust list (§5 geometry.excluded_sv).
    gate         the caller's per-replay Gate — hysteresis lives there.
    drift_bound_m / dr_ecef   continuity inputs (check 4). dr_ecef defaults
                 to x_lin: on the replay, dead reckoning is "still at the
                 antenna" (TRACK_D.md). drift_bound_m has NO default number —
                 None means the bound is not yet measured and the check reads
                 None (not evaluated), never a silently-passed guess.

    Missing inputs degrade to the fail-closed block: corrected_position and
    protection_level_m null, checks not evaluated, and the gate stepped with
    an all-None dict, which is a revoke (gate.py: nothing evaluated is not a
    pass). Never a silent pass, never a skipped gate epoch.
    """
    if tau_m is _MODULE:
        tau_m = gate_mod.TAU_M
    if chi2_cutoff is _MODULE:
        chi2_cutoff = gate_mod.CHI2_CUTOFF

    excluded = set(excluded_sv or [])
    weights = {sv: 0.0 for sv in excluded}

    if context is None or not d_rho_by_sv:
        return _emit(gate, evaluate_checks(alert_limit_m=alert_limit_m,
                                           chi2_cutoff=chi2_cutoff),
                     None, None, weights, alert_limit_m)

    h = np.asarray(context["H_trusted"], dtype=float)
    sv_order = list(context["sv_order"])
    x_lin = np.asarray(context["rx_ecef"], dtype=float)

    rows = [i for i, sv in enumerate(sv_order)
            if math.isfinite(d_rho_by_sv.get(sv, math.nan))]
    if not rows:
        return _emit(gate, evaluate_checks(alert_limit_m=alert_limit_m,
                                           chi2_cutoff=chi2_cutoff),
                     None, None, weights, alert_limit_m)

    svs = [sv_order[i] for i in rows]
    h_sub = h[rows]
    # Clock columns with no supporting row (constellation entirely outside
    # the correctable domain) are dropped — identical to build_H over the
    # subset, whose sorted one-hot construction never creates them.
    cols = [0, 1, 2] + [j for j in range(3, h_sub.shape[1])
                        if np.any(h_sub[:, j] != 0.0)]
    h_sub = h_sub[:, cols]
    k = h_sub.shape[1] - 3

    w = np.array([0.0 if sv in excluded else 1.0 for sv in svs])
    for sv, wi in zip(svs, w):
        weights[sv] = float(wi)
    # TRACK_D.md: "Track C's excluded_sv must equal {sv : w == 0} — assert
    # it, same as the sky.trusted assertion."
    zero_set = {sv for sv, v in weights.items() if v == 0.0}
    assert zero_set == excluded, (
        f"weight/exclusion mismatch: w==0 {sorted(zero_set)} "
        f"!= excluded_sv {sorted(excluded)}")

    d_rho = np.array([d_rho_by_sv[sv] for sv in svs], dtype=float)
    try:
        sol = weighted_solve(h_sub, w, d_rho)
    except RankDeficientError:
        # The weighted system cannot support the (3+k) state (D1, GOTCHA
        # 2a): redundancy in TRACK_D.md check 2's own sense — "with no
        # redundancy P is zero, the slope is infinite, and the test means
        # nothing" — is False, whatever the raw counts say.
        checks = evaluate_checks(pl_m=None, w=w, k=k, r=None,
                                 alert_limit_m=alert_limit_m,
                                 chi2_cutoff=chi2_cutoff)
        checks["redundancy"] = False
        return _emit(gate, checks, None, None, weights, alert_limit_m)

    # TRACK_D.md D4: corrected_position must be finite when emitted.
    assert np.all(np.isfinite(sol.dx[:3])), \
        f"non-finite correction dx={sol.dx[:3]} at t={context.get('t')}"
    corrected = x_lin + sol.dx[:3]

    pl_m = None
    if tau_m is not None:
        pl_m = protection_level(sol.S, sol.P, float(tau_m)).pl_m

    dr_ref = x_lin if dr_ecef is None else np.asarray(dr_ecef, dtype=float)
    delta = float(np.linalg.norm(corrected - dr_ref))
    checks = evaluate_checks(pl_m=pl_m, w=w, k=k, r=sol.r,
                             corrected_dr_delta_m=delta,
                             drift_bound_m=drift_bound_m,
                             alert_limit_m=alert_limit_m,
                             chi2_cutoff=chi2_cutoff)
    return _emit(gate, checks, pl_m, ecef_to_lla(*corrected), weights,
                 alert_limit_m)


def _engine_context():
    """Default context source: Track C's singleton engine, read after the
    epoch's geometry block was computed (the seams call geometry first)."""
    from backend.geometry import engine
    return engine.last_solve_context()


class CorrectionEmitter:
    """The `corrector(ep, excluded_sv)` seam for backend.replay.run and
    backend.demo.score_stream. One instance per replay: owns the Gate
    (hysteresis history) and the nav tables. reset() before each replay,
    the same discipline as CrossConstellation.reset().

    Must be called AFTER the epoch's geometry block: it reads the solve
    context Track C left behind for exactly this epoch. With no context or
    no nav tables it emits the fail-closed block, never a silent pass.
    """

    def __init__(self, nav=None, tau_m=_MODULE, chi2_cutoff=_MODULE,
                 alert_limit_m: float = ALERT_LIMIT_M,
                 drift_bound_m=None, context_fn=None):
        self.nav = nav
        self.gate = Gate()
        self.tau_m = tau_m
        self.chi2_cutoff = chi2_cutoff
        self.alert_limit_m = alert_limit_m
        self.drift_bound_m = drift_bound_m
        self._context_fn = context_fn or _engine_context

    def reset(self) -> None:
        """Fresh replay: hysteresis history restarts (browser-reload rule)."""
        self.gate.reset()

    def __call__(self, ep, excluded_sv) -> dict:
        context = self._context_fn()
        d_rho = {}
        if context is not None and self.nav is not None:
            d_rho = d_rho_from_epoch(ep, self.nav, context["rx_ecef"],
                                     context["sv_order"])
        return correction_block(context, d_rho, excluded_sv, self.gate,
                                tau_m=self.tau_m,
                                chi2_cutoff=self.chi2_cutoff,
                                alert_limit_m=self.alert_limit_m,
                                drift_bound_m=self.drift_bound_m)
