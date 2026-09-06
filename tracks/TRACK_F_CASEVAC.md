# TRACK F / CASEVAC -- mission simulation (scoped 2026-09-05, NOT BUILT)

One of the four mission plans of tracks/TRACK_F.md, produced by a separate
planning agent against the shared brief and verified on the real USN8 day
where it says VERIFIED. Read TRACK_F.md first: the F-0 prerequisites (feature 2
unscored in backend/demo.py, stale thresholds, residual-exclusion cascade) apply
to this mission and are fixed once, page-wide, before it is built. Environment
(terrain, sky, ground, vegetation) is assigned in TRACK_F.md §2 and overrides
the palette hints below.

## CASEVAC — a UGV sent to a casualty collection point is walked into believing it has arrived 240 m short of it

All numbers below are VERIFIED on the real USN8 day unless marked ASSUMED. Experiment:
`scratchpad/casevac_exp.py` — 200/260-epoch slices from 12:00, onset 12:30 = idx 60, top-6 GPS
= G05 G11 G15 G20 G21 G29, `CARRY_OFF(carrier_rate_error=0.0, bearing_deg=135)`, differential
WLS, extractor fed the post-fit residual as `backend/replay.py` does, `CrossConstellation`,
`compute_geometry_block`, `CorrectionEmitter`, `console.replay.arbitrate`; logs beside it.

**Shared finding, blocks every mission, not only this one.** `backend/demo.py` calls
`fx.step(ep)` with no `resid=` and `fit(clean, floor)` with no `resid_panel`, so in every
demo-generated stream feature 2 (`pseudorange_residual`, the post-fit residual since commit
76293c9) is 0.000 on every epoch, `distrusted()` excludes nothing, and the arbiter never leaves
NOMINAL on signal: tonight's `out/demo.jsonl` (21:08) has attack-window confidence min 0.774 /
p50 0.802 and its only transition is the credential force at epoch 390 (`python -m
console.replay out/demo.jsonl`). Re-run on my slice with the demo wiring: TTA = None, 90/90
attack epochs NOMINAL, believed pin inside the CCP ring at onset+19 with the badge reading
NOMINAL. `backend/replay.py` already has the six lines that fix it (`residual_panel(clean,
nav)` → `fit(..., resid_panel=)`; per epoch `fx.step(ep, resid=sols["all"]["resid_m"])`).
F-C0 below is that fix; it is a prerequisite for CASEVAC and for the other three.

### Rank — should this move up or down the build order, and why (2 sentences)
Move CASEVAC up to #2, above RECON: it is the one mission whose attack is blind to feature 3
by construction and is still caught in 2 epochs by the residual/geometry halves (the honest
answer to "your injector was easy"), its numbers are verified end to end today, and the only
new console logic is a three-line arrival rule over fields Track D already emits. RECON's
meaconing moves the fix 0.000 m, so its consequence overlay has nothing to draw; CASEVAC's
consequence (believed pin inside the ring, vehicle 240 m short) is the most legible failure
on the page.

### The mission
A UGV leaves a Role 1 aid station, drives ~420 m to a casualty collection point (CCP), loads
a litter, and comes back — PROJECT.md §1's "casualty evacuation back, across the segment under
the greatest threat from enemy observation and fires". It is time-critical and point-to-point:
a medic is standing at the CCP with a patient, and the vehicle's arrival report commits the
next step of the chain (medevac window, dismount exposure). The failure that matters here is
not drifting off the road — it is stopping at the wrong place, or reporting "arrived" when it
has not. An along-track walk does exactly that: the believed position runs ahead of the true
one along the route, so the corridor overlay never fires (lateral offset stays ~0) and an
unprotected stack halts 240 m short in the open, reporting success.

The operator's question is therefore two questions: *may the vehicle keep driving on this
position*, and, at the end, *may it declare arrival and stop*. ARBITER answers the first with
the trust state and the second with the Track D gate: "at CCP" is a claim about the corrected
fix and its protection level, never about the believed pin. Advisory only, never a motion
command (design.md §8).

### The attack
Design.md §7 row 2, position domain, sophisticated (1–3 dB): `CARRY_OFF(onset=12:30,
carrier_rate_error=0.0, bearing_deg=135.0, duration_s=60*30-1, target_svs=top_n_by_elevation(6))`,
power 2 dB, capture 10 s, walk 1 m/s commanded horizontal displacement. `carrier_rate_error
= 0.0` is the injector's supported coherent case: code and carrier move together, no lift-off
CMC transient, so feature 3 sees nothing by construction. The injector docstring's "features 2
and 3 see nothing" is stale since feature 2 became the post-fit residual — feature 2 sees the
displacement, and that is the whole point of this mission.

Bearing 135° (compass, clockwise from ENU north at USN8) is the heading of the route's CCP leg.
It is chosen for presentation, from the §7 sweep set, and is not derived from H or from the
detector; the detector does not know the route. Sweep over all eight bearings at rate 0
(VERIFIED, k=1 rule): achieved bearing 2.7/38.3/79.1/135.1/182.7/218.3/259.1/315.1° for
commanded 0/45/…/315°; achieved rate 12.7/14.4/11.6/9.4/12.7/14.4/11.6/9.4 m per 30 s epoch
(achieved/commanded 0.37–0.47 — the 13 unspoofed G+E satellites pull the fix back); TTA 2 at
every bearing; feature firing identical. 135° is used because its achieved displacement tracks
the command to 0.1°, so "along-track" is measured, not asserted.

What happens to the observables (60-epoch attack, VERIFIED):
- Believed fix: 7.6 m at +1, then 9.41 m/epoch, 632.5 m at +59 (1760 m commanded); 0.0 m
  from +60 (post-attack max 0.000 m). Lead-in |D| 0.0000 m.
- Feature 2 (post-fit residual): 0.76 at +1, 0.89 at +2, 1.00 from +5 (clean-slice max 0.314).
- Feature 4 (cross-constellation): 0.28 at +1, ≥0.5 at +4, 1.00 from +6 — but one clean epoch
  in the 200-epoch slice also reads 1.0 and the lead-in wanders 0.02–0.55; it fires late and
  is not clean on its own.
- Feature 1 (C/N0): one epoch, 0.37 at +0 vs 0.31 clean max, back to 0.25 at +1. Never ≥ 0.5.
- Feature 3 (CMC): never fires — peak 0.36 vs clean-slice max 0.43.
- Exclusions (k=1 demo rule): 6 at +1, 16 at +2, 25 at +5, 26–29 through the walk;
  information ratio 0.735 → 0.356 → 0.126 → 0.000 (+1, +2, +4, +5); bound 15.9 → 22.9 →
  26.9 → null.

Coherence cost, same run at the demo pin 0.02 m/s (VERIFIED): TTA 1 instead of 2 (0.618 →
DEGRADED at +1, SURRENDERED at +2, vs 0.669 NOMINAL at +1, SURRENDERED at +2); pre-alert
displacement 0.0 m instead of 7.6 m; feature 3 0.71 at +1 vs 0.29; attack-window confidence
p50 0.178 vs 0.199. Exclusions, displacement, correction gate and recovery are identical.
Coherence costs the detector exactly one epoch (30 s of file time) and 7.6 m.

### What ARBITER sees
State trajectory, epochs from onset (k=1 demo distrust rule, current thresholds 0.643 /
0.548 / 0.518; VERIFIED on the 60-epoch mission run):
- +0 CAPTURE: conf 0.865, NOMINAL; correction_ok true, PL 4.56 m.
- +1 WALK: conf 0.669, NOMINAL; 6 excluded; correction_ok true, corrected fix 1.3 m from
  truth, PL 7.56 m; believed 7.6 m ahead.
- +2: conf 0.445 → **SURRENDERED** directly (invariant 1, states skipped); 16 excluded, ir
  0.356; **correction_ok revoked** (`pl_under_al`: PL 35.2 m > 15 m); corrected fix 5.7 m.
- +3: PL 862.9 m; +4 onward `redundancy` fails, corrected_position null, bound null.
- +2…+59: SURRENDERED, conf p50 0.20 (p25 0.19, p95 0.26).
- +60 attack stops: displacement 0.0 m, but 26–28 satellites stay distrusted for 21 epochs —
  the residual feature's 40-epoch trailing median is full of attack residuals.
- +81 exclusions clear (feature 2 0.34), ir 1.000, PL 3.46 m; gate starts counting.
- +90 correction_ok re-granted (10 passes) and SURRENDERED → RESTRICTED (10 epochs above
  0.518); +100 DEGRADED; +110 NOMINAL. Time from attack end to full authority: 50 epochs.

Is feature 2 enough on its own? Through the tuned half, no: with equal weights and β 0.5 the
feature sum peaks at anomaly 0.625 (cn0 0.2 + pr 1.0 + cmc 0.3 + xc 1.0)/4 → conf 0.69, above
0.643. Through the derived half, yes: feature 2 drives the exclusions, the exclusions drive
the information ratio, and at +2 the deficit (0.644) contributes 0.322 of the 0.555 drop
(anomaly 0.466 → 0.233). The coherent walk is caught by geometry, fed by residuals. Under the
same wiring the C/N0 feature is one epoch of evidence and CMC is none.

Alternative rule, FLAT_K5 (VERIFIED, 135°): full staircase on signal — DEGRADED +3 (0.628),
RESTRICTED +5 (0.537), SURRENDERED +6 (0.474); exclusions 2/5/9 at +2/+3/+5 (ir 0.918 /
0.749 / 0.625), next_best_observation "E"; the corrected fix holds truth to ≤ 2.9 m through
+11 while the believed fix is 120 m off, then loses redundancy at +17. But pre-alert
displacement is 19.0 m against a 15.4 m bound and the 15 m alert limit — the §10 integrity
claim fails by one epoch. Under k=1: 7.6 m against 15.9 m, holds. CASEVAC uses k=1.

Correction gate summary: revokes at +2, stays revoked through +89 (`pl_under_al` then
`redundancy`), re-grants at +90; max corrected-fix error while correction_ok is true: 1.34 m.

### Course correction
"Correct" for CASEVAC is the arrival decision, on top of the shared three-track picture.
TRUE = `route_point(s)`; BELIEVED = TRUE + D, D = ENU(`position`) − ENU(`_truth`); CORRECTED =
TRUE + D_c, D_c = ENU(`geometry.correction.corrected_position`) − ENU(`_truth`). With `ccp`
the prop of kind `ccp` and R = `ccp.radius_m`:

    at_ccp = correction.correction_ok
             && correction.corrected_position != null
             && |CORRECTED − ccp| <= R
             && correction.protection_level_m != null && protection_level_m < R
    believed_at_ccp = |BELIEVED − ccp| <= R

Status string: `AT CCP · CONFIRMED` when `at_ccp`; `BELIEVED AT CCP · NOT CONFIRMED` when
`believed_at_ccp && !at_ccp`; else `EN ROUTE · <range> m to CCP`. The believed pin never
confirms anything. On the mission stream: NOT CONFIRMED from idx 80 to 84 (believed inside
the ring, true 240 → 231 m short, state SURRENDERED, correction_ok false), then the believed
pin overshoots; CONFIRMED first at idx 176 (NOMINAL, correction_ok true, PL 3.19 m, corrected
fix 0.48 m from truth). Both VERIFIED.

Per state, advisory only: NOMINAL — drive to the CCP on GNSS, declare arrival only via
`at_ccp`. DEGRADED — continue on inertial, pursue `geometry.next_best_observation` (reweight
advisory; lateral-offset sentence per `EMIT_LATERAL_ADVISORY`), arrival cannot be declared
unless `at_ccp` also holds. RESTRICTED — finish the current leg only, no arrival declaration
(`correction_ok` is false whenever the state is below DEGRADED on this stream). SURRENDERED —
hold, report "believed at CCP, NOT confirmed — true position unknown to the vehicle" to the
operator. Honest dwell: under k=1 the vehicle spends 0 epochs in DEGRADED under attack (2
under FLAT_K5, nbo "E"); the DEGRADED beat on this mission is the recovery step (idx 160–169,
nothing excluded, no advisory). The mission's active behaviour is the arrival gate.

### Presentation frame
Origin = USN8 surveyed point = the aid station. Route ENU (e, n) metres, 960.0 m:
`[(0.0, 0.0), (297.0, -297.0), (339.4, -254.6), (42.4, 42.4), (0.0, 0.0)]` — a 420.0 m
outbound leg on heading 135°, a 60.0 m turn at the CCP, a 420.0 m return leg 60 m to port,
60.0 m back to the start. Speed 2.25 m/epoch (unchanged; the vehicle does not stop at the CCP
in this frame — captioned). Onset idx 60 = s 135 m; CCP at s 420 = idx 187; ring entry
s 395 = idx 176; stream ends at idx 299 = s 673 m, on the return leg.

Alert limit 15.0 m, provenance `PLACEHOLDER` (corridor half-width, `console/mission.py`;
the Track D thresholds were fit against it, so it is not changed per mission). Arrival radius
25.0 m, provenance "half the 50 m diameter of a size-3 landing point (UH-60 class) in the
Army pathfinder landing-point table — STATED from doctrine, citation unverified, mark
`arrival_radius_provenance: PLACEHOLDER` until checked"; the data constraint is that it must
exceed the clean-day protection-level tail (fit max 6.67 m; slice lead-in 4.03–4.70 m) or
clean arrivals could never confirm. Ground station (−90.0, 60.0): the aid station's uplink,
distinct from the satellite links.

Props: `{"kind":"ccp","e":297.0,"n":-297.0,"label":"CCP · casualty collection point","radius_m":25.0}`;
`{"kind":"litter","e":304.0,"n":-290.0,"label":"casualty · medic waiting"}` (inside the ring,
the medevac marker); `{"kind":"aid_station","e":0.0,"n":0.0,"label":"Role 1 aid station"}`.
Consequence overlay: the ring turns amber with the caption `BELIEVED AT CCP · NOT CONFIRMED ·
true position 240 m short (replay truth)` while the believed pin is inside and `at_ccp` is
false; green `AT CCP · CONFIRMED` when `at_ccp`; the true vehicle keeps its route-frame caption.

Camera/palette: dusk, low warm key (continuity with the shared HDRI), a red-cross panel and
a ground litter at the CCP, ring drawn flat on the terrain; the map view must fit TRUE,
BELIEVED and the CCP in frame — the believed pin reaches 632 m SE of the vehicle. Beats
(stream idx → caption, 15 epochs/s):

| idx | caption |
|---|---|
| 0 | Role 1 aid station. UGV dispatched to the CCP, 420 m south-east. Real observables, USN8, 20 Aug, 30 s epochs. NOMINAL. |
| 60 | Carry-off begins: six highest GPS satellites captured, code and carrier coherent. Signal strength blips for one epoch. |
| 61 | Ranges no longer fit one position: post-fit residual 0.76, six satellites distrusted, believed fix 7.6 m ahead of truth. |
| 62 | SURRENDERED. Confidence 0.45, information ratio 0.36. Protection level 35 m exceeds the 15 m limit — corrected fix withdrawn. |
| 80 | The believed pin enters the collection-point ring. The vehicle is 240 m short. Arrival NOT confirmed. |
| 84 | Believed pin overshoots the CCP; the walk continues at 9.4 m per epoch, straight along the route. |
| 120 | Attack stops. Displacement 0.0 m — but the residual baseline is polluted: 28 satellites stay distrusted. |
| 141 | Distrust clears, 21 epochs after the attack stopped. Protection level 3.5 m. |
| 150 | Corrected fix re-granted after ten clean passes. RESTRICTED. |
| 170 | NOMINAL, restored one step at a time. |
| 176 | AT CCP — confirmed. Corrected fix 0.5 m from truth, protection level 3.2 m, inside the 25 m ring. |
| 187 | Casualty aboard. Return leg. |

### Stream recipe
`MissionSpec("casevac")` in `backend/missions.py`, read by `python -m backend.demo --mission
casevac`: spoof `CARRY_OFF(onset=datetime(2026,8,20,12,30), carrier_rate_error=0.0,
bearing_deg=135.0, duration_s=60*EPOCH_S-1, target_svs=top_n_by_elevation(6))`; PRE 60,
ATTACK 60, POST 180 → slice idx [onset−60, onset+240) = 300 epochs, 12:00:00 → 14:29:30;
credential `VALID` throughout (the credential beat belongs to LOGISTICS/COMBAT); output
`out/casevac.jsonl` plus `out/casevac_truth.csv`. Shared once: load, floor, `residual_panel`,
`fit(resid_panel=)`, `fit_cross`, `NavTables`, `set_sigma_uere(1.934)`; per mission `xc.reset()`,
`corrector.reset()`. Distrust rule k=1 (`distrusted()`), as the demo.

Provenance rows to add to `docs/stream_provenance.md` (casevac section):
- `_attack.bearing_deg 135` — CCP-leg heading, presentational, from the §7 sweep set, not
  derived from H or the detector; achieved bearing measured 135.0° (sweep: 2.7–11° off at
  four other bearings).
- `carrier_rate_error 0.0` — fully coherent: feature 3 peak 0.36 vs clean 0.43 (sees nothing,
  by construction); feature 2 0.76 at +1 (sees the displacement).
- `duration_s 1799` — 60 attack epochs; 632.5 m achieved at +59 (0.374 of commanded).
- `target` — `top_n_by_elevation(6)` resolved at 12:30: G05 G11 G15 G20 G21 G29, held.
- `props[ccp].radius_m 25.0` — STATED (doctrine), PLACEHOLDER provenance; > clean PL tail.
- post-attack distrust hangover 21 epochs — derived: `FeatureConfig.cmc_window` (40) shared
  by the residual deque; time to re-grant 30 epochs, to NOMINAL 50 epochs.
- `features.cross_constellation` in the tail 0.78–0.92 for the remaining 180 epochs vs
  0.02–0.55 before onset — the gated trailing baseline does not recover within the slice.

### Console specifics
- Registry `console/missions/casevac.py`: `Mission(name="casevac", title="CASEVAC",
  tagline="Extraction to a collection point. The failure is arriving at the wrong place.",
  route_enu=…, speed_m_per_epoch=2.25, alert_limit_m=15.0, alert_limit_provenance="PLACEHOLDER",
  ground_station_enu=(-90.0, 60.0), props=[…], stream="out/casevac.jsonl", attack={...}, beats=[…],
  scene={"palette":"dusk","ambience":"medevac"})`. Attack card: name "Coherent carry-off,
  along-track"; mechanism "six GPS satellites captured at +2 dB, believed position walked 1 m/s
  along the route heading; code and carrier stay coherent"; watch "post-fit residual, distrusted
  count, information ratio, protection level"; ARBITER does "surrenders at +2, withdraws the
  corrected fix, refuses to confirm arrival until the gate re-grants".
- Props rendering: `EnvScene` gains a `props` pass — ring (`radius_m`) as a flat torus on the
  terrain, red-cross panel + litter mesh at `ccp`/`litter`, a tent primitive at `aid_station`;
  `Plot` draws the ring and CCP marker in ENU. Ring colour bound to the arrival status.
- Readouts (new `Arrival` strip tile, all from one sampled epoch): `CCP RANGE` = |TRUE − ccp|
  (route frame); `BELIEVED` = |BELIEVED − ccp|; `CORRECTED` = |CORRECTED − ccp| or `—`;
  `PL` = `geometry.correction.protection_level_m`; status string. Contract paths on every
  number: `position`, `_truth` (replay metadata), `geometry.correction.corrected_position`,
  `.protection_level_m`, `.correction_ok`, `.checks`, mission `props[ccp].radius_m`.
- Explanation phrases (operator language, verifiable claims): update
  `FEATURE_PHRASE["pseudorange_residual"]` to "the satellite ranges no longer fit a single
  position" (the CMC-era wording is stale); CASEVAC detail suffix, console-side, appended to
  `explanation.detail` with claims: NOT CONFIRMED → "The vehicle believes it is at the
  collection point. The corrected fix cannot be confirmed (protection level {PL} m; ring
  {R} m). Do not stop on this position." CONFIRMED → "Collection point confirmed: corrected fix
  inside the {R} m ring, protection level {PL} m." Under SURRENDERED the existing headline
  stands ("Holding position. Control is with the operator.").
- Preview tile (shared renderer, scissor viewport, scripted, captioned "preview · scripted"):
  5 s loop — vehicle approaches the ring; at 40 % a ghost peels ahead along the leg, enters the
  ring, ring pulses amber; ghost fades; vehicle reaches the ring, ring turns green; reset.

### Build decomposition
- **F-C0 residual wiring in the stream generator** (0.5 h, shared prerequisite). In
  `backend/demo.py`/`backend/missions.py`: `rp = residual_panel(clean, nav_xc)`;
  `cal = fit(clean, floor, resid_panel=rp)`; per epoch `sols = solve_per_constellation(ep, nav_xc)`
  shared between `fx.step(ep, resid=sols["all"]["resid_m"])` and `xc.score(ep, sols)` — mirror
  `backend/replay.py` lines 84–88. Acceptance: `python -m console.replay out/casevac.jsonl`
  prints `NOMINAL -> SURRENDERED` at epoch 62; `features.pseudorange_residual` ≥ 0.5 at epoch
  61; clean-day FSR check still 0 (thresholds are stale — see limitations).
- **F-C1 registry + stream** (0.75 h): `console/missions/casevac.py`, `MissionSpec` entry,
  `python -m backend.demo --mission casevac`. Acceptance: `/mission?name=casevac` returns
  `route_length_m` 960.0, a `ccp` prop with `radius_m` 25.0; stream 300 epochs, `_attack.stage`
  WALK at idx 61–119 and CLEAN at 120; `_solution.displacement_m` 0.0 before idx 61 and ≥ 600
  at idx 119.
- **F-C2 arrival rule + readout** (1.0 h): `arrival(m, epoch)` in `route.js` (pure, mirrored in
  `console/mission.py` for the test), `Arrival` tile. Acceptance: unit test with three fixture
  epochs (ok+PL 3.2+inside → CONFIRMED; ok false → NOT CONFIRMED; PL 30 → NOT CONFIRMED);
  on the stream NOT CONFIRMED at idx 80–84, CONFIRMED first at idx 176.
- **F-C3 props + consequence overlay** (1.0 h): ring/panel/litter/tent in `EnvScene` and
  `Plot`; ring colour and caption from the arrival status; map fit-to-bounds over TRUE,
  BELIEVED, CCP. Acceptance: screenshots at idx 82 (amber, "240 m short") and 176 (green).
- **F-C4 phrases + beats** (0.5 h): phrase update, CASEVAC detail suffix with claims, beats
  table. Acceptance: `verify()` reports zero `explanation_failures` over all 300 epochs.
- **F-C5 preview tile** (0.75 h): scripted loop in the shared renderer. Acceptance: four tiles
  animate from one WebGL context at ≥ 30 fps on the demo laptop; caption "preview · scripted".
Total 4.5 h.

### Cut rule
If F-C0 is not merged, cut CASEVAC entirely: without feature 2 the badge reads NOMINAL while
the believed pin sits inside the ring, which contradicts the page. If F-C2/F-C3 slip, ship
CASEVAC as a route-and-props variant of LOGISTICS (ring drawn, no arrival logic) with the
believed-in-ring caption only. Removal is one registry file and one `MissionSpec`; the
selector falls back to three tiles; nothing else depends on CASEVAC.

### Limitations to state in the README
1. The bearing is presentational: the CCP leg was laid along the commanded 135°, whose
   achieved displacement happens to track it (135.0°); at 45/90/225/270° the fix walks
   7–11° off the command. The detector never sees the route.
2. In the presentation frame the vehicle never stops: a real UGV would halt at "believed CCP"
   240 m short. The consequence is drawn, not enacted, and captioned as such.
3. The k=1 distrust rule over-excludes under a 6-satellite walk: 25 of ~30 H rows gone by +5,
   information ratio 0.000, bound null, corrector without redundancy from +4. The geometry
   half is legible for three epochs, then reads "no bound" — alarming, correctly, but not a
   number. Under FLAT_K5 it degrades gradually (0.92 → 0.06 over +2…+20) at the cost of a
   19.0 m pre-alert displacement that exceeds the 15.4 m bound and the alert limit.
4. Arrival can only be confirmed after full recovery: 21 epochs of distrust hangover from the
   residual feature's trailing median plus the 10-epoch grant — 30 epochs (15 min of file time)
   after the attack stops, 50 to NOMINAL.
5. `THRESHOLD_PROVENANCE` describes streams generated with the earlier feature 2; on the
   post-fit residual the attack-window confidence is p50 0.20, the staircase is skipped
   (NOMINAL → SURRENDERED at +2) and DEGRADED's active behaviour is not exercised by this
   attack. Re-measure at the threshold session or say so on screen.
6. The cross-constellation baseline does not recover within the slice (0.78–0.92 for 180
   post-attack epochs vs 0.02–0.55 before); one clean epoch in the slice reads 1.0.
7. The 25 m arrival radius is a stated doctrine figure, unverified; the 15 m limit is PLACEHOLDER.
8. Perfect coherence is a modelling extreme in feature 3's favour (the 0.02 m/s pin buys one
   epoch); synthetic attack, one station, one day, static receiver (shared limitations 1–2).
