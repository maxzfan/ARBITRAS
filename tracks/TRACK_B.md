# TRACK B — Arbitration, Console, Explanation

You own `console/`. You consume the §5 contract and never touch an observable.
You own the thresholds mapping confidence -> state, and what to do with
`geometry.next_best_observation`.

## Setup (~10 min, unattended)
    git clone https://github.com/maxzfan/ARBITRAS.git && cd ARBITRAS
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
    console/arbitras/states.py    thresholds -- THE SINGLE SWAP POINT for 21:00
    console/arbitras/machine.py   the arbitras; 5 invariants; pure and importable
    console/arbitras/explain.py   templated explanation + the claim verifier
    console/replay.py            headless replay; arbitrate() + false_surrender_rate()
    console/server.py            tails/replays JSONL, arbitrates, serves SSE
    console/web/index.html       React console (vendored React -- runs offline)
    console/fixture_stream.py    SYNTHETIC dev stream. Not data. Stamped _synthetic.

## Decisions made, and why
- **The state machine is Python, not JS.** §10's FSR needs the arbitras replayed
  over 2,880 epochs per Dirichlet draw. Track C imports `console.replay`. If the
  arbitras lived in React, FSR would be uncomputable.
- **Each SSE connection gets a fresh Arbitras and replays from epoch 0**, so a
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
- [ ] Replay rate reads at 5 epochs/sec in a terminal; §11b says confirm it is
      intelligible on video and slow beats 2 and 3 if not. `--rate 3`.
- [ ] `EMIT_LATERAL_ADVISORY` in machine.py is the cut-order item 3 switch.

## FIXED — tail-mode staleness was tick-based (2026-09-05 11:1x)
`follow()` yielded None on every 0.2 s poll timeout. The arbitras treats each
None as a missing epoch and steps authority down after STALE_GRACE_TICKS of
them, so **a healthy producer emitting slower than the poll interval was driven
to SURRENDERED on clean data.** Appending 3 epochs produced 29 events, 17 of
them spurious SURRENDERED.

Staleness is now wall-clock against the expected epoch cadence: one None per
`--stale-after` window (default 2.0 s), and tail mode is paced by the producer
rather than by `--rate`. Same test now yields 4 events, all justified.

**The wall clock lives in `follow()`, not in `Arbitras`.** The arbitras stays pure
and deterministic so Track C's Dirichlet sweep replays it identically on every
draw. Putting a timeout inside the arbitras would have made FSR non-reproducible.

**At the 18:30 checkpoint: set `--stale-after` to ~3x Track A's actual emit
interval.** The 2.0 s default is a guess about a producer that does not exist yet.

---

# ROUTE FRAME — the vehicle drives; divergence is measured (2026-09-05 ~14:00)

## The construction (do not dilute it)
The receiver at USN8 is a static IGS station. It never moved. The console shows a
vehicle driving an ~820 m route because the operator's question is about a moving
UGV and the arbitration does not care whether the antenna moved. So:

    TRUE     = route_point(s),  s = epoch_index × speed        scripted presentation frame
    BELIEVED = TRUE + D,        D = ENU(position) − ENU(_truth) measured, from the stream

D is the only thing that separates the tracks, and D is data. On out/demo.jsonl it
is now Track C's WLS fix (0 on the lead-in, >15 m at idx 71, 273 m peak at 149, 0
after); on the fixture it reaches 64 m under the SYNTHETIC banner. Nothing invents D.
The on-screen caption states the frame every time the scene is visible.

Files: console/mission.py (ROUTE_ENU, ROUTE_SPEED_M_PER_EPOCH=2.25 for the 510-epoch
demo, CORRIDOR_HALF_WIDTH_M = alert limit, GROUND_STATION_ENU, route_point,
lateral_offset, enu↔latlon), console/web/route.js (JS mirror, parity <1e-6 m —
`node console/tests/route_parity.mjs`), console/tests/test_mission.py, server.py
(/route.js), index.html (Plot, EnvScene, App).

## Views and URL parameters
    ?view=env (default) | split | map | sky
    ?lock=1      no user camera input — use for video capture (§11b comparable frames)
    ?orbit=1     slow camera drift (chase cam only)
    ?exposure=N  tone-mapping exposure (default 1.5; tune on the demo GPU, see below)
    ?post=0      bypass the composer;  ?shadow=0 / ?env=0  lighting diagnostics
    drag         hands the camera to OrbitControls (env: target = true rover, sky: receiver)
    R            reload = hard reset
Camera: damped chase rig RIDES WITH the true rover (offset damped, never position — so
it cannot lag at any frame rate); widening capped at 92 m so the rover stays readable;
beyond that the ghost gets an edge marker "← BELIEVED · N M · OFF FRAME". 2D map follows
the true rover, north-up, corridor band ± alert limit, both trails, receiver cross at
the origin, ground station + uplink.

Ground uplink (design.md §9, from the GROUND station, never a satellite): VALID solid
--truth · PENDING dashed, marching · UNVERIFIED dashed --degraded · EXPIRED/REVOKED
broken at 45 % in --accent. layer=off draws it static VALID (beat 2 is the signal side).

Earth backdrop applied per console/web/EARTH_BACKDROP.md: kloofendal HDRI for
lighting only, procedural sky ~2 stops darker so the constellation stays legible, sun
az 124.4 / el 47.3 (HDR brightest pixel measured at 124.2 / 47.7 — agrees), fog #8F94A1,
grass_path_2 ground (3.5 m tiles), rocks keep the rocky_terrain maps.

## Verified (Playwright, SwiftShader)
- Fixture: |D| 11→64 m, ghost separates and crosses the corridor edge; readout and
  ghost label agree by construction (both written on the epoch event).
- Real stream: rover moves (s 261.7 vs 263.3 expected at epoch 117 — within one epoch);
  headings 93.6° / 97.1° match route legs 2 and 4 exactly; |D| = 121 and 133 m frames
  with the off-frame marker; map at 161 m: believed trail leaves the corridor NW.
- Drag → camMode chase→orbit; ?lock=1 respected. Badge rect identical ON/OFF at 1280
  ([1112.34, 242, 147.66, 36.5]); body overflow 0; six chips fit. 76 tests + node parity.
- Perf: 1.62 M tris / 1,630 draw calls per frame with the composer (×3 passes).
  60 fps on the demo machine is UNVERIFIED — if it stutters, ?post=0 halves the cost.

## Headless caveats — read before trusting a screenshot
- The composer's first frame after the GLB loads takes >8 s under SwiftShader; use
  ≥ 20–26 s per env capture or the canvas is black with a correct HUD and no error.
- Canvas pixels can lag the DOM by seconds; the effective epoch at capture differs from
  ?rate × seconds by a few seconds of page-load. Read __envInfo().epoch, not the clock.
- Headless CANNOT judge absolute brightness (a frame may predate the env map). Tune
  ?exposure on the real GPU; 1.5 is a bright-erring default for a ~0.1-albedo ground.

## Open
- Brightness/exposure on the demo machine; 60 fps check.
- Uplink dash animation verified by material probe, not pixels.
- Ground-station location, route geometry and speed are presentation choices — agree
  them as a team before recording; they are in console/mission.py, one place.
