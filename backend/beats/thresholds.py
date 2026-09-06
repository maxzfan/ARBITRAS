"""Threshold-selection tables. Prints distributions; picks nothing.

    python -m backend.beats.thresholds

Everything is measured against the CURRENT detector and nothing is inherited:
5 degree mask, post-fit-residual feature 2, corrected cross-constellation
calibration, weighted_sum, geometry half live at beta 0.5, exclusion k = 3.

**Transients OFF**, per the measurement ruling. Note the deliberate asymmetry:
the demo renders with transients ON, and thresholds are chosen with them OFF,
because a threshold must not be fitted to a one-epoch injected spike. The gap
is small and was measured -- composite d' 10.38 (on) vs 10.22 (off) -- but it
is real and is stated rather than hidden.

Clean side is the full 2,880-epoch day. Attack side is the 90-minute window
from onset (12:30-14:00), with 60 epochs of pre-onset warm-up so the causal
baselines are primed before the first scored epoch.

No value in this module is a threshold. It prints the two distributions and
their crossing behaviour so a human can place three numbers on them.

Two artefacts of the clean distribution to know before reading it:

- The minimum prints as 0.7500 and is NOT a bound. It is 0.750017 at a single
  epoch where `cross_constellation` saturated at 1.0 on clean sky with the
  geometry deficit at zero, so `1 - 0.5 x 0.499966`. The round-looking figure
  is a coincidence; it is data.
- The maximum of exactly 1.0000 occurs on ONE epoch: the first, where no
  causal baseline exists yet, so every per-SV feature is unscored and the
  anomaly reads 0. That epoch is a warm-up artefact rather than a measurement
  of a very clean sky. It is left in rather than silently dropped, because
  which epochs count toward a false-surrender denominator is a measurement
  decision and not this module's to make.
"""
from __future__ import annotations

import argparse
from datetime import datetime

import numpy as np
import pandas as pd

from ..injector import DEMO_CARRIER_RATE_ERROR
from . import config as cfg
from .core import build

CLEAN_WINDOW = (datetime(2026, 8, 20, 0, 0), datetime(2026, 8, 21, 0, 0))
ATTACK_WINDOW = (datetime(2026, 8, 20, 12, 0), datetime(2026, 8, 20, 14, 0))
NO_ATTACK = datetime(2026, 8, 20, 23, 59, 59)

SCENARIOS = (
    ("carry-off coherent", dict(scenario="carry_off", carrier_rate_error=0.0)),
    ("carry-off pin", dict(scenario="carry_off",
                           carrier_rate_error=DEMO_CARRIER_RATE_ERROR)),
    ("clock-domain walk", dict(scenario="clock_carry_off",
                               carrier_rate_error=0.0)),
    ("meaconing", dict(scenario="meaconing")),
)
PCTS = (0.1, 1, 2, 5, 10, 25, 50)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--lo", type=float, default=0.50)
    ap.add_argument("--hi", type=float, default=0.80)
    ap.add_argument("--step", type=float, default=0.01)
    args = ap.parse_args(argv)

    clean = build(window=CLEAN_WINDOW, onset=NO_ATTACK, layer_on=True,
                  transients=False)
    c = pd.Series(clean.confidence)
    print(f"detector: {clean.provenance}\n")

    print("=" * 78)
    print("1. CLEAN DAY — confidence distribution (false-surrender side)")
    print("=" * 78)
    print(f"  epochs {len(c)}   mean {c.mean():.4f}   sigma {c.std():.4f}")
    print(f"  min {c.min():.4f}   max {c.max():.4f}")
    print()
    print(f"  {'percentile':>12}{'confidence':>13}")
    for q in PCTS:
        print(f"  {'p' + format(q, 'g'):>12}{c.quantile(q / 100):>13.4f}")
    print()

    att = {}
    for label, kw in SCENARIOS:
        b = build(window=ATTACK_WINDOW, onset=cfg.ONSET, layer_on=True,
                  transients=False, **kw)
        mask = np.array([t >= cfg.ONSET for t in b.times])
        att[label] = pd.Series(b.confidence[mask])

    print("=" * 78)
    print("2. ATTACK WINDOWS — confidence distribution (detection side)")
    print("=" * 78)
    print(f"  {'scenario':<22}{'n':>5}{'mean':>9}{'sigma':>9}"
          f"{'p50':>9}{'p90':>9}{'p99':>9}{'max':>9}")
    for label, s in att.items():
        print(f"  {label:<22}{len(s):>5}{s.mean():>9.4f}{s.std():>9.4f}"
              f"{s.quantile(.50):>9.4f}{s.quantile(.90):>9.4f}"
              f"{s.quantile(.99):>9.4f}{s.max():>9.4f}")
    print()

    print("=" * 78)
    print("3. THE TRADEOFF — one row per candidate NOMINAL threshold")
    print("   FSR = fraction of CLEAN epochs below the threshold")
    print("   det = fraction of ATTACK epochs below it (correctly flagged)")
    print("=" * 78)
    head = f"  {'thresh':>7}{'clean FSR':>11}"
    for label, _ in SCENARIOS:
        head += f"{label.split()[0][:9] + ('.' + label.split()[-1][:4] if len(label.split()) > 1 else ''):>15}"
    print(head)
    steps = int(round((args.hi - args.lo) / args.step)) + 1
    for i in range(steps):
        thr = round(args.lo + i * args.step, 4)
        row = f"  {thr:>7.2f}{(c < thr).mean():>11.4f}"
        for label, _ in SCENARIOS:
            row += f"{(att[label] < thr).mean():>15.4f}"
        print(row)
    print()

    print("=" * 78)
    print("4. PLACING DEGRADED AND RESTRICTED ON THE CLEAN DISTRIBUTION")
    print("   The same clean percentiles, with the FSR each would cost if it")
    print("   were used as a threshold. Spacing evenly below NOMINAL would")
    print("   ignore the shape of this distribution; this is the shape.")
    print("=" * 78)
    print(f"  {'percentile':>12}{'confidence':>13}{'FSR at it':>12}"
          f"{'attack epochs below it (per scenario)':>44}")
    for q in PCTS:
        v = c.quantile(q / 100)
        dets = "  ".join(f"{(att[l] < v).mean():.3f}" for l, _ in SCENARIOS)
        print(f"  {'p' + format(q, 'g'):>12}{v:>13.4f}{(c < v).mean():>12.4f}"
              f"      {dets}")
    print()
    print("  scenario order: " + ", ".join(l for l, _ in SCENARIOS))
    print()
    print("No threshold is chosen here. Nothing is written to config.")


if __name__ == "__main__":
    main()
