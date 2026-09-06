"""The ARBITRAS guide's script -- console/web/DIALOGUE.md §3.

The contract that matters here is design.md §14: nothing numeric reaches the
screen that cannot be sourced. Every numeral in a line's `text` must be covered
by a claim, every claim carries exactly one of `path` or `source`, and every
`path` claim must actually resolve -- and agree -- against the real epoch of
that mission's stream it is pinned to. A path that does not resolve is not a
harmless typo: the runtime verifier suppresses the line, so the script silently
loses it.
"""
import json
import re
from pathlib import Path

import pytest

from console import missions
from console.arbitras.explain import _dig

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "out"

# DIALOGUE.md "Voice": 2-3 sentences, <= 220 characters, one idea per line.
CHAR_BUDGET = 220
TONES = {"calm", "alert", "bad", "good", "act"}
# Any run of digits, with an optional decimal part: "0.80", "118", "2.9".
NUMERAL = re.compile(r"\d+(?:\.\d+)?")


def _streams():
    """{mission: [epoch, ...]} for the streams actually on disk."""
    out = {}
    for name in missions.ORDER:
        p = ROOT / missions.get(name).stream
        if p.exists():
            out[name] = [json.loads(line) for line in p.read_text().splitlines() if line]
    return out


STREAMS = _streams()
MISSIONS = [missions.get(n) for n in missions.ORDER]


def _lines(m):
    for entry in m.guide:
        for line in entry["lines"]:
            yield entry, line


def _claims(m):
    for entry, line in _lines(m):
        for claim in line.get("claims", []):
            yield entry, line, claim


# --------------------------------------------------------------- shape

@pytest.mark.parametrize("m", MISSIONS, ids=lambda m: m.name)
def test_every_mission_has_a_guide(m):
    assert m.guide, f"{m.name} has no guide script"
    for entry in m.guide:
        assert isinstance(entry["epoch"], int)
        assert 1 <= len(entry["lines"]) <= 3, "contract §3: 1-3 lines per entry"


@pytest.mark.parametrize("m", MISSIONS, ids=lambda m: m.name)
def test_guide_entries_are_ordered_and_unique(m):
    epochs = [e["epoch"] for e in m.guide]
    assert epochs == sorted(epochs)
    assert len(set(epochs)) == len(epochs), "an epoch fires at most one entry"


@pytest.mark.parametrize("m", MISSIONS, ids=lambda m: m.name)
def test_guide_is_anchored_on_real_beats(m):
    """Each entry sits on a beat epoch: the guide is the rewrite of `beats`."""
    beats = {i for i, _ in m.beats}
    for entry in m.guide:
        assert entry["epoch"] in beats, f"{m.name} epoch {entry['epoch']} is not a beat"


@pytest.mark.parametrize("m", MISSIONS, ids=lambda m: m.name)
def test_guide_covers_the_arc(m):
    """Departure, the attack, and an ending -- never a script that stops at the halt."""
    epochs = [e["epoch"] for e in m.guide]
    assert epochs[0] == 0, "the arc opens on departure"
    assert epochs[-1] == max(i for i, _ in m.beats), "and ends on the last beat"
    assert len(m.guide) >= 6, "the whole arc, not just the attack"


@pytest.mark.parametrize("m", MISSIONS, ids=lambda m: m.name)
def test_lines_are_within_the_character_budget(m):
    for _, line in _lines(m):
        assert line["text"].strip(), "no empty line"
        assert len(line["text"]) <= CHAR_BUDGET, (
            f"{m.name}: {len(line['text'])} chars > {CHAR_BUDGET}: {line['text'][:60]}...")
        assert line["tone"] in TONES, line["tone"]


@pytest.mark.parametrize("m", MISSIONS, ids=lambda m: m.name)
def test_guide_survives_as_dict(m):
    d = missions.as_dict(m)
    assert d["guide"] == [dict(e) for e in m.guide]
    json.dumps(d)                                     # rides the wire as JSON
    d["guide"][0]["lines"][0]["text"] = "mutated"     # and is a copy
    assert m.guide[0]["lines"][0]["text"] != "mutated"


def test_the_default_is_empty_so_older_callers_do_not_break():
    assert missions.Mission.__dataclass_fields__["guide"].default == ()


# --------------------------------------------------- design.md §14: sourcing

@pytest.mark.parametrize("m", MISSIONS, ids=lambda m: m.name)
def test_every_numeral_on_screen_is_covered_by_a_claim(m):
    """design.md §14. The important one: no unsourced number reaches the screen."""
    for entry, line in _lines(m):
        covered = set()
        for claim in line.get("claims", []):
            covered |= set(NUMERAL.findall(claim["text"]))
        for numeral in NUMERAL.findall(line["text"]):
            assert numeral in covered, (
                f"{m.name} epoch {entry['epoch']}: {numeral!r} in the text is "
                f"covered by no claim -- {line['text']!r}")


@pytest.mark.parametrize("m", MISSIONS, ids=lambda m: m.name)
def test_every_claim_has_exactly_one_of_path_or_source(m):
    for entry, _, claim in _claims(m):
        has = [k for k in ("path", "source") if claim.get(k)]
        assert has and len(has) == 1, (
            f"{m.name} epoch {entry['epoch']}: claim {claim} needs exactly one "
            f"of path/source")
        assert isinstance(claim["value"], (int, float))
        assert NUMERAL.search(claim["text"]), "a claim covers a number"


@pytest.mark.parametrize("m", MISSIONS, ids=lambda m: m.name)
def test_source_claims_name_a_file_that_exists(m):
    """A `source` is a scenario fact; it is verified only in that its source is real."""
    for entry, _, claim in _claims(m):
        if "source" not in claim:
            continue
        named = re.findall(r"[\w./_-]+\.(?:md|py|json)", claim["source"])
        assert named, f"{m.name} epoch {entry['epoch']}: source names no file: {claim['source']}"
        for f in named:
            assert (ROOT / f).exists(), f"{m.name}: {f} does not exist"


# ------------------------------------------- the paths against the real stream

@pytest.mark.skipif(not STREAMS, reason="out/ is empty -- run `python -m backend.missions` first")
@pytest.mark.parametrize("m", MISSIONS, ids=lambda m: m.name)
def test_every_epoch_exists_in_the_stream(m):
    if m.name not in STREAMS:
        pytest.skip(f"{m.stream} not on disk -- run `python -m backend.missions --mission {m.name}`")
    n = len(STREAMS[m.name])
    for entry in m.guide:
        assert 0 <= entry["epoch"] < n, (
            f"{m.name} epoch {entry['epoch']} outside the {n}-epoch stream")


@pytest.mark.skipif(not STREAMS, reason="out/ is empty -- run `python -m backend.missions` first")
@pytest.mark.parametrize("m", MISSIONS, ids=lambda m: m.name)
def test_every_path_claim_resolves_against_its_own_epoch(m):
    """The verifier suppresses a line whose path claim fails, so a wrong path
    costs the line silently. Check them here instead."""
    if m.name not in STREAMS:
        pytest.skip(f"{m.stream} not on disk -- run `python -m backend.missions --mission {m.name}`")
    stream = STREAMS[m.name]
    for entry, _, claim in _claims(m):
        if "path" not in claim:
            continue
        ep = stream[entry["epoch"]]
        actual = _dig(ep, claim["path"])
        assert actual is not None, (
            f"{m.name} epoch {entry['epoch']}: {claim['path']} absent from the epoch")
        if isinstance(actual, list):
            actual = len(actual)
        assert isinstance(actual, (int, float)) and not isinstance(actual, bool), (
            f"{m.name}: {claim['path']} is {actual!r}, not a number")
        assert float(actual) == pytest.approx(float(claim["value"]), abs=1e-9), (
            f"{m.name} epoch {entry['epoch']}: claimed {claim['value']} for "
            f"{claim['path']}, epoch says {actual}")


@pytest.mark.skipif(not STREAMS, reason="out/ is empty -- run `python -m backend.missions` first")
@pytest.mark.parametrize("m", MISSIONS, ids=lambda m: m.name)
def test_a_claims_display_text_rounds_to_its_value(m):
    """`text` is the rounded display of `value` (explain.py does the same). It
    may not be a different number."""
    if m.name not in STREAMS:
        pytest.skip(f"{m.stream} not on disk")
    for entry, _, claim in _claims(m):
        shown = NUMERAL.findall(claim["text"])
        assert len(shown) == 1, f"{m.name}: one number per claim, got {claim['text']!r}"
        got, want = float(shown[0]), abs(float(claim["value"]))
        assert got == pytest.approx(want, abs=0.5 * 10 ** -len(shown[0].split(".")[1])
                                    if "." in shown[0] else 0.5), (
            f"{m.name} epoch {entry['epoch']}: text {claim['text']!r} does not "
            f"round to value {claim['value']}")
