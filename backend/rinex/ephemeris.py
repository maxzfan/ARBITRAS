"""Keplerian satellite positions and elevations from broadcast ephemeris.

Needed by the injector's elevation-based target selection and by the subset
sweep. GPS/Galileo/BeiDou only — the Kepler broadcast model. GLONASS
broadcasts state vectors, a different propagation, and is deliberately not
handled here; anything needing R positions waits for Track C's geometry.

Algorithm is IS-GPS-200 §20.3.3.4.3, the standard 16-parameter broadcast
propagation, with per-system gravitational constant and earth rotation rate.
BeiDou Toe is in BDT, offset 14 s from GPS time; that shifts the satellite
~0.1 deg of elevation, harmless for ranking, corrected anyway.

Accuracy check (tests): observed pseudorange minus computed geometric range
stays within receiver-clock scale (< 1000 km) for every tracked GPS SV, and
tracked satellites compute above the horizon.
"""
from __future__ import annotations

import pickle
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

BRDC_GZ = Path("data/BRDC00IGS_R_20262320000_01D_MN.rnx.gz")
BRDC = Path("data/brdc_filtered.rnx")
CACHE = Path(".cache/rinex/nav.pkl")

# (mu, earth rotation rate) per system ICD
GM_OMEGA = {
    "G": (3.986005e14, 7.2921151467e-5),
    "E": (3.986004418e14, 7.2921151467e-5),
    "C": (3.986004418e14, 7.292115e-5),
}
TOE_OFFSET_S = {"C": 14.0}          # BDT = GPST - 14 s
MAX_EPH_AGE_S = 4 * 3600.0

KEPLER_FIELDS = ("sqrtA", "Eccentricity", "M0", "DeltaN", "Toe", "Omega0",
                 "OmegaDot", "Io", "IDOT", "omega", "Cuc", "Cus", "Crc",
                 "Crs", "Cic", "Cis")


def _ensure_filtered() -> Path:
    if not BRDC.exists():
        from .nav_prefilter import prefilter
        prefilter(BRDC_GZ, BRDC)
    return BRDC


def load_nav(systems: str = "GEC") -> pd.DataFrame:
    """All Kepler ephemeris records as one frame: index (sv, record time)."""
    if CACHE.exists():
        with CACHE.open("rb") as fh:
            return pickle.load(fh)

    import warnings

    import georinex as gr
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        ds = gr.load(str(_ensure_filtered()), use=set(systems))

    rows = []
    for sv in np.asarray(ds["sv"].values, dtype=str):
        if sv[0] not in GM_OMEGA:
            continue
        sub = ds.sel(sv=sv)
        for i, t in enumerate(pd.to_datetime(ds["time"].values)):
            rec = {f: float(sub[f].values[i]) for f in KEPLER_FIELDS
                   if f in sub}
            if any(not np.isfinite(v) for v in rec.values()) or len(rec) < 16:
                continue
            rec["sv"], rec["t"] = sv, t.to_pydatetime()
            rows.append(rec)
    nav = pd.DataFrame(rows).set_index(["sv", "t"]).sort_index()
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    with CACHE.open("wb") as fh:
        pickle.dump(nav, fh, protocol=pickle.HIGHEST_PROTOCOL)
    return nav


def seconds_of_week(t: datetime) -> float:
    """GPS seconds of week: time since the preceding Sunday 00:00, GPS time."""
    midnight = datetime(t.year, t.month, t.day)
    sunday = midnight - timedelta(days=(t.weekday() + 1) % 7)
    return (t - sunday).total_seconds()


def _kepler_ecef(eph: pd.Series, t: datetime, system: str) -> np.ndarray:
    mu, omega_e = GM_OMEGA[system]
    a = eph["sqrtA"] ** 2
    e = eph["Eccentricity"]
    n = np.sqrt(mu / a ** 3) + eph["DeltaN"]

    tk = seconds_of_week(t) - TOE_OFFSET_S.get(system, 0.0) - eph["Toe"]
    if tk > 302400:
        tk -= 604800
    elif tk < -302400:
        tk += 604800

    m = eph["M0"] + n * tk
    ecc = m
    for _ in range(12):
        ecc = m + e * np.sin(ecc)
    nu = np.arctan2(np.sqrt(1 - e ** 2) * np.sin(ecc), np.cos(ecc) - e)
    phi = nu + eph["omega"]

    s2, c2 = np.sin(2 * phi), np.cos(2 * phi)
    u = phi + eph["Cus"] * s2 + eph["Cuc"] * c2
    r = a * (1 - e * np.cos(ecc)) + eph["Crs"] * s2 + eph["Crc"] * c2
    inc = eph["Io"] + eph["IDOT"] * tk + eph["Cis"] * s2 + eph["Cic"] * c2

    xp, yp = r * np.cos(u), r * np.sin(u)
    lon = (eph["Omega0"] + (eph["OmegaDot"] - omega_e) * tk
           - omega_e * (eph["Toe"] + TOE_OFFSET_S.get(system, 0.0)))
    return np.array([xp * np.cos(lon) - yp * np.cos(inc) * np.sin(lon),
                     xp * np.sin(lon) + yp * np.cos(inc) * np.cos(lon),
                     yp * np.sin(inc)])


def positions_at(t: datetime, svs, nav: pd.DataFrame | None = None) -> pd.DataFrame:
    """ECEF positions (m) at time t for the SVs that have usable ephemeris."""
    nav = load_nav() if nav is None else nav
    out = {}
    for sv in svs:
        if sv[0] not in GM_OMEGA or sv not in nav.index.get_level_values(0):
            continue
        recs = nav.loc[sv]
        age = np.abs((recs.index - t).total_seconds())
        i = int(np.argmin(age))
        if age[i] > MAX_EPH_AGE_S:
            continue
        out[sv] = _kepler_ecef(recs.iloc[i], t, sv[0])
    df = pd.DataFrame(out, index=["x", "y", "z"]).T
    df.index.name = "sv"
    return df


def elevations(sat_ecef: pd.DataFrame, sta_ecef) -> pd.Series:
    """Elevation angle in degrees of each satellite from a station."""
    from ..detection.emit import ecef_to_lla     # no import cycle: emit is leaf
    sta = np.asarray(sta_ecef, dtype=float)
    lla = ecef_to_lla(*sta)
    lat, lon = np.radians(lla["lat"]), np.radians(lla["lon"])
    up = np.array([np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon),
                   np.sin(lat)])
    los = sat_ecef[["x", "y", "z"]].to_numpy() - sta
    los /= np.linalg.norm(los, axis=1, keepdims=True)
    return pd.Series(np.degrees(np.arcsin(los @ up)), index=sat_ecef.index,
                     name="elevation_deg")


def elevations_at(t: datetime, svs, sta_ecef,
                  nav: pd.DataFrame | None = None) -> pd.Series:
    return elevations(positions_at(t, svs, nav), sta_ecef)
