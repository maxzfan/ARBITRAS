"""Real satellite sky geometry from broadcast ephemeris.

Propagates broadcast Keplerian elements to satellite ECEF positions, then
converts to azimuth/elevation as seen from USN8's surveyed position. Feeds the
console's 3D constellation view, where satellites go dark as they leave the
trusted set and the information volume collapses -- design.md §6b, drawn.

These numbers are REAL. They come from BRDC00IGS broadcast ephemeris for
2026-08-20 (DOY 232), propagated with the standard broadcast orbit model via
gnss-lib-py's `find_sv_states`. Nothing here is synthesised.

CONSTELLATION SCOPE -- G and E only (GPS, Galileo). Verified, not assumed:

* GLONASS broadcasts a PZ-90 state vector (position/velocity/acceleration)
  integrated with Runge-Kutta, not Keplerian elements. Feeding those rows to a
  Keplerian propagator returns confident, plausible, WRONG positions.
* BeiDou parses fine and has finite sqrtA, but gnss-lib-py's `find_sv_states`
  returns all-NaN for it -- BeiDou's broadcast time base is BDT, not GPS, and
  the library does not convert. Measured: 37 BeiDou satellites selected at
  2026-08-20T12:00Z, 0 finite positions. Excluded deliberately rather than
  silently vanishing at the elevation mask.
* SBAS is geostationary and broadcasts corrections, not a Keplerian orbit.
  QZSS is regional to East Asia and irrelevant to this corridor.

If Track C wants BeiDou in the trusted set, the BDT->GPS week/second offset
(14 s, plus a different week epoch) has to be applied before propagation.

The Galileo dedupe below is not optional -- see `_base_prn`.
"""
import warnings
from datetime import datetime, timezone
from functools import lru_cache

import numpy as np

warnings.filterwarnings("ignore")

import gnss_lib_py as glp                                    # noqa: E402
from gnss_lib_py.utils.coordinates import ecef_to_el_az       # noqa: E402
from gnss_lib_py.utils.time_conversions import datetime_to_gps_millis  # noqa: E402

from console.mission import ECEF as RX_ECEF                   # noqa: E402

DEFAULT_NAV = "data/brdc_filtered.rnx"
ELEVATION_MASK_DEG = 10.0

# Keplerian constellations only. See the module docstring.
KEPLERIAN = {"gps": "G", "galileo": "E"}

# Broadcast ephemeris is valid for a couple of hours either side of t_oe.
# Beyond this the extrapolation is not trustworthy and we drop the satellite
# rather than report a degrading position as if it were good.
MAX_EPHEM_AGE_S = 4 * 3600


def _base_prn(gnss_sv_id: str) -> str:
    """'E02_1' -> 'E02'.  'G07' -> 'G07'.

    This is where the Galileo trap is closed, and it is subtler than it looks.
    georinex surfaces Galileo as E02, E02_1, E02_2, E02_3 -- it splits by nav
    MESSAGE TYPE (I/NAV vs F/NAV), not by satellite, which is why E appears to
    have 120 satellites instead of ~26.

    gnss-lib-py's RinexNav does NOT fix this. It folds the suffix into the
    numeric sv_id, so E14, E14_1, E14_2, E14_3 become sv_id 14, 141, 142, 143 --
    and keying on sv_id yields four satellites at identical azimuth and
    elevation, which looks entirely plausible on a skyplot. The string
    `gnss_sv_id` is the only field that survives round-tripping, so that is what
    we key on.
    """
    return str(gnss_sv_id).split("_")[0]


@lru_cache(maxsize=4)
def load_ephemeris(path: str = DEFAULT_NAV):
    """Parse the nav file once. Cached -- reparsing costs ~20 s."""
    nav = glp.RinexNav(path)
    keep = np.isin(nav["gnss_id"], list(KEPLERIAN))
    idx = np.flatnonzero(keep)
    return nav.copy(cols=idx)


@lru_cache(maxsize=4)
def _index(path: str = DEFAULT_NAV):
    """Per-row base PRN and epoch, computed once.

    Without this, selecting an ephemeris walks all ~21k rows per epoch, which
    turns a 520-epoch fixture regeneration into a coffee break.
    """
    ephem = load_ephemeris(path)
    prns = np.array([_base_prn(x) for x in ephem["gnss_sv_id"]])
    return ephem, prns, ephem["gps_millis"].astype(float)


def _select(ephem, gps_millis: float, path: str = DEFAULT_NAV):
    """One ephemeris record per satellite: the most recent one still valid.

    Returns (NavData subset, [prn, ...]) aligned by column.
    """
    _, all_prns, t_ms = _index(path)
    # t_oe is seconds into the GPS week; the record's own week start is already
    # folded into gps_millis, so compare on gps_millis directly.
    age = np.abs(gps_millis - t_ms) / 1000.0
    cand = np.flatnonzero(age <= MAX_EPHEM_AGE_S)
    if cand.size == 0:
        return None, []

    # Closest ephemeris first, so the first time we see a PRN it is the best one.
    order = cand[np.argsort(age[cand], kind="stable")]
    best = {}
    for col in order:
        prn = all_prns[col]
        if prn not in best:
            best[prn] = int(col)

    prns = sorted(best)
    return ephem.copy(cols=np.array([best[p] for p in prns], dtype=int)), prns


def sky_at(when: datetime, nav_path: str = DEFAULT_NAV,
           mask_deg: float = ELEVATION_MASK_DEG) -> list:
    """Visible satellites at `when`, as [{"sv","az","el"}, ...].

    az in [0,360), el in [mask, 90]. Sorted by descending elevation, so the
    console can draw the highest satellites last.
    """
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    gps_millis = float(datetime_to_gps_millis(when))

    ephem = load_ephemeris(nav_path)
    subset, prns = _select(ephem, gps_millis, nav_path)
    if not prns:
        return []

    states = glp.find_sv_states(gps_millis, subset)
    sv_pos = np.vstack([states["x_sv_m"], states["y_sv_m"], states["z_sv_m"]])
    el_az = ecef_to_el_az(np.array(RX_ECEF).reshape(1, 3), sv_pos)
    el, az = el_az[0], el_az[1]

    out = []
    for prn, e, a in zip(prns, el, az):
        if not np.isfinite(e) or not np.isfinite(a) or e < mask_deg:
            continue
        out.append({"sv": prn, "az": round(float(a) % 360.0, 1),
                    "el": round(float(e), 1)})
    out.sort(key=lambda s: -s["el"])
    return out


def sanity_check(when: datetime = None, nav_path: str = DEFAULT_NAV) -> dict:
    """Assert the propagation is not producing nonsense. Prints its findings.

    A mid-latitude station with a 10 deg mask should see roughly 8-14 GPS and a
    similar number of Galileo. Two would mean the ephemeris selection is broken;
    forty would mean satellites are being counted more than once.
    """
    when = when or datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
    sky = sky_at(when, nav_path)
    by_sys = {}
    for s in sky:
        by_sys.setdefault(s["sv"][0], []).append(s)

    els = [s["el"] for s in sky]
    azs = [s["az"] for s in sky]
    report = {
        "epoch": when.isoformat(),
        "visible_total": len(sky),
        "by_constellation": {k: len(v) for k, v in sorted(by_sys.items())},
        "elevation_range": (min(els), max(els)) if els else None,
        "azimuth_range": (min(azs), max(azs)) if azs else None,
        "duplicate_prns": len(sky) - len({s["sv"] for s in sky}),
    }
    missing = sorted(set(KEPLERIAN.values()) - set(by_sys))
    report["constellations_missing"] = missing
    assert not missing, (
        f"constellation(s) {missing} produced no visible satellites -- the "
        f"propagation is failing silently, do not ship this")
    assert report["duplicate_prns"] == 0, "a satellite is counted twice"
    assert all(ELEVATION_MASK_DEG <= e <= 90.0 for e in els), "elevation out of range"
    assert all(0.0 <= a < 360.0 for a in azs), "azimuth out of range"
    return report


if __name__ == "__main__":
    import json
    r = sanity_check()
    print(json.dumps(r, indent=2))
    print("\nhighest 6:")
    for s in sky_at(datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc))[:6]:
        print(f"  {s['sv']}  el {s['el']:5.1f}  az {s['az']:6.1f}")
