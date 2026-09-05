# TRACK B — Arbitration, Console, Explanation

You own `console/`. You consume the §5 contract and never touch an observable.
You own the thresholds mapping confidence -> state, and what to do with
`geometry.next_best_observation`.

## Setup (~10 min, unattended)
    git clone https://github.com/maxzfan/HOLDFAST.git && cd HOLDFAST
    bash bootstrap.sh
    source .venv/bin/activate

## You are never blocked on Track A
`fixtures/epoch.json` is a committed, hand-written contract object. Build the
whole console against it. Transport is JSON Lines appended to a file; the
console tails it. Replay 10-20 epochs/sec.

## 1. Console skeleton against the fixture
React. Dark, low-chrome. It has to read on video at 1080p — design.md §11b.
Show: confidence, the four sub-scores, the geometry block, state, and the
plain-language explanation. **Never the composite number alone.**

## 2. State machine + hysteresis + credential override (§8)
Thresholds NOMINAL >=0.75 / DEGRADED 0.50 / RESTRICTED 0.25 are PLACEHOLDERS.
Track A and C replace them with measured separation points at 21:00. Write the
code so swapping them is one constant.

Invariants — note design.md §8 says "four" and lists five; there are **five**:
1. Authority monotone non-increasing on a single epoch's evidence. Downgrades
   fire immediately and may skip states.
2. Recovery walks up ONE state at a time, gated on 10 consecutive epochs above
   the higher threshold, 5-epoch minimum dwell.
3. Credential state dominates.
4. Clock coupling — below NOMINAL, stop disciplining the clock to GNSS and
   stop accepting new credentials.
5. DEGRADED is an ACTIVE state.

Credential override table: REVOKED/EXPIRED -> force SURRENDERED. UNVERIFIED ->
cap at DEGRADED. PENDING and VALID -> no override.

**Stale epochs:** a missing or malformed epoch is evidence of degradation.
Step state down on a timeout. Silence is not consent.

## 3. Explanation layer — templated first
Name which signal diverged, in the operator's language. "The vehicle stops
accepting waypoints," not "transitions to RESTRICTED." Later: an explanation
verifier that checks every quantitative claim against the epoch record before
display.

## Deliverable by 18:30
Full contract flowing into the console including the `geometry` block, state
machine stepping, explanation rendering. Plus: **hard reset under 5 seconds.**
The demo runs three times and every beat gets re-shot; slow reset turns a
two-hour video block into three.

---

# BUILT (first pass) — 2026-09-05

    source .venv/bin/activate
    python -m console.fixture_stream        # synthetic dev stream, 520 epochs
    python -m console.server                # -> http://localhost:8420
    python -m console.replay                # headless: state timeline + FSR
    python -m pytest console/tests -q       # 23 invariant tests

## Layout
    console/arbiter/states.py    thresholds -- THE SINGLE SWAP POINT for 21:00
    console/arbiter/machine.py   the arbiter; 5 invariants; pure and importable
    console/arbiter/explain.py   templated explanation + the claim verifier
    console/replay.py            headless replay; arbitrate() + false_surrender_rate()
    console/server.py            tails/replays JSONL, arbitrates, serves SSE
    console/web/index.html       React console (vendored React -- runs offline)
    console/fixture_stream.py    SYNTHETIC dev stream. Not data. Stamped _synthetic.

## Decisions made, and why
- **The state machine is Python, not JS.** §10's FSR needs the arbiter replayed
  over 2,880 epochs per Dirichlet draw. Track C imports `console.replay`. If the
  arbiter lived in React, FSR would be uncomputable.
- **Each SSE connection gets a fresh Arbiter and replays from epoch 0**, so a
  browser reload IS the hard reset (§11a, "under five seconds"). Press `r`.
  Nothing restarts server-side between takes.
- **`?layer=off` serves video beat 2** from the same stream: arbitration is
  suppressed and the corner badge reads `Trust layer: OFF` in the same fixed
  position as beat 3, so a viewer can compare frames (§11b).
- **React is vendored** in `console/web/vendor/`, not CDN. §14 lists venue wifi
  dying as a medium risk.
- **Three integrity banners** that cannot be missed on video: SYNTHETIC stream,
  PLACEHOLDER thresholds, and explanation-claim verification failure. The
  threshold banner disappears only when `THRESHOLD_PROVENANCE` is changed off
  `"PLACEHOLDER"` after the 21:00 block -- so a placeholder number physically
  cannot reach the video unnoticed.
- **The explanation verifier is already in the loop**, not deferred. Every
  number the console prints carries its contract path; a claim that does not
  match the epoch record is suppressed and the banner fires.

## Verified on the synthetic stream
    epoch 223  NOMINAL     -> DEGRADED     (confidence 0.743)
    epoch 276  DEGRADED    -> RESTRICTED   (confidence 0.485)
    epoch 321  RESTRICTED  -> SURRENDERED  (confidence 0.246)
    epoch 409  SURRENDERED -> RESTRICTED   (recovery)
    epoch 419  RESTRICTED  -> DEGRADED     (recovery)
    epoch 429  DEGRADED    -> NOMINAL      (recovery)
    epoch 445  NOMINAL     -> SURRENDERED  (credential_force, confidence 0.93)

Recovery lands exactly 10 epochs apart -- invariant 2, visible in the data.
Epoch 445 is video beat 4: clean sky, confidence 0.93, stands down anyway.

## Still open on this track
- [ ] Nobody has looked at the console in a browser yet. No browser on the
      build machine. **Open http://localhost:8420 and eyeball it.**
- [ ] Contract gap: `_truth` is not in §5. Flagged in TRACK_A.md. Beat 2 needs it.
- [ ] Freeze-at-last-NOMINAL: the console reports divergence of the emitted
      geometry against the last-NOMINAL snapshot. Track C item 4 additionally
      recomputes the ratio against the frozen LOS set. **Reconcile at 18:30 so
      it is not built twice.**
- [ ] Replay rate reads at 15 epochs/sec in a terminal; §11b says confirm it is
      intelligible on video and slow beats 2 and 3 if not. `--rate 8`.
- [ ] `EMIT_LATERAL_ADVISORY` in machine.py is the cut-order item 3 switch.

## FIXED — tail-mode staleness was tick-based (2026-09-05 11:1x)
`follow()` yielded None on every 0.2 s poll timeout. The arbiter treats each
None as a missing epoch and steps authority down after STALE_GRACE_TICKS of
them, so **a healthy producer emitting slower than the poll interval was driven
to SURRENDERED on clean data.** Appending 3 epochs produced 29 events, 17 of
them spurious SURRENDERED.

Staleness is now wall-clock against the expected epoch cadence: one None per
`--stale-after` window (default 2.0 s), and tail mode is paced by the producer
rather than by `--rate`. Same test now yields 4 events, all justified.

**The wall clock lives in `follow()`, not in `Arbiter`.** The arbiter stays pure
and deterministic so Track C's Dirichlet sweep replays it identically on every
draw. Putting a timeout inside the arbiter would have made FSR non-reproducible.

**At the 18:30 checkpoint: set `--stale-after` to ~3x Track A's actual emit
interval.** The 2.0 s default is a guess about a producer that does not exist yet.
