"""RECON's automatic spoof correction: the stationary deduction.

tracks/TRACK_F.md (Revision, mechanism "stationary") and tracks/TRACK_F_RECON.md.
Registered with backend/missions.py as the RECON post-pass::

    register_augment("recon", stationary_deduction)

and runnable on a finished stream (the runner rewrites the provenance file on
every regeneration, so the measured section is appended by this module)::

    PYTHONPATH=. python -m backend.missions_recon              # measure, append the doc section
    PYTHONPATH=. python -m backend.missions_recon --apply      # also rewrite out/recon.jsonl with the block

Every record gets an ADDITIVE block ``geometry.correction.stationary``. Nothing
else in the record is touched; the §5 contract is unchanged.

## The deduction

A scout stops at an observation point by design. While it is stopped the
believed fix should not move; any motion of the fix is the spoof, and the
direction it moves is the direction of the emitter (the receiver is pulled
toward the repeater it is solving on). The scout therefore:

1. **Anchors** on the last fix taken while the arbitras still trusted the
   signal (the last NOMINAL epoch before a downgrade -- ``console.replay.arbitrate``
   is the reference arbitras, so the anchor epoch is exactly what the console
   shows) and dead-reckons from it with its own odometry. Time-to-alert is 0
   epochs on this stream, so the anchor fix is clean; a late alert would
   anchor on a spoofed fix, which is the stated risk of the method.
2. Reads the **deduced offset** believed - anchor. In the presentation frame
   the odometry is the route itself and is exact, so the deduced offset
   equals the measured displacement ``position - _truth`` up to the clean-fix
   jitter between the anchor epoch and now. In reality odometry drifts, and
   the anchor degrades with distance travelled -- which is exactly why
   stopping matters: while the scout is stationary the anchor does not drift
   at all, and any drag of the believed fix is pure spoof.
3. While stationary, measures the **drag** (believed-fix motion with the
   vehicle stopped), names the **emitter bearing** (the offset direction),
   and reads the **path delay** off the inter-constellation clock: the
   repeater adds its extra path to every GPS range alike, which the solver
   can only put in the GPS clock column, so the jump in dt_G - dt_E since the
   anchor epoch is the repeater's extra path in metres. That number is a
   path-delay estimate, not a position fix of the emitter.
4. Cross-checks the anchor against the Galileo re-solve
   (``geometry.correction.corrected_position``): ``agreement_m``. The two are
   independent -- one is RF-free odometry from a fix taken before the attack,
   the other is a trusted-subset solve of this epoch's ranges.
5. Stamps the spot report: state, constellations in the trusted set, PL, and
   "clock in holdover" when the arbitras's ``clock_discipline`` is false.

## Frames

The receiver at USN8 never moved. Positions in the stream are all near the
surveyed point; the console places any stream lat/lon X at
``TRUE(s) + (ENU(X) - ENU(_truth))``. The anchor is emitted in that same
frame: the fix at the anchor epoch, carried forward by odometry that is zero
in the stream frame (static receiver) and the route in the presentation
frame (the console adds it). Arc length ``s`` and the stationary windows are
computed with the console's own progress rule -- speed x the mission's gain
for the arbitrated state, so a SURRENDERED halt is a real stationary window
in the frame -- plus the observation-point dwell: the scout is treated as
stationary while inside an ``obs_point`` / ``observation_post`` prop radius
on its route. The console does not yet slow the vehicle at an OP, so that
dwell is scripted, and the block says which kind of stationary it is.

## Path delay: why metres come from the clock column, not the z-scores

``features.cross_constellation_detail`` carries the detector's compensated
channel z-scores, and ``CrossCal.scale`` is each channel's clean-day p99 of
|z| -- both dimensionless, so z x scale is a multiple of the clean tail, not
metres (the detector's trailing sigma is not emitted). The z-scores are used
here for what they are: attribution (which constellation is the odd one out,
how many times its clean tail each clock channel sits at). Metres come from
``_solution.clock_bias_m`` (the believed fix's per-constellation clock bias,
metres) differenced against the anchor epoch. When the detail block is
absent, attribution falls back to ``geometry.excluded_sv`` -- the
constellation-first rule already named the constellation.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from pathlib import Path
from statistics import median

from console.arbitras.states import TrustState
from console.mission import LAT, LON, M_PER_DEG_LAT, M_PER_DEG_LON
from console.missions import RECON, route_point
from console.replay import arbitrate

BLOCK = "stationary"
STATIONARY_PROPS = ("obs_point", "observation_post")
SOLVABLE = "GEC"                     # the constellations the cross-constellation rule can name
PATH_DELAY_SOURCE = ("_solution.clock_bias_m.G - _solution.clock_bias_m.E, "
                     "minus the same at the anchor epoch")
STREAM = Path("out/recon.jsonl")
DOC = Path("docs/stream_provenance_recon.md")
SECTION = "## Stationary deduction (geometry.correction.stationary)"


# --------------------------------------------------------------------------- frame helpers

def enu(lat: float, lon: float) -> tuple[float, float]:
    """ENU metres from the surveyed point -- the console's latLonToEnu."""
    return (lon - LON) * M_PER_DEG_LON, (lat - LAT) * M_PER_DEG_LAT


def bearing_deg(e: float, n: float) -> float:
    """Compass bearing of an ENU vector, degrees clockwise from north."""
    return (math.degrees(math.atan2(e, n)) + 360.0) % 360.0


def _latlon(obj) -> tuple[float, float] | None:
    if not isinstance(obj, dict) or obj.get("lat") is None or obj.get("lon") is None:
        return None
    return enu(float(obj["lat"]), float(obj["lon"]))


def progress(states: list[str], mission=RECON) -> list[float]:
    """Arc length per epoch, the console's rule: epoch 0 at s = 0, then
    speed x gain(state) each epoch (index.html, per-state progress gain)."""
    s, out = 0.0, []
    for j, st in enumerate(states):
        if j > 0:
            s += mission.speed_m_per_epoch * float(mission.gain.get(st, 1.0))
        out.append(s)
    return out


def dwell_prop(s: float, mission=RECON) -> dict | None:
    """The obs_point / observation_post whose radius the route point at s is inside."""
    e, n, _ = route_point(mission.route_enu, s)
    for p in mission.props:
        if p.get("kind") in STATIONARY_PROPS and \
                math.hypot(e - p["e"], n - p["n"]) <= float(p.get("radius_m", 0.0)):
            return p
    return None


def _clock_ge(rec: dict) -> float | None:
    clk = ((rec.get("_solution") or {}).get("clock_bias_m") or {})
    if clk.get("G") is None or clk.get("E") is None:
        return None
    return float(clk["G"]) - float(clk["E"])


def _attribution(rec: dict, scale: dict | None) -> dict | None:
    """Which constellation the disagreement is charged to, with its evidence."""
    detail = (rec.get("features") or {}).get("cross_constellation_detail") or {}
    clk = {k: v for k, v in detail.items() if k.startswith("clk_")}
    if clk and scale and all(k in scale and scale[k] for k in clk):
        x = {k: abs(float(v)) / float(scale[k]) for k, v in clk.items()}
        # Odd one out on the clock channels: both channels touching S hot, the
        # third quiet (backend/missions.py odd_constellation, same pattern).
        odd = None
        for sysc, touching, other in (("G", ("clk_GE_m", "clk_GC_m"), "clk_EC_m"),
                                      ("E", ("clk_GE_m", "clk_EC_m"), "clk_GC_m"),
                                      ("C", ("clk_GC_m", "clk_EC_m"), "clk_GE_m")):
            if all(t in x for t in touching) and other in x and \
                    all(x[t] >= 1.0 for t in touching) and x[other] < 0.5:
                odd = sysc
                break
        return {"constellation": odd,
                "channels": {k: {"z": round(float(detail[k]), 2), "x_scale": round(x[k], 1)} for k in clk},
                "source": "features.cross_constellation_detail (z) / CrossCal.scale (clean p99 of |z|)"}
    excluded = (rec.get("geometry") or {}).get("excluded_sv") or []
    if excluded:
        by = {}
        for sv in excluded:
            by[sv[0]] = by.get(sv[0], 0) + 1
        sysc, n = max(by.items(), key=lambda kv: kv[1])
        if n >= 4 and n == len(excluded):
            return {"constellation": sysc, "channels": None,
                    "source": f"geometry.excluded_sv ({n} {sysc} satellites, constellation-first rule)"}
    return None


def report_tag(state: str, rec: dict, clock_discipline: bool) -> str:
    geom = rec.get("geometry") or {}
    sky = geom.get("sky") or []
    tracked, trusted = set(), set()
    for sv in sky:
        sysc = str(sv.get("sv", ""))[:1]
        if sysc in SOLVABLE:
            tracked.add(sysc)
            if sv.get("trusted"):
                trusted.add(sysc)
    if not sky:                                   # older record: fall back to the corrector's weights
        for sv, w in (geom.get("correction") or {}).get("weights", {}).items():
            if sv[0] in SOLVABLE:
                tracked.add(sv[0])
                if w:
                    trusted.add(sv[0])
    order = [c for c in SOLVABLE if c in trusted]
    dropped = [c for c in SOLVABLE if c in tracked and c not in trusted]
    parts = [state, "on " + "+".join(order) if order else "no trusted constellation"]
    if dropped:
        parts.append("+".join(dropped) + " dropped")
    pl = (geom.get("correction") or {}).get("protection_level_m")
    parts.append(f"PL {float(pl):.1f} m" if pl is not None else "PL —")
    if not clock_discipline:
        parts.append("clock in holdover")
    return " · ".join(parts)


# --------------------------------------------------------------------------- the post-pass

def stationary_deduction(records: list, ctx: dict | None = None) -> list:
    """Add ``geometry.correction.stationary`` to every record (in place, returned)."""
    ctx = ctx or {}
    mission = ctx.get("mission") or RECON
    scale = None
    try:
        scale = dict(ctx["shared"].xc.cal.scale)
    except (KeyError, AttributeError, TypeError):
        pass

    decisions = arbitrate(records)
    states = [d.state.name for d in decisions]
    s_by_epoch = progress(states, mission)

    anchor = None            # {"epoch", "timestamp", "enu", "latlon", "clk_ge"}
    win_start = None         # first epoch of the current stationary window
    win_enu = None
    for j, (rec, d) in enumerate(zip(records, decisions)):
        if not isinstance(rec, dict):
            continue
        state = d.state.name
        pos_enu = _latlon(rec.get("position"))
        nominal = d.state is TrustState.NOMINAL
        if nominal and pos_enu is not None:
            anchor = {"epoch": j, "timestamp": rec.get("timestamp"), "enu": pos_enu,
                      "latlon": {"lat": rec["position"]["lat"], "lon": rec["position"]["lon"]},
                      "clk_ge": _clock_ge(rec)}

        s = s_by_epoch[j]
        gain = float(mission.gain.get(state, 1.0))
        prop = dwell_prop(s, mission)
        if gain == 0.0:
            stationary, reason = True, f"halted:{state}"
        elif prop is not None:
            stationary, reason = True, f"op_dwell:{prop.get('label')} (scripted dwell, presentation frame)"
        else:
            stationary, reason = False, None
        if stationary and (win_start is None):
            win_start, win_enu = j, pos_enu
        elif not stationary:
            win_start, win_enu = None, None

        block = {
            "anchored": (not nominal) and anchor is not None,
            "anchor": None if anchor is None else dict(anchor["latlon"]),
            "anchor_epoch": None if anchor is None else anchor["epoch"],
            "anchor_timestamp": None if anchor is None else anchor["timestamp"],
            "anchor_source": ("last NOMINAL fix (console.replay.arbitrate) + odometry; the route in "
                              "the presentation frame, zero in the stream frame (static receiver)"),
            "frame": {"s_m": round(s, 2), "stationary": stationary, "reason": reason,
                      "prop": None if prop is None else prop.get("label"),
                      "window_start_epoch": win_start},
            "deduced_offset": None,
            "agreement_m": None,
            "drag": None,
            "emitter_bearing_deg": None,
            "bearing_gate": None,
            "path_delay_m": None,
            "path_delay_source": PATH_DELAY_SOURCE,
            "emitter_standoff_estimate_m": None,
            "emitter_standoff_note": "path-delay estimate from the clock jump, not a position fix",
            "attribution": _attribution(rec, scale),
            "report_tag": report_tag(state, rec, bool(d.clock_discipline)),
        }
        geom = rec.setdefault("geometry", {}) or {}
        rec["geometry"] = geom
        corr = geom.setdefault("correction", {}) or {}
        geom["correction"] = corr

        if anchor is None or pos_enu is None:
            corr[BLOCK] = block
            continue

        de, dn = pos_enu[0] - anchor["enu"][0], pos_enu[1] - anchor["enu"][1]
        mag = math.hypot(de, dn)
        block["deduced_offset"] = {"e": round(de, 2), "n": round(dn, 2), "mag_m": round(mag, 2),
                                   "bearing_deg": round(bearing_deg(de, dn), 1) if mag > 0 else None,
                                   "note": "believed - anchor; equals position - _truth in the "
                                           "presentation frame (odometry exact) up to fix jitter"}
        c_enu = _latlon(corr.get("corrected_position"))
        if c_enu is not None:
            block["agreement_m"] = round(math.hypot(anchor["enu"][0] - c_enu[0],
                                                    anchor["enu"][1] - c_enu[1]), 2)

        if stationary and block["anchored"]:
            pl = corr.get("protection_level_m")
            if win_enu is not None and j > win_start:
                we, wn = pos_enu[0] - win_enu[0], pos_enu[1] - win_enu[1]
                n_ep = j - win_start
                wmag = math.hypot(we, wn)
                block["drag"] = {"rate_m_per_epoch": round(wmag / n_ep, 3),
                                 "bearing_deg": round(bearing_deg(we, wn), 1) if wmag > 0 else None,
                                 "epochs": n_ep, "since_epoch": win_start,
                                 "note": "believed-fix motion with the vehicle stopped; no odometry involved"}
            if pl is not None and mag <= float(pl):
                block["bearing_gate"] = (f"offset {mag:.1f} m inside the corrected fix's PL "
                                         f"{float(pl):.1f} m: no bearing claimed")
            else:
                block["emitter_bearing_deg"] = block["deduced_offset"]["bearing_deg"]
                block["bearing_gate"] = (f"offset {mag:.1f} m exceeds PL {float(pl):.1f} m"
                                         if pl is not None else "no PL to gate on")
            clk = _clock_ge(rec)
            if clk is not None and anchor["clk_ge"] is not None:
                block["path_delay_m"] = round(clk - anchor["clk_ge"], 1)
                if block["emitter_bearing_deg"] is not None:
                    block["emitter_standoff_estimate_m"] = block["path_delay_m"]
        corr[BLOCK] = block
    return records


# --------------------------------------------------------------------------- measurement

def measure(records: list, pre: int = 60, attack: int = 30) -> dict:
    """The numbers the provenance section reports, from a stream carrying the block."""
    blocks = [((r.get("geometry") or {}).get("correction") or {}).get(BLOCK) or {} for r in records]
    states = [d.state.name for d in arbitrate(records)]
    a0, a1 = pre, pre + attack
    atk = range(a0, min(a1, len(records)))
    agree = [blocks[j]["agreement_m"] for j in atk if blocks[j].get("agreement_m") is not None]
    offs = [blocks[j]["deduced_offset"]["mag_m"] for j in atk if blocks[j].get("deduced_offset")]
    disp = [(records[j].get("_solution") or {}).get("displacement_m") for j in atk]
    off_vs_d = [abs(o - d) for o, d in zip(offs, disp) if d is not None]
    injected = [(records[j].get("_attack") or {}).get("range_offset_m") for j in atk]
    injected = [v for v in injected if v]

    # stationary windows inside the attack, and the last epoch of each
    windows = []
    for j in atk:
        f = blocks[j].get("frame") or {}
        if f.get("stationary"):
            if windows and windows[-1]["end"] == j - 1 and windows[-1]["reason"] == f.get("reason"):
                windows[-1]["end"] = j
            else:
                windows.append({"start": j, "end": j, "reason": f.get("reason")})
    for w in windows:
        b = blocks[w["end"]]
        w.update({"drag": b.get("drag"), "emitter_bearing_deg": b.get("emitter_bearing_deg"),
                  "path_delay_m": b.get("path_delay_m"), "report_tag": b.get("report_tag")})
    pd_all = [blocks[j]["path_delay_m"] for j in atk if blocks[j].get("path_delay_m") is not None]
    bearings = [blocks[j]["emitter_bearing_deg"] for j in atk if blocks[j].get("emitter_bearing_deg") is not None]

    # clean lead-in: anchored must be false; a held anchor from epoch 0 vs _truth
    lead = range(0, a0)
    anchored_lead = sum(1 for j in lead if blocks[j].get("anchored"))
    held = None
    if records and _latlon(records[0].get("position")) is not None:
        e0 = _latlon(records[0]["position"])
        errs = []
        for j in lead:
            t = _latlon(records[j].get("_truth"))
            if t is not None:
                errs.append(math.hypot(e0[0] - t[0], e0[1] - t[1]))
        held = {"median_m": round(median(errs), 2), "max_m": round(max(errs), 2)} if errs else None
    lead_offsets = [blocks[j]["deduced_offset"]["mag_m"] for j in lead if blocks[j].get("deduced_offset")]

    # the method's noise floor: drag measured on a stationary window with the repeater off
    clean_drag = [blocks[j]["drag"]["rate_m_per_epoch"] for j in range(a1, len(records))
                  if blocks[j].get("drag")]
    post_windows = sorted({(blocks[j].get("frame") or {}).get("reason") for j in range(a1, len(records))
                           if (blocks[j].get("frame") or {}).get("stationary")} - {None})
    transitions = [(j, states[j - 1], states[j]) for j in range(1, len(states)) if states[j] != states[j - 1]]
    return {
        "epochs": len(records), "onset": a0, "attack_end": a1,
        "anchor_epoch": blocks[a0].get("anchor_epoch") if a0 < len(blocks) else None,
        "transitions": transitions,
        "anchored_attack": sum(1 for j in atk if blocks[j].get("anchored")),
        "agreement": {"median_m": round(median(agree), 2), "max_m": round(max(agree), 2)} if agree else None,
        "offset": {"min_m": round(min(offs), 1), "max_m": round(max(offs), 1)} if offs else None,
        "offset_vs_displacement_max_m": round(max(off_vs_d), 2) if off_vs_d else None,
        "windows": windows,
        "path_delay": {"median_m": round(median(pd_all), 1), "min_m": round(min(pd_all), 1),
                       "max_m": round(max(pd_all), 1), "injected_m": injected[0] if injected else None,
                       "n": len(pd_all)} if pd_all else None,
        "emitter_bearing": {"median_deg": round(median(bearings), 1), "min_deg": round(min(bearings), 1),
                            "max_deg": round(max(bearings), 1), "n": len(bearings)} if bearings else None,
        "lead_in": {"anchored_epochs": anchored_lead, "held_anchor_error": held,
                    "deduced_offset_max_m": round(max(lead_offsets), 2) if lead_offsets else None},
        "post": {"stationary_windows": post_windows,
                 "drag_rate_median_m_per_epoch": round(median(clean_drag), 3) if clean_drag else None,
                 "drag_rate_max_m_per_epoch": round(max(clean_drag), 3) if clean_drag else None},
        "tags": {j: blocks[j].get("report_tag") for j in (0, a0, a0 + 10, a1, len(records) - 1)
                 if j < len(blocks)},
    }


def section(m: dict) -> str:
    w = "\n".join(
        f"| {x['start']}–{x['end']} | {x['reason']} | "
        f"{'—' if not x['drag'] else str(x['drag']['rate_m_per_epoch']) + ' m/epoch on ' + str(x['drag']['bearing_deg']) + '°'} | "
        f"{x['emitter_bearing_deg'] if x['emitter_bearing_deg'] is not None else '—'} | "
        f"{x['path_delay_m'] if x['path_delay_m'] is not None else '—'} | `{x['report_tag']}` |"
        for x in m["windows"]) or "| — | no stationary window inside the attack | | | | |"
    tr = ", ".join(f"{j} {a}→{b}" for j, a, b in m["transitions"]) or "none"
    pdm = m["path_delay"]
    eb = m["emitter_bearing"]
    ag = m["agreement"]
    li = m["lead_in"]
    return f"""{SECTION}

Added by `backend/missions_recon.py` (RECON's post-pass, `register_augment`);
measured on this stream by `python -m backend.missions_recon`. The anchor is the
last NOMINAL fix (arbitras transitions: {tr}) carried by odometry -- the route in
the presentation frame, zero in the stream frame -- and the anchor cross-check
and the Galileo re-solve are independent: one is RF-free odometry from a fix
taken before the attack, the other the trusted-subset solve of each epoch's
ranges.

| quantity | value |
|---|---|
| anchor epoch (last NOMINAL before the downgrade) | {m['anchor_epoch']} (onset {m['onset']}) |
| anchored during the attack | {m['anchored_attack']}/{m['attack_end'] - m['onset']} epochs |
| agreement_m (anchor vs corrected_position) over the attack, median / max | {ag['median_m'] if ag else '—'} / {ag['max_m'] if ag else '—'} m |
| deduced offset over the attack, min / max | {m['offset']['min_m'] if m['offset'] else '—'} / {m['offset']['max_m'] if m['offset'] else '—'} m |
| deduced offset vs measured displacement, max difference (fix jitter since the anchor) | {m['offset_vs_displacement_max_m']} m |
| emitter bearing while stationary, median (min–max) | {f"{eb['median_deg']}° ({eb['min_deg']}–{eb['max_deg']}°), {eb['n']} epochs" if eb else '—'} (injected bearing 90°) |
| path-delay estimate while stationary, median (min–max) | {f"{pdm['median_m']} m ({pdm['min_m']}–{pdm['max_m']}), {pdm['n']} epochs" if pdm else '—'} vs {pdm['injected_m'] if pdm else '—'} m injected |
| clean lead-in: epochs anchored (must be 0) | {li['anchored_epochs']} |
| clean lead-in: deduced offset max (anchor refreshed every NOMINAL epoch) | {li['deduced_offset_max_m']} m |
| clean lead-in: an anchor HELD from epoch 0 vs `_truth`, median / max | {li['held_anchor_error']['median_m'] if li['held_anchor_error'] else '—'} / {li['held_anchor_error']['max_m'] if li['held_anchor_error'] else '—'} m |
| post-attack stationary windows | {', '.join(m['post']['stationary_windows']) or 'none'} |
| drag rate with the repeater off (method noise floor), median / max | {m['post']['drag_rate_median_m_per_epoch']} / {m['post']['drag_rate_max_m_per_epoch']} m/epoch |

Stationary windows inside the attack (drag and bearing at the window's last epoch):

| epochs | why stationary | drag | emitter bearing | path delay m | report tag |
|---|---|---|---|---|---|
{w}

Report tags: {"; ".join(f"epoch {j} `{t}`" for j, t in m['tags'].items())}.

Notes. The path-delay estimate is the jump in the believed fix's dt_G - dt_E
since the anchor epoch (`_solution.clock_bias_m`, metres); it reads below the
injected figure because the joint G+E fix absorbs part of the repeater's
position step into position (the 118–125 m displacement) rather than into the
GPS clock. The detector's channel z-scores (`features.cross_constellation_detail`)
and `CrossCal.scale` are dimensionless and are used for attribution only. The
emitter bearing is claimed only when the deduced offset exceeds the corrected
fix's protection level; inside it the offset is fix noise. A SURRENDERED halt
is a stationary window the console actually draws (progress gain 0); an
observation-point dwell is scripted (the console does not yet slow the vehicle
at an OP) and is labelled as such in `frame.reason`.
"""


def write_section(doc: Path, text: str) -> None:
    body = doc.read_text() if doc.exists() else ""
    pat = re.compile(re.escape(SECTION) + r".*?(?=\n## |\Z)", re.S)
    if pat.search(body):
        body = pat.sub(lambda _: text.rstrip("\n") + "\n", body)
    else:
        body = body.rstrip("\n") + "\n\n" + text
    doc.write_text(body)


def _read(path: Path) -> list:
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def _write_atomic(records: list, path: Path) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")
    os.replace(tmp, path)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Measure RECON's stationary deduction; append the provenance section.")
    ap.add_argument("stream", nargs="?", default=str(STREAM))
    ap.add_argument("--doc", default=str(DOC))
    ap.add_argument("--apply", action="store_true",
                    help="rewrite the stream with the block (the runner does this itself via register_augment)")
    ap.add_argument("--no-doc", action="store_true")
    a = ap.parse_args(argv)
    recs = _read(Path(a.stream))
    has = all(((r.get("geometry") or {}).get("correction") or {}).get(BLOCK) for r in recs)
    if a.apply or not has:
        recs = stationary_deduction(recs, {})
        if a.apply:
            _write_atomic(recs, Path(a.stream))
            print(f"wrote {a.stream} with geometry.correction.{BLOCK} on {len(recs)} epochs")
    m = measure(recs)
    print(json.dumps(m, indent=1, default=str))
    if not a.no_doc:
        write_section(Path(a.doc), section(m))
        print(f"section written to {a.doc}")


try:                                     # registered when imported by the runner
    from backend.missions import register_augment
    register_augment("recon", stationary_deduction)
except ImportError:                      # standalone use (tests, --apply) needs no runner
    pass

if __name__ == "__main__":
    main()
