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
