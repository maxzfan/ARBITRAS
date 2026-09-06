"""CASEVAC's automatic spoof correction: a route-constrained terrain-referenced
fix (tracks/TRACK_F.md Revision 1; TRACK_E.md option C in one dimension).

    register_augment("casevac", augment)          # backend/missions.py post-pass
    python -m backend.missions_casevac            # provenance section from out/casevac.jsonl

The known route is a schedule of land-cover class transitions. Walking the
route at 1 m of arc length over the signed OSM pre-map gives the class under
the vehicle at every s and the list of transitions (s_k, a, b). A wheel sensor
that reports a SUSTAINED change of class a -> b has, with no help from the
antenna, crossed one of the schedule's a -> b boundaries; the nearest one to
where the vehicle thinks it is pins the arc length. Between pins the pin is
carried forward by odometry. GNSS enters only through s_believed, the
believed position projected onto the route, so the correction s_pinned -
s_believed is the along-track error of the spoofed fix, measured by the
ground, not by the signal. TERCOM / SITAN with classes instead of elevation.

Frame. Everything is in the console's presentation frame (console/mission.py):
TRUE = route_point(s_frame), BELIEVED = TRUE + D, D = ENU(position) - ENU(_truth).
s_frame advances speed x gain[state] per epoch exactly as index.html does (the
state from console.replay.arbitrate over the same records), so the fix drawn
by the overlay sits where the console's vehicle is. Odometry is therefore
EXACT in this frame; in reality it drifts with distance and each pin re-zeroes
it -- the block says so.

Sensor. SIMULATED. The Track E channel's sensor sits at the antenna (it never
moves; channel.py), which in this frame is the aid station, so it cannot see
a transition. The route fix re-simulates the same confusion sensor (the
channel's M and seed) at the frame's true point; every block carries
sensor_source "simulated". On cells the map leaves UNKNOWN the simulated
ground is held at the last labelled class -- the same absorption rule
rastermap.py applies to the extent -- so the schedule and the sensor are
consistent by construction; a real schedule comes from a route survey.

Numbers chosen here and how (all reported in the block's `params`):
  N   consecutive identical readings to declare a transition: smallest N with
      zero false transitions on the clean lead-in AND an analytic expectation
      (k-1)((1-d)/(k-1))^N x epochs of fewer than 0.1 false pins per replay.
  W0  default search window when the map's consistent extent is None: half
      the minimum spacing between two schedule transitions of the same
      (a, b) pair -- the largest window under which "nearest" is unambiguous.
  K   pin freshness for the arrival gate: the longest gap between consecutive
      scheduled transitions before the CCP, in epochs at route speed, plus N.
"""
from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path

import numpy as np

from backend.terrain.rastermap import UNKNOWN, RasterMap, m_per_deg
from backend.terrain.sensor import ConfusionSensor, confusion_from_diag

SECTION_MARK = "## Route-constrained terrain fix (terrain.route_fix)"


# --------------------------------------------------------------------------- route maths

def seg_lengths(route):
    return [math.hypot(e1 - e0, n1 - n0) for (e0, n0), (e1, n1) in zip(route, route[1:])]


def route_point(route, s: float) -> tuple[float, float]:
    """(e, n) at arc length s, clamped to the polyline (console/missions route_point)."""
    segs = seg_lengths(route)
    s = min(max(s, 0.0), sum(segs))
    acc = 0.0
    for i, ((e0, n0), (e1, n1), seg) in enumerate(zip(route, route[1:], segs)):
        if s <= acc + seg or i == len(segs) - 1:
            t = 0.0 if seg == 0 else min(max((s - acc) / seg, 0.0), 1.0)
            return (e0 + t * (e1 - e0), n0 + t * (n1 - n0))
        acc += seg
    return route[-1]


def project_onto_route(route, e: float, n: float) -> tuple[float, float]:
    """(s, off_m): arc length of the nearest polyline point and the distance to it.
    Ties go to the smaller s (a closed loop's start beats its end)."""
    best = None
    acc = 0.0
    for (e0, n0), (e1, n1) in zip(route, route[1:]):
        de, dn = e1 - e0, n1 - n0
        L2 = de * de + dn * dn
        t = 0.0 if L2 == 0 else min(max(((e - e0) * de + (n - n0) * dn) / L2, 0.0), 1.0)
        pe, pn = e0 + t * de, n0 + t * dn
        d = math.hypot(e - pe, n - pn)
        if best is None or d < best[1] - 1e-9:
            best = (acc + t * math.sqrt(L2), d)
        acc += math.sqrt(L2)
    return best


def enu_to_lla(rmap: RasterMap, e: float, n: float) -> dict:
    """Inverse of RasterMap.enu_from_lla (same closed forms)."""
    m_lat, m_lon = m_per_deg(rmap.origin_lla[0])
    return {"lat": rmap.origin_lla[0] + n / m_lat, "lon": rmap.origin_lla[1] + e / m_lon}


# --------------------------------------------------------------------------- the schedule

def route_schedule(rmap: RasterMap, route, step_m: float = 1.0) -> dict:
    """Class along the route at `step_m` of arc length and its transitions.

    `raw` is the map's own label (UNKNOWN where OSM says nothing); `held` is
    the class the simulated ground takes there (last labelled class, the
    extent's absorption rule). Transitions are between held classes, so each
    is the arc length at which a NEW labelled class begins."""
    total = sum(seg_lengths(route))
    ss = np.arange(0.0, total + step_m / 2, step_m)
    raw, held, last = [], [], None
    for s in ss:
        c = rmap.class_at(*route_point(route, float(s)))
        c = UNKNOWN if c is None else int(c)
        raw.append(c)
        if c != UNKNOWN:
            last = c
        held.append(UNKNOWN if last is None else last)
    transitions = []
    for i in range(1, len(held)):
        a, b = held[i - 1], held[i]
        if a != b and a != UNKNOWN and b != UNKNOWN:
            transitions.append((float(ss[i]), int(a), int(b)))
    return {"s": ss, "raw": raw, "held": held, "transitions": transitions,
            "length_m": float(total), "step_m": float(step_m),
            "unknown_fraction": float(np.mean([c == UNKNOWN for c in raw]))}


def held_class_at(sched: dict, s: float | None) -> int | None:
    if s is None:
        return None
    i = int(round(s / sched["step_m"]))
    i = min(max(i, 0), len(sched["held"]) - 1)
    c = sched["held"][i]
    return None if c == UNKNOWN else int(c)


def default_window(transitions) -> float | None:
    """Half the minimum spacing between two transitions of the same (a, b) pair."""
    gaps = []
    by_pair: dict[tuple, list] = {}
    for s, a, b in transitions:
        by_pair.setdefault((a, b), []).append(s)
    for ss in by_pair.values():
        ss = sorted(ss)
        gaps += [s1 - s0 for s0, s1 in zip(ss, ss[1:])]
    return None if not gaps else min(gaps) / 2.0


def freshness_epochs(transitions, s_ccp: float, speed: float, n_consec: int) -> int:
    ss = [0.0] + [s for s, _, _ in transitions if s <= s_ccp]
    gap = max((s1 - s0 for s0, s1 in zip(ss, ss[1:])), default=s_ccp)
    return int(math.ceil(gap / speed)) + n_consec


def choose_n(true_classes, sensor: ConfusionSensor, epochs_total: int,
             n_max: int = 6, budget: float = 0.1) -> dict:
    """N from the clean lead-in's false-transition rate (see module docstring)."""
    sensor.reset()
    reads = []
    for c in true_classes:
        sensor.read(int(c))
        reads.append(int(sensor.last_reading))
    sensor.reset()
    k = len(sensor.classes)
    d = float(np.mean(np.diag(sensor.M)))
    off = (1.0 - d) / (k - 1) if k > 1 else 0.0
    table = {}
    chosen = None
    for n in range(1, n_max + 1):
        false = 0
        for i in range(n - 1, len(reads)):
            run = reads[i - n + 1:i + 1]
            if len(set(run)) == 1 and all(r != t for r, t in zip(run, true_classes[i - n + 1:i + 1])):
                if i == n - 1 or reads[i - n] != run[0]:      # count each run once, at its start
                    false += 1
        rate = (k - 1) * off ** n
        table[n] = {"false_transitions_lead_in": false,
                    "analytic_rate_per_epoch": rate,
                    "analytic_expected_per_replay": rate * epochs_total}
        if chosen is None and false == 0 and rate * epochs_total < budget:
            chosen = n
    if chosen is None:
        chosen = n_max
    return {"N": chosen, "table": table, "lead_in_epochs": len(reads), "budget": budget}


# --------------------------------------------------------------------------- the fix

class RouteFix:
    def __init__(self, rmap: RasterMap, route, sensor: ConfusionSensor, speed_m_per_epoch: float,
                 gain: dict, ccp_enu: tuple[float, float], ccp_radius_m: float,
                 n_consec: int | None = None, freshness_k: int | None = None,
                 window_default_m: float | None = None, lead_in_epochs: int = 60,
                 epochs_total: int = 300):
        if sensor.classes != rmap.classes:
            raise ValueError("sensor and map must share one class list")
        self.rmap, self.route, self.sensor = rmap, tuple(route), sensor
        self.speed, self.gain = float(speed_m_per_epoch), dict(gain)
        self.ccp, self.ccp_r = tuple(ccp_enu), float(ccp_radius_m)
        self.sched = route_schedule(rmap, self.route)
        self.s_ccp = project_onto_route(self.route, *self.ccp)[0]
        self.lead_in_epochs = lead_in_epochs
        # N: clean lead-in at route speed, all NOMINAL (no attack, so no halt).
        lead_true = [held_class_at(self.sched, self.speed * j) for j in range(lead_in_epochs)]
        lead_true = [UNKNOWN if c is None else c for c in lead_true]
        self.n_choice = choose_n(lead_true, sensor, epochs_total) if n_consec is None else None
        self.N = int(n_consec if n_consec is not None else self.n_choice["N"])
        self.W0 = default_window(self.sched["transitions"]) if window_default_m is None else float(window_default_m)
        if self.W0 is None:                        # no pair repeats: any candidate is unambiguous
            self.W0 = self.sched["length_m"]
        self.K = int(freshness_k if freshness_k is not None
                     else freshness_epochs(self.sched["transitions"], self.s_ccp, self.speed, self.N))

    # ------------------------------------------------------------ describe
    def names(self, c):
        return None if c is None or c == UNKNOWN else self.rmap.classes[int(c)]

    def transitions_named(self, s_max: float | None = None):
        return [{"s_m": s, "from": self.names(a), "to": self.names(b)}
                for s, a, b in self.sched["transitions"] if s_max is None or s <= s_max]

    def params(self) -> dict:
        return {"N_consecutive": self.N, "K_fresh_epochs": self.K,
                "window_default_m": round(self.W0, 1),
                "speed_m_per_epoch": self.speed, "gain": self.gain,
                "ccp_enu": list(self.ccp), "ccp_radius_m": self.ccp_r, "s_ccp_m": round(self.s_ccp, 1),
                "route_length_m": round(self.sched["length_m"], 1), "cell_m": self.rmap.cell_m,
                "lead_in_epochs": self.lead_in_epochs,
                "schedule_step_m": self.sched["step_m"],
                "transitions_total": len(self.sched["transitions"]),
                "transitions_outbound": len([t for t in self.sched["transitions"] if t[0] <= self.s_ccp]),
                "unknown_fraction_on_route": round(self.sched["unknown_fraction"], 3),
                "unknown_rule": "held at the last labelled class (rastermap extent absorption rule)",
                "odometry": "exact in the presentation frame (speed x gain[state]); drifts in reality, each pin re-zeroes it",
                "sensor": {**self.sensor.describe(), "simulated_at": "route_point(s_frame), the frame's TRUE point"}}

    # ------------------------------------------------------------ run
    def run(self, records: list, states: list[str], onset_index: int | None = None) -> dict:
        """Adds terrain.route_fix to every record; returns the run summary."""
        assert len(states) == len(records)
        self.sensor.reset()
        s_frame = 0.0
        s_hist = []                                           # s_hist[j] = s_frame at epoch j
        run_class, run_len, sustained = None, 0, None
        run_start, last_sustained_epoch = None, None          # crossing-epoch estimate (see pin)
        pinned, s_pinned, pinned_at, last_tr = False, None, None, None
        pins, unmatched, first_at_ccp, first_believed_in_ring = [], [], None, None
        summary_err = []
        R = self.route
        for j, (r, state) in enumerate(zip(records, states)):
            if j > 0:
                s_frame += self.speed * float(self.gain.get(state, 1.0))
            s_hist.append(s_frame)
            true_pt = route_point(R, s_frame)
            true_c = held_class_at(self.sched, s_frame)
            reading = None
            if true_c is not None:                       # off-map or never-labelled: no reading
                self.sensor.read(true_c)
                reading = int(self.sensor.last_reading)

            # sustained-class tracker
            declared = None
            if reading is not None:
                if reading == run_class:
                    run_len += 1
                else:
                    run_class, run_len, run_start = reading, 1, j
                if reading == sustained and run_len >= self.N:
                    last_sustained_epoch = j          # an isolated glitch reading the old class does not count
                if run_len >= self.N and run_class != sustained:
                    # The boundary was crossed after the last SUSTAINED reading of
                    # the old class and no later than the run's first reading;
                    # glitches in between leave that interval open, so take its
                    # midpoint and carry the half-width as the pin's uncertainty.
                    lo = (last_sustained_epoch + 1) if last_sustained_epoch is not None else run_start
                    declared = (sustained, run_class, (lo + run_start) / 2.0, (run_start - lo) / 2.0)
                    sustained = run_class
                    last_sustained_epoch = j

            # believed arc length in the frame
            pos, tru = r.get("position"), r.get("_truth")
            s_bel = bel_pt = off = None
            if pos and tru:
                be, bn = self.rmap.enu_from_lla(pos["lat"], pos["lon"])
                te, tn = self.rmap.enu_from_lla(tru["lat"], tru["lon"])
                bel_pt = (true_pt[0] + be - te, true_pt[1] + bn - tn)
                s_bel, off = project_onto_route(R, *bel_pt)
                if first_believed_in_ring is None and math.dist(bel_pt, self.ccp) <= self.ccp_r:
                    first_believed_in_ring = j

            # odometry carries the pin forward
            if pinned:
                s_pinned += s_hist[-1] - s_hist[-2]

            # a sustained a -> b: pin to the schedule's nearest a -> b within the window
            pin_now = None
            window, wsrc = self.W0, "default"
            if bel_pt is not None:
                ext = self.rmap.consistent_extent_m(*bel_pt)
                if ext is not None:
                    window, wsrc = float(ext), "consistent_extent_m"
            if declared is not None:
                a, b, t_cross, half = declared
                s_ref, ref_name = (s_pinned, "s_pinned") if pinned else (s_bel, "s_believed")
                if a is not None:
                    cands = [s for s, ca, cb in self.sched["transitions"] if ca == a and cb == b]
                elif s_ref is not None and held_class_at(self.sched, s_ref) == b:
                    cands = []          # first sustained class agrees with the map here: not a transition
                    declared = None
                else:   # first sustained class disagrees with the map: a unique transition INTO b
                    cands = [s for s, ca, cb in self.sched["transitions"] if cb == b]
                if s_ref is not None and cands:
                    near = [s for s in cands if abs(s - s_ref) <= window]
                    best = min(near, key=lambda s: abs(s - s_ref)) if near else None
                    if best is not None and (a is not None or len(near) == 1):
                        # odometry since the estimated crossing epoch (s_frame interpolated)
                        i0 = int(math.floor(t_cross))
                        f = t_cross - i0
                        s_cross = s_hist[i0] + f * (s_hist[min(i0 + 1, len(s_hist) - 1)] - s_hist[i0])
                        s_pinned = best + (s_hist[-1] - s_cross)
                        pinned, pinned_at = True, j
                        unc = half * self.speed + self.speed / 2.0 + self.sched["step_m"] / 2.0
                        last_tr = {"from": self.names(a), "to": self.names(b), "s_m": round(best, 1),
                                   "epoch": j, "crossing_epoch": t_cross, "uncertainty_m": round(unc, 2)}
                        pin_now = {"epoch": j, "s_m": best, "from": self.names(a), "to": self.names(b),
                                   "s_believed_m": s_bel, "s_pinned_m": s_pinned,
                                   "correction_m": None if s_bel is None else s_pinned - s_bel,
                                   "window_m": window, "window_source": wsrc, "state": state,
                                   "ref": ref_name, "uncertainty_m": unc}
                        pins.append(pin_now)
                if pin_now is None and declared is not None:
                    unmatched.append({"epoch": j, "from": self.names(a), "to": self.names(b),
                                      "s_ref_m": s_ref, "window_m": window})

            fix_pt = route_point(R, s_pinned) if pinned else None
            err = None if fix_pt is None else math.dist(fix_pt, true_pt)
            fresh = pinned and (j - pinned_at) <= self.K
            ccp_range = None if fix_pt is None else math.dist(fix_pt, self.ccp)
            at_ccp = bool(fresh and ccp_range is not None and ccp_range <= self.ccp_r)
            if at_ccp and first_at_ccp is None:
                first_at_ccp = j
            if pinned and err is not None:
                summary_err.append(err)

            block = {
                "available": bool(pinned),
                "sensor_source": "simulated",
                "frame": "presentation (console/mission.py): TRUE = route_point(s_frame), BELIEVED = TRUE + D",
                "sensed_class": self.names(reading),
                "sustained_class": self.names(sustained),
                "map_class_at_believed": self.names(held_class_at(self.sched, s_bel)),
                "s_believed_m": None if s_bel is None else round(s_bel, 2),
                "believed_off_route_m": None if off is None else round(off, 2),
                "s_pinned_m": None if s_pinned is None else round(s_pinned, 2),
                "correction_m": None if (s_pinned is None or s_bel is None) else round(s_pinned - s_bel, 2),
                "position": None if fix_pt is None else {k: round(v, 9) for k, v in enu_to_lla(self.rmap, *fix_pt).items()},
                "position_enu": None if fix_pt is None else {"e": round(fix_pt[0], 2), "n": round(fix_pt[1], 2)},
                "pinned": bool(pinned),
                "pinned_at_epoch": pinned_at,
                "pin_fresh": bool(fresh),
                "transition": last_tr,
                "pin_this_epoch": pin_now is not None,
                "search_window_m": round(window, 1),
                "search_window_source": wsrc,
                "ccp_range_m": None if ccp_range is None else round(ccp_range, 2),
                "at_ccp": at_ccp,
                "_error_vs_truth_m": None if err is None else round(err, 2),
                "_s_frame_m": round(s_frame, 2),
                "params": self.params() if j == 0 else None,
            }
            t = r.get("terrain")
            if not isinstance(t, dict):
                t = {"available": False}
                r["terrain"] = t
            t["route_fix"] = block

        after = summary_err[1:] if len(summary_err) > 1 else summary_err
        onset = self.lead_in_epochs if onset_index is None else onset_index
        return {
            "params": self.params(),
            "n_choice": self.n_choice,
            "transitions_outbound": self.transitions_named(self.s_ccp),
            "transitions_total": len(self.sched["transitions"]),
            "pins": pins,
            "unmatched_transitions": unmatched,
            "false_pins_lead_in": [p for p in pins if p["epoch"] < onset
                                   and (p["correction_m"] is None or abs(p["correction_m"]) > self.rmap.cell_m * 2)],
            "pins_lead_in": [p["epoch"] for p in pins if p["epoch"] < onset],
            "error_after_first_pin": {"n": len(after),
                                      "median_m": None if not after else float(np.median(after)),
                                      "max_m": None if not after else float(np.max(after))},
            "first_at_ccp_epoch": first_at_ccp,
            "first_believed_in_ring_epoch": first_believed_in_ring,
            "s_frame_final_m": s_frame,
        }


# --------------------------------------------------------------------------- the augment

def _print_schedule(fix: RouteFix) -> None:
    out = fix.transitions_named(fix.s_ccp)
    print(f"  route_fix: {len(fix.sched['transitions'])} class transitions on the {fix.sched['length_m']:.0f} m route, "
          f"{len(out)} on the outbound leg (s <= {fix.s_ccp:.0f} m); unknown cells on route "
          f"{fix.sched['unknown_fraction']:.0%} (held at the last labelled class)")
    for t in out:
        print(f"    s {t['s_m']:6.0f} m  {t['from']} -> {t['to']}")
    if not out:
        print("  !!! ROUTE_FIX: THE OUTBOUND LEG CROSSES NO CLASS BOUNDARY -- the terrain fix can never pin.")
        mid = route_point(fix.route, fix.s_ccp / 2)
        nb = fix.rmap.nearest_boundary(*mid, c0=held_class_at(fix.sched, fix.s_ccp / 2))
        ccp_nb = fix.rmap.nearest_boundary(*fix.ccp, c0=held_class_at(fix.sched, fix.s_ccp))
        print(f"  !!! smallest route change (NOT applied): from the leg midpoint the nearest other class is "
              f"{nb} ; from the CCP {ccp_nb}. Bend the leg through that cell, or move the CCP onto it.")
    print(f"  route_fix: N={fix.N} consecutive readings"
          + (f" (lead-in false transitions by N: "
             + ", ".join(f"{n}:{v['false_transitions_lead_in']}/{v['analytic_expected_per_replay']:.3f} exp"
                         for n, v in fix.n_choice['table'].items()) + ")" if fix.n_choice else "")
          + f"; default window W0={fix.W0:.1f} m; freshness K={fix.K} epochs")


def augment(records: list, ctx: dict) -> list:
    """backend/missions.py post-pass for CASEVAC (registered below)."""
    from console.missions import CASEVAC
    from console.replay import arbitrate
    rmap, terrain, spec = ctx.get("rmap"), ctx.get("terrain"), ctx["spec"]
    if rmap is None or terrain is None:
        print("  route_fix: terrain channel off -> terrain.route_fix unavailable on every record")
        for r in records:
            t = r.get("terrain") if isinstance(r.get("terrain"), dict) else {"available": False}
            r["terrain"] = t
            t["route_fix"] = {"available": False, "sensor_source": "simulated", "reason": "terrain channel off"}
        return records
    ccp = next(p for p in CASEVAC.props if p["kind"] == "ccp")
    sensor = ConfusionSensor(terrain.sensor.M, rmap.classes, seed=terrain.sensor.seed,
                             footprint_m=terrain.sensor.footprint_m, sensor_id=terrain.sensor.sensor_id)
    fix = RouteFix(rmap, CASEVAC.route_enu, sensor, CASEVAC.speed_m_per_epoch, CASEVAC.gain,
                   (ccp["e"], ccp["n"]), ccp["radius_m"],
                   lead_in_epochs=spec.pre_epochs, epochs_total=len(records))
    _print_schedule(fix)
    states = [d.state.name for d in arbitrate(records)]
    summary = fix.run(records, states, onset_index=ctx.get("onset_index", spec.pre_epochs))
    print(f"  route_fix: {len(summary['pins'])} pins at epochs {[p['epoch'] for p in summary['pins']]}; "
          f"corrections {[None if p['correction_m'] is None else round(p['correction_m'], 1) for p in summary['pins']]} m; "
          f"error after first pin median {summary['error_after_first_pin']['median_m']} max {summary['error_after_first_pin']['max_m']}; "
          f"first at_ccp {summary['first_at_ccp_epoch']} vs believed-in-ring {summary['first_believed_in_ring_epoch']}; "
          f"unmatched {len(summary['unmatched_transitions'])}; false lead-in pins {len(summary['false_pins_lead_in'])}")
    return records


try:                                    # registered when the runner imports this module
    from backend.missions import register_augment
    register_augment("casevac", augment)
except Exception:                       # importable standalone (tests, provenance CLI)
    pass


# --------------------------------------------------------------------------- provenance

def summarise_stream(path: Path, onset: int = 60) -> dict:
    recs = [json.loads(l) for l in open(path) if l.strip()]
    rf = [((r.get("terrain") or {}).get("route_fix") or {}) for r in recs]
    params = rf[0].get("params") or {}
    pins = [(j, b) for j, b in enumerate(rf) if b.get("pin_this_epoch")]
    errs = [b["_error_vs_truth_m"] for b in rf if b.get("_error_vs_truth_m") is not None]
    first_pin = pins[0][0] if pins else None
    after = [b["_error_vs_truth_m"] for j, b in enumerate(rf)
             if first_pin is not None and j > first_pin and b.get("_error_vs_truth_m") is not None]
    corr = [b["correction_m"] for b in rf if b.get("correction_m") is not None]
    return {
        "epochs": len(recs), "params": params,
        "pins": [{"epoch": j, "from": b["transition"]["from"], "to": b["transition"]["to"],
                  "s_m": b["transition"]["s_m"], "s_believed_m": b["s_believed_m"],
                  "correction_m": b["correction_m"], "window_m": b["search_window_m"],
                  "window_source": b["search_window_source"], "err_m": b["_error_vs_truth_m"]} for j, b in pins],
        "lead_in_pins": [j for j, _ in pins if j < onset],
        "false_lead_in_pins": [j for j, b in pins if j < onset and b["correction_m"] is not None
                               and abs(b["correction_m"]) > 2 * params.get("cell_m", 5.0)],
        "err_after_first_pin": {"n": len(after), "median_m": None if not after else float(np.median(after)),
                                "max_m": None if not after else float(np.max(after))},
        "err_all": {"n": len(errs), "max_m": None if not errs else float(np.max(errs))},
        "correction_extreme_m": None if not corr else float(max(corr, key=abs)),
        "first_at_ccp": next((j for j, b in enumerate(rf) if b.get("at_ccp")), None),
        "at_ccp_epochs": sum(1 for b in rf if b.get("at_ccp")),
        "first_available": next((j for j, b in enumerate(rf) if b.get("available")), None),
        "first_believed_in_ring": next((j for j, r in enumerate(recs)
                                        if _believed_in_ring(r, params)), None),
        "attack_window": (onset, onset + 60),
    }


def _believed_in_ring(r: dict, params: dict) -> bool:
    b = (r.get("terrain") or {}).get("route_fix") or {}
    if b.get("s_believed_m") is None or not params:
        return False
    # believed-in-ring in the frame: the block carries s_believed and the off-route distance
    from console.missions import CASEVAC
    pos, tru = r.get("position"), r.get("_truth")
    if not pos or not tru:
        return False
    de = (pos["lon"] - tru["lon"]) * m_per_deg(tru["lat"])[1]
    dn = (pos["lat"] - tru["lat"]) * m_per_deg(tru["lat"])[0]
    te, tn = route_point(CASEVAC.route_enu, b["_s_frame_m"])
    ccp = params["ccp_enu"]
    return math.hypot(te + de - ccp[0], tn + dn - ccp[1]) <= params["ccp_radius_m"]


def provenance_section(s: dict) -> str:
    p = s["params"]
    sensor = p.get("sensor", {})
    rows = "\n".join(
        f"| {x['epoch']} | {x['from']} → {x['to']} | {x['s_m']:.0f} | {x['s_believed_m']:.1f} | "
        f"{x['correction_m']:+.1f} | {x['window_m']:.0f} ({x['window_source']}) | {x['err_m']:.1f} |"
        for x in s["pins"])
    e = s["err_after_first_pin"]
    return f"""{SECTION_MARK}

Added by `backend/missions_casevac.py` (post-pass registered on the runner;
this section written by `python -m backend.missions_casevac` from the stream on
disk). One-dimensional terrain-referenced navigation along the known route:
the signed OSM pre-map walked at {p.get('schedule_step_m', 1.0):g} m of arc length gives a schedule of
class transitions; a SUSTAINED change in the (SIMULATED) wheel sensor's class
pins arc length to the schedule's nearest matching transition; odometry carries
the pin between transitions. Frame: the console's presentation frame (TRUE =
route_point(s_frame), s_frame advancing speed × gain[state], the state from
`console.replay.arbitrate`; BELIEVED = TRUE + D). Every number below is
measured on `out/casevac.jsonl` as generated.

- **Sensor:** `{sensor.get('id')}`, source **{sensor.get('source')}**, confusion diagonal {sensor.get('confusion_diag', 0):.2f}, re-simulated at
  the frame's true point with the channel's seed (the channel's own sensor sits at the antenna, which
  in this frame is the aid station and never crosses a boundary). Unlabelled (UNKNOWN) cells on the
  route: {p.get('unknown_fraction_on_route', 0):.0%}, held at the last labelled class — the absorption rule `rastermap.py`
  applies to the extent; a real schedule needs a route survey.
- **Boundaries on the route:** {p.get('transitions_total')} labelled transitions on the {p.get('route_length_m')} m loop,
  **{p.get('transitions_outbound')} on the outbound leg** (s ≤ {p.get('s_ccp_m')} m, the CCP).
- **N = {p.get('N_consecutive')}** consecutive identical readings to declare a transition — smallest N with zero
  false transitions on the {p.get('lead_in_epochs')}-epoch clean lead-in and an analytic expectation (k−1)((1−d)/(k−1))^N × {s['epochs']}
  below 0.1 false pins per replay (N=2 would expect ≈{6*(0.15/6)**2*s['epochs']:.2f}; N=3 ≈{6*(0.15/6)**3*s['epochs']:.3f}).
- **Search window:** the map's `consistent_extent_m` at the believed point when it exists, else
  W0 = {p.get('window_default_m')} m (half the minimum spacing between two schedule transitions of the same class
  pair). Reference: the odometry-propagated pin once pinned, else s_believed.
- **K = {p.get('K_fresh_epochs')} epochs** pin freshness for the arrival gate (longest gap between scheduled transitions
  before the CCP at route speed, plus N). `at_ccp` = fix within the {p.get('ccp_radius_m')} m ring AND fresh.
- **Odometry** is exact in the presentation frame; in reality it drifts with distance and each pin re-zeroes it.
- **Halt:** the frame halts the vehicle in SURRENDERED (`gain`), so during epochs 62–149 the true point
  is fixed and the fix is carried by zero odometry while the believed pin runs to the CCP.

### Measured

| quantity | value |
|---|---|
| pins observed (epochs) | {[x['epoch'] for x in s['pins']]} |
| pins on the clean lead-in / false pins there (\\|correction\\| > 2 cells) | {len(s['lead_in_pins'])} / {len(s['false_lead_in_pins'])} |
| first epoch with a fix (`available`) | {s['first_available']} |
| `_error_vs_truth_m` after the first pin: median / max (n) | {e['median_m'] if e['median_m'] is None else round(e['median_m'], 2)} / {e['max_m'] if e['max_m'] is None else round(e['max_m'], 2)} m ({e['n']}) |
| largest \\|correction_m\\| on the stream | {s['correction_extreme_m'] if s['correction_extreme_m'] is None else round(s['correction_extreme_m'], 1)} m |
| believed pin first inside the CCP ring (frame) | epoch {s['first_believed_in_ring']} |
| `at_ccp` first true / epochs true | {s['first_at_ccp']} / {s['at_ccp_epochs']} |

| pin epoch | transition | s_k (m) | s_believed (m) | correction (m) | window (m) | error vs truth (m) |
|---|---|---|---|---|---|---|
{rows}

`_error_vs_truth_m`, `_s_frame_m` are replay metadata (underscored, out of contract).
"""


def write_provenance(doc: Path, stream: Path, onset: int = 60) -> None:
    s = summarise_stream(stream, onset)
    section = provenance_section(s)
    text = doc.read_text() if doc.exists() else ""
    if SECTION_MARK in text:
        text = re.split(re.escape(SECTION_MARK), text)[0].rstrip() + "\n\n" + section
    else:
        text = text.rstrip() + "\n\n" + section
    doc.write_text(text)
    print(f"wrote {doc} ({len(s['pins'])} pins; first at_ccp {s['first_at_ccp']}; "
          f"believed in ring {s['first_believed_in_ring']})")


def apply_to_stream(stream: Path, map_path="data/terrain_usn8.npz", pub="data/terrain_map_pub.pem") -> None:
    """Run the post-pass over a stream already on disk (same result as the
    runner's augment: a pure function of the records, the signed map and the
    sensor parameters the stream's own terrain block states)."""
    from backend.terrain.signing import load_verified
    from backend.missions import REGISTRY
    from backend.demo import write_atomic
    recs = [json.loads(l) for l in open(stream) if l.strip()]
    sd = (recs[0].get("terrain") or {}).get("sensor") or {}
    if not sd:
        raise SystemExit("stream has no terrain block: generate it with the channel on")
    rmap = load_verified(map_path, pub)
    sensor = ConfusionSensor(confusion_from_diag(len(rmap.classes), float(sd["confusion_diag"])),
                             rmap.classes, seed=20260820, footprint_m=float(sd.get("footprint_m", 0.0)),
                             sensor_id=sd.get("id", "sim-confusion-v0"))
    spec = REGISTRY["casevac"]
    recs = augment(recs, {"rmap": rmap, "terrain": type("T", (), {"sensor": sensor})(),
                          "spec": spec, "onset_index": spec.pre_epochs})
    write_atomic(recs, stream)
    print(f"wrote {stream} ({len(recs)} epochs)")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="route_fix: apply to a stream on disk / append the provenance section")
    ap.add_argument("--stream", default="out/casevac.jsonl")
    ap.add_argument("--doc", default="docs/stream_provenance_casevac.md")
    ap.add_argument("--onset", type=int, default=60)
    ap.add_argument("--apply", action="store_true", help="run the post-pass over --stream in place")
    a = ap.parse_args(argv)
    if a.apply:
        apply_to_stream(Path(a.stream))
    write_provenance(Path(a.doc), Path(a.stream), a.onset)


if __name__ == "__main__":
    main()
