"""Mission context: the surveyed truth position and the alert limit.

This is mission data, not receiver output. It belongs to the console because
the mission defines it -- the receiver never reports it, and under attack the
receiver's own numbers are exactly what we do not trust. Keeping it here means
nothing crosses the §5 boundary to draw it.
"""
import math

# design.md §4 gives USN8's ECEF directly. Converting it is how we get truth:
# USN8 is a SURVEYED IGS station, so its position is known to millimetres and
# does not have to be modelled. That is what makes displacement measurable
# rather than estimated (design.md §10, maximum adversarial displacement).
ECEF = (1112161.8802, -4842854.4026, 3985497.3830)


def ecef_to_geodetic(x, y, z):
    a, f = 6378137.0, 1 / 298.257223563          # WGS84
    e2 = f * (2 - f)
    lon = math.atan2(y, x)
    p = math.hypot(x, y)
    lat = math.atan2(z, p * (1 - e2))
    for _ in range(8):                            # Bowring iteration
        n = a / math.sqrt(1 - e2 * math.sin(lat) ** 2)
        h = p / math.cos(lat) - n
        lat = math.atan2(z, p * (1 - e2 * n / (n + h)))
    n = a / math.sqrt(1 - e2 * math.sin(lat) ** 2)
    return math.degrees(lat), math.degrees(lon), p / math.cos(lat) - n


LAT, LON, ALT = ecef_to_geodetic(*ECEF)

# design.md §10: "Alert limit -- error beyond which the situation is hazardous.
# Pick one for the corridor (road width or standoff distance), justify it, and
# report whether displacement stays under it."
#
# 15 m is a single-track unimproved roadway half-width: a resupply UGV displaced
# further than this is off the trafficable surface. IT IS NOT YET AGREED.
ALERT_LIMIT_M = 15.0
ALERT_LIMIT_PROVENANCE = "PLACEHOLDER"   # -> "agreed" once the team signs off

# Metres per degree at this latitude, for plotting.
M_PER_DEG_LAT = 111132.92 - 559.82 * math.cos(2 * math.radians(LAT))
M_PER_DEG_LON = 111412.84 * math.cos(math.radians(LAT))




# ---------------------------------------------------------------------------
# THE ROUTE -- a presentation frame, not a measurement.
#
# The receiver at USN8 is a static reference station. It never moved. The
# console shows a vehicle driving a route because the operator's question
# (design.md §1) is about a moving UGV, and the arbitration logic does not care
# whether the antenna moved. So:
#
#     TRUE     = route_point(s)                   scripted presentation frame
#     BELIEVED = TRUE + D,  D = position - _truth measured displacement, ENU
#
# The DIVERGENCE between the two tracks is therefore 100% data: D comes from the
# stream, nothing else. If the stream carries no displacement (Track A emits
# position == surveyed until Track C's solution exists), both tracks coincide.
# That is correct behaviour, not a bug. The on-screen caption states this frame
# explicitly (design.md §11b: nothing on screen may read as a measurement it
# is not).
#
# Waypoints are local ENU metres relative to the surveyed point (the origin).
# ~800 m -- "last tactical mile" scale (§1). Heading trends east so the route
# reads left-to-right on a north-up map.
ROUTE_ENU = [
    (0.0, 0.0),
    (140.0, 30.0),
    (300.0, 20.0),
    (460.0, 80.0),
    (620.0, 60.0),
    (790.0, 130.0),
]

# Presentation speed, metres of route per epoch. Chosen so the 360-epoch demo
# window (12:00-15:00 UTC, out/demo.jsonl) traverses the ~820 m route almost
# exactly once. This is NOT 30 s x a real vehicle speed -- at 30 s epochs a real
# UGV at 3 m/s would cover 90 m per epoch and finish the route in nine epochs.
# The number is a framing choice and is labelled as such on screen.
ROUTE_SPEED_M_PER_EPOCH = 2.25

# Corridor half-width = the alert limit (design.md §10: "road width"). In the
# moving views the alert ring becomes a band along the route. "Beyond alert
# limit" is judged on |D| (the measured displacement magnitude), NOT on the
# believed position's lateral offset from the route -- |D| is what the readout
# shows and what §10 defines; lateral offset is a derived convenience for
# drawing. Keep them from drifting apart: the band is drawn at |D|'s limit.
CORRIDOR_HALF_WIDTH_M = ALERT_LIMIT_M

# Ground station -- the source of the TESLA mission authorisation (design.md §9).
# It is NOT a satellite: the credential channel is a ground uplink, and the
# scene draws it as a distinct link so the two channels (satellite ranging ->
# position; ground uplink -> authority) cannot be conflated. Fixed mission
# location, ENU metres from the surveyed origin, ~125 m off the route start.
GROUND_STATION_ENU = (-60.0, 110.0)


def _seg_lengths():
    return [math.hypot(e1 - e0, n1 - n0)
            for (e0, n0), (e1, n1) in zip(ROUTE_ENU, ROUTE_ENU[1:])]


def route_length() -> float:
    return sum(_seg_lengths())


def route_point(s: float):
    """Position and heading at arc length s along the route.

    Returns (east_m, north_m, heading_rad). heading_rad is the mathematical
    angle of the travel direction: 0 = +east, pi/2 = +north, counter-clockwise
    positive. s is clamped to [0, route_length()].
    """
    segs = _seg_lengths()
    total = sum(segs)
    s = min(max(s, 0.0), total)
    acc = 0.0
    last = len(segs) - 1
    for i, ((e0, n0), (e1, n1), seg) in enumerate(zip(ROUTE_ENU, ROUTE_ENU[1:], segs)):
        if s <= acc + seg or i == last:
            t = 0.0 if seg == 0 else min(max((s - acc) / seg, 0.0), 1.0)
            return (e0 + t * (e1 - e0), n0 + t * (n1 - n0),
                    math.atan2(n1 - n0, e1 - e0))
        acc += seg


def lateral_offset(east: float, north: float) -> float:
    """Signed distance from the route polyline, metres.

    Positive = LEFT of the direction of travel (port), negative = right.
    Uses the nearest point on the polyline.
    """
    best = None
    for (e0, n0), (e1, n1) in zip(ROUTE_ENU, ROUTE_ENU[1:]):
        de, dn = e1 - e0, n1 - n0
        L2 = de * de + dn * dn
        t = 0.0 if L2 == 0 else ((east - e0) * de + (north - n0) * dn) / L2
        t = min(max(t, 0.0), 1.0)
        pe, pn = e0 + t * de, n0 + t * dn
        d = math.hypot(east - pe, north - pn)
        if best is None or d < best[0]:
            # sign: cross(tangent, offset) > 0 means offset is to the left
            cross = de * (north - pn) - dn * (east - pe)
            best = (d, math.copysign(d, cross) if cross != 0 else d)
    return best[1]


def enu_to_latlon(east: float, north: float):
    return LAT + north / M_PER_DEG_LAT, LON + east / M_PER_DEG_LON


def latlon_to_enu(lat: float, lon: float):
    return (lon - LON) * M_PER_DEG_LON, (lat - LAT) * M_PER_DEG_LAT


def as_dict() -> dict:
    route = []
    for e, n in ROUTE_ENU:
        lat, lon = enu_to_latlon(e, n)
        route.append({"e": e, "n": n, "lat": round(lat, 8), "lon": round(lon, 8)})
    return {
        "surveyed": {"lat": round(LAT, 7), "lon": round(LON, 7), "alt": round(ALT, 2)},
        "station": "USN8",
        "alert_limit_m": ALERT_LIMIT_M,
        "alert_limit_provenance": ALERT_LIMIT_PROVENANCE,
        "m_per_deg_lat": M_PER_DEG_LAT,
        "m_per_deg_lon": M_PER_DEG_LON,
        # Presentation frame (see the ROUTE comment block). Receiver is static.
        "route": route,
        "route_length_m": round(route_length(), 3),
        "speed_m_per_epoch": ROUTE_SPEED_M_PER_EPOCH,
        "corridor_half_width_m": CORRIDOR_HALF_WIDTH_M,
        "heading_convention": "radians, 0=east, pi/2=north, CCW positive",
        "ground_station": {"e": GROUND_STATION_ENU[0], "n": GROUND_STATION_ENU[1],
                           **dict(zip(("lat", "lon"),
                                      (round(v, 8) for v in enu_to_latlon(*GROUND_STATION_ENU))))},
        "route_is_presentation_frame": True,
    }


if __name__ == "__main__":
    import json
    print(json.dumps(as_dict(), indent=2))
