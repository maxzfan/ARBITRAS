"""Mission registry for the Track F console (tracks/TRACK_F.md §3).

One `Mission` per use case: the presentation frame (route, speed, props), the
attack card, the beats, the correction mechanism and the environment spec the
web page renders. Mission data rides on `/mission?name=` -- never in the §5
stream -- and the receiver at USN8 never moves: the route is a presentation
frame under the measured displacement, exactly as console/mission.py says.

    from console import missions
    m = missions.get("recon")
    missions.as_dict(m)              # what /mission?name=recon returns

The four missions and their mechanisms (decided 2026-09-05, late):
    logistics  human takeover without a trusted fix (drive by the view)
    recon      AUTOMATIC: stationary deduction at an observation point
    casevac    AUTOMATIC: terrain-referenced fix (Track E signed pre-map)
    combat     human takeover on the Galileo corrected fix
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict

from console.mission import (ALERT_LIMIT_M, ALERT_LIMIT_PROVENANCE, LAT, LON, ALT,
                             M_PER_DEG_LAT, M_PER_DEG_LON, enu_to_latlon)

PROP_KINDS = frozenset({
    "fob", "resupply_point", "checkpoint", "phase_line", "objective", "ccp",
    "litter", "aid_station", "patrol_base", "obs_point", "observation_post",
    "hold_marker",
})
MECHANISMS = frozenset({"takeover", "stationary", "terrain"})


@dataclass(frozen=True)
class Mission:
    name: str
    title: str
    tagline: str
    route_enu: tuple[tuple[float, float], ...]
    speed_m_per_epoch: float
    alert_limit_m: float
    alert_limit_provenance: str
    ground_station_enu: tuple[float, float]
    props: tuple[dict, ...]
    stream: str                       # out/<name>.jsonl
    fallback_stream: str | None       # served until the mission stream exists
    attack: dict                      # name, mechanism, watch, response
    mechanism: str                    # takeover | stationary | terrain
    beats: tuple[tuple[int, str], ...]
    behaviour: dict                   # per-state operator sentence
    scene: dict                       # environment spec (TRACK_F.md §2)
    gain: dict = field(default_factory=lambda: {
        "NOMINAL": 1.0, "DEGRADED": 1.0, "RESTRICTED": 1.0, "SURRENDERED": 0.0})
    built: bool = False
    # Half-width of the TRACK the terrain is flattened along (environment
    # modules, contract invariant 1). Defaults to the alert limit, which is the
    # corridor for a road-bound convoy; a scout's 100 m grid-square limit is not
    # a road, so RECON sets it to a single-track width.
    corridor_half_width_m: float | None = None


# ----------------------------------------------------------------- kinematics
# Generic over an explicit route; console/mission.py keeps the logistics-shaped
# module-level API its tests use.

def seg_lengths(route):
    return [math.hypot(e1 - e0, n1 - n0)
            for (e0, n0), (e1, n1) in zip(route, route[1:])]


def route_length(route) -> float:
    return sum(seg_lengths(route))


def route_point(route, s: float):
    segs = seg_lengths(route)
    total = sum(segs)
    s = min(max(s, 0.0), total)
    acc = 0.0
    last = len(segs) - 1
    for i, ((e0, n0), (e1, n1), seg) in enumerate(zip(route, route[1:], segs)):
        if s <= acc + seg or i == last:
            t = 0.0 if seg == 0 else min(max((s - acc) / seg, 0.0), 1.0)
            return (e0 + t * (e1 - e0), n0 + t * (n1 - n0),
                    math.atan2(n1 - n0, e1 - e0))
        acc += seg


# ----------------------------------------------------------------- the four

_CORRECTION_BEHAVIOUR = {
    "NOMINAL": "Full autonomy. Navigating on GNSS and accepting new waypoints.",
    "DEGRADED": "Continuing the mission on the corrected fix. Discrepancy flagged.",
    "RESTRICTED": "Completing the current leg only. New waypoints refused.",
    "SURRENDERED": "Holding position. Control is with the operator.",
}

LOGISTICS = Mission(
    name="logistics", title="LOGISTICS",
    tagline="Last-tactical-mile resupply. The failure is leaving the corridor.",
    route_enu=((0.0, 0.0), (-25.0, 160.0), (15.0, 330.0), (-20.0, 500.0),
               (10.0, 660.0), (-10.0, 815.0)),
    speed_m_per_epoch=3.0,
    alert_limit_m=ALERT_LIMIT_M, alert_limit_provenance=ALERT_LIMIT_PROVENANCE,
    ground_station_enu=(-45.0, -20.0),
    props=(
        {"kind": "fob", "e": 0.0, "n": 0.0, "label": "FOB · SUPPLY POINT", "radius_m": 30.0},
        {"kind": "phase_line", "e": -1.8, "n": 411.8, "label": "PL AMBER", "radius_m": 60.0},
        {"kind": "resupply_point", "e": -10.0, "n": 815.0, "label": "RP KILO · RESUPPLY POINT", "radius_m": 25.0},
    ),
    stream="out/logistics.jsonl", fallback_stream="out/demo.jsonl",
    attack={
        "name": "Intermediate carry-off, cross-corridor",
        "mechanism": "Six highest GPS satellites captured at +2 dB; believed position walked east across the corridor; code and carrier drift apart at 0.02 m/s.",
        "watch": "range residual, code−carrier, dropped satellites, protection level",
        "response": "RAIM re-solve on the trusted subset while it holds; corridor breach withdraws autonomy and hands the convoy to the operator.",
    },
    mechanism="takeover",
    beats=(
        (0, "Convoy departs the FOB on GNSS. Real USN8 observables, 30 s epochs. NOMINAL."),
        (60, "Carry-off begins: six highest GPS satellites captured at +2 dB. Nothing visible."),
        (65, "DEGRADED: believed 10 m east, inside the corridor. Corrected fix valid, PL under the 15 m limit."),
        (67, "Believed position crosses the corridor edge. SURRENDERED: control to the operator. No trusted fix."),
        (150, "Attack ends. The fix snaps back; the residual baseline needs 20 epochs to clear."),
        (190, "DEGRADED: operator hands back; convoy rejoins the route under autonomy."),
        (200, "NOMINAL. Convoy crosses PL AMBER on GNSS."),
        (390, "Authorisation expired at the end of the window: resupply delivered, convoy stands down at RP KILO."),
    ),
    behaviour={
        "NOMINAL": "Convoy proceeding on GNSS. Accepting route updates from the FOB.",
        "DEGRADED": "Convoy proceeding on the trusted-satellite fix; corridor position verified.",
        "RESTRICTED": "Convoy completes the current leg to the phase line. No new route segments accepted.",
        "SURRENDERED": "Convoy under operator control. Corridor position unverified.",
    },
    scene={"env": "logistics", "time": "noon", "ambience": "prairie"},
    gain={"NOMINAL": 1.0, "DEGRADED": 1.0, "RESTRICTED": 1.0, "SURRENDERED": 0.5},
    built=True,
)

RECON = Mission(
    name="recon", title="RECON",
    tagline="A scout files timestamped, georeferenced reports. The failure is a wrong grid.",
    route_enu=((0.0, 0.0), (90.0, 45.0), (150.0, 70.0), (230.0, 85.0), (250.0, 95.0),
               (310.0, 120.0), (400.0, 100.0), (450.0, 30.0), (400.0, -40.0),
               (300.0, -70.0), (180.0, -60.0), (80.0, -30.0), (0.0, 0.0)),
    speed_m_per_epoch=2.2,
    alert_limit_m=100.0,
    alert_limit_provenance="PLACEHOLDER: one 6-figure grid square, the resolution a spot report locates an observation to; doctrine convention, not yet agreed",
    ground_station_enu=(-70.0, 100.0),
    props=(
        {"kind": "patrol_base", "e": 0.0, "n": 0.0, "label": "PATROL BASE", "radius_m": 20.0},
        {"kind": "obs_point", "e": 150.0, "n": 70.0, "label": "OP-1", "radius_m": 15.0},
        {"kind": "obs_point", "e": 250.0, "n": 95.0, "label": "OP-2", "radius_m": 15.0},
        {"kind": "observation_post", "e": 310.0, "n": 120.0, "label": "OP KESTREL", "radius_m": 40.0},
        {"kind": "obs_point", "e": 300.0, "n": -70.0, "label": "OP-3", "radius_m": 15.0},
    ),
    stream="out/recon.jsonl", fallback_stream=None,
    attack={
        "name": "Repeater at a standoff (offset meaconing)",
        "mechanism": "A repeater re-radiates every GPS signal from its own antenna at +8 dB with 300 m of path delay; the receiver solves toward the repeater's position.",
        "watch": "cross-constellation clock channels, GPS dropped as a constellation, information ratio, the drag vector while stationary",
        "response": "Automatic: at the observation point the scout knows it is stationary, reads the drag as the spoof, anchors on the zero-velocity fix, drops GPS, continues on Galileo, and reports the emitter's bearing.",
    },
    mechanism="stationary",
    beats=(
        (0, "Patrol departs. Real USN8 observables, 30 s epochs. NOMINAL."),
        (60, "Repeater on: GPS +8 dB from a 300 m standoff. Believed position dragged 118 m toward the emitter. GPS is the odd constellation out: all 12 dropped, ratio 0.80, Galileo fix valid (PL 7.4 m). Three features saturate: SURRENDERED, scout halts."),
        (61, "Stationary. Anchor held from the last trusted epoch (59). The believed fix is 118 m off the anchor on 85 deg: that offset is the spoof."),
        (70, "Drag measured over the halt. Emitter bearing 85 deg; clock-channel path delay ~258 m (300 m injected). Anchor agrees with the Galileo fix to 0.5 m."),
        (84, "RESTRICTED: authority back one step on Galileo + BeiDou. Scout resumes toward OP-1 on the corrected fix; GPS stays dark."),
        (90, "Repeater off. GPS readmitted once its clock channels re-agree; nothing else was ever excluded."),
        (100, "OP-1 report filed, tagged: constellations behind the fix, PL, clock in holdover during the attack."),
        (104, "NOMINAL, full sky."),
        (390, "Authorisation renewed: key disclosed, uplink solid."),
        (461, "Loop complete. Every report delivered with a trust tag; the emitter's bearing reported."),
    ),
    behaviour={
        "NOMINAL": "Patrol continuing on GNSS. Reports tagged clean.",
        "DEGRADED": "Patrol continuing on Galileo. GPS distrusted; reports tagged; clock in holdover.",
        "RESTRICTED": "Completing the leg to the next observation point only.",
        "SURRENDERED": "Holding at the observation point. Control is with the operator.",
    },
    scene={"env": "recon", "time": "dusk", "ambience": "hills"},
    built=True,
    corridor_half_width_m=12.0,        # single-track patrol path; the 100 m limit is a grid square, not a road
)

CASEVAC = Mission(
    name="casevac", title="CASEVAC",
    tagline="Extraction to a collection point. The failure is arriving at the wrong place.",
    route_enu=((0.0, 0.0), (297.0, -297.0), (339.4, -254.6), (42.4, 42.4), (0.0, 0.0)),
    speed_m_per_epoch=2.25,
    alert_limit_m=ALERT_LIMIT_M, alert_limit_provenance=ALERT_LIMIT_PROVENANCE,
    ground_station_enu=(-90.0, 60.0),
    props=(
        {"kind": "aid_station", "e": 0.0, "n": 0.0, "label": "ROLE 1 AID STATION", "radius_m": 15.0},
        {"kind": "ccp", "e": 297.0, "n": -297.0, "label": "CCP · CASUALTY COLLECTION POINT", "radius_m": 25.0},
        {"kind": "litter", "e": 304.0, "n": -290.0, "label": "CASUALTY · MEDIC WAITING"},
    ),
    stream="out/casevac.jsonl", fallback_stream=None,
    attack={
        "name": "Coherent carry-off, along-track",
        "mechanism": "Six GPS satellites captured at +2 dB; believed position walked 1 m/s along the route heading with code and carrier kept coherent, so code−carrier sees nothing.",
        "watch": "post-fit residual, terrain mismatch, dropped count, protection level",
        "response": "Automatic: the ground under the vehicle disagrees with the signed pre-map at the believed position; each class boundary the sensor crosses pins arc length along the route and corrects the along-track error.",
    },
    mechanism="terrain",
    beats=(
        (0, "Aid station. UGV dispatched to the CCP, 420 m south-east. NOMINAL."),
        (60, "Carry-off begins, code and carrier coherent: code-carrier sees nothing. Believed position starts running ahead along the route."),
        (62, "Ranges no longer fit one position: SURRENDERED, vehicle halts at 140 m. The wheel sensor reads tree cover; the map at the believed position says building. Terrain fix pins arc length: correction -17 m."),
        (84, "The believed pin enters the collection-point ring. Terrain fix holds the vehicle at 140 m: arrival NOT confirmed."),
        (120, "Attack ends; the fix snaps back. Residual baseline needs 30 epochs to clear."),
        (150, "RESTRICTED: authority back one step. Vehicle resumes; odometry carries the terrain fix between boundaries."),
        (158, "Class boundary crossed: sensor and map agree on where. Pin re-zeroes odometry (correction -3 m)."),
        (170, "NOMINAL. Pins at 181, 199 and 235 m keep the fix within 2 m of truth (replay metadata)."),
        (264, "AT CCP, terrain-confirmed: fix inside the 25 m ring on evidence that never came through the antenna."),
        (290, "Casualty aboard. Return leg; pins continue."),
    ),
    behaviour={
        "NOMINAL": "Driving to the collection point on GNSS.",
        "DEGRADED": "Driving on the terrain-referenced fix. GNSS position flagged.",
        "RESTRICTED": "Completing the outbound leg only. Arrival not yet confirmable.",
        "SURRENDERED": "Holding. Control is with the operator.",
    },
    scene={"env": "casevac", "time": "overcast", "ambience": "valley", "terrain_map": True},
    built=True,
)

COMBAT = Mission(
    name="combat", title="COMBAT",
    tagline="Armed advance through phase lines. Position feeds fire control.",
    route_enu=((0.0, 0.0), (150.0, 20.0), (320.0, 40.0), (480.0, 110.0), (640.0, 130.0),
               (800.0, 200.0), (880.0, 260.0)),
    speed_m_per_epoch=3.0,
    alert_limit_m=ALERT_LIMIT_M,
    alert_limit_provenance="ASSUMED: target-location-error category II upper bound (15 m); doctrine from memory, verify before quoting",
    ground_station_enu=(-90.0, 80.0),
    props=(
        {"kind": "phase_line", "e": 0.0, "n": 0.0, "label": "LD", "radius_m": 80.0},
        {"kind": "phase_line", "e": 178.5, "n": 23.4, "label": "PL AMBER", "radius_m": 80.0},
        {"kind": "phase_line", "e": 544.9, "n": 118.1, "label": "PL RED", "radius_m": 80.0},
        {"kind": "objective", "e": 880.0, "n": 260.0, "label": "OBJ HAWK", "radius_m": 60.0},
    ),
    stream="out/combat.jsonl", fallback_stream=None,
    attack={
        "name": "Crude high-power step, then authorisation revoked",
        "mechanism": "A 15 dB spoofer throws every GPS range to a position 250 m east in one epoch; the joint solution lands 98 m past the phase line.",
        "watch": "signal strength, cross-constellation, GPS dropped as a constellation, protection level of the Galileo fix",
        "response": "Autonomy withdrawn on a crude attack; the operator drives the advance on the Galileo corrected fix ARBITRAS stands behind (PL 8 m); fire-control input marked conditional.",
    },
    mechanism="takeover",
    beats=(
        (0, "Advance from LD toward PL AMBER. NOMINAL. Fire-control position input: TRUSTED."),
        (60, "Crude spoofer: 15 dB, all GPS, abrupt. Believed jumps 98 m past PL AMBER. GPS dropped wholesale. SURRENDERED: control to the operator."),
        (61, "What the operator drives by: corrected fix on Galileo, 8 m protection level. Fire-control input: CONDITIONAL."),
        (80, "Spoofer off. GPS readmitted; the residual baseline needs 20 epochs to clear."),
        (120, "DEGRADED. Operator hands back; vehicle rejoins the route under autonomy."),
        (130, "NOMINAL. Fire-control input: TRUSTED."),
        (344, "OBJ HAWK reached. 70 epochs operator-driven, none on a spoofed fix."),
        (390, "Authorisation renewed: key disclosed."),
    ),
    behaviour={
        "NOMINAL": "Full autonomy. Advancing on GNSS; position feeds fire control.",
        "DEGRADED": "Advancing on the corrected fix; fire-control position input conditional.",
        "RESTRICTED": "Completing the current bound only. Fire-control input withheld.",
        "SURRENDERED": "Control is with the operator. Fire-control input conditional on the corrected fix.",
    },
    scene={"env": "combat", "time": "dawn", "ambience": "broken_ground"},
    gain={"NOMINAL": 1.0, "DEGRADED": 1.0, "RESTRICTED": 0.25, "SURRENDERED": 0.5},
    built=True,
)

REGISTRY = {m.name: m for m in (LOGISTICS, RECON, CASEVAC, COMBAT)}
ORDER = ("recon", "logistics", "casevac", "combat")     # selector column order
DEFAULT = "logistics"


def get(name: str | None) -> Mission:
    if name is None:
        return REGISTRY[DEFAULT]
    try:
        return REGISTRY[name]
    except KeyError:
        raise KeyError(f"unknown mission {name!r}; one of {sorted(REGISTRY)}") from None


def as_dict(m: Mission) -> dict:
    """The /mission object. Same keys as console/mission.py as_dict() plus the
    mission fields, so route.js needs no change."""
    route = []
    for e, n in m.route_enu:
        lat, lon = enu_to_latlon(e, n)
        route.append({"e": e, "n": n, "lat": round(lat, 8), "lon": round(lon, 8)})
    gs_lat, gs_lon = enu_to_latlon(*m.ground_station_enu)
    props = []
    for p in m.props:
        lat, lon = enu_to_latlon(p["e"], p["n"])
        props.append(dict(p, lat=round(lat, 8), lon=round(lon, 8)))
    return {
        "name": m.name, "title": m.title, "tagline": m.tagline,
        "surveyed": {"lat": round(LAT, 7), "lon": round(LON, 7), "alt": round(ALT, 2)},
        "station": "USN8",
        "alert_limit_m": m.alert_limit_m,
        "alert_limit_provenance": m.alert_limit_provenance,
        "m_per_deg_lat": M_PER_DEG_LAT,
        "m_per_deg_lon": M_PER_DEG_LON,
        "route": route,
        "route_length_m": round(route_length(m.route_enu), 3),
        "speed_m_per_epoch": m.speed_m_per_epoch,
        "gain": dict(m.gain),
        "corridor_half_width_m": (m.corridor_half_width_m if m.corridor_half_width_m is not None else m.alert_limit_m),
        "heading_convention": "radians, 0=east, pi/2=north, CCW positive",
        "ground_station": {"e": m.ground_station_enu[0], "n": m.ground_station_enu[1],
                           "lat": round(gs_lat, 8), "lon": round(gs_lon, 8)},
        "route_is_presentation_frame": True,
        "props": props,
        "attack": dict(m.attack),
        "mechanism": m.mechanism,
        "beats": [{"epoch": i, "caption": c} for i, c in m.beats],
        "behaviour": dict(m.behaviour),
        "scene": dict(m.scene),
        "stream": m.stream,
        "built": m.built,
    }


def summary() -> list[dict]:
    """What the selector needs for all missions, in column order."""
    return [{"name": m.name, "title": m.title, "tagline": m.tagline,
             "attack": dict(m.attack), "mechanism": m.mechanism,
             "scene": dict(m.scene), "built": m.built}
            for m in (REGISTRY[n] for n in ORDER)]
