"""Single-point pseudorange position solutions.

Iterative least squares over the ionosphere-free code combination, per
constellation or all-in-view. Used two ways:

- the **believed position** in the §5 contract (`position_source: "solution"`)
  — under attack this is the spoofed one; that is the point;
- the per-constellation solutions the cross-constellation feature compares.

Model, and what is deliberately left out:

- Dual-frequency iono-free combination; satellites missing band 2 are dropped
  rather than mixed in with a different bias.
- Broadcast SV clock af0 + af1*(t - toc). Sagnac correction by rotating the
  satellite through the signal flight time. Relativistic eccentricity and
  group-delay terms (metres) are absorbed by the per-constellation clock or
  the feature's clean baseline and are not modelled.
- Troposphere: standard-atmosphere Saastamoinen zenith delay mapped by
  1/sin(el). Constants are the standard atmosphere's, not tuned. Without this
  the residual RMS is ~30 m, dominated by low-elevation satellites; with it,
  metres. Residual atmosphere and group delays are absorbed by the
  per-constellation clock or the feature's clean baseline. Nothing downstream
  reads an absolute coordinate off this solver.
- 5 degree elevation mask, the ordinary receiver convention, applied from the
  second iteration (geometry is not trusted until there is a position).
- GPS/Galileo/BeiDou only: GLONASS broadcasts state vectors, whose propagation
  is Track C's. One clock unknown per constellation in the solution.
"""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from . import bands, ephemeris

C_LIGHT = 299_792_458.0
OMEGA_E = 7.2921151467e-5
EL_MASK_DEG = 5.0
SOLVABLE = "GEC"

# Saastamoinen hydrostatic zenith delay for the standard atmosphere at sea
# level (1013.25 hPa): 0.002277 * P metres. USN8 sits at 59 m; close enough
# for a correction whose residual the clock absorbs.
TROPO_ZENITH_M = 0.002277 * 1013.25


def tropo_delay(el_deg: np.ndarray) -> np.ndarray:
    """Zenith delay mapped by 1/sin(el), the flat-earth mapping. Fine above
    the 5 degree mask; wrong below it, which is one more reason for the mask."""
    return TROPO_ZENITH_M / np.sin(np.radians(np.clip(el_deg, 3.0, None)))


def iono_free(df: pd.DataFrame) -> pd.Series:
    """First-order ionosphere-free code combination, metres."""
    out = pd.Series(np.nan, index=df.index)
    for sysc in set(df["system"]):
        if sysc not in bands.FREQ:
            continue
        f1, f2 = bands.FREQ[sysc]
        g = f1 ** 2 / (f1 ** 2 - f2 ** 2)
        rows = df["system"] == sysc
        out[rows] = g * df.loc[rows, "code_1"] - (g - 1) * df.loc[rows, "code_2"]
    return out


def _sagnac(pos: np.ndarray, tau: np.ndarray) -> np.ndarray:
    """Rotate satellite positions through the earth rotation during flight."""
    ang = OMEGA_E * tau
    ca, sa = np.cos(ang), np.sin(ang)
    return np.stack([ca * pos[:, 0] + sa * pos[:, 1],
                     -sa * pos[:, 0] + ca * pos[:, 1],
                     pos[:, 2]], axis=1)


def solve(epoch, systems: str = SOLVABLE, nav: pd.DataFrame | None = None,
          x0=None, sat_pos: pd.DataFrame | None = None,
          iters: int = 6) -> dict | None:
    """One epoch, one solution. Returns None when underdetermined.

        {"pos": ecef (3,), "clock_m": {sys: metres}, "n_used": int,
         "resid_rms_m": float, "systems": str}

    `x0` seeds the iteration (default: earth centre — converges in 5). Passing
    `sat_pos` skips the ephemeris call so several subset solutions per epoch
    share one propagation.
    """
    nav = ephemeris.load_nav() if nav is None else nav
    df = epoch.df[[s in systems for s in epoch.df["system"]]]
    pr = iono_free(df).dropna()
    if sat_pos is None:
        sat_pos = ephemeris.positions_at(
            epoch.time, list(pr.index), nav,
            tx_delay_s={sv: pr[sv] / C_LIGHT for sv in pr.index})
    svs = [sv for sv in pr.index if sv in sat_pos.index]
    if not svs:
        return None
    dts = np.array([ephemeris.clock_bias(sv, epoch.time, nav) for sv in svs])
    p = sat_pos.loc[svs].to_numpy()
    y_all = pr[svs].to_numpy() + C_LIGHT * dts       # PR corrected for SV clock

    x = np.zeros(3) if x0 is None else np.asarray(x0, dtype=float)
    use = np.ones(len(svs), dtype=bool)
    sys_of = np.array([sv[0] for sv in svs])
    clocks = None
    for it in range(iters):
        present = sorted(set(sys_of[use]))
        if use.sum() < 3 + len(present):
            return None
        rho0 = np.linalg.norm(p[use] - x, axis=1)
        psag = _sagnac(p[use], rho0 / C_LIGHT)
        d = psag - x
        rho = np.linalg.norm(d, axis=1)
        h = np.zeros((use.sum(), 3 + len(present)))
        h[:, :3] = -d / rho[:, None]
        for j, sysc in enumerate(present):
            h[sys_of[use] == sysc, 3 + j] = 1.0
        prev_clk = np.array([clocks.get(sysc, 0.0) if clocks else 0.0
                             for sysc in sys_of[use]])
        dy = y_all[use] - rho - prev_clk
        try:
            dx, *_ = np.linalg.lstsq(h, dy, rcond=None)
        except np.linalg.LinAlgError:
            return None
        x = x + dx[:3]
        clocks = {sysc: (clocks.get(sysc, 0.0) if clocks else 0.0) + dx[3 + j]
                  for j, sysc in enumerate(present)}
        if it == 1:                                   # position now credible
            el = ephemeris.elevations(sat_pos.loc[svs], x)
            use = (el.to_numpy() >= EL_MASK_DEG)
            y_all = y_all - tropo_delay(el.to_numpy())
        if np.linalg.norm(dx[:3]) < 1e-4:
            break
    resid = dy - h @ dx
    return {"pos": x, "clock_m": clocks, "n_used": int(use.sum()),
            "svs_used": [sv for sv, u in zip(svs, use) if u],
            "resid_rms_m": float(np.sqrt(np.mean(resid ** 2))),
            "systems": "".join(sorted(set(sys_of[use])))}


def solve_per_constellation(epoch, nav: pd.DataFrame | None = None,
                            systems: str = SOLVABLE, x0=None) -> dict:
    """All-in-view solution plus one per constellation, sharing one ephemeris
    propagation. Keys: "all" and each constellation letter that solved."""
    nav = ephemeris.load_nav() if nav is None else nav
    df = epoch.df[[s in systems for s in epoch.df["system"]]]
    pr_all = iono_free(df).dropna()
    sat_pos = ephemeris.positions_at(
        epoch.time, list(pr_all.index), nav,
        tx_delay_s={sv: pr_all[sv] / C_LIGHT for sv in pr_all.index})
    out = {}
    full = solve(epoch, systems=systems, nav=nav, x0=x0, sat_pos=sat_pos)
    if full:
        out["all"] = full
    for sysc in systems:
        one = solve(epoch, systems=sysc, nav=nav,
                    x0=full["pos"] if full else x0, sat_pos=sat_pos)
        if one:
            out[sysc] = one
    return out
