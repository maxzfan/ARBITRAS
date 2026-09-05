"""Track A end to end: RINEX in, design.md §5 contract objects out.

    python -m backend.replay                        # clean + all three §7 attacks
    python -m backend.replay --scenario carry_off --out out/

Produces one JSON Lines file per run, which the console tails (§5). This is the
18:30 integration deliverable in TRACK_A.md: clean and injected replays both
flowing in the contract shape.

The calibration is always fitted on the **clean** replay and then reused for
the injected ones. Fitting on injected data would let the attack define its own
normal, and the false-surrender rate measured afterwards would be meaningless.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from .detection import (CrossConstellation, ExclusionRule, FeatureExtractor,
                        FLAT_K5, MASKED_K3, Weights, fit, fit_cross,
                        flagged_sv, record, score, write_jsonl)
from .detection.emit import USN8_ECEF, ecef_to_lla
from .injector import DEMO_CARRIER_RATE_ERROR, SCENARIOS, inject, summarise
from .rinex import ephemeris, noise
from .rinex.loader import load_obs
from .rinex.solve import residual_panel, solve_per_constellation

OBS = "data/USN800USA_R_20262320000_01D_30S_MO.crx.gz"
ONSET = datetime(2026, 8, 20, 12, 30)


def run(epochs, cal, weights=None, geometry_for=None, credential_for=None,
        xc: CrossConstellation | None = None, nav=None,
        exclusion: ExclusionRule | None = None):
    """Score a replay. Returns the list of §5 records.

    `geometry_for(epoch)` and `credential_for(epoch)` are the seams for Track C
    and the credential layer. Absent, the geometry block is null and the blend
    collapses to the feature half (see confidence.score).

    With `xc` and `nav` given, each epoch also gets per-constellation position
    solutions: the cross_constellation feature is scored from them and the
    believed position becomes the all-in-view solution (`position_source:
    "solution"`) — under attack, the spoofed one, which is the point (§5).
    Without them the feature reads 0.0 and the surveyed position is emitted,
    exactly the degraded fallback §6b requires to be visible.

    `exclusion` is the rule behind `excluded_sv` (detection.ExclusionRule).
    The ruled default MASKED_K3 is disarmed until its mask cutoff is set, so
    the list is empty and the geometry block carries whatever Track C put
    there. See ExclusionRule for the measured clean-day cost.
    """
    weights = weights or Weights()
    fx = FeatureExtractor(cal)
    out = []
    for ep in epochs:
        # One solve per epoch, shared: feature 2 needs the post-fit residuals,
        # feature 4 needs the per-constellation solutions, and §5 needs the
        # believed position. Solving twice would be the same quantity computed
        # two ways, which is how implementations drift.
        sols = solve_per_constellation(ep, nav) if nav is not None else {}
        res = fx.step(ep, resid=(sols.get("all") or {}).get("resid_m"))
        feats = res["features"]
        position = None
        if xc is not None and nav is not None:
            feats["cross_constellation"] = xc.score(ep, sols)["value"]
            if "all" in sols:
                position = ecef_to_lla(*sols["all"]["pos"])
        geom = geometry_for(ep) if geometry_for else None
        cred = credential_for(ep) if credential_for else "VALID"
        el = None
        if (isinstance(exclusion, ExclusionRule)
                and exclusion.elevation_mask_deg is not None
                and nav is not None):
            el = ephemeris.elevations_at(ep.time, list(ep.df.index),
                                         USN8_ECEF, nav)
        excluded = flagged_sv(res["per_sv"], cal.z_sat, exclusion,
                              elevations=el)
        if excluded:
            # The detector's own distrust list. Track C owns the rest of the
            # block; this is the one field only the detector can fill.
            geom = dict(geom or {})
            geom["excluded_sv"] = excluded
        out.append(record(ep.time, feats, score(feats, geom, weights),
                          n_sv=ep.n_sv, geometry=geom, credential_status=cred,
                          position=position))
    return out


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--obs", default=OBS)
    ap.add_argument("--systems", default="GERCS")
    ap.add_argument("--scenario", choices=list(SCENARIOS) + ["all"], default="all")
    ap.add_argument("--onset", default=ONSET.isoformat())
    ap.add_argument("--exclusion", choices=("masked_k3", "flat_k5", "off"),
                    default="masked_k3",
                    help="exclusion rule behind excluded_sv. masked_k3 is the "
                         "ruled default and is DISARMED until its mask cutoff "
                         "is set (emits nothing); flat_k5 is the configured "
                         "fallback.")
    ap.add_argument("--elevation-mask-deg", type=float, default=None,
                    help="mask cutoff for masked_k3. Deliberately unset: "
                         "picking it is a threshold-session decision.")
    ap.add_argument("--carrier-rate-error", type=float,
                    default=DEMO_CARRIER_RATE_ERROR,
                    help="carry-off code/carrier divergence rate, m/s. "
                         f"Default is the demo pin {DEMO_CARRIER_RATE_ERROR} "
                         "(k=2 sigma at t=30 s), chosen for demo legibility, "
                         "not as a physical claim. Pass 0 for the coherent "
                         "spoofer the displacement bound is measured against.")
    ap.add_argument("--out", default="out")
    args = ap.parse_args(argv)

    onset = datetime.fromisoformat(args.onset)
    print(f"carrier_rate_error {args.carrier_rate_error} m/s "
          f"({'demo pin: legibility, not a physical claim'
             if args.carrier_rate_error == DEMO_CARRIER_RATE_ERROR else 'override'})")
    clean = load_obs(args.obs, systems=args.systems)
    floor = noise.measure(clean)
    print(floor)

    nav = ephemeris.load_nav()
    resid = residual_panel(clean, nav)
    print(f"post-fit residual panel: {resid.shape[0]} epochs x "
          f"{resid.shape[1]} SV")
    cal = fit(clean, floor, resid_panel=resid)
    print(cal)
    xc = CrossConstellation(fit_cross(clean, nav), nav)
    print(xc.cal)

    xc.reset()
    rule = {"masked_k3": MASKED_K3, "flat_k5": FLAT_K5,
            "off": None}[args.exclusion]
    if rule is not None and args.elevation_mask_deg is not None:
        rule = replace(rule, elevation_mask_deg=args.elevation_mask_deg)
    print(rule if rule else "exclusion rule: off")

    recs = run(clean, cal, xc=xc, nav=nav, exclusion=rule)
    print(f"clean    {len(recs):5d} epochs -> "
          f"{write_jsonl(recs, Path(args.out) / 'clean.jsonl')}")

    names = list(SCENARIOS) if args.scenario == "all" else [args.scenario]
    for name in names:
        if name in ("carry_off", "clock_carry_off"):
            spoof = SCENARIOS[name](onset=onset,
                                    carrier_rate_error=args.carrier_rate_error)
        else:
            spoof = SCENARIOS[name](onset=onset)
        injected, truth = inject(clean, spoof, floor)
        xc.reset()
        recs = run(injected, cal, xc=xc, nav=nav, exclusion=rule)
        truth.to_csv(Path(args.out) / f"{name}_truth.csv")
        print(f"{name:9s}{len(recs):5d} epochs -> "
              f"{write_jsonl(recs, Path(args.out) / f'{name}.jsonl')}")
        print(f"          {summarise(truth)}")


if __name__ == "__main__":
    main()
