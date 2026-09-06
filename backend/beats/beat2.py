"""BEAT 2 — split screen, two UGVs, same attack, same epochs.

    python -m backend.beats.beat2 --nominal N --degraded D --restricted R

REQUIRES thresholds and has no defaults (backend/beats/config.py). Beats 2 and
7 arbitrate states; states need confidence thresholds; no threshold has been
measured for the current detector. The beat fails loudly naming the missing
value rather than inventing one to make itself run.

One attack, one set of observables, two vehicles. The instrumented one steps
down through states as its confidence falls; the uninstrumented one keeps
driving on a position that is wrong by a number printed on screen. Both
panels share an x axis, so the comparison is horizontal and needs no words.
"""
from __future__ import annotations

import argparse

import numpy as np

from . import config as cfg
from . import render as R
from .core import build, write_jsonl


def arbitrate(records):
    from console.arbiter.machine import Arbiter
    arb = Arbiter()
    return [arb.step(r) for r in records]


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=cfg.OUT)
    cfg.add_threshold_args(ap)
    args = ap.parse_args(argv)

    thr = cfg.resolve_or_exit(args)
    cfg.apply_thresholds(thr)

    on = build(layer_on=True)
    off = build(layer_on=False, geometry=False)
    decisions = arbitrate(on.records)
    states = [d.state.name for d in decisions]

    write_jsonl(on.records, f"{args.out}/beat2_layer_on.jsonl")
    write_jsonl(off.records, f"{args.out}/beat2_layer_off.jsonl")

    n = len(on.times)
    t = np.arange(n) * 0.5
    k = on.onset_index
    order = ["SURRENDERED", "RESTRICTED", "DEGRADED", "NOMINAL"]
    ylev = [order.index(s) for s in states]

    fig, ax = R.figure(3, 1, size=(11, 9.4),
                       gridspec_kw={"height_ratios": [1.25, 1, 1.25],
                                    "hspace": 0.30}, sharex=True)
    a0, a1, a2 = ax

    # -- vehicle A: instrumented
    a0.plot(t, on.confidence, color=R.TRUTH, lw=2.0)
    for name, thr_v in (("NOMINAL", thr.nominal), ("DEGRADED", thr.degraded),
                        ("RESTRICTED", thr.restricted)):
        a0.axhline(thr_v, color=R.STATE_C[name], lw=0.8, ls=":")
        a0.annotate(f"{name} {thr_v:.4g}", (t[-1], thr_v),
                    textcoords="offset points", xytext=(-4, 3), ha="right",
                    color=R.STATE_C[name], fontsize=7, family="monospace")
    a0.axvline(t[k], color=R.DIM, lw=0.9, ls="--")
    a0.set_ylim(-0.03, 1.06)
    a0.set_ylabel("confidence", color=R.DIM, fontsize=8)
    R.label(a0, "VEHICLE A — trust layer ON", color=R.TRUTH, size=11)

    a1.step(t, ylev, where="post", color=R.FG, lw=2.0)
    for i, name in enumerate(order):
        a1.axhline(i, color=R.STATE_C[name], lw=0.5, alpha=0.35)
    a1.set_yticks(range(4))
    a1.set_yticklabels(order, fontsize=7)
    a1.set_ylim(-0.45, 3.35)
    a1.axvline(t[k], color=R.DIM, lw=0.9, ls="--")
    a1.set_ylabel("authority", color=R.DIM, fontsize=8)
    first_down = next((i for i, s in enumerate(states)
                       if s != "NOMINAL" and i >= k), None)
    if first_down is not None:
        _n = first_down - k
        a1.annotate(f"steps down {_n} epoch{'' if _n == 1 else 's'} after onset",
                    (t[first_down], ylev[first_down]),
                    textcoords="offset points", xytext=(8, 8), color=R.FG,
                    fontsize=8, family="monospace")

    # -- vehicle B: uninstrumented, same attack
    a2.plot(t, off.displacement_m, color=R.BELIEVED, lw=2.0)
    a2.axvline(t[k], color=R.DIM, lw=0.9, ls="--")
    a2.set_ylabel("error in believed\nposition (m)", color=R.DIM, fontsize=8)
    a2.set_xlabel("minutes of file time", color=R.DIM, fontsize=8)
    R.label(a2, "VEHICLE B — trust layer OFF", color=R.ALERT, size=11)
    a2.annotate(f"still driving, {off.displacement_m[-1]:.0f} m off route",
                xy=(0.985, 0.12), xycoords="axes fraction", fontsize=9,
                color=R.BELIEVED, family="monospace", ha="right", va="bottom")

    prov = f"{on.provenance} | thresholds {thr.provenance}"
    png = R.save(fig, f"{args.out}/beat2.png", prov)

    print("BEAT 2 — split screen, two UGVs, same attack")
    print(f"  thresholds: {thr.provenance}")
    print(f"  {on.provenance}")
    print(f"  epochs {n}, onset index {k}")
    print(f"  A (layer ON):  confidence {on.confidence[k:].min():.4f} min after "
          f"onset; states visited "
          f"{' -> '.join(dict.fromkeys(states[k:]))}")
    if first_down is not None:
        _d = first_down - k
        print(f"                 first downgrade {_d} epoch"
              f"{'' if _d == 1 else 's'} after onset ({_d * 0.5:.1f} min)")
    print(f"  B (layer OFF): {off.displacement_m[-1]:.1f} m off route at end, "
          f"confidence flat {off.confidence.min():.2f}")
    print(f"  -> {png}")


if __name__ == "__main__":
    main()
