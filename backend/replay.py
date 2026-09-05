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
from datetime import datetime
from pathlib import Path

from .detection import (FeatureExtractor, Weights, fit, record, score,
                        write_jsonl)
from .injector import SCENARIOS, inject, summarise
from .rinex import noise
from .rinex.loader import load_obs

OBS = "data/USN800USA_R_20262320000_01D_30S_MO.crx.gz"
ONSET = datetime(2026, 8, 20, 12, 30)


def run(epochs, cal, weights=None, geometry_for=None, credential_for=None):
    """Score a replay. Returns the list of §5 records.

    `geometry_for(epoch)` and `credential_for(epoch)` are the seams for Track C
    and the credential layer. Absent, the geometry block is null and the blend
    collapses to the feature half (see confidence.score).
    """
    weights = weights or Weights()
    fx = FeatureExtractor(cal)
    out = []
    for ep in epochs:
        res = fx.step(ep)
        geom = geometry_for(ep) if geometry_for else None
        cred = credential_for(ep) if credential_for else "VALID"
        out.append(record(ep.time, res["features"], score(res["features"], geom, weights),
                          n_sv=ep.n_sv, geometry=geom, credential_status=cred))
    return out


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--obs", default=OBS)
    ap.add_argument("--systems", default="GERCS")
    ap.add_argument("--scenario", choices=list(SCENARIOS) + ["all"], default="all")
    ap.add_argument("--onset", default=ONSET.isoformat())
    ap.add_argument("--carrier-rate-error", type=float, default=None,
                    help="carry-off code/carrier divergence rate, m/s. The "
                         "demo pin is picked by hand and is pending; carry_off "
                         "is skipped until one is given.")
    ap.add_argument("--out", default="out")
    args = ap.parse_args(argv)

    onset = datetime.fromisoformat(args.onset)
    clean = load_obs(args.obs, systems=args.systems)
    floor = noise.measure(clean)
    print(floor)

    cal = fit(clean, floor)
    print(cal)

    recs = run(clean, cal)
    print(f"clean    {len(recs):5d} epochs -> "
          f"{write_jsonl(recs, Path(args.out) / 'clean.jsonl')}")

    names = list(SCENARIOS) if args.scenario == "all" else [args.scenario]
    for name in names:
        if name == "carry_off":
            if args.carrier_rate_error is None:
                print("carry_off: SKIPPED -- carrier_rate_error demo pin is "
                      "pending (picked by hand; pass --carrier-rate-error)")
                continue
            spoof = SCENARIOS[name](onset=onset,
                                    carrier_rate_error=args.carrier_rate_error)
        else:
            spoof = SCENARIOS[name](onset=onset)
        injected, truth = inject(clean, spoof, floor)
        recs = run(injected, cal)
        truth.to_csv(Path(args.out) / f"{name}_truth.csv")
        print(f"{name:9s}{len(recs):5d} epochs -> "
              f"{write_jsonl(recs, Path(args.out) / f'{name}.jsonl')}")
        print(f"          {summarise(truth)}")


if __name__ == "__main__":
    main()
