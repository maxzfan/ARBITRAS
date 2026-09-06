"""The ARBITRAS guide's script -- console/web/DIALOGUE.md §3 and Revision 2.

Every entry is a CHECKPOINT: a `kind` (`waypoint` | `event`) and a `label` the
box prints as "CHECKPOINT n / N · <label>". A waypoint checkpoint sits on a prop
the operator can watch the vehicle reach, at the epoch the vehicle ARRIVES
there; an event checkpoint sits on something the stream actually did -- a beat,
a credential change -- or closes the run.

Arrival is not `arc_length / speed_m_per_epoch`. That is where the vehicle would
be if nothing ever happened to it, and every mission halts or slows under attack
(`index.html` accrues `s += speed_m_per_epoch * gain[state]` per epoch), so the
unimpeded epoch fires while the vehicle is still short of the prop. The arrival
epochs come from `dialogue.waypoint_checkpoints(mission, mission_states(...))`,
which replays the mission's own stream for the per-epoch gain -- the same
computation the Guide schedules against, so a scripted waypoint and the
generated one are ONE checkpoint rather than two.

An event checkpoint, by contrast, may not be nudged anywhere: its `path` claims
are verified against the epoch it is emitted on, and the same number is not true
fifty epochs later.

The contract that matters here is design.md §14: nothing numeric reaches the
screen that cannot be sourced. Every numeral in a line's `text` must be covered
by a claim, every claim carries exactly one of `path` or `source`, and every
`path` claim must actually resolve -- and agree -- against the real epoch of
that mission's stream it is pinned to. A path that does not resolve is not a
harmless typo: the runtime verifier suppresses the line, so the script silently
loses it.
"""
import json
import math
import re
from pathlib import Path

import pytest

from console import missions
from console.arbitras import dialogue
from console.arbitras.explain import _dig

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "out"

# DIALOGUE.md "Voice": 2-3 sentences, <= 220 characters, one idea per line.
CHAR_BUDGET = 220
TONES = {"calm", "alert", "bad", "good", "act"}
KINDS = {"waypoint", "event"}
# DIALOGUE.md Revision 2 pace: the replay never pauses, so the gap between two
# checkpoints is time with nothing new said. 150 epochs is 30 s at the 5
# epochs/s the console replays at (console/server.py) -- a ceiling on dead
# stretches, not a target.
MAX_GAP_EPOCHS = 150
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


def _arc_length(route, e, n):
    """Arc length along `route` of the closest point to (e, n), and its offset.

    Returned together so the caller can assert the prop really is on the route
    rather than assume it.
    """
    best_d, best_s, acc = float("inf"), 0.0, 0.0
    for (e0, n0), (e1, n1) in zip(route, route[1:]):
        dx, dy = e1 - e0, n1 - n0
        seg = math.hypot(dx, dy)
        if seg == 0:
            continue
        t = min(max(((e - e0) * dx + (n - n0) * dy) / (seg * seg), 0.0), 1.0)
        d = math.hypot(e - (e0 + t * dx), n - (n0 + t * dy))
        if d < best_d:
            best_d, best_s = d, acc + t * seg
        acc += seg
    return best_s, best_d


def waypoint_table(m):
    """{arrival epoch: prop label} -- the CORRECTED table of DIALOGUE.md
    Revision 2, recomputed rather than copied out of it.

    Deliberately NOT `arc_length / speed`: arrival is the first epoch accrued
    progress reaches the prop, which needs the per-epoch gain and therefore the
    stream. `waypoint_checkpoints` shortens the prop label for the box, so map
    it back to the label the script has to carry.
    """
    md = missions.as_dict(m)
    states = dialogue.mission_states(md)
    assert states is not None, (
        f"{m.name}: {m.stream} is not on disk, so arrival cannot be replayed")
    full = {dialogue._short(p["label"]): p["label"] for p in m.props}
    table = {}
    for w in dialogue.waypoint_checkpoints(md, states):
        assert w["offset_m"] < 0.5, (
            f"{m.name}: prop {w['label']} sits {w['offset_m']:.2f} m off the route")
        table[w["epoch"]] = full[w["label"]]
    return table


def credential_changes(name):
    """Epochs at which `credential_status` differs from the epoch before."""
    out, last = set(), None
    for i, ep in enumerate(STREAMS[name]):
        cur = ep.get("credential_status")
        if i and cur != last:
            out.add(i)
        last = cur
    return out


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
def test_every_checkpoint_has_a_kind_and_a_label(m):
    """The box prints "CHECKPOINT n / N · <label>"; both halves are required."""
    for entry in m.guide:
        assert entry.get("kind") in KINDS, (
            f"{m.name} epoch {entry['epoch']}: kind {entry.get('kind')!r} is "
            f"not one of {sorted(KINDS)}")
        label = entry.get("label")
        assert isinstance(label, str) and label.strip(), (
            f"{m.name} epoch {entry['epoch']}: empty label")


@pytest.mark.parametrize("m", MISSIONS, ids=lambda m: m.name)
def test_props_lie_on_the_route(m):
    """The corrected table is quoted for props lying exactly on the route.

    A prop that does not lie on it is not a waypoint on it, and its arc length
    is not the distance the vehicle covers to reach it. Checked without the
    stream so it holds even where out/ is empty.
    """
    for prop in m.props:
        _s, off = _arc_length(m.route_enu, prop["e"], prop["n"])
        assert off < 0.5, (
            f"{m.name}: prop {prop['label']} sits {off:.2f} m off the route")


@pytest.mark.skipif(not STREAMS, reason="out/ is empty -- run `python -m backend.missions` first")
@pytest.mark.parametrize("m", MISSIONS, ids=lambda m: m.name)
def test_waypoint_checkpoints_sit_on_their_prop(m):
    """A waypoint checkpoint is a place, at the epoch the vehicle ARRIVES there.

    Epoch = `dialogue.waypoint_checkpoints(mission, mission_states(mission))`,
    which accrues `speed_m_per_epoch * gain[state]` off the mission's own
    stream, and the label is the prop's own label -- so what the box names and
    what the operator watches the vehicle reach are the same thing, at the same
    moment. Under the old `arc / speed` pinning they were not: the line fired
    tens of epochs before the vehicle got there.
    """
    if m.name not in STREAMS:
        pytest.skip(f"{m.stream} not on disk")
    table = waypoint_table(m)
    seen = set()
    for entry in m.guide:
        if entry["kind"] != "waypoint":
            continue
        ep = entry["epoch"]
        assert ep in table, (
            f"{m.name} epoch {ep} is a waypoint checkpoint but the vehicle "
            f"arrives at no prop there; the arrival table is {table}")
        assert entry["label"] == table[ep], (
            f"{m.name} epoch {ep}: label {entry['label']!r} is not the prop's "
            f"own label {table[ep]!r}")
        seen.add(ep)
    assert 0 in seen, f"{m.name}: departure is not a waypoint checkpoint"
    assert len(seen) >= 2, f"{m.name}: the run reaches more places than it names"


@pytest.mark.skipif(not STREAMS, reason="out/ is empty -- run `python -m backend.missions` first")
@pytest.mark.parametrize("m", MISSIONS, ids=lambda m: m.name)
def test_a_place_is_never_two_checkpoints(m):
    """A scripted waypoint and the checkpoint the Guide generates for the same
    prop must be ONE checkpoint.

    `Guide._build_schedule` keys slots by epoch and merges what shares one. A
    script entry pinned to the unimpeded epoch therefore does NOT merge with the
    arrival checkpoint for the same prop: the operator is walked past the place
    twice, and the first time the vehicle is not there yet -- which is the whole
    reason waypoints were chosen as checkpoints. So every waypoint entry sits on
    an epoch the generated table gives that same label.
    """
    if m.name not in STREAMS:
        pytest.skip(f"{m.stream} not on disk")
    md = missions.as_dict(m)
    generated = dialogue.waypoint_checkpoints(md, dialogue.mission_states(md))
    arrivals = {}
    for w in generated:
        arrivals.setdefault(w["label"], []).append(w["epoch"])
    for entry in m.guide:
        if entry["kind"] != "waypoint":
            continue
        short = dialogue._short(entry["label"])
        assert short in arrivals, (
            f"{m.name}: waypoint entry {entry['label']!r} names no prop the "
            f"vehicle reaches; arrivals are {arrivals}")
        assert entry["epoch"] in arrivals[short], (
            f"{m.name}: {entry['label']!r} is scripted at epoch {entry['epoch']} "
            f"but the vehicle arrives at {arrivals[short]}; that is two "
            f"checkpoints for one place, the first of them before it gets there")


@pytest.mark.parametrize("m", MISSIONS, ids=lambda m: m.name)
def test_the_original_beat_entries_kept_their_epochs(m):
    """The one constraint this rework may not break.

    A guide entry whose text is verified against the epoch it fires on cannot be
    moved: `path` claims are dug out of THAT epoch and the same number is not
    true later, so a moved line is silently suppressed at runtime. Every beat
    that carried a checkpoint before is still a checkpoint, on its own epoch.
    """
    epochs = {e["epoch"] for e in m.guide}
    for beat_epoch, caption in m.beats:
        assert beat_epoch in epochs, (
            f"{m.name}: beat {beat_epoch} ({caption[:40]}...) lost its "
            f"checkpoint; guide epochs are {sorted(epochs)}")


@pytest.mark.parametrize("m", MISSIONS, ids=lambda m: m.name)
def test_guide_covers_the_arc(m):
    """Departure, the attack, and an ending -- never a script that stops at the halt."""
    epochs = [e["epoch"] for e in m.guide]
    assert epochs[0] == 0, "the arc opens on departure"
    assert epochs[-1] >= max(i for i, _ in m.beats), (
        "and never stops before the last beat; LOGISTICS and COMBAT run on past "
        "it, because their routes finish long before their streams do")
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
def test_event_checkpoints_name_something_that_happened(m):
    """"Do not invent events." An event checkpoint has to be a beat, a
    credential change on the real stream, or the line that closes the run."""
    if m.name not in STREAMS:
        pytest.skip(f"{m.stream} not on disk")
    beats = {i for i, _ in m.beats}
    credential = credential_changes(m.name)
    last = m.guide[-1]["epoch"]
    for entry in m.guide:
        if entry["kind"] != "event":
            continue
        ep = entry["epoch"]
        assert ep in beats or ep in credential or ep == last, (
            f"{m.name} epoch {ep} ({entry['label']!r}) is an event checkpoint "
            f"on no beat, no credential change and not the closing line")


@pytest.mark.skipif(not STREAMS, reason="out/ is empty -- run `python -m backend.missions` first")
@pytest.mark.parametrize("m", MISSIONS, ids=lambda m: m.name)
def test_the_script_is_paced_to_the_end_of_the_stream(m):
    """DIALOGUE.md Revision 2 "Alignment": the replay ends when the STREAM ends,
    not when the route does, so that is the clock the script is paced against.
    The last checkpoint lands inside the stream and no stretch of it is silent
    for longer than MAX_GAP_EPOCHS."""
    if m.name not in STREAMS:
        pytest.skip(f"{m.stream} not on disk")
    n = len(STREAMS[m.name])
    epochs = [e["epoch"] for e in m.guide]
    assert epochs[-1] < n, (
        f"{m.name}: last checkpoint {epochs[-1]} is outside the {n}-epoch stream")
    gaps = [(a, b, b - a) for a, b in zip(epochs, epochs[1:])]
    gaps.append((epochs[-1], n - 1, n - 1 - epochs[-1]))     # the run-out
    worst = max(gaps, key=lambda g: g[2])
    assert worst[2] <= MAX_GAP_EPOCHS, (
        f"{m.name}: nothing is said between epoch {worst[0]} and {worst[1]} "
        f"({worst[2]} epochs)")


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
