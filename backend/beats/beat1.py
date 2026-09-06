"""BEAT 1 — confidently wrong. Trust layer OFF.

    python -m backend.beats.beat1

Needs NO thresholds: with the layer off there are no states to arbitrate.

The beat plays in silence (design.md §11b beat 2 timing), so it has to be
legible with no narration. Three panels, top to bottom:

  1. the two tracks in local ENU metres -- truth and believed, separating
  2. horizontal separation in metres, the number a judge can hold onto
  3. confidence, flat at 1.0, because nothing is asking the question

The argument is the vertical alignment: the moment the tracks part is the
moment the confidence trace does nothing at all.
"""
from __future__ import annotations

import argparse

import numpy as np

from . import config as cfg
from . import render as R
from .core import build, write_jsonl


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=cfg.OUT)
    args = ap.parse_args(argv)

    beat = build(layer_on=False, geometry=False)
    jsonl = write_jsonl(beat.records, f"{args.out}/beat1.jsonl")

    n = len(beat.times)
    t = np.arange(n) * 0.5          # minutes at 30 s epochs
    k = beat.onset_index
    e, no = beat.believed_enu[:, 0], beat.believed_enu[:, 1]

    fig, ax = R.figure(3, 1, size=(11, 9),
                       gridspec_kw={"height_ratios": [2.1, 1, 1], "hspace": 0.32})
    a0, a1, a2 = ax

    # Truth is static (a fixed reference station), so it is a marker, not a
    # track. Before onset the believed position sits on top of it; the dim
    # segment is that overlap, drawn so the eye sees where the walk begins.
    a0.plot(e[:k + 1], no[:k + 1], color=R.DIM, lw=1.2)
    a0.plot(e[k:], no[k:], color=R.BELIEVED, lw=1.8)
    a0.scatter([0], [0], s=110, color=R.TRUTH, zorder=6,
               edgecolor=R.BG, linewidth=1.5)
    a0.scatter(e[k:k + 1], no[k:k + 1], s=45, color=R.DIM, zorder=5)
    a0.scatter(e[-1:], no[-1:], s=110, color=R.BELIEVED, zorder=6,
               edgecolor=R.BG, linewidth=1.5)
    a0.annotate("true position — static, the vehicle never moves", (0, 0),
                textcoords="offset points", xytext=(16, 12),
                color=R.TRUTH, fontsize=9, family="monospace")
    a0.annotate(f"believed position\n{beat.displacement_m[-1]:.0f} m off route",
                (e[-1], no[-1]), textcoords="offset points", xytext=(-8, -34),
                color=R.BELIEVED, fontsize=9, family="monospace", ha="right")
    a0.set_xlabel("east (m)", color=R.DIM, fontsize=8)
    a0.set_ylabel("north (m)", color=R.DIM, fontsize=8)
    a0.set_aspect("equal", adjustable="datalim")
    R.label(a0, "Trust layer: OFF", color=R.ALERT, size=11)

    a1.plot(t, beat.displacement_m, color=R.BELIEVED, lw=2.0)
    a1.axvline(t[k], color=R.DIM, lw=0.9, ls="--")
    a1.annotate("spoofer transmitting", (t[k], 0),
                textcoords="offset points", xytext=(8, 6), color=R.DIM,
                fontsize=8, family="monospace", va="bottom")
    a1.set_ylabel("separation (m)", color=R.DIM, fontsize=8)
    R.label(a1, f"max {np.nanmax(beat.displacement_m):.0f} m from true position",
            size=9, color=R.BELIEVED)

    a2.plot(t, beat.confidence, color=R.DIM, lw=2.0)
    a2.axvline(t[k], color=R.DIM, lw=0.9, ls="--")
    a2.set_ylim(0, 1.05)
    a2.set_ylabel("confidence", color=R.DIM, fontsize=8)
    a2.set_xlabel("minutes of file time", color=R.DIM, fontsize=8)
    R.label(a2, "nothing is scored, nothing has alarmed", size=9,
            color=R.DIM, loc="lower left")

    png = R.save(fig, f"{args.out}/beat1.png", beat.provenance)

    print("BEAT 1 — confidently wrong (trust layer OFF)")
    print(f"  {beat.provenance}")
    print(f"  epochs {n}, onset at index {k}")
    print(f"  separation at end        {beat.displacement_m[-1]:8.1f} m")
    print(f"  separation max           {np.nanmax(beat.displacement_m):8.1f} m")
    print(f"  confidence min           {beat.confidence.min():8.4f}  "
          f"(layer off: nothing is scored)")
    print(f"  -> {jsonl}\n  -> {png}")


if __name__ == "__main__":
    main()
