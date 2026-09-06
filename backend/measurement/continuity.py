"""State-level continuity risk. Per-epoch FSR understates the cost.

    python -m backend.measurement.continuity

design.md §10 defines the false surrender rate epoch-weighted, and §8 makes
recovery deliberately asymmetric: a downgrade fires on one epoch's evidence,
an upgrade needs `RECOVERY_EPOCHS` sustained above the higher threshold plus
`MIN_DWELL_EPOCHS` in the current state. So one bad epoch does not cost one
epoch of authority -- it costs one epoch plus the whole climb back. A
per-epoch threshold sweep cannot see that, because it has no memory.

This measures what the arbiter actually does:

  clean day    distinct downgrade EVENTS, epochs spent below NOMINAL, and the
               fraction of the day not in NOMINAL
  attacked     epochs from onset to first downgrade (time-to-alert), and
               whether the vehicle climbs back to NOMINAL before the window
               closes

**Epoch 0 is excluded from the clean denominator** (ruled 2026-09-06). It has
no causal baseline, so every per-SV feature is unscored and the anomaly reads
0, giving confidence exactly 1.0000 -- that is an artefact of having no history
yet, not a measurement of a clean sky. The 0.750017 epoch is kept: it is data.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime

import numpy as np

from ..beats import config as cfg
from ..beats.core import build
from ..beats.thresholds import ATTACK_WINDOW, CLEAN_WINDOW, NO_ATTACK, SCENARIOS

FSR_SKIP_EPOCHS = 1          # epoch 0: no causal baseline (ruled 2026-09-06)


def arbitrate(records):
    from console.arbitras.machine import Arbiter
    arb = Arbiter()
    return [arb.step(r) for r in records]


def clean_stats(records, thr) -> dict:
    """Downgrade events and time below NOMINAL under the real hysteresis."""
    from console.arbitras.states import TrustState

    cfg.apply_thresholds(thr)
    decisions = arbitrate(records)
    states = [d.state for d in decisions]

    scored = states[FSR_SKIP_EPOCHS:]
    below = [s < TrustState.NOMINAL for s in scored]
    events = sum(1 for a, b in zip(states, states[1:]) if b < a)
    # An "episode" is a maximal run below NOMINAL: what an operator experiences
    # as one interruption, however many states it stepped through.
    episodes, run, runs = 0, 0, []
    for b in below:
        if b:
            run += 1
        elif run:
            episodes += 1
            runs.append(run)
            run = 0
    if run:
        episodes += 1
        runs.append(run)
    return {
        "n_scored": len(scored),
        "downgrade_events": events,
        "episodes_below_nominal": episodes,
        "epochs_below_nominal": int(sum(below)),
        "fraction_not_nominal": float(np.mean(below)) if scored else 0.0,
        "longest_episode_epochs": max(runs) if runs else 0,
        "median_episode_epochs": float(np.median(runs)) if runs else 0.0,
        "states_visited": sorted({s.name for s in scored}),
    }


def attack_stats(records, times, thr, onset) -> dict:
    from console.arbitras.states import TrustState

    cfg.apply_thresholds(thr)
    decisions = arbitrate(records)
    states = [d.state for d in decisions]
    k = int(np.searchsorted(times, onset))

    first = next((i for i in range(k, len(states))
                  if states[i] < TrustState.NOMINAL), None)
    after = states[first:] if first is not None else []
    recovered = next((i for i, s in enumerate(after)
                      if s == TrustState.NOMINAL), None)
    return {
        "onset_index": k,
        "epochs_to_first_downgrade": (None if first is None else first - k),
        "min_state_after_onset": (min(states[k:]).name if states[k:] else None),
        "recovers_to_nominal": recovered is not None,
        "epochs_to_recovery": recovered,
        "epochs_after_onset": len(states) - k,
    }


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--compare", type=float, default=0.80,
                    help="second NOMINAL threshold to print alongside the "
                         "ruled one")
    args = ap.parse_args(argv)

    ruled = cfg.RULED
    alt = replace(ruled, nominal=args.compare,
                  source=f"comparison only: NOMINAL {args.compare:g}, "
                         f"DEGRADED/RESTRICTED as ruled")

    print("hysteresis (design.md §8, policy not fitted): ", end="")
    from console.arbitras.states import MIN_DWELL_EPOCHS, RECOVERY_EPOCHS
    print(f"downgrade immediate, upgrade needs {RECOVERY_EPOCHS} sustained "
          f"epochs + {MIN_DWELL_EPOCHS} dwell\n")

    clean = build(window=CLEAN_WINDOW, onset=NO_ATTACK, layer_on=True,
                  transients=False)
    print(f"detector: {clean.provenance}")
    print(f"clean epochs {len(clean.records)}, "
          f"FSR denominator excludes the first {FSR_SKIP_EPOCHS} "
          f"(no causal baseline)\n")

    print("=" * 74)
    print("CLEAN DAY — state-level continuity cost")
    print("=" * 74)
    for label, t in (("ruled 0.8247", ruled), (f"compare {args.compare:g}", alt)):
        st = clean_stats(clean.records, t)
        print(f"\n  NOMINAL = {t.nominal:.4f}   [{label}]")
        print(f"    scored epochs                  {st['n_scored']:6d}")
        print(f"    distinct downgrade events      {st['downgrade_events']:6d}")
        print(f"    episodes below NOMINAL         "
              f"{st['episodes_below_nominal']:6d}")
        print(f"    epochs below NOMINAL           "
              f"{st['epochs_below_nominal']:6d}")
        print(f"    fraction of day not NOMINAL    "
              f"{st['fraction_not_nominal']:9.4f}")
        print(f"    longest / median episode       "
              f"{st['longest_episode_epochs']:6d} / "
              f"{st['median_episode_epochs']:.1f} epochs")
        print(f"    states visited                 "
              f"{', '.join(st['states_visited'])}")

    print("\n" + "=" * 74)
    print("ATTACKED — time to alert, and whether authority ever comes back")
    print("=" * 74)
    print(f"\n  NOMINAL = {ruled.nominal:.4f} [ruled]")
    print(f"  {'scenario':<22}{'to alert':>10}{'min state':>13}"
          f"{'recovers':>10}{'epochs after onset':>20}")
    for label, kw in SCENARIOS:
        b = build(window=ATTACK_WINDOW, onset=cfg.ONSET, layer_on=True,
                  transients=False, **kw)
        st = attack_stats(b.records, b.times, ruled, cfg.ONSET)
        tta = ("never" if st["epochs_to_first_downgrade"] is None
               else f"{st['epochs_to_first_downgrade']} ep")
        print(f"  {label:<22}{tta:>10}{st['min_state_after_onset']:>13}"
              f"{('yes' if st['recovers_to_nominal'] else 'no'):>10}"
              f"{st['epochs_after_onset']:>20}")
    print("\n  'to alert' is epochs from onset to the first state below "
          "NOMINAL; at 30 s")
    print("  epochs, 1 epoch = 30 s of file time.")


if __name__ == "__main__":
    main()
