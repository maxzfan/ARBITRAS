"""The ARBITRAS guide: one `Decision` -> the lines the guide speaks.

`console/web/DIALOGUE.md` is the contract for this module -- §1 fixes the
`payload.dialogue` shape, §2 fixes the gate reasons and the firing rules.
design.md §14 ("Explanation layer, templated first", and the 22:30 explanation
verifier) is the rule that governs every number in it.

This is the same job `console/arbitras/explain.py` does, said differently. The
explanation layer emits one headline and one dense detail paragraph; the guide
emits the same mechanism one thought at a time, in the order an operator can
follow, and holds the replay while it does. `explain.py` stays on the wire --
`test_server.py` and the verification banner read it -- and nothing here
replaces it. Both are console-side: nothing in `backend/` produces or consumes
`payload.dialogue`, and it is NOT part of the design.md §5 contract.

Three rules from DIALOGUE.md govern every string below:

  - Operator language, not builder language. "The vehicle stops accepting
    waypoints," never "the state machine transitions to RESTRICTED."
  - ARBITRAS narrates and advises. It never commands motion (invariant 5 says
    DEGRADED is active; it does not say the trust layer drives).
  - **No numeral reaches the screen that cannot be sourced.** Every numeral in
    a line's `text` must be covered by a claim, and every claim carries either
    a `path` (dug out of this epoch and compared before display) or a `source`
    (a named scenario parameter). `verify_lines()` enforces both halves and
    REPLACES any line that fails; it does not merely flag it.
"""
from __future__ import annotations

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
from .machine import Decision
from .states import RECOVERY_EPOCHS, TrustState

SPEAKER = "ARBITRAS"

# DIALOGUE.md §1: 0-3 lines per gate, empty on most epochs.
MAX_LINES = 3
# The opening gate carries one more. The briefing is two thoughts and a
# mission's own opener is usually two; at a cap of three, one of the four is
# dropped in silence, and the line that loses is whichever sorts last.
INTRO_MAX_LINES = 4

# DIALOGUE.md §1: `tone` only colours the box.
TONES = frozenset({"calm", "alert", "bad", "good", "act"})

# DIALOGUE.md §2, in the order they are merged into one gate. `beat` first,
# EXCEPT on the opening gate -- see `step`.
GATE_REASONS = ("beat", "intro", "state", "credential", "end")

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

    Mission `behaviour` sentences and titles live in
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
    The gate still fires: the operator learns that a number was withheld,
    which is itself the §14 behaviour worth demonstrating.
    """
    out: list = []
    failures: list = []
    for i, line in enumerate(lines):
        problems: list = []
        for c in line.get("claims") or []:
            if "path" in c:
                problems.extend(_check_path(c, epoch))
            elif not c.get("source"):
                problems.append(f"claim {c.get('text')!r} carries neither path nor source")
        missing = uncovered(line)
        if missing:
            problems.append("unsourced numerals in text: " + ", ".join(sorted(missing)))
        if problems:
            failures.extend(f"line {i}: {p}" for p in problems)
            out.append(_line(FALLBACK_TEXT, "alert"))
        else:
            out.append(line)
    return out, (not failures), failures


def _payload(lines: list, reason: Optional[str], epoch: Optional[dict]) -> dict:
    lines, ok, failures = verify_lines(lines, epoch)
    return {
        "lines": lines,
        "gate": bool(lines),
        "gate_reason": reason if lines else None,
        "verified": ok,
        "failures": failures,
    }


def silent() -> dict:
    """The free-running epoch: no gate, nothing to say. Most epochs are this."""
    return {"lines": [], "gate": False, "gate_reason": None,
            "verified": True, "failures": []}


# -------------------------------------------------------------------- guide

class Guide:
    """Stateful across epochs. One instance per SSE connection, like `Arbitras`.

    A browser reload therefore replays the intro and every beat from the top,
    which is the hard reset design.md §11a asks the console to have.
    """

    def __init__(self, mission: Optional[dict] = None) -> None:
        self.mission = mission or {}
        # DIALOGUE.md §3 is being added to console/missions/__init__.py
        # separately; a mission without a `guide` is not an error, it is a
        # mission whose script has not been written yet.
        self._script = list(self.mission.get("guide") or [])
        self._behaviour = dict(self.mission.get("behaviour") or {})
        self._fired: set = set()          # script indices already spoken
        self._started = False
        self._finished = False
        self._last_credential: Optional[str] = None

    # ------------------------------------------------------------ the tick

    def step(self, d: Decision, epoch: Optional[dict]) -> dict:
        """One epoch -> `payload.dialogue`. Empty on most epochs."""
        beat = self._beat_lines(d)
        intro = [] if self._started else self._intro_lines()
        state = self._state_lines(d) if d.changed else []
        # Reads AND advances the credential memory, so it runs exactly once
        # per epoch and before `_started` is set.
        credential = self._credential_lines(d)
        self._started = True

        # `beat` leads every gate but the first. On the opening gate the guide
        # has to say what it is before it says what the mission is: a reader
        # meeting this box for the first time is the whole reason it exists,
        # and an unintroduced narrator opening on "Full sky, confidence 0.91"
        # is the complexity the box was built to remove.
        groups = (("beat", beat), ("intro", intro),
                  ("state", state), ("credential", credential))
        if intro:
            groups = (("intro", intro), ("beat", beat),
                      ("state", state), ("credential", credential))

        lines: list = []
        origins: list = []
        for reason, group in groups:
            for ln in group:
                lines.append(ln)
                origins.append(reason)

        # DIALOGUE.md §2: several reasons merge into ONE gate, capped at three
        # lines. The reason reported is the origin of the first line, which is
        # what the box's little label is naming.
        cap = INTRO_MAX_LINES if intro else MAX_LINES
        lines, origins = lines[:cap], origins[:cap]
        return _payload(lines, origins[0] if origins else None, epoch)

    def finish(self) -> dict:
        """The caller signals the stream ended. Idempotent."""
        if self._finished:
            return silent()
        self._finished = True
        # There is no epoch to dig against at the end of a stream, so the end
        # lines carry no `path` claim -- and therefore no numeral at all.
        return _payload(self._end_lines(), "end", None)

    # --------------------------------------------------------------- beats

    def _beat_lines(self, d: Decision) -> list:
        """The earliest unspoken script entry this epoch has reached.

        `>=` rather than `==`, and one entry per epoch: a beat whose exact
        index is skipped (a stale epoch, a stream that starts late) is still
        delivered, and a backlog is paid off one gate at a time instead of
        arriving as a wall of text. On a stream whose indices line up -- every
        stream we ship -- this is identical to an exact match.
        """
        for i, entry in enumerate(self._script):
            if i in self._fired:
                continue
            try:
                at = int(entry.get("epoch"))
            except (TypeError, ValueError):
                self._fired.add(i)          # malformed entry: never fires
                continue
            if d.epoch_index < at:
                continue
            self._fired.add(i)
            lines = [self._script_line(ln) for ln in (entry.get("lines") or [])]
            return lines[:MAX_LINES]
        return []

    @staticmethod
    def _script_line(ln: dict) -> dict:
        """A DIALOGUE.md §3 script line, normalised into the §1 wire shape."""
        return _line(str(ln.get("text") or ""),
                     ln.get("tone") or "calm",
                     [dict(c) for c in (ln.get("claims") or [])])

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
                "Every number I say is checked against the epoch it came from. If one cannot be "
                "sourced I withhold the line rather than show it.",
                "calm",
            ),
        ]

    # --------------------------------------------------------------- state

    def _state_lines(self, d: Decision) -> list:
        """Why authority moved, what the vehicle does now, and what it costs.

        Three thoughts in the order an operator asks them. The cap means a
        beat on the same epoch can crowd the third out; the beat is telling
        the same story in the mission's own words, so that is the right loss.
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
