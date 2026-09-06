# TRACK F / RECON -- mission simulation (scoped 2026-09-05, NOT BUILT)

One of the four mission plans of tracks/TRACK_F.md, produced by a separate
planning agent against the shared brief and verified on the real USN8 day
where it says VERIFIED. Read TRACK_F.md first: the F-0 prerequisites (feature 2
unscored in backend/demo.py, stale thresholds, residual-exclusion cascade) apply
to this mission and are fixed once, page-wide, before it is built. Environment
(terrain, sky, ground, vegetation) is assigned in TRACK_F.md §2 and overrides
the palette hints below.

## RECON — a scout on a patrol loop whose product is a timestamped, georeferenced report; a GPS meaconer corrupts its time, not its position, and the scout keeps patrolling on Galileo + BeiDou

Experiments: `scratchpad/recon_exp.py`, `recon_exp2.py`, `recon_exp3.py` (logs and per-epoch CSVs
beside them). USN8 slice [1380,1650) = 11:30–13:44:30, 120 clean lead-in, 90 attack from 12:30,
60 clean tail; tail check on [1380,1950) (360-epoch tail). Feature 2 fed with `resid` and fitted
with `residual_panel` exactly as `backend/replay.py` does. Every number below is VERIFIED unless
marked ASSUMED. Offsets are epochs from onset.

### Rank
Keep #2 only on the condition that F-R1a is done first as shared plumbing: on `track_f`,
`backend/demo.py score_stream` calls `fx.step(ep)` without `resid` and `main()` fits without
`resid_panel`, so `distrusted()` reads an all-NaN column and returns `[]` on every epoch of every
mission (VERIFIED from code + the preload log "resid sigma UNSET (feature 2 not scored)") — no
mission's geometry block works until that is fixed. With F-R1a in, RECON's own risk is a 12-line
rule with a measured 0/2880 clean false-fire rate; if F-R1a slips, move RECON to #3 behind COMBAT,
whose mechanisms (SIMPLISTIC + credential REVOKED) already exist end to end.

### The mission
A scout UGV leaves a patrol base, runs a 1,013 m loop past three observation points (OP-1, OP-2,
OP-3) and an observation post (OP KESTREL), and returns. At each OP it files a spot report: a
grid, a time, and what it saw. PROJECT.md §1 anchors the platform to the Army's last-tactical-mile
UGV (GroundBreaker 1, Camp Grafton); reconnaissance is the leg "under the greatest threat from
enemy observation and fires", and the report is the thing the fires cell acts on. A report with the
wrong grid or the wrong time is worse than no report.

The operator's question is not "where is the scout" but "can I act on this report": which
constellations stood behind the fix, was the clock its own or a repeater's, and what authority did
the scout hold when it filed. Meaconing is the attack that makes that question sharp: the position
stays right to the metre while the time and the trust behind it are gone.

### The attack — GPS meaconing (design.md §7 row "Meaconing: rebroadcast, common bias across one constellation")
`MEACONING(onset=12:30:00Z, duration_s=2699)` as in `backend/injector/spoof.py`: +8 dB on every
tracked GPS SV (live selection, 12 SVs), +300 m common path delay on code and phase, no walk, no
code/carrier mismatch, capture jitter 1 sigma. 300 m = 1 µs of extra path and 8 dB are stated,
not derived (spoof.py docstring); both are §10 sweep parameters. Observables: C/N0 steps +8 dB;
every GPS pseudorange and carrier shift +300 m together; CMC is untouched by construction.

**Current detector (per-SV residual rule), VERIFIED:** C/N0 anomaly 1.00 at +0, 0.66 at +10, 0.34
at +15, 0.14 at +20 (the 20-epoch trailing mean absorbs the step); pseudorange residual max 0.31
(never fires); CMC max 0.36 (never); cross-constellation 1.00 on 90/90 attack epochs, channels
clk_GE 44.6× scale, clk_GC 32.1×, clk_EC 0.0×, position channels ≤ 0.3×. G clock bias of the
G+E fix +300.0 m. Displacement |D| = 0.000 m on every epoch. **Excluded GPS: 0 of 12 on every
epoch.** Ratio 1.000, bound 12–16 m, `correction_ok` true, next_best_observation None. Confidence
min 0.695, median 0.796 → **NOMINAL 90/90, time-to-alert None.** As built, the default pairing puts
nothing on screen except the feature panel.

Why, from the code: feature 2 is the post-fit residual of the all-in-view solve
(`backend/rinex/solve.py solve()`, one clock column per constellation). A bias that is identical on
every GPS row lies exactly in the G clock column, so the least squares moves dt_G by 300 m and the
residuals do not change — the same linear-algebra fact that keeps D at 0.000 m. A residual rule
cannot see a constellation-uniform bias, by construction.

**Offset-repeater variant (`step_displacement_m`), VERIFIED:** a repeater at a standoff re-radiates
all GPS from its own antenna, so the receiver solves toward it: position-domain step of 300 m
(bearing 90°) on all 12 GPS plus a 300 m common delay (`StepSpoof` in `recon_exp.py`, ~10 lines;
same 300 m provenance). C/N0, residual and cross-constellation all 1.00 at +0; |D| 118.2 m at +0
rising to 138.6 m at +89 (the 12 G / 12 E fix splits the disagreement, 40–46 % of the step); G
clock 222–252 m. Per-SV rule excludes **28 of 30** satellites (10 G, 11 E, 7 C): the all-in-view
solution cannot satisfy both subsets, every row carries a large residual, authentic ones included
(the classic RAIM multi-fault failure); ratio 0.000, bound None, trusted_count 1, confidence 0.092
→ SURRENDERED at +0. With the constellation rule alone: 12/12 G, ratio 0.72, Galileo-only corrected
fix within 0.79 m median / 1.62 m max of truth, `correction_ok` 100 %, PL 8.4 m — the course
correction is demonstrably right while the believed fix is 118 m off. At 100 m standoff: |D| 39→46 m.

**Why the variant is not the stream (360-epoch tail, VERIFIED):** after the repeater switches off,
feature 2 stays ≥ 0.5 until +110 (its 40-epoch trailing median learned the attacked level) and
feature 4 stays at 1.00 for **all 360 tail epochs** (the rise/set compensation in `cross.py` absorbed
part of the position-domain bias at the G rise/set inside the attack; the increment gate then
refuses the return step forever). GPS stays excluded through +449 at the saturation level, and the
45 epochs before the credential lapse are 45/45 below NOMINAL (all DEGRADED). Beat 4 is not legible.
Under plain meaconing the compensation is exact: feature 4 reads 0.05 at +90, no exclusions in the
tail, NOMINAL from +96, pre-lapse window confidence 0.87–0.90, 0/45 below NOMINAL.
**Recommendation: meaconing is the stream; the offset repeater is a README-table variant behind a
flag** (injector extension F-R7, cut first).

### What ARBITER sees — with the constellation-level exclusion (F-R1b), VERIFIED
Rule `odd_constellation(channels)`: for the clock channels (and separately the position
channels), constellation S is distrusted when both channels touching S are ≥ L × their calibrated
scale and the channel not touching S is < L. Scale = each channel's clean-day p99 of trailing |z|
(already in `CrossCal.scale`); L = `CrossCal.feature_sat` = 2.01 = clean-day p99.9 of the
max-over-channels statistic, the level at which the feature itself reads 1.0. No new number.
Clean-day false fires over 2,880 epochs: **0 at L = 2.01** (14 = 0.49 % at L = 1.0, all BeiDou;
0 at L = 3.0). `excluded_sv` = per-SV set ∪ every tracked SV of S.

| offset | features (cn0 / res / cmc / xc) | excluded | ratio | reweight→ / NBO | correction | conf | state |
|---|---|---|---|---|---|---|---|
| −1 | 0.19 / 0.16 / 0.36 / 0.35 | 0 | 1.000 | E / — | ok, PL 4.6 m, 19 trusted | 0.868 | NOMINAL |
| +0 | 1.00 / 0.15 / 0.26 / 1.00 | 12 G | 0.799 | E / G | ok, PL 7.4 m, 12 trusted (E only), D_c 1.25 m | 0.598 | **DEGRADED** |
| +6 | 0.82 / 0.16 / 0.29 / 1.00 | 12 G | 0.718 | E / G | ok, PL 8.7 m, D_c 0.76 m | 0.576 | DEGRADED |
| +20 | 0.14 / 0.17 / 0.30 / 1.00 | 12 G | 0.718 | E / G | ok, PL 9.0 m | 0.670 | DEGRADED (recovery gated) |
| +31 | 0.15 / 0.19 / 0.26 / 1.00 | 12 G | 0.719 | E / G | ok | 0.655 | **NOMINAL** (10 sustained + dwell, on E+C) |
| +60 | 0.14 / 0.17 / 0.30 / 1.00 | 10 G of 10 | 0.794 | E / G | ok, PL 7.9 m | 0.696 | NOMINAL |
| +69 | — | 11 G | 0.69 | E / G | ok | 0.624 | **DEGRADED** (a GPS SV set; ratio fell) |
| +89 | 0.16 / 0.17 / 0.25 / 1.00 | 11 G | 0.692 | E / G | ok, PL 6.8 m, D_c 1.29 m | 0.649 | DEGRADED |
| +90 | 0.98 / 0.17 / 0.22 / 0.05 | 0 | 1.000 | E / — | ok, PL 3.2 m, 19 trusted | 0.822 | DEGRADED (gated) |
| +96 | 0.84 / — / — / 0.06 | 0 | 1.000 | — | ok | 0.84 | **NOMINAL** |
| +165..+209 | clean | 0 | 1.000 | — | ok | 0.87–0.90 | NOMINAL 45/45 |

Ratio 0.68–0.80 (median 0.719) with H = G+E+C, k 3→2 (CLAUDE.md's 0.653 was for k 2→1); bound
13.8 → 14.6 m median (E+C geometry is nearly as good); displacement 0.000 m throughout; the C/N0
spike at +90 is the repeater switching off (−8 dB), itself an event. Time-to-alert 0 epochs.
`next_best_observation` = **"G"** on 90/90 epochs — it ranks readmission (design.md §6b), which is
correct, but `console/arbiter/machine.py` prints it as "Reweight toward G": the spoofed
constellation. Fixed in F-R2 with a derived `geometry.reweight_toward` (argmax over trusted
constellations of norm_info of that constellation's own rows, one determinant each): **E on 90/90
attack epochs** (E 2.40, C 1.24; pre-attack G 1.35, E 2.73).
Confidence sits at 0.57–0.72 under the untuned equal weights, straddling NOMINAL 0.643 (fit on
carry-off epochs only): DEGRADED for 52 of 90 epochs, NOMINAL on E+C for 38. Read honestly this is
§8's "way back" — DEGRADED reweights, ten sustained epochs later authority is restored on the trusted
subset, GPS stays dark — but it is a threshold-session item (limitation 1), not a claim.

### Course correction — a constellation swap and a trust tag, never a motion command
Driven by `geometry.excluded_sv` (all G), `geometry.reweight_toward` (E), `geometry.
next_best_observation` (G: pursue readmission when its clock channels re-agree),
`geometry.correction.{correction_ok, corrected_position, protection_level_m, trusted_count}`,
`credential_status`, and the state. Per state (advisory only):
- NOMINAL — patrol continues; report filed with tag `CLEAN · G+E+C · PL 4.6 m`.
- DEGRADED (active) — reweight toward E; report filed on the **corrected** fix when
  `correction_ok`, tagged `GPS DISTRUSTED · on Galileo · PL 7–9 m · clock in holdover` (invariant 4:
  the clock free-runs, the report's time is flagged as holdover); pursue readmission of G.
- RESTRICTED — finish the leg to the next OP only; report filed, tagged RESTRICTED; no new OPs.
- SURRENDERED — hold; report **withheld** (queued, tagged "authority surrendered"); operator control.
On this stream the trajectory is NOMINAL → DEGRADED (60) → NOMINAL-on-E+C (91) → DEGRADED (129)
→ NOMINAL (156) → SURRENDERED by credential force (390).

### Presentation frame
Route ENU (metres from the surveyed origin), a loop: (0,0) (90,45) (150,70) (230,85) (250,95)
(310,120) (400,100) (450,30) (400,−40) (300,−70) (180,−60) (80,−30) (0,0); length 1,013.3 m.
Speed 2.2 m/epoch (framing choice, labelled): OP-1 at s 165.6 → epoch 75, OP-2 at s 269.4 →
epoch 122, OP KESTREL at s 334.4 → epoch 152, OP-3 at s 703.0 → epoch 320; the credential lapse
at epoch 390 halts the scout at s 858 = (146.9, −50.1) on the home leg — it never gets home.
Alert limit 100 m: one 6-figure grid square, the resolution a spot report locates an observation
to; a report displaced by more than this lands in the wrong square. Provenance "doctrine
convention, PLACEHOLDER until agreed" (same rule as the 15 m). Under meaconing |D| never breaches
(0.0 m); the overlay says so rather than pretending.
Props: `patrol_base` (0,0) "PATROL BASE"; `obs_point` (150,70) "OP-1", (250,95) "OP-2",
(300,−70) "OP-3", radius 15 m; `observation_post` (310,120) "OP KESTREL", radius 40 m;
`hold_marker` at (146.9,−50.1) appears at epoch 390. Ground station (−70, 100).
Camera/palette: dusk, low sun from the west, cooler haze than logistics; camera trails the scout
at 35 m, 18° down; the OPs are small flag-and-berm props on rises of the existing terrain function.
Beats (epoch → caption):
| 0 | Patrol departs. Real observables, USN8, 20 Aug 2026, 30 s epochs. NOMINAL. |
| 60 | A repeater captures GPS: +8 dB, 300 m of extra path. Position: 0.0 m. Nothing moves. |
| 61 | G−E clock jumps 300 m. 12 GPS satellites dropped. Information ratio 0.80. DEGRADED. |
| 75 | OP-1 report filed — GPS distrusted · on Galileo · PL 7 m · clock in holdover. |
| 91 | Authority restored on Galileo + BeiDou after 10 sustained epochs. GPS still dark. |
| 122 | OP-2 report filed — NOMINAL on E+C, 12 GPS excluded. |
| 129 | A GPS satellite sets; ratio 0.69; DEGRADED again. |
| 150 | Repeater off: C/N0 steps −8 dB, clock channels return, GPS readmitted. |
| 156 | NOMINAL, full sky. |
| 320 | OP-3 report filed — clean, credential PENDING (no override). |
| 390 | Authorisation lapsed. SURRENDERED under a clean sky. Scout holds at (147,−50); report queue frozen. |

### Stream recipe (`backend/missions.py` MissionSpec "recon")
- spoof: `MEACONING(onset=datetime(2026,8,20,12,30), duration_s=90*30-1)` — defaults (8 dB, 300 m,
  svs "G", live selection, capture_jitter_sigma 1.0, carrier_rate_error 0.0).
- slice [1440, 1950): 60 clean, 90 attack (12:30:00 → 13:14:30), 360 tail — the team's 12:30 window.
- credential schedule: `credential_schedule()` unchanged (VALID 120 / PENDING 120 / EXPIRED 120).
- exclusion: `distrusted(per_sv, cal) ∪ odd_constellation(xc.score(...)["channels"], xc.cal)`;
  `CorrectionEmitter(nav, alert_limit_m=100.0)`; `set_sigma_uere(1.934)` as today.
- output `out/recon.jsonl`; `_attack` rows as today (stage, n_spoofed 12, range_offset_m 300).
- provenance rows: `geometry.excluded_sv` — derived; per-SV residual ≥ saturation ∪ whole
  constellation when feature 4's odd-constellation rule fires at the feature saturation (clean p99
  scale, p99.9 max statistic; 0/2880 clean fires). `geometry.reweight_toward` — derived, one
  determinant per trusted constellation. `features.cross_constellation_detail` (if F-R6) — derived,
  compensated channel values in metres. Solver sanity on the slice: clean fix h_p50 0.81 m.
- README variant (flag `--variant repeater_offset`): `REPEATER_OFFSET(onset, standoff_m=300)`,
  numbers from the section above, tail pollution stated.

### Console specifics
- Props: flag-and-berm `obs_point`s, a mast for `observation_post`, the base as a ring; the hold
  marker appears only in SURRENDERED.
- Mission readouts (replacing "measured displacement" as the headline): `TRUSTED SKY  E 12 · C 8 ·
  G 0/12`, `REPORT TAG` (state · constellations · PL), `CLOCK` (`GNSS` in NOMINAL, `HOLDOVER since
  12:30:00Z` below it — from `clock_discipline`), displacement 0.0 m kept as a secondary number.
- Observation stamp overlay: when the true track crosses an OP's arc length, a card pins to the map
  and the scene: OP name, local 100 m grid of the believed position (floor(e/100), floor(n/100)),
  UTC timestamp, tag; a tie-line from where the report says it was (believed) to where it was
  (true) — 0.0 m here, captioned "position unchanged · time distrusted". In SURRENDERED the card is
  greyed "WITHHELD". Stamps stay on screen for the rest of the run.
- Explanation phrases: "One constellation disagrees with the others on time" (cross_constellation
  when the clock channels fire), "GPS dropped from the trusted set; position holds on Galileo",
  "Reweight toward Galileo. Pursue GPS readmission when its clock channel re-agrees." (DEGRADED
  advisory), "Repeater switched off; GPS readmitted after ten sustained epochs."
- Preview tile: the sky dome (existing `SatLayer`) with the scout as a moving dot on the loop below
  it; at tile-time 2 s the 12 GPS sprites go dark while the dot keeps moving, at 5 s they relight.
  Caption "RECON · meaconing · position 0.0 m, time 300 m".

### Build decomposition
- **F-R1a** (shared, 0.5 h) — `demo.score_stream`: `res = fx.step(ep, resid=(sols.get("all") or
  {}).get("resid_m"))` with `sols = solve_per_constellation(ep, nav_xc)` computed once and passed to
  `xc.score`; `main()` fits `fit(clean, floor, resid_panel=residual_panel(clean, nav_xc))`.
  Acceptance: regenerated `out/clean.jsonl` has ≥1 excluded SV on ~60 % of epochs (features.py
  table, k = 1), not 0 %.
- **F-R1b** (0.5 h) — `cross.py odd_constellation(channels, cal, level=None)`; `score()` returns
  `"odd_constellation"`. Acceptance: 0/2880 fires on the clean day; "G" on 90/90 meaconing epochs.
- **F-R1c** (0.25 h) — `demo.distrusted` unions the constellation. Acceptance: recon stream shows
  `excluded_sv` = all tracked G on every attack epoch, `information_ratio` 0.68–0.80.
- **F-R2** (0.75 h) — `engine.compute` emits `reweight_toward` (null with < 2 trusted
  constellations); `machine._emit` advisory uses it and names NBO as the readmission target;
  explain.py phrases. Acceptance: `test_machine.py` unchanged and green; verifier 0 failures on
  recon.jsonl; advisory reads "Reweight toward E" on every DEGRADED epoch.
- **F-R3** (0.75 h) — `console/missions/recon.py` + `backend/missions.py` spec; `python -m
  backend.demo --mission recon`. Acceptance: `python -m console.replay out/recon.jsonl` prints
  transitions at 60 →DEGRADED, 91 →NOMINAL, 129 →DEGRADED, 156 →NOMINAL, 390 →SURRENDERED
  (credential_force); `/mission?name=recon` returns the route and props above.
- **F-R4** (1.25 h) — stamp overlay + mission readouts in `index.html` (`App`, `Plot`, `EnvScene`).
  Acceptance: three cards at epochs 75, 122, 320 with the tags above; WITHHELD text from 390.
- **F-R5** (0.75 h) — props and the preview tile. Acceptance: tile cycles in ~6 s on the shared
  renderer; props at the listed ENU coordinates; caption present.
- **F-R6** (0.5 h, optional) — `features.cross_constellation_detail` and the "repeater within
  300 m" ring (|clk_GE| jump) in DEGRADED. Acceptance: ring radius equals the channel value ±1 m.
- **F-R7** (0.5 h, optional) — `step_displacement_m` on `Spoof` (`displacement_m = step + rate ×
  walked_s`) and the README variant run. Acceptance: |D| 118.2 m at +0 on the slice above.
Total 5.75 h with options, 4.75 h without.

### Cut rule
F-R1a is not cut: it is a bug fix every mission's geometry block depends on. Cut order: F-R7, F-R6,
F-R5 props (keep the tile), F-R4 collapses to a one-line stamp in the explanation bar. If F-R1b/c
are not in by the time the page owner wires missions, **cut RECON entirely**: without them the stream
reads NOMINAL 90/90 with nothing excluded and 0.0 m of displacement (VERIFIED), which argues
against the product. The selector then shows three columns; nothing else on the page changes.

### Limitations to state in the README
1. Under equal placeholder weights the meaconing signature (one saturated feature + a 0.28 geometry
   deficit) scores 0.57–0.72, straddling the NOMINAL threshold 0.643 fit on carry-off epochs only;
   the state recovers to NOMINAL on E+C at +31 and dips at +69 when a GPS satellite sets. The §10
   session must include meaconing epochs before any threshold is claimed for it.
2. The believed position is unchanged by construction (0.000 m); the consequence is the trust tag
   and the clock, not a displaced stamp. The offset-repeater variant displaces the fix 118–139 m at
   300 m standoff, and its post-attack pollution of feature 4 (360/360 tail epochs saturated, GPS
   excluded through +449) is measured, not fixed — which is why it is a table row, not the stream.
3. The constellation rule needs three solvable constellations (≥ 4 SVs each); with two, "odd one
   out" is undefined and it returns None, falling back to the per-SV rule, which is blind to a
   uniform bias.
4. The corrected fix under a GPS drop is Galileo-only: the corrector's nav tables cover G+E, so
   BeiDou rows are uncorrectable (trusted_count 11–12, PL 7.4–9.05 m).
5. `clock_discipline` is a single boolean; the honest state under a constellation drop is
   "disciplined to the trusted subset's clock", which the contract cannot yet express. The 1 µs
   (300 m) offset is operationally small at 30 s epochs; a record-and-replay meaconer with seconds
   of delay is the credential-forgery case (§9) and was not injected.
6. `next_best_observation` names the constellation whose readmission recovers the most
   information — "G" under a G drop. The old advisory string misread it as a reweighting target.
7. 300 m / 8 dB are stated (spoof.py); the 100 m alert limit is a doctrine convention; the clean
   false-fire rate of the rule is one station, one day; the route is a presentation frame and the
   receiver never moved.
