"""Freeze-at-last-NOMINAL divergence plot (design.md §9, README finding).

Below NOMINAL the engine evaluates geometry against the LOS set held at
the last trusted epoch, because live LOS derives from the receiver's own
— under attack, spoofed — position estimate. The cost of that safety is
staleness: satellites keep moving while the snapshot does not. The
engine's divergence_log records, at every frozen epoch, the information
ratio on the frozen basis alongside the ratio the live basis would give.

This script simulates a 2 h freeze starting mid-day (attack onset 12:00,
detector excludes the two highest-elevation GPS SVs) and plots both
ratios plus their divergence. The finding: divergence stays within a few
points of the ratio scale for tens of minutes — freeze is viable for
realistic attack durations, and the gap itself is loggable evidence.

Run from repo root:  python -m backend.geometry.plot_freeze_divergence
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from backend.geometry.engine import GeometryEngine
from backend.geometry.hmatrix import elevation_deg

ONSET = datetime(2026, 8, 20, 12, 0, 0)
FREEZE_H = 2.0
STEP_S = 30
OUT = Path("docs/plots/freeze_divergence.png")


def main() -> None:
    eng = GeometryEngine()
    # detector story: at onset the two highest-elevation GPS SVs are the
    # spoofed pair and get excluded; NOMINAL is lost, freeze begins.
    vis = eng.visible_svs(ONSET)
    gps = sorted((sv for sv in vis if sv[0] == "G"),
                 key=lambda sv: -elevation_deg(vis[sv], eng.rx_ecef))
    excl = gps[:2]

    eng.compute(ONSET, excluded_sv=[], freeze=False)     # last NOMINAL epoch
    n_epochs = int(FREEZE_H * 3600 / STEP_S)
    for k in range(1, n_epochs + 1):
        eng.compute(ONSET + timedelta(seconds=STEP_S * k),
                    excluded_sv=excl, freeze=True)

    ts = [t for t, _, _ in eng.divergence_log]
    frozen = [rf for _, rf, _ in eng.divergence_log]
    live = [rl for _, _, rl in eng.divergence_log]
    gap = [abs(a - b) for a, b in zip(frozen, live)]
    m30 = next(i for i, t in enumerate(ts) if t >= ONSET
               + timedelta(minutes=30))
    print(f"{len(ts)} frozen epochs, excluded {excl}")
    print(f"gap at 30 min: {gap[m30]:.4f}   max over 2 h: {max(gap):.4f}")

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 6), sharex=True)
    ax1.plot(ts, frozen, lw=1.2, color="tab:blue",
             label="ratio on frozen basis (what the engine reports)")
    ax1.plot(ts, live, lw=1.2, color="tab:orange", ls="--",
             label="ratio on live basis (spoof-contaminated under attack)")
    ax1.set_ylim(0, 1.05)
    ax1.set_ylabel("information ratio")
    ax1.legend(loc="lower left")
    ax1.set_title(f"Freeze-at-last-NOMINAL — onset {ONSET:%H:%M}, "
                  f"excluded {', '.join(excl)}")
    ax2.plot(ts, gap, lw=1.2, color="tab:red")
    ax2.axvline(ONSET + timedelta(minutes=30), color="grey", ls=":",
                label=f"30 min: gap {gap[m30]:.3f}")
    ax2.set_ylabel("frozen-vs-live divergence")
    ax2.set_xlabel("2026-08-20, GPS time")
    ax2.legend(loc="upper left")
    for ax in (ax1, ax2):
        ax.grid(alpha=0.3)
    fig.tight_layout()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, dpi=150)
    print("wrote", OUT)


if __name__ == "__main__":
    main()
