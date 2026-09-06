"""Shared rendering. Dark, low-chrome, no colour-only encoding (design.md §11b).

Deterministic: no timestamps, no random jitter, fixed figure size and DPI, and
PNG metadata stripped so two runs produce byte-identical files.
"""
from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

BG, FG, DIM = "#0c0e11", "#e8eaed", "#7b8794"
TRUTH, BELIEVED, ALERT = "#5aa9e6", "#e8a33d", "#e0574a"
STATE_C = {"NOMINAL": "#4fa87a", "DEGRADED": "#c8a02e",
           "RESTRICTED": "#d1762f", "SURRENDERED": "#c0392b"}
# PNG metadata is the only non-deterministic thing matplotlib writes.
NO_META = {"Software": None, "Creation Time": None}


def figure(rows=1, cols=1, size=(12, 6.5), **kw):
    fig, ax = plt.subplots(rows, cols, figsize=size, facecolor=BG, **kw)
    for a in (ax.ravel() if hasattr(ax, "ravel") else [ax]):
        a.set_facecolor(BG)
        for sp in a.spines.values():
            sp.set_color(DIM)
        a.tick_params(colors=DIM, labelsize=8)
        a.grid(color="#1b1f26", linewidth=0.6)
        a.set_axisbelow(True)
    return fig, ax


def label(ax, text, loc="upper left", size=9, color=None):
    xy = {"upper left": (0.015, 0.965), "upper right": (0.985, 0.965),
          "lower left": (0.015, 0.035), "lower right": (0.985, 0.035)}[loc]
    ax.annotate(text, xy=xy, xycoords="axes fraction", fontsize=size,
                color=color or FG, family="monospace",
                ha="right" if "right" in loc else "left",
                va="bottom" if "lower" in loc else "top")


def save(fig, path, provenance: str) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    # Provenance is burned in: a frame that reaches the video says what made it.
    fig.text(0.005, 0.004, provenance, fontsize=5.4, color=DIM,
             family="monospace", ha="left", va="bottom")
    fig.savefig(p, dpi=110, facecolor=BG, metadata=NO_META,
                bbox_inches="tight", pad_inches=0.28)
    plt.close(fig)
    return p
