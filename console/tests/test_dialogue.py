"""The guide's dialogue layer: shape, the checkpoint schedule, and design.md §14.

`console/web/DIALOGUE.md` revision 2 is the contract under test: the gate is
gone, the replay never pauses, and dialogue is anchored at checkpoints the
operator can count down (`index / total`), with every line stamped with the
epoch its claims were verified against.

The test that matters is `test_every_numeral_on_screen_is_claimed`. Everything
else here guards the contract; that one guards the promise the pitch is built
on -- no number reaches the operator that cannot be sourced. It scans the
rendered text with a regex over digits and allow-lists nothing.

Two rules for anyone editing this file:

  - Nothing here may hardcode a figure that lives in
    `console/missions/__init__.py`. That script is edited constantly; a test
    that pins its line count fails for the wrong reason. The MEASURED waypoint
    table below is the exception, because it is in the contract.
  - A test that only checks the payload shape is worth very little. Every
    firing rule here is checked against a real replay stream as well.
"""
import json
import os
import re

import pytest

from console.arbitras.dialogue import (
    _gist,
    FALLBACK_TEXT,
    INTRO_MAX_LINES,
    KINDS,
    MAX_LINES,
    SPEAKER,
    TONES,
    Guide,
    mission_states,
    numerals,
    project_on_route,
    uncovered,
    verify_lines,
    waypoint_checkpoints,
)
from console.arbitras.machine import Arbitras
from console.replay import load as load_stream
from console.arbitras.states import TrustState

# Same shape test_machine.py uses, so a failure here is comparable to one there.
BASE_TS = "2026-08-20T12:00:00Z"


def epoch(confidence, credential="VALID", **kw):
    e = {
        "timestamp": kw.pop("timestamp", BASE_TS),
        "confidence": confidence,
        "credential_status": credential,
        "features": kw.pop("features", {"cn0_anomaly": 0.1}),
        "geometry": kw.pop("geometry", {}),
    }
    e.update(kw)
    return e


SCRIPT_MISSION = {
    "title": "LOGISTICS",
    "behaviour": {
        "NOMINAL": "Convoy proceeding on GNSS.",
        "DEGRADED": "Convoy proceeding on the trusted-satellite fix.",
        "RESTRICTED": "Convoy completes the current leg only.",
        "SURRENDERED": "Convoy under operator control.",
    },
    "guide": [
        {"epoch": 1, "label": "carry-off begins", "lines": [
            {"text": "The carry-off starts here: six GPS satellites captured at +2 dB.",
             "tone": "alert",
             "claims": [{"text": "+2 dB", "value": 2.0,
                         "source": "injector parameter, backend/missions.py logistics"}]},
        ]},
        {"epoch": 4, "label": "attack ends",
         "lines": [{"text": "The attack ends.", "tone": "calm"}]},
    ],
}

# A three-leg route with a prop on it, so the waypoint arithmetic can be
# checked without the registry: 100 m in at 2 m/epoch is epoch 50.
ROUTE_MISSION = {
    "title": "ROUTE",
    "route": [{"e": 0.0, "n": 0.0}, {"e": 200.0, "n": 0.0}],
    "speed_m_per_epoch": 2.0,
    "props": [{"e": 0.0, "n": 0.0, "label": "START · ORIGIN"},
              {"e": 100.0, "n": 0.0, "label": "HALFWAY"}],
}


def drive(guide, arb, epochs):
    """-> [(index, payload)] for every epoch, speaking or not."""
    return [(i, guide.step(arb.step(e), e)) for i, e in enumerate(epochs)]


def spoken(out):
    """Every line's text across a driven run."""
    return [l["text"] for _, p in out for l in p["lines"]]


def checkpoints(out):
    """The checkpoint object of every speaking epoch that carried one."""
    return [p["checkpoint"] for _, p in out if p["checkpoint"]]


# ------------------------------------------------------------- wire shape

def test_payload_matches_the_contract_shape():
    g, arb = Guide(), Arbitras()
    p = g.step(arb.step(epoch(0.9)), epoch(0.9))
    assert set(p) == {"lines", "checkpoint", "verified", "failures"}
    assert set(p["checkpoint"]) == {"index", "total", "label", "kind"}
    assert p["checkpoint"]["kind"] == "intro" and p["checkpoint"]["index"] == 1
    for line in p["lines"]:
        assert set(line) == {"speaker", "text", "claims", "tone", "at", "epoch"}
        assert line["speaker"] == SPEAKER
        assert line["tone"] in TONES
        for c in line["claims"]:
            assert ("path" in c) != ("source" in c), "exactly one of path/source"


def test_the_gate_is_gone_from_the_wire():
    """DIALOGUE.md revision 2 removed it; a client must not find it again."""
    g, arb = Guide(SCRIPT_MISSION), Arbitras()
    for _, p in drive(g, arb, [epoch(0.9), epoch(0.9), epoch(0.2)]):
        assert "gate" not in p and "gate_reason" not in p
    assert "gate" not in g.finish()


def test_most_epochs_are_silent():
    g, arb = Guide(), Arbitras()
    out = drive(g, arb, [epoch(0.9)] * 30)
    speaking = [p for _, p in out if p["lines"]]
    assert len(speaking) == 1, "only the intro should speak on a flat clean run"
    for _, p in out[1:]:
        assert p["lines"] == [] and p["checkpoint"] is None


def test_kinds_are_exactly_the_contract_four():
    g, arb = Guide(SCRIPT_MISSION), Arbitras()
    seen = set()
    for _, p in drive(g, arb, [epoch(0.9), epoch(0.9, "UNVERIFIED"),
                               epoch(0.2), epoch(0.9), epoch(0.9)]):
        if p["checkpoint"]:
            seen.add(p["checkpoint"]["kind"])
    seen.add(g.finish()["checkpoint"]["kind"])
    assert seen <= set(KINDS)
    assert {"intro", "event", "end"} <= seen


# ------------------------------------------------ the measured waypoints

# DIALOGUE.md revision 2, "Checkpoints, measured", as CORRECTED: a waypoint is
# where the vehicle IS, so the epoch is arrival -- accrued progress (speed x the
# gain for the arbitrated state) first reaching the prop's arc length.
MEASURED = {
    "recon": [0, 100, 147, 176, 344],
    "logistics": [0, 197, 333],
    "casevac": [0, 275, 280],
    "combat": [0, 62, 209, 332]
}
# The reference column on the same table: where the vehicle would be if nothing
# ever happened to it. It is the fallback when the stream cannot be replayed.
UNIMPEDED = {
    "recon": [0, 75, 122, 152, 320],
    "logistics": [0, 140, 276],
    "casevac": [0, 187, 191],
    "combat": [0, 60, 188, 311],
}
MEASURED_LABELS = {
    "recon": ["PATROL BASE", "OP-1", "OP-2", "OP KESTREL", "OP-3"],
    "logistics": ["FOB", "PL AMBER", "RP KILO"],
    "casevac": ["ROLE 1 AID STATION", "CCP", "CASUALTY"],
    "combat": ["LD", "PL AMBER", "PL RED", "OBJ HAWK"],
}


def mission_dicts():
    from console import missions
    return {n: missions.as_dict(missions.get(n)) for n in MEASURED}


def test_waypoint_epochs_reproduce_the_contract_table():
    """Arrival, against the corrected table. Two derivations must agree.

    The bug this closes: dividing arc length by speed puts the checkpoint where
    the vehicle would be if the attack never happened. Every mission halts or
    slows under one, so the words fired while the picture was still short of
    the waypoint -- by 2 epochs in COMBAT and 89 in CASEVAC.
    """
    for name, md in mission_dicts().items():
        states = mission_states(md)
        if states is None:
            pytest.skip(f"{md.get('stream')} not on this machine")
        got = [w["epoch"] for w in waypoint_checkpoints(md, states)]
        assert got == MEASURED[name], f"{name}: {got} != {MEASURED[name]}"


def test_the_unimpeded_schedule_is_the_documented_fallback():
    """No stream to replay -> arc_length / speed, the contract's reference column."""
    for name, md in mission_dicts().items():
        got = [w["epoch"] for w in waypoint_checkpoints(md)]
        assert got == UNIMPEDED[name], f"{name}: {got} != {UNIMPEDED[name]}"
        assert got != MEASURED[name], "the two columns are not the same schedule"


def test_arrival_accrues_at_the_gain_the_vehicle_actually_had():
    """index.html: `s += speed_m_per_epoch * gain[state]`, epoch 0 at the origin.

    A halt is the whole point: a convoy in SURRENDERED does not cover ground,
    so its waypoint arrives later than the route arithmetic alone would say.
    """
    mission = dict(ROUTE_MISSION, gain={"NOMINAL": 1.0, "SURRENDERED": 0.0})
    clean = ["NOMINAL"] * 60
    assert [w["epoch"] for w in waypoint_checkpoints(mission, clean)] == [0, 50]
    halted = ["NOMINAL"] * 10 + ["SURRENDERED"] * 20 + ["NOMINAL"] * 60
    assert [w["epoch"] for w in waypoint_checkpoints(mission, halted)] == [0, 70]


def test_a_prop_the_vehicle_never_reaches_is_not_a_checkpoint():
    """It never happens, so `total` may not count it."""
    mission = dict(ROUTE_MISSION, gain={"NOMINAL": 1.0})
    short = ["NOMINAL"] * 20                  # 40 m of a 100 m leg
    assert [w["label"] for w in waypoint_checkpoints(mission, short)] == ["START"]


def test_waypoint_labels_are_the_props_own():
    for name, md in mission_dicts().items():
        got = [w["label"] for w in waypoint_checkpoints(md, mission_states(md))]
        assert got == MEASURED_LABELS[name]


def test_the_props_lie_on_the_route():
    """The contract quotes offset 0.0 m; the arithmetic is only valid if so."""
    for name, md in mission_dicts().items():
        worst = max(w["offset_m"] for w in waypoint_checkpoints(md))
        assert worst < 0.05, f"{name}: prop {worst} m off the route"


def test_projection_is_arc_length_not_straight_line():
    route = [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0)]
    s, off = project_on_route(route, (100.0, 40.0))
    assert abs(s - 140.0) < 1e-9 and off < 1e-9
    s, off = project_on_route(route, (50.0, 10.0))
    assert abs(s - 50.0) < 1e-9 and abs(off - 10.0) < 1e-9


def test_waypoints_from_a_mission_with_no_route_are_simply_none():
    assert waypoint_checkpoints(None) == []
    assert waypoint_checkpoints({"props": [{"e": 1.0, "n": 1.0, "label": "X"}]}) == []
    assert waypoint_checkpoints(dict(ROUTE_MISSION, speed_m_per_epoch=0.0)) == []


# ------------------------------------------------------ the checkpoint count

def test_total_is_known_up_front_and_reached_exactly_at_the_end():
    """`index / total` has to be meaningful on the first line and true on the last."""
    g, arb = Guide(ROUTE_MISSION), Arbitras()
    total = g.total
    out = drive(g, arb, [epoch(0.9)] * 60)
    firsts = checkpoints(out)
    assert firsts[0]["total"] == total, "the first line already carries the count"
    assert {c["total"] for c in firsts} == {total}, "the denominator never moves"
    end = g.finish()
    assert end["checkpoint"]["index"] == total == end["checkpoint"]["total"]
    assert end["checkpoint"]["kind"] == "end"


def test_a_waypoint_is_a_checkpoint_of_its_own():
    g, arb = Guide(ROUTE_MISSION), Arbitras()
    out = drive(g, arb, [epoch(0.9)] * 60)
    cps = checkpoints(out)
    # intro (epoch 0, absorbing the START prop) + HALFWAY at epoch 50.
    assert [c["kind"] for c in cps] == ["intro", "waypoint"]
    assert cps[1]["label"] == "HALFWAY"
    assert cps[1]["index"] == 2 and cps[1]["total"] == 3   # intro, HALFWAY, end
    said = " ".join(spoken(out))
    assert "HALFWAY" in said


def test_the_intro_absorbs_whatever_shares_its_epoch():
    """Epoch 0 is one checkpoint, not three. That is why `total` is exact."""
    g = Guide(dict(ROUTE_MISSION, guide=[
        {"epoch": 0, "lines": [{"text": "Rolling.", "tone": "calm"}]}]))
    sched = g.checkpoints()
    assert [s["kind"] for s in sched] == ["intro", "waypoint", "end"]
    assert sched[0]["epoch"] == 0 and sched[0]["label"] == "START"
    arb = Arbitras()
    p = g.step(arb.step(epoch(0.9)), epoch(0.9))
    assert p["checkpoint"]["index"] == 1
    assert "Rolling." in [l["text"] for l in p["lines"]]


def test_checkpoint_indices_are_strictly_increasing_and_bounded():
    """Over every real stream, and the synthetic ones beside them."""
    runs = list(streams()) or []
    checked = 0
    for name, path, mission in runs:
        arb, g = Arbitras(), Guide(mission)
        last = 0
        with open(path) as fh:
            for raw in fh:
                e = json.loads(raw)
                cp = g.step(arb.step(e), e)["checkpoint"]
                if cp is None:
                    continue
                assert cp["index"] > last, f"{name}: {cp['index']} after {last}"
                assert 1 <= cp["index"] <= cp["total"] == g.total
                last = cp["index"]
                checked += 1
        end = g.finish()["checkpoint"]
        assert end["index"] > last and end["index"] == g.total
        checked += 1
    assert checked > 0, "no stream replayed; the ordering proved nothing"


def test_an_unscripted_state_change_is_an_interjection_not_a_checkpoint():
    """It speaks on its own epoch, and it does not move the count.

    This is the honesty of `total`: a mission cannot know in advance that the
    trust state will wobble at epoch 180, so a wobble may not be allowed to
    change the denominator the operator is reading.
    """
    g, arb = Guide(ROUTE_MISSION), Arbitras()
    total = g.total
    out = drive(g, arb, [epoch(0.9), epoch(0.9), epoch(0.2), epoch(0.2)])
    p = out[2][1]
    assert p["lines"], "a trust-state change must still be spoken"
    assert p["checkpoint"] is None, "and it is not a checkpoint"
    assert g.total == total, "the denominator did not move"
    assert SCRIPT_MISSION["behaviour"]["SURRENDERED"] not in " ".join(
        l["text"] for l in p["lines"]), "wrong mission's prose"


# ---------------------------------------------------------- firing rules

def test_intro_fires_once_and_only_on_the_first_epoch():
    g, arb = Guide(), Arbitras()
    out = drive(g, arb, [epoch(0.9)] * 5)
    assert out[0][1]["checkpoint"]["kind"] == "intro"
    assert all(p["checkpoint"] is None or p["checkpoint"]["kind"] != "intro"
               for _, p in out[1:])


def test_state_change_says_what_the_vehicle_does():
    g, arb = Guide(SCRIPT_MISSION), Arbitras()
    out = drive(g, arb, [epoch(0.9), epoch(0.9), epoch(0.9), epoch(0.2)])
    p = out[3][1]
    assert p["lines"]
    text = " ".join(l["text"] for l in p["lines"])
    assert SCRIPT_MISSION["behaviour"]["SURRENDERED"] in text


def test_behaviour_falls_back_to_explain_when_no_mission():
    from console.arbitras.explain import BEHAVIOUR
    g, arb = Guide(None), Arbitras()
    out = drive(g, arb, [epoch(0.9), epoch(0.9), epoch(0.2)])
    text = " ".join(l["text"] for l in out[2][1]["lines"])
    assert BEHAVIOUR[TrustState.SURRENDERED] in text


def test_credential_change_speaks_but_not_on_the_first_epoch():
    g, arb = Guide(), Arbitras()
    # First epoch: VALID is merely learned, not announced.
    out = drive(g, arb, [epoch(0.9, "VALID"), epoch(0.9, "VALID"),
                         epoch(0.9, "PENDING"), epoch(0.9, "PENDING")])
    assert out[0][1]["checkpoint"]["kind"] == "intro"
    assert out[1][1]["lines"] == []
    assert out[2][1]["lines"], "the transition is spoken"
    assert out[3][1]["lines"] == [], "a steady status is not a transition"


def test_stale_epochs_do_not_invent_a_credential_transition():
    """A missing epoch carries the last status forward; that is not a change."""
    from console.arbitras.dialogue import CREDENTIAL_LINE
    g, arb = Guide(), Arbitras()
    drive(g, arb, [epoch(0.9)])
    announcements = set(CREDENTIAL_LINE.values())
    for _ in range(6):
        p = g.step(arb.step(None), None)
        for line in p["lines"]:
            assert line["text"] not in announcements, line["text"]


def test_a_script_entry_never_fires_twice():
    g, arb = Guide(SCRIPT_MISSION), Arbitras()
    out = drive(g, arb, [epoch(0.9)] * 12)
    said = spoken(out)
    for entry in SCRIPT_MISSION["guide"]:
        for line in entry["lines"]:
            assert said.count(line["text"]) == 1
    idx = [p["checkpoint"]["index"] for _, p in out if p["checkpoint"]]
    assert idx == sorted(set(idx))


def test_the_script_leads_its_checkpoint_and_the_cap_holds():
    g, arb = Guide(SCRIPT_MISSION), Arbitras()
    # Epoch 1 fires the script entry, a state change and a credential change
    # at once; the script is what the mission wrote, so it leads.
    out = drive(g, arb, [epoch(0.9, "VALID"), epoch(0.05, "UNVERIFIED")])
    p = out[1][1]
    assert p["lines"][0]["text"] == SCRIPT_MISSION["guide"][0]["lines"][0]["text"]
    assert len(p["lines"]) == MAX_LINES
    assert p["checkpoint"]["label"] == "carry-off begins"
    # The opening checkpoint is the documented exception: the guide introduces
    # itself first and is allowed one extra line to do it (DIALOGUE.md §2).
    assert all(
        len(q["lines"]) <= (INTRO_MAX_LINES
                            if q["checkpoint"] and q["checkpoint"]["kind"] == "intro"
                            else MAX_LINES)
        for _, q in out
    )


def test_finish_speaks_once():
    g = Guide()
    first, second = g.finish(), g.finish()
    assert first["lines"] and first["checkpoint"]["kind"] == "end"
    assert second["lines"] == [] and second["checkpoint"] is None


# ------------------------- an event checkpoint stays on its own epoch

DRIFT_MISSION = {
    "title": "DRIFT",
    "guide": [{"epoch": 2, "label": "pinned", "lines": [
        {"text": "Confidence is 0.91 at this instant.", "tone": "alert",
         "claims": [{"text": "0.91", "value": 0.91, "path": "confidence"}]}]}],
}


def test_an_event_checkpoint_fires_where_its_claims_hold():
    g, arb = Guide(DRIFT_MISSION), Arbitras()
    out = drive(g, arb, [epoch(0.91)] * 5)
    hit = [p for _, p in out if p["checkpoint"]
           and p["checkpoint"]["label"] == "pinned"]
    assert len(hit) == 1
    assert hit[0]["checkpoint"]["kind"] == "event"
    assert "0.91" in hit[0]["lines"][0]["text"]
    assert hit[0]["verified"] and hit[0]["lines"][0]["epoch"] == 2


def test_an_event_checkpoint_is_never_emitted_where_its_claims_do_not_verify():
    """DIALOGUE.md revision 2: it may not be moved, so it is not said at all.

    The failure this rules out is the worst one available to a narrator: a
    number that was true at epoch 2 read out over epoch 30's instruments.
    """
    g, arb = Guide(DRIFT_MISSION), Arbitras()
    # Epoch 2 disagrees with the pinned claim, so the checkpoint must not fire.
    # Epochs 3-6 DO carry 0.91 -- and it must not fire there either.
    out = drive(g, arb, [epoch(0.5), epoch(0.5), epoch(0.5)] + [epoch(0.91)] * 4)
    said = " ".join(spoken(out))
    assert "0.91 at this instant" not in said
    assert FALLBACK_TEXT not in said, "silence, not a withheld-line placeholder"
    assert all(p["checkpoint"] is None or p["checkpoint"]["label"] != "pinned"
               for _, p in out)
    assert all(p["verified"] for _, p in out)


def test_a_waypoint_still_speaks_when_the_script_pinned_to_it_cannot():
    """The place is measured; only the script's numbers were epoch-bound."""
    mission = dict(ROUTE_MISSION, guide=[
        {"epoch": 50, "lines": [
            {"text": "Confidence is 0.91 here.", "tone": "calm",
             "claims": [{"text": "0.91", "value": 0.91, "path": "confidence"}]}]}])
    g, arb = Guide(mission), Arbitras()
    out = drive(g, arb, [epoch(0.5)] * 55)
    cp = [p["checkpoint"] for _, p in out if p["checkpoint"]][-1]
    assert cp["kind"] == "waypoint" and cp["label"] == "HALFWAY"
    said = " ".join(spoken(out))
    assert "0.91 here" not in said and "HALFWAY" in said


def test_an_unlabelled_checkpoint_is_named_by_what_happened():
    """DIALOGUE.md: "event checkpoints are labelled by what happened".

    And never by a state name -- "SURRENDERED" is builder language, which
    invariant 3 keeps off the screen even when the mission's own beat caption
    opens with it.
    """
    mission = {
        "beats": [{"epoch": 2, "caption": "Carry-off begins: six satellites captured."},
                  {"epoch": 5, "caption": "SURRENDERED: control to the operator."}],
        "guide": [{"epoch": 2, "lines": [{"text": "It starts.", "tone": "alert"}]},
                  {"epoch": 5, "lines": [{"text": "Handing over.", "tone": "bad"}]},
                  {"epoch": 7, "lines": [{"text": "Key is late.", "tone": "alert"}]},
                  {"epoch": 9, "lines": [{"text": "Nothing named.", "tone": "calm"}]}],
    }
    g, arb = Guide(mission), Arbitras()
    out = drive(g, arb, [epoch(0.9)] * 5 + [epoch(0.05)] * 2
                + [epoch(0.05, "PENDING")] * 2 + [epoch(0.05, "PENDING")])
    labels = {c["label"] for c in checkpoints(out)}
    assert "Carry-off begins" in labels, labels
    assert "OPERATOR IN CONTROL" in labels, labels
    assert "SURRENDERED" not in labels, "builder language reached the screen"
    assert "AWAITING KEY DISCLOSURE" in labels, labels
    assert "MISSION BEAT" in labels, labels


def test_every_scripted_entry_is_spoken_on_its_own_stream():
    """Abandonment is silent by design, so assert nothing is being abandoned.

    Each mission's `guide` is pinned to epochs of its own stream, so every
    entry must fire there, verbatim. The epoch-0 entry shares the opening
    checkpoint with the briefing and is capped at four lines between them, so
    it is checked for at least its first line.
    """
    for name, path, mission in streams():
        if not (mission or {}).get("guide"):
            continue
        arb, g = Arbitras(), Guide(mission)
        said = []
        with open(path) as fh:
            for raw in fh:
                e = json.loads(raw)
                said += [l["text"] for l in g.step(arb.step(e), e)["lines"]]
        for entry in mission["guide"]:
            wanted = [l["text"] for l in entry["lines"]][:MAX_LINES]
            if int(entry["epoch"]) <= 0:
                wanted = wanted[:1]
            for text in wanted:
                assert said.count(text) == 1, (
                    f"{name}: epoch {entry['epoch']} line not spoken exactly once: "
                    f"{text[:60]!r}")


# ------------------------------------------ the epoch every line was checked at

def test_every_line_carries_the_epoch_it_was_checked_against():
    """DIALOGUE.md revision 2. The drift this closes was a real defect: a line
    reading "confidence 0.91" stayed on screen while the strip read 0.92."""
    checked = 0
    for name, path, mission in streams():
        arb, g = Arbitras(), Guide(mission)
        with open(path) as fh:
            for i, raw in enumerate(fh):
                e = json.loads(raw)
                p = g.step(arb.step(e), e)
                want = e["timestamp"].split("T", 1)[1].rstrip("Z")
                for line in p["lines"]:
                    assert line["epoch"] == i, f"{name}[{i}]: {line['epoch']}"
                    assert line["at"] == want, f"{name}[{i}]: {line['at']} != {want}"
                    checked += 1
        end = g.finish()
        for line in end["lines"]:
            assert line["epoch"] == i, "the end speaks at the last epoch it saw"
    assert checked > 0, "no stream replayed; the stamping proved nothing"


def test_the_stamp_follows_the_epoch_handed_to_step_not_the_wall_clock():
    g, arb = Guide(), Arbitras()
    late = epoch(0.9, timestamp="2026-08-20T23:59:01Z")
    p = g.step(arb.step(late), late)
    assert all(l["at"] == "23:59:01" and l["epoch"] == 0 for l in p["lines"])
    p = g.step(arb.step(None), None)      # a stale epoch has no time of its own
    for line in p["lines"]:
        assert line["at"] is None and line["epoch"] == 1


# ------------------------------------------------------- mission handling

def test_guide_with_no_mission_at_all():
    """`Guide(None)` is the no-mission SSE connection. It must still speak."""
    g, arb = Guide(None), Arbitras()
    assert g.total == 2, "with no mission there is only the intro and the end"
    out = drive(g, arb, [epoch(0.9), epoch(0.2), epoch(0.2, "REVOKED")])
    assert out[0][1]["checkpoint"]["kind"] == "intro"
    assert all(p["lines"] for _, p in out), "state and credential still speak"
    assert [p["checkpoint"] for _, p in out[1:]] == [None, None]
    assert all(p["verified"] for _, p in out)
    assert g.finish()["checkpoint"]["index"] == 2


def test_mission_without_a_guide_field_is_not_an_error():
    """The script is one field; a mission may not have written one yet."""
    mission = {k: v for k, v in ROUTE_MISSION.items() if k != "guide"}
    g, arb = Guide(mission), Arbitras()
    out = drive(g, arb, [epoch(0.9)] * 8)
    assert [c["kind"] for c in checkpoints(out)] == ["intro"]
    assert all(p["verified"] for _, p in out)
    assert g.total == 3, "waypoints alone are still checkpoints"


def test_a_malformed_script_entry_is_ignored_not_fatal():
    g = Guide({"guide": [{"epoch": "soon", "lines": [{"text": "?"}]},
                         {"epoch": 3, "lines": [{"text": "ok"}]}]})
    assert [s["epoch"] for s in g.checkpoints()] == [0, 3, None]


# --------------------------------------------------- design.md §14 itself

NUMBER = re.compile(r"\d+(?:\.\d+)?")


def assert_all_numerals_claimed(payload, where):
    """Regex over digits. Nothing is allow-listed; a bare number fails."""
    for line in payload["lines"]:
        tokens = set(NUMBER.findall(line["text"]))
        claimed = set()
        for c in line["claims"]:
            claimed |= set(NUMBER.findall(str(c["text"])))
        missing = tokens - claimed
        assert not missing, (
            f"{where}: unsourced numeral(s) {sorted(missing)} in {line['text']!r}"
        )


def streams():
    """The real replay streams that exist on this machine, mission by mission."""
    from console import missions
    for name in ("logistics", "recon", "casevac", "combat", "demo"):
        path = os.path.join("out", f"{name}.jsonl")
        if not os.path.exists(path):
            continue
        try:
            mission = missions.as_dict(missions.get(name))
        except KeyError:
            mission = None          # out/demo.jsonl has no mission of its own
        yield name, path, mission


def test_every_numeral_on_screen_is_claimed():
    """design.md §14, over every line the guide produces on real data.

    Two assertions, and both are needed. The coverage scan alone would pass
    trivially if every line had been swapped for the numeral-free fallback, so
    the run must also come back fully verified.
    """
    found = 0
    for name, path, mission in streams():
        for m in (mission, None):       # with the mission's prose, and without
            arb, g = Arbitras(), Guide(m)
            with open(path) as fh:
                for i, raw in enumerate(fh):
                    e = json.loads(raw)
                    p = g.step(arb.step(e), e)
                    assert_all_numerals_claimed(p, f"{name}[{i}]")
                    assert p["verified"], (name, i, p["failures"])
                    found += sum(len(l["claims"]) for l in p["lines"])
            assert_all_numerals_claimed(g.finish(), f"{name}[end]")
    assert found > 0, "no stream replayed; the coverage scan proved nothing"
    print(f"\n§14 coverage: {found} claims checked across the replay streams")


def test_a_wrong_path_claim_is_caught_and_the_line_replaced():
    line = {"speaker": SPEAKER, "tone": "alert",
            "text": "The box an attacker could move me inside is 118 m wide.",
            "claims": [{"text": "118 m", "value": 118.0,
                        "path": "geometry.displacement_bound_m"}]}
    e = epoch(0.5, geometry={"displacement_bound_m": 13.9})
    lines, ok, failures = verify_lines([line], e)
    assert ok is False
    assert lines[0]["text"] == FALLBACK_TEXT
    assert "geometry.displacement_bound_m" in failures[0]
    assert "118" in failures[0] and "13.9" in failures[0]


def test_an_absent_path_is_a_failure_not_a_pass():
    line = {"speaker": SPEAKER, "tone": "alert", "text": "Match likelihood 0.05.",
            "claims": [{"text": "0.05", "value": 0.05, "path": "terrain.match_likelihood"}]}
    lines, ok, failures = verify_lines([line], epoch(0.5))
    assert ok is False and lines[0]["text"] == FALLBACK_TEXT
    assert "absent from epoch" in failures[0]


def test_an_unclaimed_numeral_is_caught_even_when_every_claim_checks_out():
    """The half of §14 a claim list alone cannot enforce."""
    line = {"speaker": SPEAKER, "tone": "alert",
            "text": "Confidence 0.50, and 42 satellites went dark.",
            "claims": [{"text": "0.50", "value": 0.5, "path": "confidence"}]}
    lines, ok, failures = verify_lines([line], epoch(0.5))
    assert ok is False and lines[0]["text"] == FALLBACK_TEXT
    assert "42" in failures[0]


def test_source_claims_are_not_dug():
    line = {"speaker": SPEAKER, "tone": "alert",
            "text": "Six GPS satellites got louder at the same instant, +8 dB.",
            "claims": [{"text": "+8 dB", "value": 8.0,
                        "source": "injector parameter, backend/missions.py recon"}]}
    lines, ok, failures = verify_lines([line], epoch(0.5))
    assert ok is True and failures == [] and lines[0] is line


def test_a_claim_with_neither_path_nor_source_is_refused():
    line = {"speaker": SPEAKER, "tone": "alert", "text": "It is 7 m.",
            "claims": [{"text": "7 m", "value": 7.0}]}
    lines, ok, failures = verify_lines([line], epoch(0.5))
    assert ok is False and lines[0]["text"] == FALLBACK_TEXT


def test_the_fallback_can_never_itself_fail_verification():
    lines, ok, failures = verify_lines(
        [{"speaker": SPEAKER, "tone": "alert", "text": FALLBACK_TEXT, "claims": []}], None)
    assert ok is True and failures == []
    assert numerals(FALLBACK_TEXT) == set()


def test_the_guide_withholds_a_line_when_the_epoch_disagrees():
    """End to end: the decision and the epoch handed to `step` do not match.

    This is the real failure mode -- a console that dug its numbers out of one
    epoch and rendered them beside another -- and the guide has to notice.
    """
    real = epoch(0.2, geometry={"displacement_bound_m": 118.0,
                                "excluded_sv": ["G05", "G19"],
                                "information_ratio": 0.80})
    arb, g = Arbitras(), Guide()
    g.step(arb.step(epoch(0.9)), epoch(0.9))
    d = arb.step(real)
    p = g.step(d, epoch(0.2, geometry={"displacement_bound_m": 13.9,
                                       "excluded_sv": [],
                                       "information_ratio": 1.0}))
    assert p["verified"] is False
    assert p["failures"], "a mismatch must be recorded for the banner"
    assert any(l["text"] == FALLBACK_TEXT for l in p["lines"])
    assert_all_numerals_claimed(p, "mismatch")


def test_named_satellites_are_a_claim_like_any_other_numeral():
    """A small exclusion set is spoken by name, so the ids carry a list claim."""
    g, arb = Guide(None), Arbitras()
    geometry = {"excluded_sv": ["G19", "R20"], "information_ratio": 0.91,
                "displacement_bound_m": 22.0}
    out = drive(g, arb, [epoch(0.9), epoch(0.4, geometry=geometry)])
    p = out[1][1]
    assert p["verified"], p["failures"]
    named = [l for l in p["lines"] if "G19" in l["text"]]
    assert named, [l["text"] for l in p["lines"]]
    claim = named[0]["claims"][0]
    assert claim["path"] == "geometry.excluded_sv"
    assert claim["value"] == ["G19", "R20"]
    assert_all_numerals_claimed(p, "named satellites")

    # ...and the list claim is a real check, not decoration.
    lines, ok, failures = verify_lines(
        named[:1], epoch(0.4, geometry=dict(geometry, excluded_sv=["G19"])))
    assert ok is False and lines[0]["text"] == FALLBACK_TEXT


def test_uncovered_and_numerals_are_token_level_not_substring():
    """'6' is not covered by a claim that happens to read '0.65'."""
    assert numerals("0.65 and 6 and G19") == {"0.65", "6", "19"}
    line = {"text": "6 satellites", "claims": [{"text": "0.65"}]}
    assert uncovered(line) == {"6"}


# ------------------------------------------------------------- the replay

def test_full_replay_produces_a_sane_number_of_checkpoints(capsys):
    """A whole real stream through Arbitras + Guide.

    The number is the point: too few and the guide is asleep through the
    attack, too many and it is a wall of text nobody can read at replay speed.
    """
    path = next((p for p in ("out/logistics.jsonl", "out/demo.jsonl")
                 if os.path.exists(p)), None)
    if path is None:
        pytest.skip("no replay stream in out/; run python -m backend.demo")
    from console import missions
    mission = missions.as_dict(missions.get("logistics"))

    arb, g = Arbitras(), Guide(mission)
    epochs = speaking = interjections = 0
    kinds = {}
    with open(path) as fh:
        for raw in fh:
            e = json.loads(raw)
            p = g.step(arb.step(e), e)
            epochs += 1
            if not p["lines"]:
                assert p["checkpoint"] is None
                continue
            speaking += 1
            cp = p["checkpoint"]
            if cp is None:
                interjections += 1
            else:
                kinds[cp["kind"]] = kinds.get(cp["kind"], 0) + 1
                cap = INTRO_MAX_LINES if cp["kind"] == "intro" else MAX_LINES
                assert 1 <= len(p["lines"]) <= cap
            assert p["verified"], p["failures"]
    g.finish()

    with capsys.disabled():
        print(f"\n{path}: {epochs} epochs -> {speaking} speaking "
              f"({interjections} interjections) {kinds}")

    assert epochs > 100
    assert 4 <= speaking <= 40, f"{speaking} speaking epochs over {epochs}"
    assert speaking < epochs / 20, "a line every twentieth epoch is not a replay"
    assert set(kinds) <= set(KINDS)
    assert interjections <= sum(kinds.values()), (
        "unscripted events outnumber the script; `total` would mislead")


def test_generated_lines_keep_the_house_voice():
    """DIALOGUE.md: 2-3 sentences, <= 220 characters, one idea per line.

    `Guide(None)` so that every line under test is one this module wrote --
    mission prose and script lines are their author's to keep short.
    """
    path = next((p for p in ("out/logistics.jsonl", "out/demo.jsonl")
                 if os.path.exists(p)), None)
    if path is None:
        pytest.skip("no replay stream in out/")
    arb, g = Arbitras(), Guide(None)
    longest = 0
    with open(path) as fh:
        for raw in fh:
            e = json.loads(raw)
            for line in g.step(arb.step(e), e)["lines"]:
                longest = max(longest, len(line["text"]))
                assert len(line["text"]) <= 220, line["text"]
                assert line["tone"] in TONES
                assert line["text"] == line["text"].strip()
    for line in g.finish()["lines"]:
        assert len(line["text"]) <= 220, line["text"]
    assert longest > 80, "lines this short are not saying the mechanism"


def test_waypoint_lines_stay_inside_the_house_voice():
    """The generated arrival line is ours, so it is held to the same rule."""
    g, arb = Guide(ROUTE_MISSION), Arbitras()
    out = drive(g, arb, [epoch(0.9)] * 60)
    said = [l for _, p in out if p["checkpoint"]
            and p["checkpoint"]["kind"] == "waypoint" for l in p["lines"]]
    assert said
    for line in said:
        assert len(line["text"]) <= 220 and line["tone"] in TONES


def test_a_numeral_in_a_prop_label_is_sourced_like_any_other():
    """"OP-1" is a digit on the screen, and §14 does not exempt a place name."""
    mission = dict(ROUTE_MISSION,
                   props=[{"e": 100.0, "n": 0.0, "label": "OP-1 · LOOKOUT"}])
    g, arb = Guide(mission), Arbitras()
    out = drive(g, arb, [epoch(0.9)] * 60)
    hit = [p for _, p in out if p["checkpoint"]
           and p["checkpoint"]["kind"] == "waypoint"]
    assert len(hit) == 1 and hit[0]["checkpoint"]["label"] == "OP-1"
    assert "OP-1" in hit[0]["lines"][0]["text"]
    assert hit[0]["verified"], hit[0]["failures"]
    assert_all_numerals_claimed(hit[0], "prop label")
    assert any("source" in c for c in hit[0]["lines"][0]["claims"])


# --------------------------------------------------------------- no repeats

def test_the_guide_never_says_the_same_thing_twice():
    """A generated line and a scripted one can be the same thought.

    RECON epoch 84 scripts the recovery-gating sentence and the generated
    recovery line said it too -- word for word, five seconds apart on screen.
    The guard is general rather than a patch on that pair, so this asserts the
    property over every stream on the machine: no two lines share a gist.
    """
    ran = 0
    for name, path, mission in streams():
        guide, arb = Guide(mission), Arbitras()
        first: dict = {}
        for e in load_stream(path):
            for ln in guide.step(arb.step(e), e)["lines"]:
                g = _gist(ln["text"])
                assert g not in first, (
                    f"{name}: repeated at epoch {ln['epoch']} "
                    f"(first said at {first[g]}): {ln['text'][:70]}"
                )
                first[g] = ln["epoch"]
        for ln in guide.finish()["lines"]:
            g = _gist(ln["text"])
            assert g not in first, f"{name}: closing line repeats epoch {first[g]}"
            first[g] = "end"
        # A guard that swallowed everything would also pass the assertions above.
        assert len(first) >= 15, f"{name}: only {len(first)} lines -- guard too greedy?"
        ran += 1
    if not ran:
        pytest.skip("no streams in out/; run `python -m backend.missions`")


def test_a_waypoint_entry_is_reported_as_a_waypoint():
    """A script entry at a waypoint must not demote the checkpoint to an event.

    The vehicle IS at the place, and the box prints the kind beside the label:
    `OP-2 EVENT` under a scout standing at OP-2 is simply wrong.
    """
    checked = 0
    for name, mission in mission_dicts().items():
        by_epoch = {c["epoch"]: c for c in Guide(mission).checkpoints()}
        for entry in mission.get("guide", []):
            if entry.get("kind") != "waypoint":
                continue
            cp = by_epoch.get(entry["epoch"])
            assert cp is not None, f"{name}: no checkpoint at {entry['epoch']}"
            assert cp["kind"] in ("waypoint", "intro"), (
                f"{name}: {entry.get('label')} at {entry['epoch']} "
                f"reported as {cp['kind']!r}"
            )
            checked += 1
    assert checked >= 6, f"only {checked} waypoint entries seen -- test is vacuous"
