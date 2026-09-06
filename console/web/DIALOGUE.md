# The guide -- dialogue contract

Replaces the bottom explanation bar (`.expbar`: `explanation.headline` +
`explanation.detail`) with an interactive, click-advanced dialogue box in the
manner of a Pokemon text box: one speaker, one line at a time, a typewriter
reveal, a chevron when the line is finished, and the replay HELD until the
operator clicks through.

Decided with the user, 2026-09-06:

- **Click advances.** Click is the interaction. Space is an alias.
- **Pause on key moments.** The replay stops at mission beats, trust-state
  changes and credential changes. It free-runs in between.
- **One guide.** Every line is spoken by ARBITRAS. No cast, no attacker.
- **The old explanation text goes away from the screen** -- `explain.py` and
  `payload["explanation"]` STAY on the wire (test_server.py and the
  verification banner depend on them); the console simply stops rendering
  the headline/detail pair.
- **The dialogue is still technical.** Simpler words, same mechanism, same
  numbers. It explains what is happening, it does not cartoon it.

## What does not bend

1. **design.md §14.** No number reaches the screen that cannot be sourced.
   Every numeral in a line's `text` must be covered by a claim, and every
   claim carries either a `path` (a contract path into the epoch, verified
   before display) or a `source` (a scenario parameter, named). A line whose
   `path` claim fails verification is replaced, not shown.
2. **The instrument strip is untouched.** CLAUDE.md: the composite confidence
   is never reported without the four sub-scores and the geometry block. The
   strip above the stage keeps doing that. This work only replaces the bar
   BELOW the stage.
3. **Operator language, not builder language.** "The vehicle stops accepting
   waypoints", never "the state machine transitions to RESTRICTED".
4. **ARBITRAS never commands motion.** The guide narrates and advises. It
   does not drive.
5. **Offline.** No external asset, font or network call. The speaker sigil is
   inline SVG or CSS, never a raster image.

## Voice

Three worked examples. Match these.

> Too cute -- do not write this:
> "Uh oh! The satellites are being naughty!"

> Too jargoned -- do not write this:
> "Six PRNs exhibit correlated C/N0 elevation exceeding the 2-sigma baseline."

> Right:
> "Six GPS satellites got louder at the same instant, +8 dB. Real satellites
> drift apart -- these moved together. That is one transmitter, not six."

> Right:
> "I have dropped GPS entirely and I am navigating on Galileo. Losing a whole
> constellation costs me geometry: my information ratio is 0.80 of the full
> sky, so the box an attacker could still move me inside is 24 m wide."

> Right:
> "Authority is back one step, not all the way. I have to see clean signals
> for 10 straight epochs before each step up, so nothing recovers on a lucky
> reading."

Rules of thumb: 2-3 sentences, <= 220 characters, one idea per line. Say the
mechanism, then what it means for the vehicle.

## 1. `payload.dialogue` -- backend to client

Attached in `console/server.py decide()` beside `payload["explanation"]`.
This is console-side output. **It is NOT part of the design.md §5 contract**
and nothing in `backend/` produces or consumes it.

```json
{
  "lines": [
    {
      "speaker": "ARBITRAS",
      "text": "Six GPS satellites got louder at the same instant, +8 dB. Real satellites drift apart -- these moved together.",
      "claims": [
        {"text": "+8 dB", "value": 8.0, "source": "injector parameter, backend/missions.py recon"}
      ],
      "tone": "alert"
    }
  ],
  "gate": true,
  "gate_reason": "beat",
  "verified": true,
  "failures": []
}
```

| field | meaning |
|---|---|
| `lines` | 0-3 lines. **Empty on most epochs.** |
| `gate` | `true` iff `lines` is non-empty. The client HOLDS the replay while true. |
| `gate_reason` | `intro` \| `beat` \| `state` \| `credential` \| `end` \| `null` |
| `verified` | every `path` claim checked against this epoch |
| `failures` | verification failures, for the banner |

`tone` is one of `calm` `alert` `bad` `good` `act`, and only colours the box.

### Claims

```json
{"text": "118 m", "value": 118.0, "path": "geometry.displacement_bound_m"}
{"text": "+8 dB", "value": 8.0,   "source": "injector parameter, backend/missions.py recon"}
```

Exactly one of `path` or `source`. `path` claims are dug out of the epoch and
compared (as `explain.verify` does). `source` claims are scenario facts --
what the injector was told to do -- and are not measurements; they are
verified only in the sense that the named source is real.

## 2. `console/arbitras/dialogue.py`

Stateful across epochs, one instance per SSE connection, exactly like
`Arbitras`:

```python
class Guide:
    def __init__(self, mission: dict | None = None) -> None: ...
    def step(self, d: Decision, epoch: dict | None) -> dict: ...   # -> payload.dialogue
```

`mission` is the `/mission?name=` dict (`missions.as_dict(m)`), or `None` when
no mission is selected. Read the script from `mission.get("guide", [])` and
tolerate its absence -- older callers pass a mission without it.

`Guide` fires lines on, and only on:

| reason | when |
|---|---|
| `intro` | the first epoch of the connection |
| `beat` | an epoch matching a `guide` entry's `epoch` |
| `state` | `d.changed` -- the trust state moved |
| `credential` | `d.credential_status` differs from the last epoch's |
| `end` | the caller signals the stream ended (`Guide.finish()`) |

When several fire on one epoch, merge into one gate, `beat` first, capped at
3 lines. Never emit the same beat twice.

**The opening gate is the exception.** On the first epoch `intro` leads and
the cap is 4. A guide that has not said what it is, opening on "Full sky,
confidence 0.91", is the complexity this box exists to remove; and at a cap
of 3 the fourth line is dropped in silence.

## 3. `console/missions/__init__.py` -- the `guide` field

```python
guide: tuple[dict, ...] = ()
```

```python
guide=(
    {"epoch": 60, "lines": [
        {"text": "...", "tone": "alert",
         "claims": [{"text": "+8 dB", "value": 8.0, "source": "..."}]},
    ]},
)
```

Surfaced by `as_dict` as `"guide": [...]`. The existing `beats` tuple stays
exactly as it is -- it is the builder-facing script and other code reads it.
`guide` is the operator-facing rewrite of the same story.

## 4. `console/web/dialogue.js` -- classic script, like the overlays

```js
window.ARBITRAS_DIALOGUE = { Box };
```

`Box` is a React component (globals `React` + `htm`, as index.html uses).
It injects its own namespaced `<style>` on load; it adds no CSS to
index.html.

```js
html`<${Box} lines=${lines} gateReason=${r} verified=${v}
             state=${state} onDone=${fn} />`
```

| prop | meaning |
|---|---|
| `lines` | the current gate's lines, or `[]` when free-running |
| `gateReason` | for the small label ("mission beat", "trust state changed", ...) |
| `verified` | `false` dims the box and shows the withheld note |
| `state` | trust state, for the accent colour |
| `onDone()` | called ONCE when the operator clicks past the last line |

Behaviour:

- One line at a time, typewriter reveal.
- **Click on the box** completes the reveal if still typing, else advances to
  the next line, else calls `onDone()`. Space does the same.
- Chevron appears when a line is fully revealed.
- `lines` empty: hold the last delivered line, dimmed, with a running
  indicator. **The band never changes height** -- the layout above it must
  not reflow. Fixed height, same as the `.expbar` it replaces.
- **Do not bind a click handler wider than the box.** The stage pane owns
  orbit drag and `overlays/takeover.js` owns pane touch/drag and the `T`,
  `W/A/S/D` keys. `R` reloads. Space is free; take nothing else.

## 5. Integration -- `console/web/index.html`, `console/server.py`

The client currently applies every epoch as it arrives
(`src.onmessage -> setD`). The stream is **server-paced**
(`server.py`, `time.sleep(delay)`), so a gate cannot stop the server: the
client buffers.

- `onmessage` pushes epochs onto a queue.
- A consumer takes one epoch per stream interval and sets `d`, UNLESS a gate
  is open.
- An arriving epoch carrying `dialogue.gate` opens the gate; `onDone()`
  closes it and the consumer resumes.
- The backlog drains at the normal rate afterwards. This is a replay; lagging
  wall-clock is fine and no epoch is skipped.

`decide()` gains a `guide` argument and attaches `payload["dialogue"]`.
`_events` builds `Guide(mission_dict)` next to `Arbitras()`, per connection, so
a browser reload is still a hard reset (design.md §11a).

Serve `/dialogue.js` in `Handler.do_GET` alongside `/route.js`.

The `explanation_verified === false` banner repoints at
`dialogue.verified === false`.

---

# Revision 2 -- decided with the user, 2026-09-06

The gate is removed. Everything above describing `gate` / `gate_reason` /
`onDone` and a held replay is SUPERSEDED by this section.

**Why.** Holding the epoch queue froze the vehicle on screen, and under
`overlays/takeover.js` a human (`T`, take) or the scripted stand-in (`S`,
simulate) is driving -- freezing the replay under a driver is wrong, and
nobody at the controls can stop to click a text box.

## The model

- **The replay never pauses.** The vehicle keeps moving through every line.
- **Lines auto-advance** on a dwell timer. Clicking (or Space) skips to the
  next line early; it is a fast-forward, never a requirement.
- **Checkpoints.** Dialogue is anchored at checkpoints and the box shows
  `CHECKPOINT n / N`, so the operator can see how much story is left.
- **Alignment.** The script is paced so the last line lands at or before the
  END OF THE STREAM. Stream end -- not route end: in LOGISTICS the route
  completes at epoch 276 of 510, in COMBAT at 311 of 510, and CASEVAC's route
  does not complete inside its 300-epoch stream at all. The replay ends when
  the stream ends, so that is the clock the operator is on.

## Checkpoints, measured

Waypoint epochs are `arc_length(prop) / speed_m_per_epoch`, computed from the
registry (props lie exactly on the route; measured offset 0.0 m):

| mission | stream | waypoint checkpoints (epoch · label) |
|---|---|---|
| RECON | 510 | 0 PATROL BASE · 75 OP-1 · 122 OP-2 · 152 OP KESTREL · 320 OP-3 |
| LOGISTICS | 510 | 0 FOB · 140 PL AMBER · 276 RP KILO |
| CASEVAC | 300 | 0 ROLE 1 · 187 CCP · 191 CASUALTY |
| COMBAT | 510 | 0 LD · 60 PL AMBER · 188 PL RED · 311 OBJ HAWK |

Three to five waypoints cannot carry 17-19 lines, so a checkpoint is a
waypoint **or** a measured event (attack onset, trust-state change,
credential change). Waypoint checkpoints carry the prop's label; event
checkpoints are labelled by what happened. **An event checkpoint may not be
moved to a waypoint**: its `path` claims are verified against the epoch it is
emitted on, and the same number is not true ten epochs later.

## `payload.dialogue`, revision 2

```json
{
  "lines": [{"speaker":"ARBITRAS","text":"...","claims":[...],"tone":"alert",
             "at":"12:30:00","epoch":60}],
  "checkpoint": {"index": 3, "total": 11, "label": "OP-1", "kind": "waypoint"},
  "verified": true,
  "failures": []
}
```

`gate` and `gate_reason` are gone. `kind` is `waypoint` | `event` | `intro` |
`end`. **Every line carries `at` (UTC) and `epoch`** -- the epoch its claims
were verified against. The box prints it. This closes the drift the held line
had before: a line saying "confidence 0.91" stayed on screen while the strip
moved to 0.92, with nothing on screen saying the number was from an earlier
epoch. Now it says so.

## Dwell

The box holds each line `max(MIN_DWELL, fair share of the gap to the next
checkpoint)`. `MIN_DWELL` is a readability floor, not a pace: at 5 epochs/s a
60-epoch gap is 12 s, and three lines in it get 4 s each. Lines never queue
past the end of the stream -- if a checkpoint's lines cannot all be shown
before the next one arrives, they are shown faster, never dropped.

Budget check at 5 epochs/s: RECON 19 lines over 510 epochs = 102 s of replay;
LOGISTICS 17 over 510 = 102 s. At a 4 s dwell that is 76 s and 68 s of
dialogue -- both finish inside the run with slack.
