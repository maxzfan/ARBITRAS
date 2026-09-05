"""D5 — validation and thresholds for the correction track (TRACK_D.md D5).

    python -m backend.correction.validate            # full day + scenarios
    python -m backend.correction.validate --stride 30 --scenario carry_off

Runs the corrector over the real USN8 clean day (same OBS path and onset as
backend.replay), FITS the gate thresholds from the clean-day distributions
(design.md §10 "Threshold procedure — by hand": stated percentile, reasoning
printed, never a round number without a reason), then replays the injected
§7 scenarios with the fitted numbers and reports:

  1. the CUT-RULE number (TRACK_D.md "Cut rule"): the clean-day distribution
     of |corrected − antenna|. If the corrected fix does not sit on the
     antenna within a few metres, D4/D5 get cut. Reported prominently; the
     decision belongs to the team, not to this script.
  2. tau_m — the per-satellite residual threshold behind the protection
     level. PL = tau * max slope claims "a single fault the residual monitor
     has not caught displaces the fix by at most PL"; tau is therefore the
     largest per-SV post-fit residual the clean day produces at the stated
     percentile — anything under it is indistinguishable from the clean
     floor, which is exactly PL's premise.
  3. the chi-square cutoff for the weighted residual test (gate check 3),
     the same percentile of the clean-day statistic r^T W r / dof.
  4. the DR drift bound for the continuity check (gate check 4): on this
     replay dead reckoning is "still at the antenna" (TRACK_D.md), so the
     clean-day |corrected − antenna| tail IS the measured drift-0-plus-noise
     floor.
  5. per scenario: gate timing (epochs from onset to revoke, to exclusions
     landing, to re-grant on the trusted subset) and the INTEGRITY count —
     epochs with correction_ok true while the ATTACK-INDUCED displacement
     of the corrected fix exceeds PL. §10: "If the empirical number ever
     exceeds the bound, the bound is wrong — that check is worth running
     explicitly and mentioning that it was run." The empirical number is
     the differential |corrected(injected) − corrected(clean)| at the same
     epoch — the house differential (backend/geometry/solve.differential):
     iono/tropo/ephemeris are common to both runs and cancel, so what
     remains is the position effect of exactly what the injector changed.
     PL is a single-FAULT bound and carries no nominal-noise term (aviation
     adds K·sigma); the fault-free nominal error is reported separately as
     a diagnostic, and the missing nominal term is a README limitation.

The clean-day fit never sees injected data (backend/replay.py: "Fitting on
injected data would let the attack define its own normal"). The fitted
numbers are printed, written to out/correction_thresholds.json, and then
copied BY HAND into backend/correction/gate.py with provenance comments —
this module does not rewrite source files.

Truth on this replay is the surveyed USN8 antenna: the receiver is static,
so |corrected − antenna| is both the cut-rule error and the continuity
delta. Iono/tropo are unmodelled and near common-mode (TRACK_D.md); the
part that does not cancel into the clock columns is real error and is IN
the clean-day distributions the thresholds are fit on — the fit absorbs it
rather than assuming it away.

The per-epoch solve mirrors backend/correction/emit.correction_block's
row/column subsetting exactly (rows without a clock-corrected pseudorange
dropped, unsupported clock columns dropped with them). It is duplicated
here, instrumented, because the emitter's contract block does not expose
r, S, P and D5 must not change emit.py; keep the two in lockstep.
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

from backend.correction.emit import d_rho_from_epoch
from backend.correction.gate import ALERT_LIMIT_M, Gate, evaluate_checks
from backend.correction.protection import slopes as fault_slopes
from backend.correction.solve import RankDeficientError, weighted_solve
from backend.detection import FeatureExtractor, fit
from backend.detection.emit import ecef_to_lla
from backend.geometry.solve import NavTables, enu_basis, solve_epoch
from backend.replay import OBS, ONSET

OUT_JSON = Path("out/correction_thresholds.json")
PLOTS = Path("docs/plots")

# The one fitting parameter, stated once and used for all three thresholds:
# the p99.9 tail of the clean-day distribution. Reasoning per threshold is
# printed by main(); the shared shape is: the threshold must sit above what
# the clean day produces (each exceedance is a false alarm that revokes
# correction_ok and costs a 10-epoch re-grant), and p99.9 leaves ~1 in 1000
# clean samples above it — ~3 epochs over the 2,880-epoch day for the
# per-epoch tests, an availability cost bounded near 1%, which the clean
# replay below then MEASURES rather than assumes. Not a round number pulled
# from the attack: the fit never sees injected data.
FIT_PERCENTILE = 99.9


# --------------------------------------------------------------- pure helpers

def fit_threshold(values, q: float = FIT_PERCENTILE) -> float:
    """Empirical percentile of a clean-day sample — the §10 by-hand pick.

    Raises on an empty pool rather than inventing a number (CLAUDE.md:
    thresholds derive from observed data, never a guess).
    """
    arr = np.asarray([v for v in values if v is not None and math.isfinite(v)],
                     dtype=float)
    if arr.size == 0:
        raise ValueError("empty sample: refusing to fabricate a threshold")
    return float(np.percentile(arr, q))


def distribution(values) -> dict:
    """p50/p95/p99/p99.9/max of a sample, for printing next to every fit."""
    arr = np.asarray([v for v in values if v is not None and math.isfinite(v)],
                     dtype=float)
    if arr.size == 0:
        return {"n": 0}
    return {"n": int(arr.size),
            "p50": float(np.percentile(arr, 50)),
            "p95": float(np.percentile(arr, 95)),
            "p99": float(np.percentile(arr, 99)),
            "p99.9": float(np.percentile(arr, 99.9)),
            "max": float(arr.max())}


def exceedances(err_m, pl_m, ok) -> list[int]:
    """Indices where correction_ok held AND the actual error exceeded PL.

    This is the integrity claim (§10 / TRACK_D.md plot 4): any hit means PL
    is wrong. The caller decides WHICH error: the §10 check proper feeds the
    attack-induced differential (attack_displacement — PL is a fault bound);
    feeding the total-vs-antenna error instead measures the fault-free
    nominal tail PL does not claim to cover (diagnostic, README limitation).
    Epochs with no error or no finite PL make no claim and are skipped —
    but an OK epoch with an error and NO PL cannot happen by construction
    (pl_under_al is False/None without a finite PL, and the gate never
    grants on it).
    """
    hits = []
    for i, (e, p, k) in enumerate(zip(err_m, pl_m, ok)):
        if not k or e is None or p is None or not math.isfinite(p):
            continue
        if e > p:
            hits.append(i)
    return hits


def first_index(flags, start: int = 0):
    """First index >= start whose flag is truthy, or None."""
    for i in range(start, len(flags)):
        if flags[i]:
            return i
    return None


def availability(ok) -> float:
    """Fraction of epochs with correction_ok true (clean-day continuity cost
    of the fitted thresholds — measured, not the back-of-envelope bound)."""
    return sum(1 for v in ok if v) / len(ok) if len(ok) else 0.0


# ------------------------------------------------------------- per-epoch pass

@dataclass
class EpochSol:
    """One instrumented epoch: what emit.correction_block computes, with the
    internals (r, slopes) the contract block does not expose."""
    t: datetime
    k: int
    svs: list
    w: np.ndarray
    r: np.ndarray
    slopes: np.ndarray            # per-SV fault slopes (protection.slopes)
    n_eff: float
    dof: float
    corrected_ecef: np.ndarray
    err3d_m: float                # |corrected − antenna|, 3D
    err_h_m: float                # horizontal component of the same
    excluded: list
    believed_err_h_m: float | None = None   # injected passes only


def epoch_solution(context, d_rho_by_sv: dict, excluded) -> EpochSol | None:
    """Mirror of emit.correction_block's subsetting + solve (see module
    docstring). None = the fail-closed cases (no rows, rank-deficient)."""
    if context is None or not d_rho_by_sv:
        return None
    h = np.asarray(context["H_trusted"], dtype=float)
    sv_order = list(context["sv_order"])
    x_lin = np.asarray(context["rx_ecef"], dtype=float)
    excluded = set(excluded or [])

    rows = [i for i, sv in enumerate(sv_order)
            if math.isfinite(d_rho_by_sv.get(sv, math.nan))]
    if not rows:
        return None
    svs = [sv_order[i] for i in rows]
    h_sub = h[rows]
    cols = [0, 1, 2] + [j for j in range(3, h_sub.shape[1])
                        if np.any(h_sub[:, j] != 0.0)]
    h_sub = h_sub[:, cols]
    k = h_sub.shape[1] - 3

    w = np.array([0.0 if sv in excluded else 1.0 for sv in svs])
    d_rho = np.array([d_rho_by_sv[sv] for sv in svs], dtype=float)
    try:
        sol = weighted_solve(h_sub, w, d_rho)
    except RankDeficientError:
        return None

    corrected = x_lin + sol.dx[:3]
    lla = ecef_to_lla(*x_lin)
    enu = enu_basis(lla["lat"], lla["lon"]) @ (corrected - x_lin)
    return EpochSol(
        t=context["t"], k=k, svs=svs, w=w, r=sol.r,
        slopes=fault_slopes(sol.S, sol.P), n_eff=sol.n_eff, dof=sol.dof,
        corrected_ecef=corrected,
        err3d_m=float(np.linalg.norm(corrected - x_lin)),
        err_h_m=float(np.hypot(enu[0], enu[1])),
        excluded=sorted(excluded))


def run_pass(epochs, cal, nav, label: str = "", believed: bool = False,
             stride: int = 1) -> list[EpochSol | None]:
    """One full replay through detector -> geometry -> instrumented solve.

    Mirrors backend.demo.score_stream's driving (engine-side exclusion via
    the distrust rule that is already in the system; test_emit shows the
    engine-side and weight-side paths give the identical fix). `believed`
    additionally solves the all-in-view WLS fix from the (injected)
    pseudoranges — what the receiver believes, for plot 3.
    """
    from backend.demo import distrusted            # the one distrust rule
    from backend.geometry.engine import (compute_geometry_block,
                                         last_solve_context)

    fx = FeatureExtractor(cal)
    out = []
    epochs = epochs[::stride]
    n = len(epochs)
    for i, ep in enumerate(epochs):
        res = fx.step(ep)
        excluded = distrusted(res["per_sv"], cal)
        compute_geometry_block(ep.time, excluded, tracked_sv=list(ep.df.index))
        ctx = last_solve_context()
        d_rho = {}
        if ctx is not None:
            d_rho = d_rho_from_epoch(ep, nav, ctx["rx_ecef"], ctx["sv_order"])
        sol = epoch_solution(ctx, d_rho, excluded)
        if sol is not None and believed:
            fix = solve_epoch(ep, nav)
            if fix is not None:
                x_lin = np.asarray(ctx["rx_ecef"], dtype=float)
                lla = ecef_to_lla(*x_lin)
                enu = enu_basis(lla["lat"], lla["lon"]) @ (fix.ecef - x_lin)
                sol.believed_err_h_m = float(np.hypot(enu[0], enu[1]))
        out.append(sol)
        if i % 500 == 0:
            print(f"  {label} {i:5d}/{n}", flush=True)
    return out


# ---------------------------------------------------------------- gate replay

def gate_replay(sols, tau_m: float, chi2_cutoff: float, drift_bound_m: float,
                alert_limit_m: float = ALERT_LIMIT_M) -> dict:
    """Replay the D3 gate over an instrumented pass with FITTED thresholds.

    Pure in (sols, arguments): evaluate_checks + Gate are exactly the
    contract path (gate.py); PL = tau * max slope is exactly protection.py's
    single-fault form. Returns parallel per-epoch lists.
    """
    gate = Gate()
    pl, ok, checks_out, resid_fail = [], [], [], []
    for s in sols:
        if s is None:
            checks = evaluate_checks(chi2_cutoff=chi2_cutoff,
                                     alert_limit_m=alert_limit_m)
            p = None
        else:
            smax = float(np.max(s.slopes)) if s.slopes.size else math.inf
            p = tau_m * smax
            checks = evaluate_checks(
                pl_m=p, w=s.w, k=s.k, r=s.r,
                corrected_dr_delta_m=s.err3d_m, drift_bound_m=drift_bound_m,
                alert_limit_m=alert_limit_m, chi2_cutoff=chi2_cutoff)
        d = gate.step(checks, p, alert_limit_m)
        pl.append(p)
        ok.append(d.correction_ok)
        checks_out.append(checks)
        resid_fail.append(checks["residual_test"] is False)
    return {"pl": pl, "ok": ok, "checks": checks_out,
            "residual_fail": resid_fail}


def attack_displacement(sols, ref_sols) -> tuple[list, list]:
    """Attack-induced displacement of the corrected fix, per epoch.

    |corrected(injected run) − corrected(clean run)| at the same epoch —
    the same differential the believed-position displacement uses
    (backend/geometry/solve.differential): everything unmodelled and common
    to both runs (iono, tropo, ephemeris error) cancels, so what remains is
    the position effect of exactly what the injector changed, including any
    change it caused in the exclusion set. Zero to numerical precision on
    pre-onset epochs. Returns (d3, dh) lists in metres, None where either
    run has no solve.
    """
    from backend.detection.emit import USN8_ECEF
    lla = ecef_to_lla(*USN8_ECEF)
    R = enu_basis(lla["lat"], lla["lon"])
    d3, dh = [], []
    for s, ref in zip(sols, ref_sols):
        if s is None or ref is None:
            d3.append(None)
            dh.append(None)
            continue
        assert s.t == ref.t, f"epoch misalignment {s.t} != {ref.t}"
        d = s.corrected_ecef - ref.corrected_ecef
        e = R @ d
        d3.append(float(np.linalg.norm(d)))
        dh.append(float(np.hypot(e[0], e[1])))
    return d3, dh


def residual_stat(s: EpochSol) -> float | None:
    """The gate-3 statistic r^T W r / dof for one epoch, None when dof<=0
    (same fail-closed convention as gate.residual_test)."""
    if s.dof <= 0.0:
        return None
    return float(np.sum(s.w * s.r * s.r) / s.dof)


# --------------------------------------------------------------------- plots

def _style_ax(ax, title, ylabel):
    ax.set_title(title)
    ax.set_ylabel(ylabel)
    ax.set_xlabel("GPS time")
    ax.grid(alpha=0.3)


def plot_clean_error(sols, out: Path) -> None:
    """Plot 1 (TRACK_D.md D5): clean-day corrected fix vs the antenna."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    pts = [(s.t, s.err_h_m, s.err3d_m) for s in sols if s is not None]
    ts, eh, e3 = zip(*pts)
    fig, ax = plt.subplots(figsize=(11, 4))
    ax.plot(ts, e3, lw=0.8, color="tab:blue", label="|corrected − antenna| 3D")
    ax.plot(ts, eh, lw=0.8, color="tab:green", label="horizontal component")
    med = float(np.median(e3))
    ax.axhline(med, color="tab:blue", ls="--", lw=0.8,
               label=f"3D median {med:.2f} m")
    _style_ax(ax, "Clean day — corrected fix vs surveyed antenna (cut rule)",
              "metres")
    ax.legend(loc="upper right")
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=150)
    plt.close(fig)


def plot_clean_pl(sols, replay, tau_m: float, out: Path) -> None:
    """Plot 2: clean-day protection level, with the alert limit on the axis
    — the first defensible alert-limit argument (TRACK_D.md)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    pts = [(s.t, p) for s, p in zip(sols, replay["pl"])
           if s is not None and p is not None and math.isfinite(p)]
    ts, pl = zip(*pts)
    fig, ax = plt.subplots(figsize=(11, 4))
    ax.plot(ts, pl, lw=0.8, color="tab:blue",
            label=f"PL = tau × max slope (tau {tau_m:.2f} m)")
    ax.axhline(ALERT_LIMIT_M, color="tab:red", ls="--", lw=1.0,
               label=f"alert limit {ALERT_LIMIT_M:.0f} m")
    _style_ax(ax, "Clean day — weighted-RAIM protection level", "metres")
    ax.legend(loc="upper right")
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=150)
    plt.close(fig)


def plot_positions(sols, replay, onset: datetime, out: Path,
                   window_h: float = 3.0) -> None:
    """Plot 3: believed vs corrected vs truth around the onset, with the
    exclusion-landing and correction_ok-grant epochs marked."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    rows = [(s.t, s.believed_err_h_m, s.err_h_m, s.excluded, o)
            for s, o in zip(sols, replay["ok"]) if s is not None]
    lo = onset.timestamp() - 1800
    hi = onset.timestamp() + window_h * 3600
    rows = [r for r in rows if lo <= r[0].timestamp() <= hi]
    ts = [r[0] for r in rows]
    bel = [r[1] if r[1] is not None else np.nan for r in rows]
    cor = [r[2] for r in rows]

    fig, ax = plt.subplots(figsize=(11, 4))
    ax.plot(ts, bel, lw=1.2, color="tab:red",
            label="believed |fix − antenna| (all-in-view, spoofed)")
    ax.plot(ts, cor, lw=1.2, color="tab:blue",
            label="corrected |fix − antenna| (trusted subset)")
    ax.axhline(0.0, color="k", lw=0.5)
    ax.axvline(onset, color="k", ls=":", lw=1.0, label="attack onset")

    after = [i for i, r in enumerate(rows) if r[0] >= onset]
    if after:
        a0 = after[0]
        i_ex = first_index([bool(rows[i][3]) for i in range(len(rows))], a0)
        if i_ex is not None:
            ax.axvline(ts[i_ex], color="tab:orange", ls="--", lw=1.0,
                       label=f"exclusions land ({','.join(rows[i_ex][3])})")
        i_ok = first_index([rows[i][4] for i in range(len(rows))], a0)
        if i_ok is not None:
            ax.axvline(ts[i_ok], color="tab:green", ls="--", lw=1.0,
                       label="correction_ok grants")
    ax.set_yscale("symlog", linthresh=10.0)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    _style_ax(ax, "Injected run — believed vs corrected horizontal error "
                  "(truth = antenna; symlog above 10 m)", "metres")
    ax.legend(loc="upper left", fontsize=8)
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=150)
    plt.close(fig)


def plot_error_vs_pl(sols, replay, d3, out: Path, n_exceed: int) -> None:
    """Plot 4: empirical attack-induced corrected displacement vs PL over
    the injected day, exceedance count in the title — the integrity claim,
    run and said."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rows = [(s.t, e, p, o) for s, e, p, o in
            zip(sols, d3, replay["pl"], replay["ok"]) if s is not None]
    ts = [r[0] for r in rows]
    err = [r[1] if r[1] is not None else np.nan for r in rows]
    pl = [r[2] if r[2] is not None and math.isfinite(r[2]) else np.nan
          for r in rows]
    fig, ax = plt.subplots(figsize=(11, 4))
    ax.plot(ts, err, lw=0.8, color="tab:red",
            label="attack-induced corrected displacement "
                  "(3D differential vs clean run)")
    ax.plot(ts, pl, lw=0.8, color="tab:blue", label="protection level")
    okt = [t for t, _, _, o in rows if o]
    ax.plot(okt, [0.0] * len(okt), "|", color="tab:green", ms=4,
            label="correction_ok epochs")
    ax.set_yscale("symlog", linthresh=10.0)
    _style_ax(ax, f"Injected run — attack-induced corrected displacement vs "
                  f"PL (exceedances while correction_ok: {n_exceed})",
              "metres")
    ax.legend(loc="upper left", fontsize=8)
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=150)
    plt.close(fig)


# ---------------------------------------------------------------- scenarios

def build_scenarios(onset: datetime) -> dict:
    """The §7 scenario set, same onset as backend.replay.

    carry_off / clock_carry_off need the carrier_rate_error pin, which Eric
    left deliberately unset; the TEST value backend.demo uses (0.02 m/s,
    from tests/test_detection.py) is used here under the same label. The
    carry_off subset is demo's top-6-by-elevation rule: the all-GPS default
    is a pure clock shift and moves the believed position 0.0000 m
    (backend/demo.py), which validates nothing about a position corrector.
    """
    from backend.demo import CARRIER_RATE_ERROR_MPS
    from backend.injector import SCENARIOS, top_n_by_elevation
    return {
        "simplistic": SCENARIOS["simplistic"](onset=onset),
        "meaconing": SCENARIOS["meaconing"](onset=onset),
        "clock_carry_off": SCENARIOS["clock_carry_off"](
            onset=onset, carrier_rate_error=CARRIER_RATE_ERROR_MPS),
        "carry_off": SCENARIOS["carry_off"](
            onset=onset, carrier_rate_error=CARRIER_RATE_ERROR_MPS,
            target_svs=top_n_by_elevation(6)),
    }


def scenario_report(name, sols, replay, truth, onset, tau_m,
                    d3, dh) -> dict:
    """Gate timing + integrity numbers for one injected pass.

    d3/dh are the attack-induced differentials from attack_displacement();
    the integrity count compares the 3D differential against PL (the slope
    is a 3D bound, and 3D >= horizontal makes this the strictest reading).
    """
    idx = [i for i, s in enumerate(sols)]
    times = [s.t if s is not None else None for s in sols]
    onset_i = first_index([t is not None and t >= onset for t in times])
    ok = replay["ok"]

    revoke_i = first_index([not v for v in ok], onset_i)
    excl_i = first_index([sols[i] is not None and bool(sols[i].excluded)
                          for i in idx], onset_i)
    regrant_i = (first_index(ok, revoke_i)
                 if revoke_i is not None else None)

    hits = exceedances(d3, replay["pl"], ok)

    # k-fault exposure (README limitation: single-fault PL is a lower bound)
    k_att = int(truth["n_spoofed"].max())
    pl_k = [tau_m * float(np.sort(s.slopes)[::-1][:k_att].sum())
            for i, s in enumerate(sols)
            if s is not None and times[i] >= onset and s.slopes.size]

    ok_d3 = [e for e, o in zip(d3, ok) if o and e is not None]
    ok_dh = [e for e, o in zip(dh, ok) if o and e is not None]
    ok_nom = [s.err_h_m for s, o in zip(sols, ok) if o and s is not None]
    return {
        "scenario": name,
        "onset_epoch": onset_i,
        "ok_at_onset": bool(ok[onset_i - 1]) if onset_i else None,
        "epochs_to_revoke": (None if revoke_i is None
                             else revoke_i - onset_i),
        "epochs_to_exclusions": (None if excl_i is None
                                 else excl_i - onset_i),
        "epochs_to_regrant": (None if regrant_i is None
                              else regrant_i - onset_i),
        "integrity_violations": len(hits),
        "violation_epochs": [str(times[i]) for i in hits[:5]],
        "max_attack_disp_3d_while_ok_m": (float(max(ok_d3)) if ok_d3
                                          else None),
        "max_attack_disp_h_while_ok_m": (float(max(ok_dh)) if ok_dh
                                         else None),
        "max_nominal_err_h_while_ok_m": (float(max(ok_nom)) if ok_nom
                                         else None),
        "availability": availability(ok),
        "k_faults_at_peak": k_att,
        "pl_k_median_m": (float(np.median(pl_k)) if pl_k else None),
    }


# ----------------------------------------------------------------------- main

def main(argv=None) -> None:
    from backend.injector import inject, summarise
    from backend.rinex import noise
    from backend.rinex.loader import load_obs

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--obs", default=OBS)
    ap.add_argument("--systems", default="GERCS")
    ap.add_argument("--onset", default=ONSET.isoformat())
    ap.add_argument("--stride", type=int, default=1,
                    help="epoch stride, debugging only (fits require 1)")
    ap.add_argument("--scenario", default="all",
                    help="one scenario name, or 'all'")
    ap.add_argument("--out", default=str(OUT_JSON))
    ap.add_argument("--plots", default=str(PLOTS))
    args = ap.parse_args(argv)
    onset = datetime.fromisoformat(args.onset)
    plots = Path(args.plots)

    print("load + clean-only calibration", flush=True)
    clean = load_obs(args.obs, systems=args.systems)
    floor = noise.measure(clean)
    cal = fit(clean, floor)
    nav = NavTables.load()

    # ---- clean pass (the ONLY data the fits see) --------------------------
    print("clean pass (instrumented corrector, full day)", flush=True)
    clean_sols = run_pass(clean, cal, nav, label="clean", stride=args.stride)
    solved = [s for s in clean_sols if s is not None]
    print(f"  solved {len(solved)}/{len(clean_sols)} epochs")

    # ---- CUT RULE ---------------------------------------------------------
    e3 = distribution([s.err3d_m for s in solved])
    eh = distribution([s.err_h_m for s in solved])
    print("\n== CUT RULE (TRACK_D.md): clean-day |corrected − antenna| ==")
    print(f"  3D          median {e3['p50']:.2f} m   p95 {e3['p95']:.2f} m   "
          f"max {e3['max']:.2f} m")
    print(f"  horizontal  median {eh['p50']:.2f} m   p95 {eh['p95']:.2f} m   "
          f"max {eh['max']:.2f} m")
    print("  Rule: 'corrected fix on the antenna within a few metres'. The "
          "numbers above are the evidence; the cut decision is the team's.")

    # ---- fits (clean only, stated percentile, reasoning printed) ----------
    resid_pool = np.concatenate(
        [np.abs(s.r[s.w > 0.0]) for s in solved if s.r.size])
    stat_pool = [residual_stat(s) for s in solved]
    tau_m = fit_threshold(resid_pool)
    chi2_cutoff = fit_threshold(stat_pool)
    drift_bound = fit_threshold([s.err3d_m for s in solved])

    rd, sd = distribution(resid_pool), distribution(stat_pool)
    print(f"\n== fitted thresholds (clean day only, p{FIT_PERCENTILE}) ==")
    print(f"  tau_m        = {tau_m:.3f} m")
    print(f"    pool: {rd['n']} per-SV |post-fit residual| (w>0); p50 "
          f"{rd['p50']:.2f}  p95 {rd['p95']:.2f}  p99 {rd['p99']:.2f}  "
          f"max {rd['max']:.2f}")
    print(f"    reasoning: PL assumes an undetected single fault keeps its "
          f"residual under tau; tau is the clean day's own p{FIT_PERCENTILE} "
          f"residual — below it a bias is indistinguishable from the clean "
          f"floor (iono/tropo leakage included, absorbed by the fit).")
    print(f"  chi2_cutoff  = {chi2_cutoff:.3f} m^2")
    print(f"    pool: {sd['n']} epoch statistics r'Wr/dof; p50 {sd['p50']:.2f}"
          f"  p95 {sd['p95']:.2f}  p99 {sd['p99']:.2f}  max {sd['max']:.2f}")
    print(f"    reasoning: each clean exceedance is a false revoke costing a "
          f"10-epoch re-grant; p{FIT_PERCENTILE} predicts ~"
          f"{len(solved) / 1000:.0f} per day (availability cost ~1%, "
          f"measured below).")
    print(f"  drift_bound  = {drift_bound:.3f} m (continuity, check 4)")
    print(f"    reasoning: replay DR is 'still at the antenna' (TRACK_D.md); "
          f"the clean-day |corrected − antenna| p{FIT_PERCENTILE} IS the "
          f"measured drift-0 + noise floor.")

    # ---- clean gate replay with the fitted numbers ------------------------
    creplay = gate_replay(clean_sols, tau_m, chi2_cutoff, drift_bound)
    avail = availability(creplay["ok"])
    n_resid_fail = sum(creplay["residual_fail"])
    pl_clean = distribution([p for p in creplay["pl"] if p is not None])
    print(f"\n== clean day with fitted thresholds ==")
    print(f"  correction_ok availability {avail:.4f} "
          f"({n_resid_fail} residual-test failures)")
    print(f"  PL: p50 {pl_clean['p50']:.2f} m  p95 {pl_clean['p95']:.2f} m  "
          f"max {pl_clean['max']:.2f} m  vs alert limit {ALERT_LIMIT_M} m")
    clean_hits = exceedances([s.err_h_m if s else None for s in clean_sols],
                             creplay["pl"], creplay["ok"])
    print(f"  nominal-tail diagnostic: {len(clean_hits)} clean epochs where "
          f"the fault-FREE horizontal error exceeds PL while correction_ok. "
          f"PL is a single-fault bound with no nominal-noise term (aviation "
          f"adds K·sigma) — README limitation, not an integrity violation; "
          f"the §10 empirical-vs-bound check below compares ATTACK-INDUCED "
          f"displacement.")

    plot_clean_error(clean_sols, plots / "correction_clean_day_error.png")
    plot_clean_pl(clean_sols, creplay, tau_m,
                  plots / "correction_clean_day_pl.png")

    # ---- persist ----------------------------------------------------------
    fitted = {
        "fit_date": "2026-09-05",
        "dataset": f"{args.obs} (USN8, 2026-08-20, {len(clean)} epochs)",
        "procedure": (f"backend.correction.validate: clean-day-only fit at "
                      f"p{FIT_PERCENTILE} (design.md §10 by-hand procedure; "
                      f"injected data never seen by the fit)"),
        "tau_m": round(tau_m, 3),
        "tau_pool": rd,
        "chi2_cutoff": round(chi2_cutoff, 3),
        "chi2_pool": sd,
        "drift_bound_m": round(drift_bound, 3),
        "cut_rule": {"err3d_m": e3, "err_h_m": eh},
        "clean_availability": round(avail, 4),
        "clean_residual_test_failures": n_resid_fail,
        # fault-free nominal tail over PL — a limitation of the no-nominal-
        # term PL, not an integrity violation (see main() print)
        "clean_nominal_tail_exceedances": len(clean_hits),
        "clean_pl": pl_clean,
        "alert_limit_m": ALERT_LIMIT_M,
    }

    # ---- injected scenarios ----------------------------------------------
    reports = []
    scenarios = build_scenarios(onset)
    names = list(scenarios) if args.scenario == "all" else [args.scenario]
    for name in names:
        spoof = scenarios[name]
        print(f"\n== scenario {name} ==", flush=True)
        injected, truth = inject(clean, spoof, floor)
        print(f"  {summarise(truth)}")
        sols = run_pass(injected, cal, nav, label=name,
                        believed=(name == "carry_off"), stride=args.stride)
        replay = gate_replay(sols, tau_m, chi2_cutoff, drift_bound)
        d3, dh = attack_displacement(sols, clean_sols)
        rep = scenario_report(name, sols, replay, truth, onset, tau_m,
                              d3, dh)
        reports.append(rep)
        print(f"  gate: ok at onset {rep['ok_at_onset']}; "
              f"revoke after {rep['epochs_to_revoke']} epochs; "
              f"exclusions after {rep['epochs_to_exclusions']}; "
              f"re-grant after {rep['epochs_to_regrant']}")
        print(f"  integrity (§10 empirical-vs-bound, run explicitly): "
              f"{rep['integrity_violations']} epochs with correction_ok and "
              f"attack-induced displacement > PL"
              + (f" (first: {rep['violation_epochs'][0]})"
                 if rep["violation_epochs"] else ""))
        print(f"  max attack-induced displacement while ok: "
              f"3D {rep['max_attack_disp_3d_while_ok_m']} m, horizontal "
              f"{rep['max_attack_disp_h_while_ok_m']} m "
              f"(nominal horizontal max while ok "
              f"{rep['max_nominal_err_h_while_ok_m']} m); availability "
              f"{rep['availability']:.4f}; PL_k (k={rep['k_faults_at_peak']}) "
              f"median {rep['pl_k_median_m']} m during attack")
        if name == "carry_off":
            plot_positions(sols, replay, onset,
                           plots / "correction_carry_off_positions.png")
            plot_error_vs_pl(sols, replay, d3,
                             plots / "correction_displacement_vs_pl.png",
                             rep["integrity_violations"])

    fitted["scenarios"] = reports
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(fitted, indent=2))
    print(f"\nwrote {out_path}")
    print(f"plots under {plots}/correction_*.png")
    print("Copy tau_m / chi2_cutoff / drift_bound into backend/correction/"
          "gate.py with the provenance above (D5 hand-off).")


if __name__ == "__main__":
    main()
