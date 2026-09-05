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


def as_dict() -> dict:
    return {
        "surveyed": {"lat": round(LAT, 7), "lon": round(LON, 7), "alt": round(ALT, 2)},
        "station": "USN8",
        "alert_limit_m": ALERT_LIMIT_M,
        "alert_limit_provenance": ALERT_LIMIT_PROVENANCE,
        "m_per_deg_lat": M_PER_DEG_LAT,
        "m_per_deg_lon": M_PER_DEG_LON,
    }


if __name__ == "__main__":
    import json
    print(json.dumps(as_dict(), indent=2))
