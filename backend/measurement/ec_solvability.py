"""E+C-only position solvability at USN8 across the demo window. NOT GATED.

At carrier_rate_error = 0 with all_gps capture, features 2 and 3 are blind by
construction and detection rests entirely on cross-constellation geometry, for
which H currently covers G/E/C. This check reports whether a Galileo + BeiDou
position solution exists at all, epoch by epoch, and how well conditioned it
is. It prints numbers and stops: no usability threshold, no judgement, no
conclusion. Those are the reader's.

Demo window default is 12:00-14:00 GPS time (the window every injector test
uses, onset 12:30 at its centre); --start/--end override it.

Solution model: E+C observables only, unknowns x,y,z + one clock per
constellation present (5 with both, 4 with one). An epoch is UNSOLVABLE when
tracked-with-ephemeris rows < unknowns, DEGENERATE when the information
matrix is numerically singular (cond > 1e12). DOPs are reported in the usual
frame: GDOP over all unknowns, PDOP position-only, HDOP/VDOP after rotating
the position block into ENU at the station.

Because the answer depends entirely on backend/rinex/ephemeris.py, the same
three sanity checks used to validate it for GPS are recomputed here for E and
C alone: tracked satellites above the horizon, |pseudorange - geometric
range| within receiver-clock scale, and elevation-to-C/N0 rank correlation.
"""
from __future__ import annotations

import argparse
from datetime import datetime

import numpy as np
import pandas as pd

from ..detection.emit import USN8_ECEF, ecef_to_lla
from ..rinex import ephemeris, noise
from ..rinex.loader import load_obs
from .sweep import build_h, los_frame

C_LIGHT = 299_792_458.0

OBS = "data/USN800USA_R_20262320000_01D_30S_MO.crx.gz"
WINDOW = (datetime(2026, 8, 20, 12, 0), datetime(2026, 8, 20, 14, 0))
COND_LIMIT = 1e12


def enu_rotation(sta_ecef) -> np.ndarray:
    lla = ecef_to_lla(*sta_ecef)
    lat, lon = np.radians(lla["lat"]), np.radians(lla["lon"])
    sl, cl, sp, cp = np.sin(lon), np.cos(lon), np.sin(lat), np.cos(lat)
    return np.array([[-sl, cl, 0.0],
                     [-sp * cl, -sp * sl, cp],
                     [cp * cl, cp * sl, sp]])


def dops(los: pd.DataFrame, rot: np.ndarray) -> dict:
    """GDOP/PDOP/HDOP/VDOP for one LOS set, or the reason there are none."""
    h = build_h(los)
    n, u = h.shape
    if n < u:
        return {"status": "unsolvable", "n": n, "unknowns": u}
    g = h.T @ h
    if np.linalg.cond(g) > COND_LIMIT:
        return {"status": "degenerate", "n": n, "unknowns": u}
    q = np.linalg.inv(g)
    q_enu = rot @ q[:3, :3] @ rot.T
    return {"status": "ok", "n": n, "unknowns": u,
            "gdop": float(np.sqrt(np.trace(q))),
            "pdop": float(np.sqrt(np.trace(q[:3, :3]))),
            "hdop": float(np.sqrt(q_enu[0, 0] + q_enu[1, 1])),
            "vdop": float(np.sqrt(q_enu[2, 2]))}


def propagator_sanity(epochs, nav, systems="EC") -> pd.DataFrame:
    """The three ephemeris sanity checks, per constellation, over the window.

    The range check is reported twice. `raw` is pseudorange minus geometric
    range, which mixes in both clock terms and can read enormous when a
    satellite clock is large (E11 broadcasts af0 = 5.7 ms = 1700 km). The
    `clock-corrected` form removes the broadcast SV clock (c * af0) and the
    per-epoch constellation median (receiver clock + inter-system bias); what
    is left is propagation error plus atmosphere, and should sit at hundreds
    of metres, not hundreds of kilometres."""
    rows = []
    for sysc in systems:
        el_min, raw_max, cc_max, n_trk, n_eph = [], [], [], [], []
        el_all, cn0_all = [], []
        for ep in epochs:
            svs = [s for s in ep.df.index if s.startswith(sysc)]
            tau = {sv: ep.df.at[sv, "code_1"] / C_LIGHT for sv in svs
                   if np.isfinite(ep.df.at[sv, "code_1"])}
            pos = ephemeris.positions_at(ep.time, list(tau), nav, tx_delay_s=tau)
            n_trk.append(len(svs)), n_eph.append(len(pos))
            if pos.empty:
                continue
            el = ephemeris.elevations(pos, USN8_ECEF)
            rng = np.linalg.norm(pos.to_numpy() - np.array(USN8_ECEF), axis=1)
            resid = ep.df.loc[pos.index, "code_1"].to_numpy() - rng
            raw_max.append(np.abs(resid).max())
            af0 = np.array([ephemeris.clock_bias(sv, ep.time, nav)
                            for sv in pos.index])
            cc = resid + C_LIGHT * af0
            cc -= np.median(cc)                  # receiver clock, common
            cc_max.append(np.abs(cc).max())
            el_min.append(el.min())
            el_all.extend(el.values), cn0_all.extend(ep.df.loc[pos.index, "cn0_1"])
        corr = pd.Series(el_all).corr(pd.Series(cn0_all), method="spearman")
        rows.append({
            "system": sysc,
            "tracked_mean": float(np.mean(n_trk)),
            "with_ephemeris_mean": float(np.mean(n_eph)),
            "min_elevation_deg": float(np.min(el_min)),
            "raw_|pr-range|_max_km": float(np.max(raw_max)) / 1e3,
            "clock_corrected_max_km": float(np.max(cc_max)) / 1e3,
            "spearman_elev_cn0": float(corr),
        })
    return pd.DataFrame(rows).set_index("system")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--obs", default=OBS)
    ap.add_argument("--start", default=WINDOW[0].isoformat())
    ap.add_argument("--end", default=WINDOW[1].isoformat())
    args = ap.parse_args(argv)
    t0, t1 = datetime.fromisoformat(args.start), datetime.fromisoformat(args.end)

    day = load_obs(args.obs, systems="GERCS")
    win = [e for e in day if t0 <= e.time < t1]
    nav = ephemeris.load_nav()
    rot = enu_rotation(USN8_ECEF)
    print(f"E+C-only solvability at USN8, {t0:%H:%M}-{t1:%H:%M} GPS time, "
          f"{len(win)} epochs\n")

    print("== ephemeris propagator sanity, E and C only ==")
    print(propagator_sanity(win, nav).round(3).to_string(), "\n")

    rows = []
    for ep in win:
        ec = [s for s in ep.df.index if s[0] in "EC"]
        los = los_frame(ep.time, ec, nav)
        d = dops(los, rot)
        d["time"] = ep.time
        d["nE"] = sum(1 for s in los.index if s[0] == "E")
        d["nC"] = sum(1 for s in los.index if s[0] == "C")
        rows.append(d)
    df = pd.DataFrame(rows).set_index("time")

    print("== per-epoch record (head / around onset / tail) ==")
    cols = ["nE", "nC", "status", "gdop", "pdop", "hdop", "vdop"]
    show = pd.concat([df.head(3), df.loc["2026-08-20 12:29:00":"2026-08-20 12:32:00"],
                      df.tail(3)])
    print(show[cols].round(2).to_string(), "\n")

    print("== distribution across the window ==")
    print("satellite counts: nE min/median/max = %d / %d / %d ;  "
          "nC = %d / %d / %d" % (df.nE.min(), df.nE.median(), df.nE.max(),
                                 df.nC.min(), df.nC.median(), df.nC.max()))
    ok = df[df.status == "ok"]
    for c in ("gdop", "pdop", "hdop", "vdop"):
        q = ok[c].quantile
        print(f"  {c.upper():4s}: min {ok[c].min():6.2f}  p10 {q(.1):6.2f}  "
              f"p50 {q(.5):6.2f}  p90 {q(.9):6.2f}  max {ok[c].max():6.2f}")
    bad = df[df.status != "ok"]
    print(f"epochs with no solution or degenerate geometry: {len(bad)} "
          f"of {len(df)}")
    if len(bad):
        print(bad[["nE", "nC", "status", "n", "unknowns"]].to_string())
    print("\nNumbers only. No usability threshold is set here and no "
          "conclusion is drawn.")


if __name__ == "__main__":
    main()
