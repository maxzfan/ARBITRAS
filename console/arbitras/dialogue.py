"""The ARBITRAS guide: one `Decision` -> the lines the guide speaks.

`console/web/DIALOGUE.md` is the contract for this module. **Revision 2 at the
bottom of that file governs**: the gate is gone, the replay never pauses, and
dialogue is anchored at CHECKPOINTS the operator can count down. design.md §14
("Explanation layer, templated first", and the 22:30 explanation verifier) is
still the rule that governs every number in it.

This is the same job `console/arbitras/explain.py` does, said differently. The
explanation layer emits one headline and one dense detail paragraph; the guide
emits the same mechanism one thought at a time, in the order an operator can
follow. `explain.py` stays on the wire -- `test_server.py` and the verification
banner read it -- and nothing here replaces it. Both are console-side: nothing
in `backend/` produces or consumes `payload.dialogue`, and it is NOT part of
the design.md §5 contract.

Four rules from DIALOGUE.md govern every string below:

  - Operator language, not builder language. "The vehicle stops accepting
    waypoints," never "the state machine transitions to RESTRICTED."
  - ARBITRAS narrates and advises. It never commands motion (invariant 5 says
    DEGRADED is active; it does not say the trust layer drives).
  - **No numeral reaches the screen that cannot be sourced.** Every numeral in
    a line's `text` must be covered by a claim, and every claim carries either
    a `path` (dug out of this epoch and compared before display) or a `source`
    (a named scenario parameter). `verify_lines()` enforces both halves and
    REPLACES any line that fails; it does not merely flag it.
  - **Every line carries the epoch its claims were verified against** (`at`,
    `epoch`). The box prints them. Before revision 2 a line reading
    "confidence 0.91" stayed on screen while the strip moved to 0.92 with
    nothing saying the number was older than the instrument beside it.

## The checkpoint schedule, and why `total` is honest

`index / total` is only worth showing if `total` is known before the first line
and is still true at the last one. So the schedule is CLOSED and computed in
`__init__`, from the mission alone:

    1  intro          the first epoch of the connection
    n  waypoints      the epoch the vehicle ARRIVES at each prop -- accrued
                      progress (speed x the gain for the arbitrated state)
                      first reaching the prop's arc length along the route
       events         one per `mission["guide"]` entry, on its pinned epoch
    1  end            `Guide.finish()`

Slots sharing an epoch are one checkpoint (the epoch-0 waypoint and the
epoch-0 script entry are absorbed by the intro). `index` is the slot's position
in that schedule, not a running counter, so indices are strictly increasing,
never exceed `total`, and `total` is reached exactly at `end`.

Unscripted trust-state and credential changes are the one thing a mission
cannot schedule. They are spoken IMMEDIATELY, on their own epoch, with
`checkpoint: null` -- an interjection, not a checkpoint. That is the whole
trick: an interjection cannot inflate the denominator, so the count the
operator is reading is never revised mid-run. On the four mission streams there
are one to four of them, all recovery wobbles and the PENDING blip; the
scripted checkpoints carry the story.

An event checkpoint may NOT be moved off its epoch (DIALOGUE.md revision 2):
its `path` claims are verified against the epoch of emission and the same
number is not true ten epochs later. So a scripted event whose claims do not
hold at the epoch it comes due is not spoken at all -- silence, never a number
from somewhere else.
"""
from __future__ import annotations

import json
import math
import os
import re
from typing import Any, Optional

# `_dig` is the contract-path walk design.md §14 verification is defined in
# terms of. Importing it rather than copying it keeps one implementation of
# "what a contract path means" in the console, which is the whole point of
# having claims carry paths at all.
from .explain import (
    BEHAVIOUR,
    CREDENTIAL_PHRASE,
    FEATURE_PHRASE,
    SALIENCE_FLOOR,
    _dig,
)
from .machine import Arbitras, Decision
from .states import RECOVERY_EPOCHS, TrustState

SPEAKER = "ARBITRAS"

# DIALOGUE.md §1: 0-3 lines per checkpoint, empty on most epochs.
MAX_LINES = 3
# The opening checkpoint carries one more. The briefing is two thoughts and a
# mission's own opener is usually two; at a cap of three, one of the four is
# dropped in silence, and the line that loses is whichever sorts last.
INTRO_MAX_LINES = 4

# DIALOGUE.md §1: `tone` only colours the box.
TONES = frozenset({"calm", "alert", "bad", "good", "act"})

# DIALOGUE.md revision 2: `checkpoint.kind`.
KINDS = ("intro", "waypoint", "event", "end")

STATE_TONE = {
    TrustState.NOMINAL: "calm",
    TrustState.DEGRADED: "alert",
    TrustState.RESTRICTED: "alert",
    TrustState.SURRENDERED: "bad",
}

# RINEX satellite-id first letters (design.md §4, five constellations).
CONSTELLATION = {
    "G": "GPS", "E": "Galileo", "R": "GLONASS", "C": "BeiDou",
    "J": "QZSS", "S": "SBAS", "I": "NavIC",
}

# The credential half of confidence, said to an operator. `explain.py` has the
# four failure phrases; the guide also has to narrate the way back to VALID,
# which the explanation layer never needed because it only ever explains why
# authority is where it is.
CREDENTIAL_LINE = {
    "VALID": "Mission authorisation checks out: the disclosed key matches the chain and the signature over it holds.",
    "PENDING": "Mission authorisation is waiting on the next key disclosure. The last verified one is still inside its window.",
    "UNVERIFIED": CREDENTIAL_PHRASE["UNVERIFIED"],
    "EXPIRED": CREDENTIAL_PHRASE["EXPIRED"],
    "REVOKED": CREDENTIAL_PHRASE["REVOKED"],
}
CREDENTIAL_MEANS = {
    "VALID": "That is a separate question from signal quality, and it is now answered in our favour.",
    "PENDING": "This lasts exactly the disclosure lag. It is the protocol working, not an alarm.",
    "UNVERIFIED": "Authority is capped there until it verifies, however clean the satellites look.",
    "EXPIRED": "That overrides the signal entirely: a perfect fix is still not permission.",
    "REVOKED": "That overrides the signal entirely: a perfect fix is still not permission.",
}
CREDENTIAL_TONE = {
    "VALID": "good", "PENDING": "alert", "UNVERIFIED": "alert",
    "EXPIRED": "bad", "REVOKED": "bad",
}

# Checkpoint labels for the things a mission cannot name in advance. Operator
# language (DIALOGUE.md invariant 3): what the vehicle does, not what the state
# machine is called.
STATE_LABEL_DOWN = {
    TrustState.DEGRADED: "AUTHORITY REDUCED",
    TrustState.RESTRICTED: "CURRENT LEG ONLY",
    TrustState.SURRENDERED: "OPERATOR IN CONTROL",
}
STATE_LABEL_UP = "AUTHORITY RESTORED"
STATE_LABEL_FULL = "FULL AUTHORITY"
CREDENTIAL_LABEL = {
    "VALID": "AUTHORISATION VALID",
    "PENDING": "AWAITING KEY DISCLOSURE",
    "UNVERIFIED": "AUTHORISATION UNVERIFIED",
    "EXPIRED": "AUTHORISATION LAPSED",
    "REVOKED": "AUTHORISATION REVOKED",
}
INTRO_LABEL = "BRIEFING"
END_LABEL = "END OF REPLAY"
EVENT_LABEL = "MISSION BEAT"
LABEL_MAX = 32

# What replaces a line whose claim did not check out. Deliberately carries no
# numeral of its own, so the replacement can never itself fail verification.
FALLBACK_TEXT = (
    "I had a figure for you here and it did not check out against this epoch. "
    "I am withholding the line rather than showing a number I cannot source."
)

_NUMERAL = re.compile(r"\d+(?:\.\d+)?")


# --------------------------------------------------------------- primitives

def _line(text: str, tone: str = "calm", claims: Optional[list] = None) -> dict:
    return {
        "speaker": SPEAKER,
        "text": text,
        "claims": list(claims or []),
        "tone": tone if tone in TONES else "calm",
    }


def _path(text: str, value: Any, path: str) -> dict:
    """A measurement. Dug out of the epoch and compared before display."""
    return {"text": text, "value": value, "path": path}


def _source(text: str, value: Any, source: str) -> dict:
    """A scenario parameter. Not a measurement; the named source is the check."""
    return {"text": text, "value": value, "source": source}


def numerals(text: Any) -> set:
    """Every numeric token in a string, as it is written.

    Token-level, not substring-level, on purpose: comparing substrings would
    let a claim of "0.65" silently cover a "6" that came from somewhere else.
    """
    return set(_NUMERAL.findall(str(text or "")))


def uncovered(line: dict) -> set:
    """Numerals in a line's text that no claim on that line accounts for."""
    covered: set = set()
    for c in line.get("claims") or []:
        covered |= numerals(c.get("text"))
    return numerals(line.get("text")) - covered


def _borrowed(text: str, source: str) -> list:
    """`source` claims for numerals inside prose the guide did not write.

    Mission `behaviour` sentences, titles and prop labels live in
    `console/missions/__init__.py`; they are scenario parameters, which is
    exactly what DIALOGUE.md §1 defines a `source` claim to be. Naming the
    field keeps §14 honest without pretending a mission sentence is a
    measurement. Mission GUIDE lines get no such courtesy -- their author is
    expected to write the claims, and a script line with a bare number is
    withheld like any other unsourced figure.
    """
    return [_source(tok, tok, source) for tok in sorted(numerals(text))]


def _pretty(cls: Any) -> str:
    """Terrain class id -> something an operator reads. 'tree_cover' -> 'tree cover'."""
    return str(cls).replace("_", " ")


def _family(svs) -> str:
    """One constellation name if the whole exclusion set shares a prefix, else ''."""
    prefixes = {str(s)[0] for s in svs if s}
    if len(prefixes) == 1:
        return CONSTELLATION.get(prefixes.pop(), "")
    return ""


def clock(timestamp: Any) -> Optional[str]:
    """'2026-08-20T12:30:00Z' -> '12:30:00'. The `at` every line carries."""
    if not timestamp:
        return None
    s = str(timestamp)
    if "T" in s:
        s = s.split("T", 1)[1]
    for cut in ("Z", "+", "."):
        if cut in s:
            s = s.split(cut, 1)[0]
    return s or None


def _short(label: Any) -> str:
    """Prop label -> the head an operator reads. 'FOB · SUPPLY POINT' -> 'FOB'."""
    s = str(label or "").split("·")[0].strip()
    return s[:LABEL_MAX].strip()


# ------------------------------------------------ waypoints along the route

def route_points(mission: Optional[dict]) -> list:
    """The mission route as [(e, n)], from either the wire or registry shape."""
    route = (mission or {}).get("route")
    if route is None:
        route = (mission or {}).get("route_enu") or ()
    out: list = []
    for p in route:
        try:
            if isinstance(p, dict):
                out.append((float(p["e"]), float(p["n"])))
            else:
                out.append((float(p[0]), float(p[1])))
        except (TypeError, ValueError, KeyError, IndexError):
            return []
    return out


def project_on_route(route: list, point) -> tuple:
    """Nearest point on the polyline. -> (arc length in m, offset in m).

    The arc length is the distance the vehicle has to cover to reach the prop,
    which is what `waypoint_checkpoints` turns into an epoch. The offset is the
    check on that -- the contract's table is quoted for props lying exactly on
    the route, and a prop that does not lie on it is not a waypoint on it.
    """
    best = (float("inf"), 0.0)
    acc = 0.0
    pe, pn = float(point[0]), float(point[1])
    for (e0, n0), (e1, n1) in zip(route, route[1:]):
        de, dn = e1 - e0, n1 - n0
        seg = math.hypot(de, dn)
        if seg == 0.0:
            t = 0.0
        else:
            t = min(max(((pe - e0) * de + (pn - n0) * dn) / (seg * seg), 0.0), 1.0)
        qe, qn = e0 + t * de, n0 + t * dn
        off = math.hypot(pe - qe, pn - qn)
        if off < best[0]:
            best = (off, acc + t * seg)
        acc += seg
    return best[1], best[0]


def mission_states(mission: Optional[dict]) -> Optional[list]:
    """The trust state at every epoch of the mission's own stream, or None.

    DIALOGUE.md revision 2 (CORRECTED): a waypoint is where the vehicle IS, and
    the vehicle only moves at `speed_m_per_epoch * gain[state]` -- it halts in
    SURRENDERED. So arrival cannot be read off the route alone; the stream has
    to be replayed through `Arbitras` for the per-epoch gain. ~11 ms for a
    510-epoch mission, once per connection.

    None when the stream is not on this machine, which is not an error: the
    caller falls back to the unimpeded schedule.
    """
    path = (mission or {}).get("stream")
    if not path or not os.path.exists(path):
        return None
    arb = Arbitras()
    states: list = []
    try:
        with open(path) as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                states.append(arb.step(json.loads(raw)).state.name)
    except (OSError, ValueError):
        return None
    return states or None


def waypoint_checkpoints(mission: Optional[dict],
                         states: Optional[list] = None) -> list:
    """Every prop, as a waypoint checkpoint. -> [{epoch, label, offset_m}].

    DIALOGUE.md revision 2, "Checkpoints, measured". With `states` -- the
    arbitrated state per epoch, from `mission_states` -- the epoch is ARRIVAL:
    the first epoch at which accrued progress reaches the prop's arc length,
    accrued exactly as index.html does it (`s += speed * gain[state]`, and
    epoch 0 is the origin). That reproduces the contract's corrected table:
    RECON 0/100/147/176/344, LOGISTICS 0/197/333, CASEVAC 0/275/280,
    COMBAT 0/62/209/332.

    Without `states` it is the UNIMPEDED schedule, `arc_length / speed` --
    where the vehicle would be if nothing ever happened to it (RECON
    0/75/122/152/320, LOGISTICS 0/140/276, CASEVAC 0/187/191,
    COMBAT 0/60/188/311). That is the fallback when the stream is not
    available to replay, and it is the reference column on the contract.

    A prop the vehicle never reaches inside the stream is not a checkpoint: it
    never happens, and `total` may not count it.
    """
    mission = mission or {}
    route = route_points(mission)
    try:
        speed = float(mission.get("speed_m_per_epoch") or 0.0)
    except (TypeError, ValueError):
        speed = 0.0
    if len(route) < 2 or speed <= 0.0:
        return []

    arcs: list = []
    for prop in mission.get("props") or ():
        try:
            s, off = project_on_route(route, (prop["e"], prop["n"]))
        except (TypeError, ValueError, KeyError):
            continue
        arcs.append((s, off, _short(prop.get("label"))))

    if states is None:
        found = [(int(round(s / speed)), off, label) for s, off, label in arcs]
    else:
        gain = mission.get("gain") or {}
        arrival: dict = {}
        travelled = 0.0
        for k, state in enumerate(states):
            if k:
                try:
                    travelled += speed * float(gain.get(state, 1.0))
                except (TypeError, ValueError):
                    travelled += speed
            for i, (s, _off, _label) in enumerate(arcs):
                if i not in arrival and travelled >= s - 1e-9:
                    arrival[i] = k
        found = [(arrival[i], off, label)
                 for i, (_s, off, label) in enumerate(arcs) if i in arrival]

    out: list = []
    seen: set = set()
    for epoch, off, label in sorted(found, key=lambda w: w[0]):
        if epoch in seen:
            continue                      # two props on one epoch is one arrival
        seen.add(epoch)
        out.append({"epoch": epoch, "label": label, "offset_m": round(off, 3)})
    return out


# ------------------------------------------------------------- verification

def _check_path(claim: dict, epoch: Optional[dict]) -> list:
    """One `path` claim against the epoch record. -> list of failure strings."""
    path = claim["path"]
    actual = _dig(epoch or {}, path)
    if actual is None:
        return [f"{path} absent from epoch"]
    claimed = claim.get("value")

    if isinstance(actual, list) and not isinstance(claimed, list):
        # explain.verify()'s convention: a list claimed as a number is a count.
        actual = len(actual)
    if isinstance(actual, list) and isinstance(claimed, list):
        # ...but a list claimed AS a list is the set of names, and the guide
        # does name satellites, so the names have to be checkable too.
        if list(actual) != list(claimed):
            return [f"{path}: claimed {claimed}, epoch says {actual}"]
        return []

    numeric = (isinstance(actual, (int, float)) and not isinstance(actual, bool)
               and isinstance(claimed, (int, float)) and not isinstance(claimed, bool))
    if numeric:
        if abs(float(actual) - float(claimed)) > 1e-9:
            return [f"{path}: claimed {claimed}, epoch says {actual}"]
        return []
    if actual != claimed:
        return [f"{path}: claimed {claimed}, epoch says {actual}"]
    return []


def line_failures(line: dict, epoch: Optional[dict]) -> list:
    """design.md §14 for one line. Empty list means it may be shown."""
    problems: list = []
    for c in line.get("claims") or []:
        if "path" in c:
            problems.extend(_check_path(c, epoch))
        elif not c.get("source"):
            problems.append(f"claim {c.get('text')!r} carries neither path nor source")
    missing = uncovered(line)
    if missing:
        problems.append("unsourced numerals in text: " + ", ".join(sorted(missing)))
    return problems


def claims_hold(lines: list, epoch: Optional[dict]) -> bool:
    """Would every one of these lines survive verification against this epoch?

    Asked BEFORE an event checkpoint is emitted. A scripted event may not be
    moved off its epoch (DIALOGUE.md revision 2), so one whose numbers do not
    hold where it comes due is not spoken at all.
    """
    return not any(line_failures(ln, epoch) for ln in lines)


def verify_lines(lines: list, epoch: Optional[dict]) -> tuple:
    """design.md §14, applied line by line. -> (lines, ok, failures).

    Two checks, and a line has to pass both:

      1. every `path` claim digs out of THIS epoch and matches its value;
      2. every numeral in the text is covered by some claim on the line.

    Check 2 is what makes check 1 worth anything: without it a line could
    carry one honest claim and three invented figures. `source` claims are not
    dug -- DIALOGUE.md §1 says they are verified in the sense that the named
    source is real -- but they must still name one.

    A failing line is REPLACED with the fallback, not hidden and not shown.
    The checkpoint still speaks: the operator learns that a number was
    withheld, which is itself the §14 behaviour worth demonstrating.
    """
    out: list = []
    failures: list = []
    for i, line in enumerate(lines):
        problems = line_failures(line, epoch)
        if problems:
            failures.extend(f"line {i}: {p}" for p in problems)
            out.append(_line(FALLBACK_TEXT, "alert"))
        else:
            out.append(line)
    return out, (not failures), failures


def _stamp(lines: list, at: Optional[str], index: Optional[int]) -> list:
    """DIALOGUE.md revision 2: every line carries the epoch it was checked at."""
    return [dict(ln, at=at, epoch=index) for ln in lines]


def _gist(text: str) -> str:
    """A line's identity for repetition purposes: lowercase words only.

    Punctuation and digits are dropped so that a generated line and a scripted
    one saying the same thing in the same words collide even when one of them
    spells a threshold out and the other quotes it.
    """
    return " ".join(re.findall(r"[a-z]+", (text or "").lower()))


def _checkpoint(index: int, total: int, label: str, kind: str) -> dict:
    return {"index": index, "total": total,
            "label": str(label), "kind": kind if kind in KINDS else "event"}


def silent() -> dict:
    """The free-running epoch: nothing to say. Most epochs are this."""
    return {"lines": [], "checkpoint": None, "verified": True, "failures": []}


# -------------------------------------------------------------------- guide

class Guide:
    """Stateful across epochs. One instance per SSE connection, like `Arbitras`.

    A browser reload therefore replays the intro and every checkpoint from the
    top, which is the hard reset design.md §11a asks the console to have.
    """

    def __init__(self, mission: Optional[dict] = None) -> None:
        self.mission = mission or {}
        # DIALOGUE.md §3: a mission without a `guide` is not an error, it is a
        # mission whose script has not been written yet.
        self._script = list(self.mission.get("guide") or [])
        self._behaviour = dict(self.mission.get("behaviour") or {})
        self._captions = {}
        for b in self.mission.get("beats") or ():
            try:
                self._captions[int(b["epoch"])] = str(b.get("caption") or "")
            except (TypeError, ValueError, KeyError):
                continue
        # Arrival, not the unimpeded schedule: DIALOGUE.md revision 2 as
        # corrected. Falls back to `arc / speed` when the stream is not here.
        self._states = mission_states(self.mission)
        self.schedule = self._build_schedule()
        self.total = len(self.schedule)
        self._cursor = 0                  # next schedule slot to consider
        self._started = False
        self._finished = False
        self._said: set = set()   # gists already spoken; see _fresh()
        self._last_credential: Optional[str] = None
        self._last_at: Optional[str] = None
        self._last_index: Optional[int] = None

    # ------------------------------------------------------- the schedule

    def _build_schedule(self) -> list:
        """Every checkpoint this mission can produce, in order, known up front.

        Waypoints and script entries that fall on one epoch are ONE checkpoint;
        anything on the opening epoch is absorbed by the intro. That is why
        `total` is exact rather than an upper bound.
        """
        slots: dict = {}
        for w in waypoint_checkpoints(self.mission, self._states):
            slots[w["epoch"]] = {"epoch": w["epoch"], "kind": "waypoint",
                                 "label": w["label"], "prop": w["label"],
                                 "entry": None}
        for entry in self._script:
            try:
                at = int(entry.get("epoch"))
            except (TypeError, ValueError):
                continue                  # malformed entry: never scheduled
            slot = slots.setdefault(at, {"epoch": at, "kind": "event",
                                         "label": None, "prop": None,
                                         "entry": None})
            # A script entry does NOT demote the slot. When the entry lands on
            # a waypoint the checkpoint IS that waypoint -- the vehicle is at
            # the place -- and the mission says so with its own `kind`. Only an
            # entry with nowhere to belong falls back to "event".
            declared = entry.get("kind")
            if declared in KINDS:
                slot["kind"] = declared
            elif slot["kind"] not in ("waypoint", "intro"):
                slot["kind"] = "event"
            slot["entry"] = entry
            if entry.get("label"):
                slot["label"] = _short(entry["label"])

        ordered = [slots[e] for e in sorted(slots)]
        # The opening epoch is the intro's; whatever else sits on it rides along.
        intro = {"epoch": 0, "kind": "intro", "label": None, "prop": None,
                 "entry": None}
        if ordered and ordered[0]["epoch"] <= 0:
            first = ordered.pop(0)
            intro["entry"] = first["entry"]
            intro["label"] = first["label"]
            intro["prop"] = first["prop"]
        intro["label"] = intro["label"] or _short(self.mission.get("title")) or INTRO_LABEL
        end = {"epoch": None, "kind": "end", "label": END_LABEL, "prop": None,
               "entry": None}
        return [intro] + ordered + [end]

    def checkpoints(self) -> list:
        """The schedule as the operator will count it. Diagnostics and tests."""
        return [{"index": i + 1, "total": self.total, "epoch": s["epoch"],
                 "kind": s["kind"], "label": s["label"]}
                for i, s in enumerate(self.schedule)]

    # ------------------------------------------------------------ the tick

    def step(self, d: Decision, epoch: Optional[dict]) -> dict:
        """One epoch -> `payload.dialogue`. Empty on most epochs."""
        at = clock(epoch.get("timestamp") if isinstance(epoch, dict) else d.timestamp)
        self._last_at, self._last_index = at, d.epoch_index

        state = self._state_lines(d) if d.changed else []
        # Reads AND advances the credential memory, so it runs exactly once per
        # epoch and before `_started` is set.
        credential = self._credential_lines(d)
        self._started = True

        slot, index = self._due(d, epoch)
        if slot is None:
            # No checkpoint here. A trust-state or credential change still has
            # to be spoken on its own epoch -- but it is an interjection, not a
            # checkpoint, so it never moves the count the operator is reading.
            lines = self._fresh(state + credential)[:MAX_LINES]
            # Everything here had already been said: an interjection with
            # nothing new in it is silence, not an empty box.
            if not lines:
                return silent()
            return self._payload(lines, None, epoch, d.epoch_index, at)

        cap = INTRO_MAX_LINES if slot["kind"] == "intro" else MAX_LINES
        lines = self._fresh(self._slot_lines(slot, d, epoch)
                            + state + credential)[:cap]
        # A checkpoint whose every line was already spoken is not worth
        # stopping on; the slot is spent either way, as an unverified one is.
        if not lines:
            return silent()
        cp = _checkpoint(index, self.total,
                         self._label(slot, d, credential), slot["kind"])
        return self._payload(lines, cp, epoch, d.epoch_index, at)

    def finish(self) -> dict:
        """The caller signals the stream ended. Idempotent."""
        if self._finished:
            return silent()
        self._finished = True
        self._cursor = len(self.schedule)
        # There is no epoch to dig against at the end of a stream, so the end
        # lines carry no `path` claim -- and therefore no numeral at all. They
        # are stamped with the last epoch the guide actually saw, which is when
        # it is speaking.
        cp = _checkpoint(self.total, self.total, END_LABEL, "end")
        return self._payload(self._end_lines(), cp, None,
                             self._last_index, self._last_at)

    def _fresh(self, lines: list) -> list:
        """Drop lines this run has already spoken.

        The guide generates a line for a condition (recovery gating, the
        behaviour sentence) AND a mission may script one for the same moment in
        nearly the same words -- RECON epoch 84 and the generated recovery line
        were word-for-word the same sentence 5 s apart on screen. Whichever
        arrives first wins; the duplicate is dropped rather than deduplicated
        into a merged line, because the two are the same thought and one saying
        of it is the correct number.
        """
        out = []
        for ln in lines:
            g = _gist(ln.get("text"))
            if not g or g in self._said:
                continue
            self._said.add(g)
            out.append(ln)
        return out

    def _payload(self, lines: list, checkpoint: Optional[dict],
                 epoch: Optional[dict], index: Optional[int],
                 at: Optional[str]) -> dict:
        lines, ok, failures = verify_lines(lines, epoch)
        if not lines:
            return silent()
        return {
            "lines": _stamp(lines, at, index),
            "checkpoint": checkpoint,
            "verified": ok,
            "failures": failures,
        }

    # ----------------------------------------------------------- schedule

    def _due(self, d: Decision, epoch: Optional[dict]) -> tuple:
        """The checkpoint this epoch has reached, or (None, None).

        Slots fire in schedule order and at most one per epoch, so `index` is
        strictly increasing. A scripted event whose claims do not hold HERE is
        abandoned rather than carried forward: DIALOGUE.md revision 2 forbids
        moving an event checkpoint, and a stale figure is exactly what §14 is
        for.
        """
        while self._cursor < len(self.schedule) - 1:      # `end` is finish()'s
            slot = self.schedule[self._cursor]
            if slot["kind"] != "intro" and d.epoch_index < (slot["epoch"] or 0):
                return None, None                          # still in the future
            index = self._cursor + 1
            self._cursor += 1
            if slot["kind"] == "intro":
                return slot, index
            entry = slot["entry"]
            if entry is not None and not claims_hold(self._entry_lines(entry), epoch):
                # An event that cannot be told truthfully here is not told. If
                # the slot is also a waypoint it still speaks, as a waypoint.
                if slot["prop"]:
                    return dict(slot, entry=None, kind="waypoint"), index
                continue
            return slot, index
        return None, None

    def _slot_lines(self, slot: dict, d: Decision, epoch: Optional[dict]) -> list:
        """What this checkpoint has to say, before state and credential lines."""
        lines: list = []
        if slot["kind"] == "intro":
            lines.extend(self._intro_lines())
        if slot["entry"] is not None:
            # The script speaks for the epoch it was pinned to. `_due` has
            # already refused any entry whose claims do not hold here; on the
            # intro, which is not an event checkpoint, refuse them here so the
            # briefing itself still opens.
            script = self._entry_lines(slot["entry"])
            if not claims_hold(script, epoch):
                script = []
            lines.extend(script)
        elif slot["prop"]:
            lines.extend(self._waypoint_lines(slot, d, epoch))
        return lines

    @staticmethod
    def _entry_lines(entry: dict) -> list:
        """A DIALOGUE.md §3 script entry, normalised into the wire shape."""
        return [_line(str(ln.get("text") or ""),
                      ln.get("tone") or "calm",
                      [dict(c) for c in (ln.get("claims") or [])])
                for ln in (entry.get("lines") or [])][:MAX_LINES]

    def _label(self, slot: dict, d: Decision, credential: list) -> str:
        """What the box calls this checkpoint.

        A place if it is one, then what actually happened, then the mission's
        own caption for the beat. Never a state name: DIALOGUE.md invariant 3
        is operator language, and "RESTRICTED" is builder language.
        """
        if slot["kind"] == "intro":
            return slot["label"] or INTRO_LABEL
        if slot["label"]:
            return slot["label"]
        if d.changed:
            if d.state > d.previous_state:
                return (STATE_LABEL_FULL if d.state is TrustState.NOMINAL
                        else STATE_LABEL_UP)
            return STATE_LABEL_DOWN.get(d.state, "AUTHORITY REDUCED")
        if credential and d.credential_status in CREDENTIAL_LABEL:
            return CREDENTIAL_LABEL[d.credential_status]
        return self._caption_label(slot["epoch"]) or EVENT_LABEL

    def _caption_label(self, epoch_index: Optional[int]) -> Optional[str]:
        """The mission's builder-facing beat caption, trimmed to a label."""
        caption = self._captions.get(epoch_index)
        if not caption:
            return None
        head = re.split(r"[:.;]", caption, 1)[0].strip()
        if not head or head.upper() in {s.name for s in TrustState}:
            return None                   # builder language; not for the screen
        # Borrowed prose keeps the case its author gave it, exactly as a
        # mission's own checkpoint labels do. Casing is the box's business.
        return head[:LABEL_MAX].strip()

    # ----------------------------------------------------------- waypoints

    def _waypoint_lines(self, slot: dict, d: Decision,
                        epoch: Optional[dict]) -> list:
        """Arrival at a prop, and the state of trust at the moment of arrival."""
        label = slot["prop"]
        if not label:
            return []
        claims = _borrowed(label, "mission prop label, console/missions/__init__.py")
        text = f"The vehicle is at {label}."
        if epoch is not None and d.confidence is not None:
            text += f" Position confidence {d.confidence:.2f}"
            claims.append(_path(f"{d.confidence:.2f}", d.confidence, "confidence"))
            excluded = list((d.geometry or {}).get("excluded_sv") or [])
            if excluded:
                n = len(excluded)
                text += (f", with {n} satellite{'s' if n != 1 else ''} outside the "
                         "trusted set.")
                claims.append(_path(str(n), n, "geometry.excluded_sv"))
            else:
                text += ", and the full sky is trusted."
        lines = [_line(text, STATE_TONE[d.state], claims)]
        if d.state is not TrustState.NOMINAL:
            lines.append(self._behaviour_line(d))
        return lines

    # --------------------------------------------------------------- intro

    def _intro_lines(self) -> list:
        title = self.mission.get("title")
        if title:
            first = _line(
                f"I am ARBITRAS, watching the satellite signals behind this {title} run. I score "
                f"how much of the position I believe, and I take authority away in steps as that "
                f"score falls.",
                "calm", _borrowed(str(title), "mission.title"),
            )
        else:
            first = _line(
                "I am ARBITRAS. I sit between the receiver and the vehicle: I score how much of "
                "the position I believe, and I take authority away in steps as that score falls.",
                "calm",
            )
        return [
            first,
            _line(
                "Every number I say is checked against the epoch it came from, and stamped with "
                "it. If one cannot be sourced I withhold the line rather than show it.",
                "calm",
            ),
        ]

    # --------------------------------------------------------------- state

    def _state_lines(self, d: Decision) -> list:
        """Why authority moved, what the vehicle does now, and what it costs.

        Three thoughts in the order an operator asks them. The cap means a
        checkpoint on the same epoch can crowd the third out; the script is
        telling the same story in the mission's own words, so that is the
        right loss.
        """
        lines = [self._cause_line(d)]
        lines.append(self._behaviour_line(d))
        consequence = self._consequence_line(d)
        if consequence:
            lines.append(consequence)
        return lines

    def _cause_line(self, d: Decision) -> dict:
        if d.state > d.previous_state:
            # Invariant 2. The operator's question on the way back up is always
            # "why only one step", so answer it before it is asked.
            gate = _source(str(RECOVERY_EPOCHS), RECOVERY_EPOCHS,
                           "console/arbitras/states.py RECOVERY_EPOCHS")
            if d.state is TrustState.NOMINAL:
                return _line(
                    "Full authority is back. It took the whole staircase — one step per "
                    f"{RECOVERY_EPOCHS} clean epochs — so no single good reading could have "
                    "bought it.",
                    "good", [gate],
                )
            return _line(
                "Authority is back one step, not all the way. I have to see clean signals for "
                f"{RECOVERY_EPOCHS} straight epochs before each step up, so nothing recovers on "
                "a lucky reading.",
                "good", [gate],
            )
        if d.reason == "credential_force":
            phrase = CREDENTIAL_PHRASE.get(d.credential_status or "",
                                           "Mission authorisation failed.")
            return _line(f"{phrase} Signal quality did not enter into it — a good fix is not "
                         "permission.", "bad")
        if d.reason == "credential_cap":
            phrase = CREDENTIAL_PHRASE.get(d.credential_status or "",
                                           "Mission authorisation is unconfirmed.")
            return _line(f"{phrase} Authority is capped there until it verifies, however clean "
                         "the satellites look.", "alert")
        if d.reason == "stale":
            return _line(
                "The position feed has gone quiet. I step authority down on silence, because "
                "silence is not consent.",
                "bad",
            )

        worst, value = self._worst_feature(d)
        if worst is not None:
            phrase = FEATURE_PHRASE.get(worst, worst).capitalize()
            claims = [_path(f"{value:.2f}", value, f"features.{worst}")]
            text = f"{phrase} — that channel reads {value:.2f}"
            if d.confidence is not None:
                text += f", and my composite confidence with it is {d.confidence:.2f}."
                claims.append(_path(f"{d.confidence:.2f}", d.confidence, "confidence"))
            else:
                text += "."
            return _line(text, STATE_TONE[d.state], claims)

        if d.confidence is not None:
            return _line(
                f"Position confidence has moved to {d.confidence:.2f}. No single channel is far "
                "enough out to name as the cause; they shifted together.",
                STATE_TONE[d.state],
                [_path(f"{d.confidence:.2f}", d.confidence, "confidence")],
            )
        return _line("Position confidence has left the trusted range.", STATE_TONE[d.state])

    def _behaviour_line(self, d: Decision) -> dict:
        """What the vehicle does now, in the mission's own words where it has any."""
        name = d.state.name
        text = self._behaviour.get(name)
        source = f"mission.behaviour.{name}"
        if not text:
            text = BEHAVIOUR[d.state]
            source = "console/arbitras/explain.py BEHAVIOUR"
        return _line(str(text), STATE_TONE[d.state], _borrowed(str(text), source))

    def _consequence_line(self, d: Decision) -> Optional[dict]:
        """The one number worth carrying out of this transition.

        Ordered by how much of the argument each carries: an exclusion set with
        a geometry ratio behind it is the whole thesis (CLAUDE.md: the geometry
        half is derived, not a heuristic); terrain is the one non-RF channel;
        the DEGRADED pursuit is invariant 5; the bare bound and the clock rule
        are the fallbacks that always have something true to say.
        """
        g = d.geometry or {}
        excluded = list(g.get("excluded_sv") or [])
        ratio = g.get("information_ratio")
        bound = g.get("displacement_bound_m")

        if excluded:
            n = len(excluded)
            if n <= 3:
                # Few enough to name, and the name is the more useful fact.
                # The claim carries the list itself, so the satellite ids in
                # the text -- G19, R20 -- are checked like any other numeral.
                spoken = (excluded[0] if n == 1
                          else " and ".join([", ".join(excluded[:-1]), excluded[-1]]))
                claims = [_path(", ".join(excluded), list(excluded),
                                "geometry.excluded_sv")]
                text = f"I have dropped {spoken} out of the trusted set."
            else:
                claims = [_path(str(n), n, "geometry.excluded_sv")]
                family = _family(excluded)
                who = f"{family} " if family else ""
                text = f"I have dropped {n} {who}satellites out of the trusted set."
            if ratio is not None:
                text += f" My information ratio is {ratio:.2f} of the full sky"
                claims.append(_path(f"{ratio:.2f}", ratio, "geometry.information_ratio"))
                if bound is not None:
                    text += (f", so the box an attacker could still move me inside is "
                             f"{bound:.0f} m wide.")
                    claims.append(_path(f"{bound:.0f} m", bound,
                                        "geometry.displacement_bound_m"))
                else:
                    text += "."
            elif bound is not None:
                text += (f" On what is left, an attacker could move us up to {bound:.0f} m and "
                         "stay consistent.")
                claims.append(_path(f"{bound:.0f} m", bound, "geometry.displacement_bound_m"))
            return _line(text, "alert", claims)

        terrain = self._terrain_line(d)
        if terrain is not None:
            return terrain

        if d.state is TrustState.DEGRADED and d.pursuing:
            family = CONSTELLATION.get(str(d.pursuing), str(d.pursuing))
            return _line(
                f"Losing authority is not the same as doing nothing: I am reweighting toward the "
                f"{family} arc, where the most independent geometry is left. Advisory only — I "
                f"never command motion.",
                "act",
                [_path(family, d.pursuing, "geometry.next_best_observation")],
            )

        if bound is not None:
            return _line(
                f"On the satellites I still trust, an attacker could move us up to {bound:.0f} m "
                "and stay consistent with every range I can check.",
                "alert",
                [_path(f"{bound:.0f} m", bound, "geometry.displacement_bound_m")],
            )

        if not d.clock_discipline:
            # Invariant 4, said out loud: below NOMINAL both borrowed inputs
            # freeze, and the credential channel is one of them.
            return _line(
                "Below the trusted range my clock free-runs and the credential channel closes. "
                "I will not accept a new authorisation until confidence comes back.",
                "alert",
            )
        return None

    def _terrain_line(self, d: Decision) -> Optional[dict]:
        """Track E. Always stamped simulated (CLAUDE.md, tracks/TRACK_E.md)."""
        t = d.terrain or {}
        sensed = (t.get("sensed") or {}).get("class")
        mapped = (t.get("map_at_position") or {}).get("class")
        if not sensed or not mapped or sensed == mapped:
            return None
        likelihood = t.get("match_likelihood")
        text = (f"The terrain sensor — simulated — reads {_pretty(sensed)} under the vehicle; the "
                f"signed pre-map has {_pretty(mapped)} where the receiver says we are.")
        claims: list = []
        if likelihood is not None:
            text += f" Match likelihood {likelihood:.2f}."
            claims.append(_path(f"{likelihood:.2f}", likelihood, "terrain.match_likelihood"))
        return _line(text, "alert", claims)

    @staticmethod
    def _worst_feature(d: Decision):
        """The salient scalar feature, or (None, None).

        Scalars only: `features` also carries the per-satellite map `by_sv` and
        the cross-constellation detail block, neither of which is a feature
        score and neither of which may be compared against one.
        """
        numeric = {k: v for k, v in (d.features or {}).items()
                   if isinstance(v, (int, float)) and not isinstance(v, bool)}
        if not numeric:
            return None, None
        worst = max(numeric, key=lambda k: numeric[k])
        if numeric[worst] < SALIENCE_FLOOR:
            return None, None
        return worst, numeric[worst]

    # ---------------------------------------------------------- credential

    def _credential_lines(self, d: Decision) -> list:
        previous = self._last_credential
        current = d.credential_status
        if current is not None:
            self._last_credential = current
        # The first epoch seeds the memory; it does not announce a transition
        # that nobody watched happen. A stale epoch carries the last known
        # status forward, so `None` here means "not known yet", never "changed".
        if not self._started or previous is None or current is None:
            return []
        if current == previous:
            return []
        tone = CREDENTIAL_TONE.get(current, "alert")
        lines = [_line(CREDENTIAL_LINE.get(current, CREDENTIAL_PHRASE.get(current, "")),
                       tone)]
        means = CREDENTIAL_MEANS.get(current)
        if means:
            lines.append(_line(means, tone))
        return lines

    # ----------------------------------------------------------------- end

    @staticmethod
    def _end_lines() -> list:
        return [
            _line(
                "That is the end of the replay. Authority moved when the evidence moved, and it "
                "never came back faster than the recovery gate allowed.",
                "calm",
            ),
            _line(
                "Every figure you saw was checked against the epoch it came from before it "
                "reached the screen. None of it needed a camera or a second radio.",
                "calm",
            ),
        ]
