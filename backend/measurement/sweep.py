"""Subset-size sweep harness. BUILT, NOT RUN.

    GATE: this sweep runs only after demo beats 1, 2 and 7 pass end to end,
    and only when explicitly cleared. `python -m backend.measurement.sweep`
    refuses without --run.

What it will measure, per decisions of 2026-09-05: carry-off attacks over a
three-axis grid, run in both attack domains --

    subset size          12 / 8 / 6 / 4, top-N by elevation at capture, frozen
    carrier_rate_error   0.0 / 0.005 / 0.01 / 0.02 / 0.05 / 0.1 m/s (cap 0.1)
    bearing              8 directions, 45 deg apart from local ENU north
                         (position domain only; the clock domain has none)

Per grid epoch it records the confidence trajectory, the geometry score
det(HtT Ht) / det(HfT Hf) raw and normalised, and:

- **commanded vs achieved displacement, and their ratio.** §10 measures
  ACHIEVED, taken from the solved position against the clean solution at the
  same epoch -- never commanded. They differ because the authentic E and C
  observations pull the least-squares back toward truth, so achieved is a
  fraction of commanded. The gap is a result in its own right: it is what the
  third constellation buys, in metres.
- **pseudorange residual RMS, split three ways.** The all-in-view solution's
  residuals partitioned into the spoofed and authentic subsets, plus the
  residual of a solution computed from the spoofed constellation ALONE. That
  third number is the one that shows the mechanism: a coordinated walk lies in
  the position columns of H, so a single-constellation solve absorbs it into
  its own position and stays near its clean baseline however far the attack
  has moved. Any residual in the all-in-view solution is therefore
  cross-constellation disagreement, not GPS-internal inconsistency. Reporting
  one pooled number would hide which of the three it was.

`cmc_features_inactive: true` is written on every carrier_rate_error = 0
record -- the spoofer is fully carrier-coherent there, so features 2 and 3 are
structurally blind, and that is stated rather than left to be inferred. Note
that under a coordinated position walk features 2 and 3 are blind to the
DISPLACEMENT at any rate: they read code-minus-carrier, which contains no
geometry at all (docs/measured.md).

One JSONL record per grid epoch; a summary record per grid cell. No plotting.

**The verification target.** Small-subset spoofs leave most of GPS authentic,
so excluding only the spoofed satellites should barely move the determinant
ratio. If the ratio moves a lot at n=4, the score is responding to something
other than satellite-subset geometry and that is a finding to FLAG, not fix.
The summary records carry the per-subset ratio distribution for exactly this
check; the harness prints distributions and stops -- it never picks a
threshold.

Honest limits, stated here rather than discovered later:

- **Exclusion is truth-derived**: the trusted set is "everything except the
  satellites the injector actually spoofed", not what a detector decided to
  distrust. This isolates the geometry score's response from the detector's.
- **H covers G/E/C only.** GLONASS broadcasts state vectors, not Keplerian
  elements; propagating them is Track C's geometry work. Which constellations
  made it into H is recorded per epoch.
- **The information-ratio implementation here is a stand-in** with the same
  corrected form CLAUDE.md specifies for Track C (H is n x (3+k), clock
  columns dropped when a constellation's trusted count reaches zero, ratio
  normalised as (det ratio)^(1/(3+k))). When backend/geometry/ lands, this
  helper is deleted and the sweep calls Track C's -- divergence between two
  implementations of the same score is a bug factory.
- **"Final trust state" is recorded as null.** States are the console's, and
  thresholds do not exist yet (they are set by hand, design.md §10). The
  harness takes the confidence trajectory down to disk so states can be
  assigned afterwards without re-running anything.
- Max adversarial displacement in the §10 sense (believed-vs-true at the
  epoch before first transition) needs both thresholds and a position
  solution; until then the summary reports the truth range-offset maximum.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from ..detection import FeatureExtractor, Weights, fit, score
from ..geometry.information import norm_info
from ..detection.emit import USN8_ECEF
from ..injector import (CLEAN, CARRY_OFF, CLOCK_CARRY_OFF, SWEEP_BEARINGS_DEG,
                        enu_basis, inject, top_n_by_elevation)
from ..rinex import ephemeris, noise
from ..rinex.loader import load_obs
from ..rinex.solve import solve

OBS = "data/USN800USA_R_20262320000_01D_30S_MO.crx.gz"
ONSET = datetime(2026, 8, 20, 12, 30)
GATE = ("GATED: run only after demo beats 1, 2 and 7 pass end to end, "
        "and only when explicitly cleared. Pass --run when that is true.")


@dataclass
class SweepConfig:
    subset_sizes: tuple = (12, 8, 6, 4)
    carrier_rate_errors: tuple = (0.0, 0.005, 0.01, 0.02, 0.05, 0.1)  # m/s
    bearings_deg: tuple = SWEEP_BEARINGS_DEG
    # Both attack domains. The clock domain has no bearing, so it contributes
    # one cell per (subset, rate) rather than one per bearing.
    domains: tuple = ("position", "clock")
    onset: datetime = ONSET
    obs: str = OBS
    systems: str = "GERCS"
    systems_in_h: str = "GEC"          # Kepler-propagatable; see module docstring
    # Attack epochs to score per cell. The full 1,380-epoch tail is not needed
    # to find the displacement maximum and the grid is large.
    epochs_after_onset: int = 120
    out: str = "out/sweep_subset.jsonl"

    def cells(self):
        """(domain, subset_size, rate, bearing_or_None) for the whole grid."""
        for domain in self.domains:
            bearings = self.bearings_deg if domain == "position" else (None,)
            for n in self.subset_sizes:
                for rate in self.carrier_rate_errors:
                    for bearing in bearings:
                        yield domain, n, rate, bearing


# --- information ratio: score math delegated to backend/geometry (Track C) ---

def build_h(los: pd.DataFrame) -> np.ndarray:
    """DataFrame adapter over precomputed unit LOS rows: H, n x (3+k), one
    clock column per constellation present. Constellations with zero
    satellites contribute no column, so H is never rank-deficient by
    construction. Column signs differ from geometry.hmatrix.build_H by a
    diagonal sign matrix, which leaves det(H'H) unchanged."""
    systems = sorted({sv[0] for sv in los.index})
    h = np.zeros((len(los), 3 + len(systems)))
    h[:, :3] = -los[["ex", "ey", "ez"]].to_numpy()
    for j, sysc in enumerate(systems):
        h[[sv.startswith(sysc) for sv in los.index], 3 + j] = 1.0
    return h


def information_ratio(trusted: pd.DataFrame, full: pd.DataFrame) -> dict:
    """Raw and normalised determinant ratio of the trusted-subset information
    matrix against the full-set solution. The normalised score is Track C's
    `norm_info` per-state geometric-mean form (each matrix at its own
    dimensionality) — single-sourced from backend.geometry.information so the
    sweep and the live engine cannot diverge."""
    k = len({sv[0] for sv in trusted.index})
    ht, hf = build_h(trusted), build_h(full)
    nf = norm_info(hf)
    norm = float(np.clip(norm_info(ht) / nf, 0.0, 1.0)) if nf > 0.0 else 0.0
    if ht.shape[0] < ht.shape[1]:           # underdetermined: no solution left
        return {"raw": 0.0, "norm": 0.0, "k_trusted": k}
    dt = float(np.linalg.det(ht.T @ ht))
    df_ = float(np.linalg.det(hf.T @ hf))
    raw = max(0.0, dt / df_) if df_ > 0 else 0.0   # kept as a diagnostic
    return {"raw": raw, "norm": norm, "k_trusted": k}


def subset_residual_rms(epoch, nav, spoofed: set, sta,
                        spoofed_systems: str = "G") -> dict:
    """Pseudorange residual RMS (m), three ways, because one number would hide
    which effect is which.

    - `resid_rms_spoofed_m` / `resid_rms_authentic_m`: the ALL-IN-VIEW
      solution's post-fit residuals partitioned by whether the satellite was
      spoofed. The solution lands between what the two subsets ask for, so
      both carry residual; smoke-measured, the spoofed subset's grows with
      the walk.
    - `resid_rms_spoofed_only_solution_m`: the residual of a solution computed
      from the SPOOFED CONSTELLATION ALONE. This is the one that shows the
      point directly: a coordinated walk lies in the position columns of H, so
      a single-constellation solve absorbs it into its own position estimate
      and its residual stays near the clean baseline no matter how far the
      attack has moved. Any residual that does appear in the all-in-view
      solution is therefore cross-constellation disagreement, not
      GPS-internal inconsistency.
    """
    out = {}
    sol = solve(epoch, nav=nav)
    if sol is None:
        out.update(resid_rms_spoofed_m=None, resid_rms_authentic_m=None,
                   resid_rms_all_m=None)
    else:
        per = sol["resid_m"]
        sp = np.array([v for sv, v in per.items() if sv in spoofed])
        au = np.array([v for sv, v in per.items() if sv not in spoofed])
        rms = lambda a: float(np.sqrt(np.mean(a ** 2))) if len(a) else None
        out.update(resid_rms_spoofed_m=rms(sp),
                   resid_rms_authentic_m=rms(au),
                   resid_rms_all_m=sol["resid_rms_m"])
    one = solve(epoch, systems=spoofed_systems, nav=nav)
    out["resid_rms_spoofed_only_solution_m"] = (one["resid_rms_m"]
                                                if one else None)
    return out


def los_frame(t: datetime, svs, nav, sta=USN8_ECEF) -> pd.DataFrame:
    """Unit line-of-sight vectors receiver -> satellite for the SVs that have
    usable Kepler ephemeris at t."""
    pos = ephemeris.positions_at(t, svs, nav)
    v = pos[["x", "y", "z"]].to_numpy() - np.asarray(sta, dtype=float)
    v /= np.linalg.norm(v, axis=1, keepdims=True)
    return pd.DataFrame(v, columns=["ex", "ey", "ez"], index=pos.index)


# --- the sweep -----------------------------------------------------------------

def run_sweep(cfg: SweepConfig | None = None) -> Path:
    cfg = cfg or SweepConfig()
    clean = load_obs(cfg.obs, systems=cfg.systems)
    floor = noise.measure(clean)
    cal = fit(clean, floor)                 # clean only; never injected data
    nav = ephemeris.load_nav()
    weights = Weights()                     # untuned and flagged as such
    sta = np.array(USN8_ECEF, dtype=float)
    east, north, up = enu_basis(sta)

    # The epochs scored per cell, and the clean solution at each of them: the
    # achieved-displacement baseline is the CLEAN solve at the same epoch, not
    # the surveyed position, so the solver's own metre-scale bias cancels.
    idx0 = next(i for i, e in enumerate(clean) if e.time >= cfg.onset)
    span = clean[idx0:idx0 + cfg.epochs_after_onset]
    base = {}
    for ep in span:
        sol = solve(ep, nav=nav)
        if sol is not None:
            base[ep.time] = sol["pos"]

    out = Path(cfg.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as fh:
        for domain, n, rate, bearing in cfg.cells():
            maker = CARRY_OFF if domain == "position" else CLOCK_CARRY_OFF
            kw = {"bearing_deg": bearing} if domain == "position" else {}
            spoof = maker(onset=cfg.onset, carrier_rate_error=rate,
                          target_svs=top_n_by_elevation(n), **kw)
            injected, truth = inject(clean, spoof, floor, nav=nav,
                                     sta_ecef=sta)
            inactive = rate == 0.0

            # Warm the causal feature baselines on the epochs before onset so
            # the first scored epoch is not scored against an empty history.
            fx = FeatureExtractor(cal)
            for ep in injected[max(0, idx0 - cfg.epochs_after_onset):idx0]:
                fx.step(ep)

            ratios, achieved, ratio_ach = [], [], []
            for ep, tr in zip(injected[idx0:idx0 + cfg.epochs_after_onset],
                              truth.iloc[idx0:idx0 + cfg.epochs_after_onset]
                              .itertuples()):
                feats = fx.step(ep)["features"]
                excluded = set(tr.spoofed_sv.split(",")) if tr.spoofed_sv else set()
                in_h = [sv for sv in ep.df.index if sv[0] in cfg.systems_in_h]
                los = los_frame(ep.time, in_h, nav)
                trusted = los.loc[[sv for sv in los.index if sv not in excluded]]
                ratio = information_ratio(trusted, los)
                geom = {"information_ratio": ratio["norm"],
                        "excluded_sv": sorted(excluded),
                        "displacement_bound_m": None,      # Track C's analytic bound
                        "next_best_observation": None}
                scored = score(feats, geom, weights)
                ratios.append(ratio["norm"])

                # Achieved displacement: solved position under attack against
                # the clean solve at the same epoch. Measured, never commanded.
                sol = solve(ep, nav=nav)
                ach = enu = None
                if sol is not None and ep.time in base:
                    d = sol["pos"] - base[ep.time]
                    ach = float(np.linalg.norm(d))
                    enu = [float(d @ east), float(d @ north), float(d @ up)]
                    achieved.append(ach)
                    if tr.commanded_displacement_m > 0:
                        ratio_ach.append(ach / tr.commanded_displacement_m)
                res = subset_residual_rms(ep, nav, excluded, sta)

                fh.write(json.dumps({
                    "type": "epoch", "domain": domain, "subset_size": n,
                    "carrier_rate_error": rate, "bearing_deg": bearing,
                    "cmc_features_inactive": inactive,
                    "time": ep.time.isoformat() + "Z", "stage": tr.stage,
                    "n_spoofed": tr.n_spoofed, "excluded_sv": sorted(excluded),
                    "commanded_displacement_m": tr.commanded_displacement_m,
                    "achieved_displacement_m": ach,
                    "achieved_over_commanded": (
                        ach / tr.commanded_displacement_m
                        if ach is not None and tr.commanded_displacement_m > 0
                        else None),
                    "achieved_enu_m": enu,
                    "range_offset_m": tr.range_offset_m,
                    "confidence": round(scored["confidence"], 4),
                    "features": {k: round(v, 4) for k, v in feats.items()},
                    **res,
                    "info_ratio_raw": ratio["raw"],
                    "info_ratio_norm": ratio["norm"],
                    "k_trusted": ratio["k_trusted"],
                    "n_in_h": len(los), "n_trusted": len(trusted),
                }) + "\n")

            r = pd.Series(ratios)
            fh.write(json.dumps({
                "type": "summary", "domain": domain, "subset_size": n,
                "carrier_rate_error": rate, "bearing_deg": bearing,
                "cmc_features_inactive": inactive,
                "inactive_note": ("carrier_rate_error = 0: spoofer is fully "
                                  "carrier-coherent; pseudorange_residual and "
                                  "code_carrier_divergence are structurally "
                                  "blind on this run") if inactive else None,
                "max_commanded_displacement_m": float(
                    truth["commanded_displacement_m"].max()),
                "max_achieved_displacement_m": (float(max(achieved))
                                                if achieved else None),
                "achieved_over_commanded_mean": (float(np.mean(ratio_ach))
                                                 if ratio_ach else None),
                "final_state": None,
                "note": "states unset: thresholds are set by hand (design.md "
                        "§10); assign states from the confidence trajectory "
                        "afterwards",
                "info_ratio_norm_during_attack": {
                    "min": float(r.min()), "p10": float(r.quantile(.1)),
                    "p50": float(r.quantile(.5)), "p90": float(r.quantile(.9)),
                },
            }) + "\n")
            tag = f"{domain[:3]} n={n:2d} rate={rate:5.3f}"
            tag += f" brg={bearing:3.0f}" if bearing is not None else " brg=  -"
            print(f"{tag}  achieved max "
                  f"{max(achieved) if achieved else float('nan'):8.1f} m"
                  f"  ach/cmd {np.mean(ratio_ach) if ratio_ach else float('nan'):5.3f}"
                  f"  info_ratio p50 {r.quantile(.5):.3f}")
    print(f"wrote {out}")
    print("Distributions above are the output. No threshold is picked here.")
    return out


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run", action="store_true",
                    help="actually run the sweep (gated; see module docstring)")
    args = ap.parse_args(argv)
    if not args.run:
        raise SystemExit(GATE)
    run_sweep()


if __name__ == "__main__":
    main()
