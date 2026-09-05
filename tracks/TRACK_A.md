# TRACK A — Detection + Injector

You own `backend/rinex/`, `backend/injector/`, `backend/detection/`.
You produce `confidence` and `features`. You never touch state names.

## Setup (~10 min, unattended)
    git clone https://github.com/maxzfan/HOLDFAST.git && cd HOLDFAST
    bash bootstrap.sh
    source .venv/bin/activate

Already done for you: data downloaded (5 files, `data/`), nav file
pre-filtered and verified, libraries installed. Do not re-solve these.

## 1. RINEX loader -> per-epoch dataframe   [use Sonnet, this is plumbing]
    import georinex as gr
    obs = gr.load('data/USN800USA_R_20262320000_01D_30S_MO.crx.gz', use={'G'})

- 2,880 epochs, 30 s, 00:00:00-23:59:30 GPS time. 109 SV, 5 constellations.
- Observable codes are in design.md §4. C/N0 lives in `S1C`, `S2W`, `S5Q`, etc.
- georinex emits a FutureWarning per epoch from xarray. Harmless.
  `warnings.filterwarnings("ignore")` in code; `2>/dev/null` at the shell —
  but NOT while debugging, it swallows tracebacks.
- Emit one dataframe per epoch. That is the only thing tracks B and C consume.

## 2. Injector   [Opus. Physics specified BY HAND — see design.md §7]
Claude will fabricate a confident number if you ask it for the attack physics.
Do not ask. Specify them yourself from §7, then have Claude write the code.

Three scenarios (§7): simplistic 10-20 dB, **intermediate carry-off 1-3 dB
(primary demo)**, meaconing. Reproduce the four-stage temporal signature —
onset spike, ~10 s later power drops back as loops lock, lift-off, steady
state with residuals slightly elevated. Walk-off ~1 m/s to start.

## 3. Three features (§6a)
C/N0 anomaly, pseudorange residual, code-minus-carrier divergence. Each
normalised [0,1], higher = more anomalous. The fourth (cross-constellation)
comes after the 18:30 checkpoint and is first on the cut list.

**The number everything derives from:** measured C/N0 noise floor 35-50 dB-Hz
by elevation, epoch-to-epoch variation ~ +/-0.5 dB. A 1-3 dB spoofer is 2-6 sigma out.

## Deliverable by 18:30 integration checkpoint
Clean and injected replays both producing `features` + `confidence` in the
§5 contract shape. Not polished. Flowing.

## 21:00-22:30 — THRESHOLDS, with Track C, by hand
The most important 90 minutes of the weekend. Not delegated to Claude.
Procedure in design.md §10. Never a round number without a reason.

## CONTRACT GAP found by Track B — needs your output
Video beat 2 shows the believed and true tracks separating. The §5 contract
carries only `position` (what the receiver believes). **There is no truth
channel**, so the console cannot draw the separation.

A real vehicle has no truth channel; a replay does, because you injected the
attack. Emit it as `_truth: {lat, lon}` — underscore-prefixed to mark it as
out-of-contract replay metadata, not part of the interface. The console already
reads it and computes metres-off-route on screen.

Without this, beat 2 is a static dot and the most important 30 seconds of the
video does not work. Confirm at the 18:30 checkpoint.
