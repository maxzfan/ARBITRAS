"""Broadcast ephemeris loader for the geometry track.

Loads data/brdc_filtered.rnx (IRNSS already stripped by
backend/rinex/nav_prefilter.py), keeps only Keplerian constellations
G/E/C, and serves the nearest-preceding record per SV.

GOTCHA 1 (tracks/TRACK_C.md): georinex splits Galileo by nav message
type — E02, E02_1, E02_2 ... are the SAME satellite. Keep only base
PRNs matching ^[GEC]\\d{2}$ or H double-counts rows and the information
matrix is wrong in a way that looks plausible.

GLONASS (R) is dropped: ECEF state-vector nav needing RK4 integration,
not Keplerian — see README limitations. BeiDou GEO/IGSO-GEO PRNs
(C01-C05, C59+) are dropped: special propagation, below USN8's horizon.

Times: nav record epochs are in each constellation's own frame. GST is
aligned to GPS time; BDT = GPST - 14 s. We convert every record epoch to
GPS time on load (`toc_gpst`) and match against GPST obs epochs.
"""
from __future__ import annotations

import bisect
import pickle
import re
import warnings
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from backend.geometry.sv_positions import FIELDS, kepler_to_ecef

NAV_PATH = Path("data/brdc_filtered.rnx")
CACHE_PATH = Path("data/brdc_geometry_cache.pkl")
BASE_PRN = re.compile(r"^[GEC]\d{2}$")
BDS_GEO = {f"C{p:02d}" for p in (1, 2, 3, 4, 5)} | {f"C{p}" for p in range(59, 64)}
BDT_OFFSET = timedelta(seconds=14)
MAX_AGE = timedelta(hours=4)


def load_records(nav_path: Path = NAV_PATH,
                 cache_path: Path | None = CACHE_PATH) -> dict[str, list[dict]]:
    """{sv: [record, ...] sorted by toc_gpst}. Parses once, caches to disk."""
    if cache_path and cache_path.exists():
        with open(cache_path, "rb") as fh:
            return pickle.load(fh)

    import georinex as gr
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        nav = gr.load(nav_path)
    df = nav.to_dataframe().reset_index()

    records: dict[str, list[dict]] = {}
    for _, row in df.iterrows():
        sv = row["sv"]
        if not BASE_PRN.match(sv) or sv in BDS_GEO:
            continue
        if any(np.isnan(row.get(f, np.nan)) for f in FIELDS):
            continue
        toc = row["time"].to_pydatetime()
        rec = {f: float(row[f]) for f in FIELDS}
        rec["toc_gpst"] = toc + BDT_OFFSET if sv[0] == "C" else toc
        records.setdefault(sv, []).append(rec)

    for recs in records.values():
        recs.sort(key=lambda r: r["toc_gpst"])

    if cache_path:
        with open(cache_path, "wb") as fh:
            pickle.dump(records, fh)
    return records


def best_ephemeris(records: dict[str, list[dict]], sv: str,
                   t_gpst: datetime) -> dict | None:
    """Latest record with toc_gpst <= t (nearest overall as fallback for
    the first minutes of the day); None if older than MAX_AGE."""
    recs = records.get(sv)
    if not recs:
        return None
    tocs = [r["toc_gpst"] for r in recs]
    i = bisect.bisect_right(tocs, t_gpst) - 1
    rec = recs[max(i, 0)]
    if abs(t_gpst - rec["toc_gpst"]) > MAX_AGE:
        return None
    return rec


def sv_positions_at(records: dict[str, list[dict]],
                    t_gpst: datetime) -> dict[str, np.ndarray]:
    """{sv: ecef} for every SV with a usable record at t."""
    out = {}
    for sv in records:
        rec = best_ephemeris(records, sv, t_gpst)
        if rec is not None:
            out[sv] = kepler_to_ecef(rec, t_gpst)
    return out
