"""BEAT 7 — credential revocation under a clean sky.

    python -m backend.beats.beat7 --nominal N --degraded D --restricted R

REQUIRES thresholds and has no defaults, for the same reason as beat 2.

No attack. Every signal nominal, confidence high, geometry healthy — and
mid-run the ground station's authorisation is revoked. `REVOKED` forces
SURRENDERED regardless of confidence (design.md §8), so the vehicle stands
down with a perfect fix.

The beat is the vertical gap between the two traces: confidence stays flat
across the moment authority collapses. That gap is the claim — *signal quality
is not provenance* — and it is the one beat where nothing in the signal
explains what the vehicle did.

Revocation is modelled the way §9 specifies: the ground simply stops renewing.
There is no revocation message, because the protocol has none. The credential
is VALID to the end of its window and then absent, which is fail-closed by
construction rather than by policy.
"""
from __future__ import annotations

import argparse

import numpy as np

from . import config as cfg
from . import render as R
from .core import build, write_jsonl

REVOKE_AT = 75          # epoch index within the window; no attack in this beat


def credential_schedule(revoke_at: int = REVOKE_AT):
    """VALID until renewal stops, REVOKED after. No message is sent."""
    def sched(epoch, index):
        return "VALID" if index < revoke_at else "REVOKED"
    return sched


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=cfg.OUT)
    ap.add_argument("--revoke-at", type=int, default=REVOKE_AT,
                    help="epoch index within the window at which renewal stops")
    cfg.add_threshold_args(ap)
    args = ap.parse_args(argv)

    thr = cfg.resolve_or_exit(args)
    cfg.apply_thresholds(thr)

    # No attack: onset is pushed past the end of the window, so the observables
    # are the clean ones throughout. The sky really is clean.
    from datetime import datetime
    beat = build(onset=datetime(2026, 8, 20, 23, 59), layer_on=True,
                 credential_for=credential_schedule(args.revoke_at))
    jsonl = write_jsonl(beat.records, f"{args.out}/beat7.jsonl")

    from console.arbiter.machine import Arbiter
    arb = Arbiter()
    decisions = [arb.step(r) for r in beat.records]
    states = [d.state.name for d in decisions]
    causes = [getattr(d, "cause", None) or getattr(d, "reason", "") for d in decisions]

    n = len(beat.times)
    t = np.arange(n) * 0.5
    k = args.revoke_at
    order = ["SURRENDERED", "RESTRICTED", "DEGRADED", "NOMINAL"]
    ylev = [order.index(s) for s in states]
    ratio = [(r["geometry"] or {}).get("information_ratio") for r in beat.records]

    fig, ax = R.figure(3, 1, size=(11, 9.4),
                       gridspec_kw={"height_ratios": [1.2, 1, 1.2],
                                    "hspace": 0.30}, sharex=True)
    a0, a1, a2 = ax

    a0.plot(t, beat.confidence, color=R.TRUTH, lw=2.0)
    a0.axhline(thr.nominal, color=R.STATE_C["NOMINAL"], lw=0.8, ls=":")
    a0.annotate(f"NOMINAL {thr.nominal:.4g}", (t[-1], thr.nominal),
                textcoords="offset points", xytext=(-4, 3), ha="right",
                color=R.STATE_C["NOMINAL"], fontsize=7, family="monospace")
    a0.axvline(t[k], color=R.ALERT, lw=1.1, ls="--")
    a0.set_ylabel("confidence", color=R.DIM, fontsize=8)
    R.label(a0, "clean sky — no attack, no anomaly", color=R.TRUTH, size=11)
    a0.set_ylim(min(thr.nominal, float(beat.confidence.min())) - 0.06, 1.03)
    a0.annotate(f"confidence never leaves NOMINAL "
                f"(min {beat.confidence.min():.3f})",
                xy=(0.015, 0.12), xycoords="axes fraction", fontsize=9,
                color=R.DIM, family="monospace", va="bottom")

    a1.plot(t, [r if r is not None else np.nan for r in ratio],
            color=R.DIM, lw=2.0)
    a1.axvline(t[k], color=R.ALERT, lw=1.1, ls="--")
    a1.set_ylim(0, 1.06)
    a1.set_ylabel("information ratio", color=R.DIM, fontsize=8)
    R.label(a1, "geometry healthy throughout — no satellite distrusted, "
                "ratio 1.00", size=9, color=R.DIM)

    a2.step(t, ylev, where="post", color=R.FG, lw=2.0)
    for i, name in enumerate(order):
        a2.axhline(i, color=R.STATE_C[name], lw=0.5, alpha=0.35)
    a2.set_yticks(range(4))
    a2.set_yticklabels(order, fontsize=7)
    a2.axvline(t[k], color=R.ALERT, lw=1.1, ls="--")
    a2.annotate("ground stops renewing\n(no revocation message exists)",
                (t[k], 3), textcoords="offset points", xytext=(9, -6),
                color=R.ALERT, fontsize=9, family="monospace", va="top")
    a2.set_ylabel("authority", color=R.DIM, fontsize=8)
    a2.set_xlabel("minutes of file time", color=R.DIM, fontsize=8)
    a2.set_ylim(-0.45, 3.35)
    a2.annotate("stands down anyway — signal quality is not provenance",
                xy=(0.985, 0.12), xycoords="axes fraction", fontsize=9,
                color=R.ALERT, family="monospace", ha="right", va="bottom")

    prov = f"{beat.provenance} | thresholds {thr.provenance}"
    png = R.save(fig, f"{args.out}/beat7.png", prov)

    after = set(states[k:])
    print("BEAT 7 — credential revocation under a clean sky")
    print(f"  thresholds: {thr.provenance}")
    print(f"  {beat.provenance}")
    print(f"  epochs {n}, renewal stops at index {k}")
    print(f"  confidence: min {beat.confidence.min():.4f}, "
          f"mean {beat.confidence.mean():.4f}  (never attacked)")
    print(f"  state before revocation: {states[k - 1]}")
    print(f"  state after revocation:  {sorted(after)}  "
          f"cause {causes[k]!r}")
    print(f"  -> {jsonl}\n  -> {png}")


if __name__ == "__main__":
    main()
