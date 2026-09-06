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

import copy
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
    # The ARBITRAS guide's script (console/web/DIALOGUE.md §3): the
    # operator-facing rewrite of `beats`, one entry per pinned epoch, each
    # carrying 1-3 lines. Every numeral in a line's `text` is covered by a
    # claim holding either a contract `path` (dug out of the epoch and
    # verified before display, as explain.verify does) or a named `source`
    # (a scenario parameter, which is not a measurement). design.md §14.
    # Defaulted so nothing that constructs a Mission has to supply it.
    guide: tuple[dict, ...] = ()


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
    guide=(
        # The operator-facing rewrite of `beats`, anchored on the same epochs.
        # design.md §14: every numeral below is covered by a claim carrying
        # either a contract `path` (verified live against the epoch) or a
        # named `source`. Values are the exact figures on out/logistics.jsonl.
        {"epoch": 0, "lines": [
            {"text": "The convoy is leaving the supply point on satellite navigation. Everything here is real: observations recorded at a US Naval Observatory receiver, one every 30 s.",
             "tone": "calm",
             "claims": [{"text": "30 s", "value": 30.0,
                         "source": "observation interval of the IGS daily files, CLAUDE.md Data (2026-08-20, 30 s)"}]},
            {"text": "My job is to say how far that position can be trusted. Right now every monitored signal sits in its own normal range and confidence is 0.91, so the convoy has full authority.",
             "tone": "calm",
             "claims": [{"text": "0.91", "value": 0.9079, "path": "confidence"}]},
            {"text": "The road is the constraint. More than 15 m of position error puts the convoy off a single-track corridor, so 15 m is what every fix is judged against.",
             "tone": "calm",
             "claims": [{"text": "15 m", "value": 15.0, "path": "geometry.correction.alert_limit_m"}]},
        ]},
        {"epoch": 60, "lines": [
            {"text": "A spoofer has just captured the six strongest GPS satellites and lifted them 2 dB. That is barely above the noise, and deliberately so.",
             "tone": "alert",
             "claims": [{"text": "2 dB", "value": 2.0,
                         "source": "injector power, docs/stream_provenance_logistics.md (carry_off, power 2.0 dB, 6 SV)"}]},
            {"text": "Nothing on my panel has moved. No satellite is outside the trusted set, confidence is 0.87, and the believed position is still on the road. This is what a good attack looks like at the start.",
             "tone": "alert",
             "claims": [{"text": "0.87", "value": 0.8652, "path": "confidence"}]},
            {"text": "From here the spoofer walks the believed position sideways at 0.2 m/s. I will not see it until the ranges stop agreeing on one place.",
             "tone": "alert",
             "claims": [{"text": "0.2 m/s", "value": 0.2,
                         "source": "injector walk rate, docs/stream_provenance_logistics.md (carry_off, 0.2 m/s, bearing 90)"}]},
        ]},
        {"epoch": 65, "lines": [
            {"text": "Now the ranges disagree. No single position fits them all any more: that score has climbed to 0.73, and seven satellites have dropped out of the trusted set.",
             "tone": "bad",
             "claims": [{"text": "0.73", "value": 0.7345, "path": "features.pseudorange_residual"}]},
            {"text": "Authority steps down one notch. I still stand behind a fix, re-solved on the satellites I do trust, with 7.8 m of protection level against the 15 m limit.",
             "tone": "bad",
             "claims": [{"text": "7.8 m", "value": 7.79, "path": "geometry.correction.protection_level_m"},
                        {"text": "15 m", "value": 15.0, "path": "geometry.correction.alert_limit_m"}]},
        ]},
        {"epoch": 67, "lines": [
            {"text": "Two epochs later the trusted set is down to eight satellites and the protection level is 18.3 m, wider than the road itself. I can no longer certify which side of it we are on.",
             "tone": "bad",
             "claims": [{"text": "18.3 m", "value": 18.28, "path": "geometry.correction.protection_level_m"}]},
            {"text": "So authority goes to the operator. That is not the vehicle giving up: a person drives the contested stretch by what they can see, and I keep scoring every epoch behind them.",
             "tone": "act", "claims": []},
        ]},
        {"epoch": 150, "lines": [
            {"text": "The spoofer has stopped. The ranges fit one place again, but my baselines learned the attacked level while it ran, so the residual score still reads 0.97.",
             "tone": "calm",
             "claims": [{"text": "0.97", "value": 0.9673, "path": "features.pseudorange_residual"}]},
            {"text": "I do not hand authority back on that. Confidence has to hold above the next threshold for 10 straight epochs before each step up, so nothing recovers on one lucky reading.",
             "tone": "calm",
             "claims": [{"text": "10", "value": 10,
                         "source": "RECOVERY_EPOCHS, console/arbitras/states.py"}]},
        ]},
        {"epoch": 190, "lines": [
            {"text": "Baselines have caught up. Full sky, nothing excluded, information ratio 1.00, and the corrected fix carries 2.8 m of protection level.",
             "tone": "good",
             "claims": [{"text": "1.00", "value": 1.0, "path": "geometry.information_ratio"},
                        {"text": "2.8 m", "value": 2.83, "path": "geometry.correction.protection_level_m"}]},
            {"text": "The operator hands the convoy back and it rejoins the route under its own authority, one step at a time.",
             "tone": "good", "claims": []},
        ]},
        {"epoch": 200, "lines": [
            {"text": "Full authority restored, confidence 0.92. The convoy crosses the phase line on satellite navigation, and the contested stretch was driven without a trusted fix and without stopping.",
             "tone": "good",
             "claims": [{"text": "0.92", "value": 0.9243, "path": "confidence"}]},
        ]},
        {"epoch": 390, "lines": [
            {"text": "Nothing is wrong with the sky: confidence is 0.91 and every satellite is trusted. But the authorisation for this window has lapsed and no renewal key arrived.",
             "tone": "bad",
             "claims": [{"text": "0.91", "value": 0.907, "path": "confidence"}]},
            {"text": "Credentials outrank signal quality in both directions, so authority is withdrawn anyway. The load is delivered; the convoy stands down at the resupply point under operator control.",
             "tone": "bad", "claims": []},
        ]},
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
    guide=(
        # Operator-facing rewrite of `beats`; figures are the exact values on
        # out/recon.jsonl at the epoch each entry fires (design.md §14).
        {"epoch": 0, "lines": [
            {"text": "The scout is leaving the patrol base to file spot reports: a grid, a time, and what it saw. A report with the wrong grid is worse than no report at all.",
             "tone": "calm", "claims": []},
            {"text": "Full sky, confidence 0.91. An attacker who wanted to move me without breaking the ranges has 14 m to work with. That is a bound my geometry gives me, not a guess.",
             "tone": "calm",
             "claims": [{"text": "0.91", "value": 0.9079, "path": "confidence"},
                        {"text": "14 m", "value": 13.9, "path": "geometry.displacement_bound_m"}]},
        ]},
        {"epoch": 60, "lines": [
            {"text": "A repeater has switched on. It re-broadcasts every GPS signal from its own antenna 8 dB louder, so all twelve got louder in the same instant. Real satellites drift apart.",
             "tone": "bad",
             "claims": [{"text": "8 dB", "value": 8.0,
                         "source": "injector power, docs/stream_provenance_recon.md (repeater_offset, power 8.0 dB, 12 SV)"}]},
            {"text": "Now the constellations disagree: GPS says one place, Galileo and BeiDou another. So I distrust GPS whole rather than argue satellite by satellite. All 12 are out.",
             "tone": "bad",
             "claims": [{"text": "12", "value": 12, "path": "geometry.excluded_sv"}]},
            {"text": "Losing a constellation costs geometry: my information ratio is 0.80 of the full sky. Three of my four signal checks are saturated, authority goes to the operator, and the scout halts.",
             "tone": "bad",
             "claims": [{"text": "0.80", "value": 0.7988, "path": "geometry.information_ratio"}]},
        ]},
        {"epoch": 61, "lines": [
            {"text": "This is where a stopped vehicle has the advantage. The scout is stationary, so I hold the last trusted fix as an anchor, taken at epoch 59 before the repeater, and carry it on odometry.",
             "tone": "act",
             "claims": [{"text": "59", "value": 59,
                         "path": "geometry.correction.stationary.anchor_epoch"}]},
            {"text": "The satellites now place me 118 m from that anchor while the wheels say I have not moved. A stationary vehicle cannot drift 118 m. That offset is the spoof, measured rather than assumed.",
             "tone": "act",
             "claims": [{"text": "118 m", "value": 118.2,
                         "path": "geometry.correction.stationary.deduced_offset.mag_m"}]},
        ]},
        {"epoch": 70, "lines": [
            {"text": "The offset points back down its own path. Bearing 85 degrees from the scout, and the jump in the GPS clock channel puts the emitter about 260 m out: a path delay, not a position fix.",
             "tone": "act",
             "claims": [{"text": "85 degrees", "value": 84.7,
                         "path": "geometry.correction.stationary.emitter_bearing_deg"},
                        {"text": "260 m", "value": 259.6,
                         "path": "geometry.correction.stationary.path_delay_m"}]},
            {"text": "That bearing goes into the report: the attack has become reconnaissance. The Galileo-only fix agrees with the anchor to 0.5 m, so two independent methods are saying the same thing.",
             "tone": "act",
             "claims": [{"text": "0.5 m", "value": 0.46,
                         "path": "geometry.correction.stationary.agreement_m"}]},
        ]},
        {"epoch": 84, "lines": [
            {"text": "Authority comes back one step, on Galileo and BeiDou. GPS stays dark until its clock channels agree with the others again. I do not readmit a constellation for going quiet.",
             "tone": "good", "claims": []},
            {"text": "The scout resumes toward the observation point on the corrected fix: 9.1 m of protection level, against the 100 m limit a grid-square report is held to.",
             "tone": "good",
             "claims": [{"text": "9.1 m", "value": 9.05, "path": "geometry.correction.protection_level_m"},
                        {"text": "100 m", "value": 100.0, "path": "geometry.correction.alert_limit_m"}]},
        ]},
        {"epoch": 90, "lines": [
            {"text": "The repeater is off. The GPS clock channels agree again, every GPS satellite is readmitted, and the information ratio is back to 1.00. Nothing was permanently excluded.",
             "tone": "good",
             "claims": [{"text": "1.00", "value": 1.0, "path": "geometry.information_ratio"}]},
            {"text": "Exclusion here is a judgement about this epoch, not a blacklist. When the evidence comes back, so does the satellite.",
             "tone": "good", "claims": []},
        ]},
        {"epoch": 100, "lines": [
            {"text": "The observation report is filed with its trust tag attached: which constellations stood behind the fix, a protection level of 3.8 m, and a note that the clock was free-running.",
             "tone": "calm",
             "claims": [{"text": "3.8 m", "value": 3.79, "path": "geometry.correction.protection_level_m"}]},
            {"text": "That tag is the product. An operator reading the report tomorrow can tell exactly how much of it to believe.",
             "tone": "calm", "claims": []},
        ]},
        {"epoch": 104, "lines": [
            {"text": "Full sky, full authority, confidence 0.73 and rising. Nobody drove the scout and nothing was shut down: it lost a constellation, kept patrolling, and is still on its loop.",
             "tone": "good",
             "claims": [{"text": "0.73", "value": 0.7274, "path": "confidence"}]},
        ]},
        {"epoch": 390, "lines": [
            {"text": "A fresh authorisation key was disclosed on schedule and verified, so the mission credential holds for the next window. Signal quality and credentials are two separate votes.",
             "tone": "calm", "claims": []},
        ]},
        {"epoch": 461, "lines": [
            {"text": "Loop complete, back at the patrol base. Every report was delivered, and each one carries the trust it was filed under, including those filed while GPS was lying.",
             "tone": "good", "claims": []},
            {"text": "The scout also brought back something it was not sent for: a bearing to the transmitter.",
             "tone": "good", "claims": []},
        ]},
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
    guide=(
        # Operator-facing rewrite of `beats`; figures are the exact values on
        # out/casevac.jsonl at the epoch each entry fires (design.md §14).
        {"epoch": 0, "lines": [
            {"text": "A casualty is waiting at a collection point 420 m out. The vehicle drives there, loads a litter and comes back. The failure that matters here is stopping at the wrong place.",
             "tone": "calm",
             "claims": [{"text": "420 m", "value": 420.0,
                         "source": "outbound leg to the CCP, docs/stream_provenance_casevac.md (s <= 420.0 m)"}]},
            {"text": "This mission carries one extra check: a simulated ground-class sensor at the wheels, read against a map signed before departure. Right now sensor and map both read building.",
             "tone": "calm", "claims": []},
        ]},
        {"epoch": 60, "lines": [
            {"text": "The carry-off starts here, and it is the careful kind: code and carrier are walked together, so the measurement that usually catches a spoof sees nothing at all.",
             "tone": "alert", "claims": []},
            {"text": "The believed position begins running ahead along the route at 1 m/s. Along-track is the dangerous direction: the vehicle never seems to leave the road, it just seems further down it.",
             "tone": "alert",
             "claims": [{"text": "1 m/s", "value": 1.0,
                         "source": "injector walk rate, docs/stream_provenance_casevac.md (carry_off, 1.0 m/s, bearing 135)"}]},
        ]},
        {"epoch": 62, "lines": [
            {"text": "The ranges stop fitting one position, and the ground disagrees too: the sensor reads building while the map says the reported position is on paved. Match likelihood 0.03.",
             "tone": "bad",
             "claims": [{"text": "0.03", "value": 0.025, "path": "terrain.match_likelihood"}]},
            {"text": "Authority goes to the operator and the vehicle holds. But the terrain sensor does more than object: every class boundary it crosses says how far along the route the vehicle really is.",
             "tone": "act", "claims": []},
            {"text": "First pin: 16.7 m behind where the satellites put us. That correction came off the ground, not through the antenna.",
             "tone": "act",
             "claims": [{"text": "16.7 m", "value": -16.74, "path": "terrain.route_fix.correction_m"}]},
        ]},
        {"epoch": 84, "lines": [
            {"text": "The believed position has just entered the collection-point ring. An unprotected vehicle would report arrival now, with the casualty still hundreds of metres away.",
             "tone": "bad", "claims": []},
            {"text": "It does not. The terrain fix still has the vehicle back at the last pin, no satellite is trusted, the information ratio is 0.00, and arrival is not confirmed.",
             "tone": "bad",
             "claims": [{"text": "0.00", "value": 0.0, "path": "geometry.information_ratio"}]},
        ]},
        {"epoch": 120, "lines": [
            {"text": "The spoofer stops. With no trusted satellites left, the bound on how far wrong the fix could be no longer comes from the sky at all: it comes from the terrain pin, at 267 m.",
             "tone": "alert",
             "claims": [{"text": "267 m", "value": 266.7, "path": "geometry.displacement_bound_m"}]},
            {"text": "I always quote the tighter of the two bounds and say which one it was. A number without its source is not evidence.",
             "tone": "alert", "claims": []},
        ]},
        {"epoch": 150, "lines": [
            {"text": "Ranges agree again and the full sky is back: information ratio 1.00, protection level 3.2 m. Authority returns one step and the vehicle resumes toward the collection point.",
             "tone": "good",
             "claims": [{"text": "1.00", "value": 1.0, "path": "geometry.information_ratio"},
                        {"text": "3.2 m", "value": 3.2, "path": "geometry.correction.protection_level_m"}]},
            {"text": "Between boundaries the fix rides on odometry, which drifts. Each boundary crossing sets it straight again.",
             "tone": "good", "claims": []},
        ]},
        {"epoch": 158, "lines": [
            {"text": "A boundary crossed: the sensor changes class exactly where the map says it should. The pin re-zeroes the odometry with a 2.9 m correction.",
             "tone": "good",
             "claims": [{"text": "2.9 m", "value": -2.88, "path": "terrain.route_fix.correction_m"}]},
        ]},
        {"epoch": 170, "lines": [
            {"text": "Full authority, confidence 0.87, and the terrain channel quiet again: sensor and map both reading building under the wheels.",
             "tone": "good",
             "claims": [{"text": "0.87", "value": 0.8681, "path": "confidence"}]},
        ]},
        {"epoch": 264, "lines": [
            {"text": "At the collection point, and this time it is confirmed. The fix sits inside the 25 m ring and the last pin is recent, so arrival rests on evidence that never came through the antenna.",
             "tone": "good",
             "claims": [{"text": "25 m", "value": 25.0,
                         "source": "arrival ring, docs/stream_provenance_casevac.md (at_ccp: fix inside the 25.0 m ring and fresh)"}]},
            {"text": "The believed pin claimed this long ago and was wrong. The latest pin needed 0.5 m of correction: the ground and the map have agreed the whole way in.",
             "tone": "good",
             "claims": [{"text": "0.5 m", "value": -0.5, "path": "terrain.route_fix.correction_m"}]},
        ]},
        {"epoch": 290, "lines": [
            {"text": "Casualty aboard, return leg running, pins continuing. The last boundary needed no correction at all: the vehicle knows where it is on the route to within a couple of metres.",
             "tone": "calm", "claims": []},
        ]},
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
    guide=(
        # Operator-facing rewrite of `beats`; figures are the exact values on
        # out/combat.jsonl at the epoch each entry fires (design.md §14).
        {"epoch": 0, "lines": [
            {"text": "The advance starts from the line of departure. This vehicle's reported position does two jobs: it reports phase-line crossings, and it is the origin of every target grid it sends.",
             "tone": "calm", "claims": []},
            {"text": "Fire-control input reads TRUSTED. Confidence 0.91, full sky, nothing excluded, and an attacker would have 14 m of room before the ranges stopped agreeing.",
             "tone": "calm",
             "claims": [{"text": "0.91", "value": 0.9079, "path": "confidence"},
                        {"text": "14 m", "value": 13.9, "path": "geometry.displacement_bound_m"}]},
        ]},
        {"epoch": 60, "lines": [
            {"text": "A crude spoofer, 15 dB above the real signals, throws every GPS range at once. Loud and instant: three of my four signal checks saturate in the same epoch.",
             "tone": "bad",
             "claims": [{"text": "15 dB", "value": 15.0,
                         "source": "injector power, docs/stream_provenance_combat.md (simplistic_position, power 15.0 dB, 12 SV)"}]},
            {"text": "The joint solution jumps east, past the phase line. GPS is the constellation disagreeing with the other two, so all 12 of its satellites go out together.",
             "tone": "bad",
             "claims": [{"text": "12", "value": 12, "path": "geometry.excluded_sv"}]},
            {"text": "Information ratio falls to 0.80 and confidence to 0.49. Authority goes to the operator: against an attack this blunt there is nothing left for the vehicle to navigate itself on.",
             "tone": "bad",
             "claims": [{"text": "0.80", "value": 0.7988, "path": "geometry.information_ratio"},
                        {"text": "0.49", "value": 0.4913, "path": "confidence"}]},
        ]},
        {"epoch": 61, "lines": [
            {"text": "The operator is not driving blind. Galileo and BeiDou were never touched, and the fix built on them alone carries 7.4 m of protection level against the 15 m limit.",
             "tone": "act",
             "claims": [{"text": "7.4 m", "value": 7.41, "path": "geometry.correction.protection_level_m"},
                        {"text": "15 m", "value": 15.0, "path": "geometry.correction.alert_limit_m"}]},
            {"text": "So fire-control input reads CONDITIONAL: not trusted, not withheld. A grid can still be sent, and it goes with its uncertainty attached.",
             "tone": "act", "claims": []},
        ]},
        {"epoch": 80, "lines": [
            {"text": "The spoofer is off and every satellite is back in the trusted set. My own baselines still carry the attacked level, though, so three of the four checks still read high.",
             "tone": "alert", "claims": []},
            {"text": "Confidence is 0.57. The sky is clean and my memory of it is not, so authority stays where it is. I would rather be late than early.",
             "tone": "alert",
             "claims": [{"text": "0.57", "value": 0.5721, "path": "confidence"}]},
        ]},
        {"epoch": 120, "lines": [
            {"text": "Baselines have caught up and authority has climbed all the way back: confidence 0.87, full geometry, ten sustained clean epochs paid for every step up.",
             "tone": "good",
             "claims": [{"text": "0.87", "value": 0.8685, "path": "confidence"}]},
            {"text": "The operator hands back and the vehicle rejoins the advance under its own authority. It never stopped moving; it stopped moving on its own judgement.",
             "tone": "good", "claims": []},
        ]},
        {"epoch": 130, "lines": [
            {"text": "Fire-control input reads TRUSTED again, confidence 0.82, and the advance continues toward the objective.",
             "tone": "good",
             "claims": [{"text": "0.82", "value": 0.821, "path": "confidence"}]},
        ]},
        {"epoch": 344, "lines": [
            {"text": "Objective reached. While GPS was lying, no target grid left this vehicle on a spoofed position: the grids came off Galileo and BeiDou, and they went out marked conditional.",
             "tone": "good", "claims": []},
        ]},
        {"epoch": 390, "lines": [
            {"text": "The authorisation key for the next window was disclosed on schedule and verified, so the credential holds. Had it not arrived, authority would have gone under a clean sky.",
             "tone": "calm", "claims": []},
        ]},
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
        "guide": copy.deepcopy(list(m.guide)),
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
