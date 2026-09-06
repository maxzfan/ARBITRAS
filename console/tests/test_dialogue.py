"""The guide's dialogue layer: shape, firing rules and design.md §14.

The test that matters is `test_every_numeral_on_screen_is_claimed`. Everything
else here guards the contract in console/web/DIALOGUE.md; that one guards the
promise the pitch is built on -- no number reaches the operator that cannot be
sourced. It scans the rendered text with a regex over digits and allow-lists
nothing.
"""
import json
import os
import re

import pytest

from console.arbitras.dialogue import (
    FALLBACK_TEXT,
    GATE_REASONS,
    MAX_LINES,
    INTRO_MAX_LINES,
    SPEAKER,
    TONES,
    Guide,
    numerals,
    uncovered,
    verify_lines,
)
from console.arbitras.machine import Arbitras
from console.arbitras.states import TrustState

# Same shape test_machine.py uses, so a failure here is comparable to one there.
def epoch(confidence, credential="VALID", **kw):
    e = {
        "timestamp": "2026-08-20T00:00:00Z",
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
        {"epoch": 1, "lines": [
            {"text": "The carry-off starts here: six GPS satellites captured at +2 dB.",
             "tone": "alert",
             "claims": [{"text": "+2 dB", "value": 2.0,
                         "source": "injector parameter, backend/missions.py logistics"}]},
        ]},
        {"epoch": 4, "lines": [{"text": "The attack ends.", "tone": "calm"}]},
    ],
}


def drive(guide, arb, epochs):
    """-> [(index, payload)] for every epoch, gated or not."""
    return [(i, guide.step(arb.step(e), e)) for i, e in enumerate(epochs)]


# ------------------------------------------------------------- wire shape

def test_payload_matches_the_contract_shape():
    g, arb = Guide(), Arbitras()
    p = g.step(arb.step(epoch(0.9)), epoch(0.9))
    assert set(p) == {"lines", "gate", "gate_reason", "verified", "failures"}
    assert p["gate"] is True and p["gate_reason"] == "intro"
    for line in p["lines"]:
        assert set(line) == {"speaker", "text", "claims", "tone"}
        assert line["speaker"] == SPEAKER
        assert line["tone"] in TONES
        for c in line["claims"]:
            assert ("path" in c) != ("source" in c), "exactly one of path/source"


def test_most_epochs_are_silent():
    g, arb = Guide(), Arbitras()
    out = drive(g, arb, [epoch(0.9)] * 30)
    gated = [p for _, p in out if p["gate"]]
    assert len(gated) == 1, "only the intro should speak on a flat clean run"
    for _, p in out[1:]:
        assert p["lines"] == [] and p["gate"] is False and p["gate_reason"] is None


# ---------------------------------------------------------- firing rules

def test_gate_reasons_are_exactly_the_contract_five():
    """DIALOGUE.md §2. No sixth reason may leak onto the wire."""
    g, arb = Guide(SCRIPT_MISSION), Arbitras()
    seen = set()
    for _, p in drive(g, arb, [epoch(0.9), epoch(0.9, "UNVERIFIED"),
                               epoch(0.2), epoch(0.9), epoch(0.9)]):
        if p["gate"]:
            seen.add(p["gate_reason"])
        else:
            assert p["gate_reason"] is None
    seen.add(g.finish()["gate_reason"])
    assert seen <= set(GATE_REASONS)
    assert {"intro", "beat", "state", "end"} <= seen


def test_intro_fires_once_and_only_on_the_first_epoch():
    g, arb = Guide(), Arbitras()
    out = drive(g, arb, [epoch(0.9)] * 5)
    assert out[0][1]["gate_reason"] == "intro"
    assert all(p["gate_reason"] != "intro" for _, p in out[1:])


def test_state_change_gates_and_says_what_the_vehicle_does():
    g, arb = Guide(SCRIPT_MISSION), Arbitras()
    out = drive(g, arb, [epoch(0.9), epoch(0.9), epoch(0.2)])
    p = out[2][1]
    assert p["gate"] and p["gate_reason"] == "state"
    text = " ".join(l["text"] for l in p["lines"])
    assert SCRIPT_MISSION["behaviour"]["SURRENDERED"] in text


def test_behaviour_falls_back_to_explain_when_no_mission():
    from console.arbitras.explain import BEHAVIOUR
    g, arb = Guide(None), Arbitras()
    out = drive(g, arb, [epoch(0.9), epoch(0.9), epoch(0.2)])
    text = " ".join(l["text"] for l in out[2][1]["lines"])
    assert BEHAVIOUR[TrustState.SURRENDERED] in text


def test_credential_change_gates_but_not_on_the_first_epoch():
    g, arb = Guide(), Arbitras()
    # First epoch: VALID is merely learned, not announced.
    out = drive(g, arb, [epoch(0.9, "VALID"), epoch(0.9, "VALID"),
                         epoch(0.9, "PENDING"), epoch(0.9, "PENDING")])
    assert out[0][1]["gate_reason"] == "intro"
    assert out[1][1]["gate"] is False
    assert out[2][1]["gate_reason"] == "credential"
    assert out[3][1]["gate"] is False, "a steady status is not a transition"


def test_stale_epochs_do_not_invent_a_credential_transition():
    """A missing epoch carries the last status forward; that is not a change."""
    g, arb = Guide(), Arbitras()
    drive(g, arb, [epoch(0.9)])
    for _ in range(6):
        p = g.step(arb.step(None), None)
        assert p["gate_reason"] in (None, "state")


def test_a_beat_never_fires_twice():
    g, arb = Guide(SCRIPT_MISSION), Arbitras()
    out = drive(g, arb, [epoch(0.9)] * 12)
    beats = [i for i, p in out if p["gate_reason"] == "beat"]
    assert len(beats) == 2, f"two script entries, two beats, got {beats}"
    assert beats == sorted(beats)
    # Every scripted line is spoken exactly once across the whole run.
    spoken = [l["text"] for _, p in out for l in p["lines"]]
    for entry in SCRIPT_MISSION["guide"]:
        for line in entry["lines"]:
            assert spoken.count(line["text"]) == 1


def test_beat_is_ordered_first_and_the_gate_caps_at_three_lines():
    g, arb = Guide(SCRIPT_MISSION), Arbitras()
    # Epoch 1 fires all three of beat, state and credential at once.
    out = drive(g, arb, [epoch(0.9, "VALID"), epoch(0.05, "UNVERIFIED")])
    p = out[1][1]
    assert p["gate_reason"] == "beat"
    assert p["lines"][0]["text"] == SCRIPT_MISSION["guide"][0]["lines"][0]["text"]
    assert len(p["lines"]) == MAX_LINES
    # The opening gate is the documented exception: the guide introduces
    # itself first and is allowed one extra line to do it (DIALOGUE.md §2).
    assert all(
        len(q["lines"]) <= (INTRO_MAX_LINES if q["gate_reason"] == "intro"
                            else MAX_LINES)
        for _, q in out
    )


def test_finish_speaks_once():
    g = Guide()
    first, second = g.finish(), g.finish()
    assert first["gate"] and first["gate_reason"] == "end"
    assert second["gate"] is False and second["lines"] == []


# ------------------------------------------------------- mission handling

def test_guide_with_no_mission_at_all():
    """`Guide(None)` is the no-mission SSE connection. It must still speak."""
    g, arb = Guide(None), Arbitras()
    out = drive(g, arb, [epoch(0.9), epoch(0.2), epoch(0.2, "REVOKED")])
    reasons = [p["gate_reason"] for _, p in out if p["gate"]]
    assert reasons == ["intro", "state", "credential"]
    assert all(p["verified"] for _, p in out)


def test_mission_without_a_guide_field_is_not_an_error():
    """Track F is adding DIALOGUE.md §3 concurrently; absence is normal."""
    mission = {k: v for k, v in SCRIPT_MISSION.items() if k != "guide"}
    g, arb = Guide(mission), Arbitras()
    out = drive(g, arb, [epoch(0.9)] * 8)
    assert [p["gate_reason"] for _, p in out if p["gate"]] == ["intro"]
    assert all(p["verified"] for _, p in out)


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

def test_full_replay_produces_a_sane_number_of_gates(capsys):
    """A whole real stream through Arbitras + Guide.

    The number is the point: too few and the guide is asleep through the
    attack, too many and it is a wall of text that no operator clicks through.
    """
    path = next((p for p in ("out/logistics.jsonl", "out/demo.jsonl")
                 if os.path.exists(p)), None)
    if path is None:
        pytest.skip("no replay stream in out/; run python -m backend.demo")
    from console import missions
    mission = missions.as_dict(missions.get("logistics"))

    arb, g = Arbitras(), Guide(mission)
    epochs = gates = 0
    reasons = {}
    with open(path) as fh:
        for raw in fh:
            e = json.loads(raw)
            p = g.step(arb.step(e), e)
            epochs += 1
            if p["gate"]:
                gates += 1
                reasons[p["gate_reason"]] = reasons.get(p["gate_reason"], 0) + 1
                cap = (INTRO_MAX_LINES if p["gate_reason"] == "intro"
                       else MAX_LINES)
                assert 1 <= len(p["lines"]) <= cap
                assert p["verified"], p["failures"]
    end = g.finish()
    if end["gate"]:
        gates += 1
        reasons["end"] = 1

    with capsys.disabled():
        print(f"\n{path}: {epochs} epochs -> {gates} gates {reasons}")

    assert epochs > 100
    assert 4 <= gates <= 40, f"{gates} gates over {epochs} epochs"
    assert gates < epochs / 20, "a gate every twentieth epoch is not a replay"
    assert set(reasons) <= set(GATE_REASONS)


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
