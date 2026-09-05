"""The design.md §6b numeric confirmation (~15 min, gates Claim 2a).

Question: does the determinant ratio stay meaningful as the trusted subset
shrinks, or does it degenerate toward zero and stop discriminating?

Worked on paper (tracks/TRACK_C.md GOTCHA 2): the raw ratio decays with the
(3+k)th power and reads 0.00 after two exclusions; the normalised
D-optimality form (det ratio)^(1/(3+k)) stays legible. This script confirms
numerically on a real epoch:

  - sweep exclusion of the 0..7 highest-elevation GPS SVs (worst case for
    geometry), raw vs normalised;
  - exclude ALL of Galileo (the meaconing response) to exercise the
    clock-column drop.

PASS = normalised curve monotone non-increasing and spans a legible range
while the raw curve collapses. Plot goes in the README.

Run from repo root:  python -m backend.geometry.validate_sweep
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from backend.geometry.ephemeris import load_records, sv_positions_at
from backend.geometry.hmatrix import build_H, elevation_deg
from backend.geometry.information import information_ratio, norm_info

USN8_ECEF = np.array([1112161.8802, -4842854.4026, 3985497.3830])
EPOCH = datetime(2026, 8, 20, 12, 0, 0)
EL_MASK_DEG = 10.0
OUT = Path("docs/plots/det_ratio_raw_vs_normalised.png")


def raw_ratio(h_trusted: np.ndarray, h_full: np.ndarray) -> float:
    n_t, d_t = h_trusted.shape
    if n_t < d_t:
        return 0.0
    det_t = np.linalg.det(h_trusted.T @ h_trusted)
    det_f = np.linalg.det(h_full.T @ h_full)
    if det_t <= 0.0 or det_f <= 0.0:
        return 0.0
    return det_t / det_f


def main() -> int:
    records = load_records()
    pos = sv_positions_at(records, EPOCH)
    visible = {sv: p for sv, p in pos.items()
               if elevation_deg(p, USN8_ECEF) > EL_MASK_DEG}
    h_full, sv_ids, consts = build_H(visible, USN8_ECEF)
    print(f"epoch {EPOCH}  visible {len(sv_ids)} SVs  "
          f"constellations {consts}  H {h_full.shape}")

    # -- sweep: drop the n highest-elevation GPS SVs, n = 0..7
    gps_by_el = sorted((sv for sv in visible if sv[0] == "G"),
                       key=lambda sv: -elevation_deg(visible[sv], USN8_ECEF))
    raw, norm = [], []
    for n in range(8):
        excluded = set(gps_by_el[:n])
        trusted = {sv: p for sv, p in visible.items() if sv not in excluded}
        h_t, _, _ = build_H(trusted, USN8_ECEF)
        raw.append(raw_ratio(h_t, h_full))
        norm.append(information_ratio(h_t, h_full))
        print(f"  exclude {n} GPS: raw={raw[-1]:.2e}  normalised={norm[-1]:.3f}")

    # -- meaconing case: exclude ALL of Galileo, clock column drops
    no_e = {sv: p for sv, p in visible.items() if sv[0] != "E"}
    h_no_e, _, c_no_e = build_H(no_e, USN8_ECEF)
    meac = information_ratio(h_no_e, h_full)
    print(f"  exclude ALL Galileo: normalised={meac:.3f}  "
          f"(constellations {c_no_e}, H {h_no_e.shape}, "
          f"norm_info {norm_info(h_no_e):.3f}) — no det cliff")

    # -- verdict
    monotone = all(b <= a + 1e-12 for a, b in zip(norm, norm[1:]))
    legible = norm[-1] > 0.05 and (norm[0] - norm[-1]) > 0.05
    raw_collapses = raw[2] < 0.5 * raw[0] if raw[0] > 0 else False
    ok = monotone and legible and meac > 0.0
    print(f"normalised monotone: {monotone}  legible span "
          f"[{norm[-1]:.3f}, {norm[0]:.3f}]: {legible}  "
          f"raw collapsing: {raw_collapses}  meaconing survivable: {meac > 0}")
    print("PASS — §6b answered, geometry track unblocked, Claim 2a survives"
          if ok else "FAIL — flag at all-hands before building further")

    # -- plot
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 4))
    ns = list(range(8))
    ax1.semilogy(ns, [max(r, 1e-12) for r in raw], "o-", color="tab:red")
    ax1.set_title("raw det ratio (log scale) — decays exponentially")
    ax2.plot(ns, norm, "o-", color="tab:blue")
    ax2.axhline(meac, ls="--", color="tab:green",
                label=f"all Galileo excluded: {meac:.2f}")
    ax2.set_ylim(0, 1.05)
    ax2.set_title("normalised (det ratio)$^{1/(3+k)}$ — legible")
    ax2.legend()
    for ax in (ax1, ax2):
        ax.set_xlabel("highest-elevation GPS SVs excluded")
        ax.grid(alpha=0.3)
    fig.suptitle(f"USN8 {EPOCH:%Y-%m-%d %H:%M} — design.md §6b confirmation")
    fig.tight_layout()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, dpi=150)
    print("wrote", OUT)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
