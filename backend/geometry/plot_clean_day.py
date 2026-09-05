"""Information ratio over the clean day (design.md §14, 13:30-15:30 slot).

For each epoch: visible G/E/C SVs above the elevation mask form the full
solution; a scripted exclusion scenario forms the trusted subset. On the
clean day nothing is excluded, so the headline curve is the *full-geometry*
normalised information over the day (constellation motion only), plus a
fixed-exclusion overlay to show the ratio is stable and legible.

Run from repo root:  python -m backend.geometry.plot_clean_day [step]
step = epoch subsampling (default 10 -> 288 points; 1 = all 2880).
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from backend.geometry.ephemeris import load_records, sv_positions_at
from backend.geometry.hmatrix import build_H, elevation_deg
from backend.geometry.information import information_ratio

USN8_ECEF = np.array([1112161.8802, -4842854.4026, 3985497.3830])
DAY0 = datetime(2026, 8, 20, 0, 0, 0)
EL_MASK_DEG = 10.0
OUT = Path("docs/plots/information_ratio_clean_day.png")


def main(step: int = 10) -> None:
    records = load_records()
    times, n_vis, ratio_two = [], [], []
    for k in range(0, 2880, step):
        t = DAY0 + timedelta(seconds=30 * k)
        pos = sv_positions_at(records, t)
        visible = {sv: p for sv, p in pos.items()
                   if elevation_deg(p, USN8_ECEF) > EL_MASK_DEG}
        if len(visible) < 8:
            continue
        h_full, sv_ids, _ = build_H(visible, USN8_ECEF)

        # fixed scenario: exclude the two highest-elevation GPS SVs
        gps = sorted((sv for sv in visible if sv[0] == "G"),
                     key=lambda sv: -elevation_deg(visible[sv], USN8_ECEF))
        excl = set(gps[:2])
        h_t, _, _ = build_H({s: p for s, p in visible.items()
                             if s not in excl}, USN8_ECEF)
        times.append(t)
        n_vis.append(len(sv_ids))
        ratio_two.append(information_ratio(h_t, h_full))

    print(f"{len(times)} epochs  visible SVs {min(n_vis)}-{max(n_vis)}  "
          f"ratio(excl 2 GPS) min {min(ratio_two):.3f} "
          f"max {max(ratio_two):.3f}")

    fig, ax = plt.subplots(figsize=(11, 4))
    ax.plot(times, ratio_two, lw=1, color="tab:blue",
            label="information ratio, 2 highest GPS SVs excluded")
    ax.axhline(1.0, color="grey", lw=0.5)
    ax.set_ylim(0, 1.05)
    ax.set_ylabel("normalised information ratio")
    ax.set_xlabel("2026-08-20, GPS time")
    ax2 = ax.twinx()
    ax2.plot(times, n_vis, lw=0.8, color="tab:orange", alpha=0.6,
             label="visible G/E/C SVs")
    ax2.set_ylabel("visible SVs")
    ax.set_title("USN8 clean day — geometry score stability")
    lines = [ln for ln in ax.get_lines() + ax2.get_lines()
             if not ln.get_label().startswith("_")]
    ax.legend(lines, [ln.get_label() for ln in lines], loc="lower right")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, dpi=150)
    print("wrote", OUT)


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 10)
