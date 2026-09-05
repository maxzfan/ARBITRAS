"""Subset-size sweep harness. BUILT, NOT RUN.

    GATE: this sweep runs only after demo beats 1, 2 and 7 pass end to end,
    and only when explicitly cleared. `python -m backend.measurement.sweep`
    refuses without --run.

What it will measure, per decisions of 2026-09-05: carry-off attacks over a
two-axis grid --

    subset size          12 / 8 / 6 / 4, top-N by elevation at capture, frozen
    carrier_rate_error   0.0 / 0.005 / 0.01 / 0.02 / 0.05 / 0.1 m/s (cap 0.1)

Per (subset_size, rate, epoch) it records the truth range offset, the
confidence trajectory, and the geometry score det(HtT Ht) / det(HfT Hf) in
raw and normalised form. One JSONL record per grid epoch; a summary record
per grid cell. No plotting. At carrier_rate_error = 0.0 every record carries
`cmc_features_inactive: true` -- the spoofer is fully carrier-coherent, so
features 2 and 3 are structurally blind there; that fact is written into the
output rather than left to be inferred.

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
from ..injector import CLEAN, CARRY_OFF, inject, top_n_by_elevation
from ..rinex import ephemeris, noise
from ..rinex.loader import load_obs

OBS = "data/USN800USA_R_20262320000_01D_30S_MO.crx.gz"
ONSET = datetime(2026, 8, 20, 12, 30)
GATE = ("GATED: run only after demo beats 1, 2 and 7 pass end to end, "
        "and only when explicitly cleared. Pass --run when that is true.")


@dataclass
class SweepConfig:
    subset_sizes: tuple = (12, 8, 6, 4)
    carrier_rate_errors: tuple = (0.0, 0.005, 0.01, 0.02, 0.05, 0.1)  # m/s
    onset: datetime = ONSET
    obs: str = OBS
    systems: str = "GERCS"
    systems_in_h: str = "GEC"          # Kepler-propagatable; see module docstring
    out: str = "out/sweep_subset.jsonl"


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

    out = Path(cfg.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as fh:
        for n in cfg.subset_sizes:
          for rate in cfg.carrier_rate_errors:
            spoof = CARRY_OFF(onset=cfg.onset, carrier_rate_error=rate,
                              target_svs=top_n_by_elevation(n))
            injected, truth = inject(clean, spoof, floor)
            fx = FeatureExtractor(cal)
            ratios = []
            inactive = rate == 0.0
            for ep, tr in zip(injected, truth.itertuples()):
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
                fh.write(json.dumps({
                    "type": "epoch", "subset_size": n,
                    "carrier_rate_error": rate,
                    "cmc_features_inactive": inactive,
                    "time": ep.time.isoformat() + "Z", "stage": tr.stage,
                    "n_spoofed": tr.n_spoofed, "excluded_sv": sorted(excluded),
                    "range_offset_m": tr.range_offset_m,
                    "confidence": round(scored["confidence"], 4),
                    "features": {k: round(v, 4) for k, v in feats.items()},
                    "info_ratio_raw": ratio["raw"],
                    "info_ratio_norm": ratio["norm"],
                    "k_trusted": ratio["k_trusted"],
                    "n_in_h": len(los), "n_trusted": len(trusted),
                }) + "\n")
            att = truth["stage"] != CLEAN
            r = pd.Series(ratios)[att.to_numpy()]
            fh.write(json.dumps({
                "type": "summary", "subset_size": n,
                "carrier_rate_error": rate,
                "cmc_features_inactive": inactive,
                "inactive_note": ("carrier_rate_error = 0: spoofer is fully "
                                  "carrier-coherent; pseudorange_residual and "
                                  "code_carrier_divergence are structurally "
                                  "blind on this run") if inactive else None,
                "max_range_offset_m": float(truth["range_offset_m"].max()),
                "final_state": None,
                "note": "states unset: thresholds are set by hand (design.md "
                        "§10); assign states from the confidence trajectory "
                        "afterwards",
                "info_ratio_norm_during_attack": {
                    "min": float(r.min()), "p10": float(r.quantile(.1)),
                    "p50": float(r.quantile(.5)), "p90": float(r.quantile(.9)),
                },
            }) + "\n")
            print(f"subset {n:2d} rate {rate:5.3f}"
                  f"{'  [features 2+3 inactive]' if inactive else '':<26}"
                  f" info_ratio_norm p10 {r.quantile(.1):.3f}"
                  f"  p50 {r.quantile(.5):.3f}  min {r.min():.3f}")
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
