# TRACK F / LOGISTICS -- mission simulation (scoped 2026-09-05, NOT BUILT)

One of the four mission plans of tracks/TRACK_F.md, produced by a separate
planning agent against the shared brief and verified on the real USN8 day
where it says VERIFIED. Read TRACK_F.md first: the F-0 prerequisites (feature 2
unscored in backend/demo.py, stale thresholds, residual-exclusion cascade) apply
to this mission and are fixed once, page-wide, before it is built. Environment
(terrain, sky, ground, vegetation) is assigned in TRACK_F.md §2 and overrides
the palette hints below.

## LOGISTICS — last-tactical-mile resupply convoy walked off its corridor by a position-domain carry-off

Evidence labels. **ON-DISK** = `out/demo.jsonl` / `out/carryoff.jsonl` as regenerated 21:08
tonight, replayed with `python -m console.replay` and `backend.measurement.displacement`.
**RE-SCORED** = my re-scoring of a 350-epoch slice of the same day (reported from 12:00Z,
onset = demo idx 60; `scratchpad/logistics_experiment.py` / `logistics_variants.py`, 35 s
each) with the CURRENT detector: feature 2 = post-fit residual calibrated with
`residual_panel()` exactly as `backend/replay.py` does, Track D corrector, shipped
thresholds 0.643/0.548/0.518. **CLEAN** = the same pipeline over all 2,880 clean epochs.
VERIFIED = I ran it; ASSUMED = I did not. No stream regenerated; no repo file edited.

### Rank — should this move up or down the build order, and why (2 sentences)
Keep it at #1: it is the only mission whose stream, scene, beats and provenance exist end
to end, and its first task (F-L1, the feature-2 seam fix in `backend/demo.py`) is a
prerequisite for every other mission because all four generate through the same
`backend/demo.py --mission` path. The page owner must schedule around the fact that the
first hour of LOGISTICS is a repair, not a build — the on-disk stream detects nothing on
signal alone (VERIFIED) — so no mission should regenerate its stream before F-L1 lands.

### The mission
A resupply UGV convoy leaves a forward operating base and drives a ~830 m trafficable
corridor to a resupply point — PROJECT.md §1 verbatim ("resupply forward, casualty
evacuation back, across the segment under the greatest threat from enemy observation and
fires"; GroundBreaker 1, Camp Grafton). The corridor is the only ground the vehicle may
drive: a single-track unimproved road whose half-width is the alert limit. A position error
larger than that half-width puts the convoy off the road — mines, soft ground, observation
— before any sensor on the vehicle disagrees.

Position trust matters HERE because the adversary's cheapest win is not stopping the convoy
but walking it sideways: a 1–3 dB carry-off on the high sky (§7 row 2) moves the believed
position across the corridor while every observable looks healthy. The operator's question
is two-part and the console answers both: *is the convoy on the road it thinks it is on*
(believed vs true against the corridor band), and *may it keep driving* (Track D's corrected
fix and protection level against the 15 m alert limit, gated by trust state).

### The attack
Scenario: intermediate carry-off, position domain (design.md §7 row 2, "primary demo").
`CARRY_OFF(onset=2026-08-20T12:30:00Z, carrier_rate_error=0.02, bearing_deg=90,
target_svs=top_n_by_elevation(6), duration_s=2699)`: +2 dB, per-SV range offsets
`−e_sv·dp`, CMC divergence 0.02 m/s (test value; pin unset). Captured set, resolved at
12:30:00Z and held: **G05 G11 G15 G20 G21 G29** (VERIFIED, `out/carryoff_truth.csv`). Two
walk rates were run; **0.2 m/s is the LOGISTICS recipe** (reasons below), 1 m/s is the §7
reference. Observables at 1 m/s: all-in-view residual RMS 1.9 → 4.5 → 10.9 → 17.0 → 30.6 m
over +0..+5; CMC diverges 0.6 m/epoch; C/N0 steps +2 dB for one epoch of file time.

Displacement, 1 m/s (VERIFIED, identical ON-DISK and RE-SCORED — same solver, same data):
first >1 m at +1 (7.4 m), first >15 m at +2 (18.6 m), 11.4 m/epoch, peak **1027.0 m at
+89** (commanded 2660 m, achieved/commanded 0.386), post-attack 0.0000 m. Achieved bearing
80.8° → 76.9°, set by the spoofed subset's geometry. **The README/provenance "273.5 m
peak" is stale text**: both streams read 1027.05 m at idx 149.
Displacement, 0.2 m/s (VERIFIED RE-SCORED): +1..+9 = 1, 4, 6, 8, 10.5, 13, **15.1 (+7)**,
17, 20 m; ~2.3 m/epoch; peak 205.4 m at +89.

**What the ON-DISK stream does (VERIFIED).** `console.replay out/demo.jsonl`: NOMINAL for
all 390 signal epochs, one transition, `390 NOMINAL → SURRENDERED (credential_force)`.
`backend.measurement.displacement out/carryoff.jsonl`: **FAIL 2792/2880**, first violation
12:31:00 (18.6 m > 15.6 m). `features.pseudorange_residual` is 0.0 on every epoch,
`excluded_sv` empty on all 510, `information_ratio` 1.0 throughout; attack-window
confidence p5–p95 0.789–0.840 against a clean min 0.787. Cause: `backend/demo.py main()`
calls `fit(clean, floor)` with no `resid_panel` and `score_stream` calls `fx.step(ep)` with
no `resid=`, so feature 2 is unscored (`Calibration.__str__`: "resid sigma UNSET (feature 2
not scored)"). `backend/replay.py` passes both. Commit 4426c96 (15:12) already flagged
every README §10 number as fitted to the OLD feature 2.

**What the corrected pipeline does, and the finding that affects every mission
(VERIFIED, RE-SCORED, 1 m/s, demo.py's k=1 residual exclusion rule).** TTA **1 epoch**:
NOMINAL→DEGRADED at +1 (conf 0.615, |D| 7.4 m, 6 excluded, IR 0.732, bound 15.3 m,
nbo "G"), DEGRADED→SURRENDERED at +2 (conf 0.385, |D| 18.6 m, 15 excluded, IR 0.265),
RESTRICTED skipped (invariant 1), SURRENDERED to +119, recovery R +120 (13:30Z) / D +130 /
N +140 (13:40Z). Attack confidence p95/p50/p25 0.275/0.178/0.173; lead-in min 0.822. The
k=1 rule excludes 6 at +1 (five of the six spoofed plus G06), 15 at +2 (now C27 and eight
Galileo), 23 at +5, 26–30 thereafter; **IR reads 0.000 from +5**, `next_best_observation`
null for 86 of 90 attack epochs. Mechanism: feature 2 is the ALL-IN-VIEW post-fit
residual; once the six high-elevation spoofed satellites dominate the fit, the least squares
is dragged toward the spoofed position and the AUTHENTIC satellites carry the large
residuals — residual-magnitude exclusion is detection, not fault isolation. So DEGRADED
lasts one epoch and `correction_ok` is true for two attack epochs (+0, +1: corrected fix
0.95 m off truth, PL 6.83 m; PL 126.6 m at +2 → revoked; `redundancy` false from +3).
CLEAN day, same rule and thresholds: **FSR 0.0410** (118/2880, 5 events, one clean-sky
SURRENDERED at epoch 834), composite min 0.379 / p1 0.607 / p50 0.888, ≥1 exclusion on
17.4% of epochs (max 13), IR min 0.184, gate availability 0.979. README's FSR 0.0000,
TTA 4, 3.67 m, and the four-state downgrade staircase are all stale.

Fix variants, all VERIFIED (35 s attack slice each; clean day 150–195 s):
| exclusion rule / walk | TTA | \|D\| at alert | DEGRADED epochs | `correction_ok` attack epochs | excl at +3 | IR median (attack) | §10 at NOMINAL | CLEAN FSR |
|---|---|---|---|---|---|---|---|---|
| k=1 (demo) / 1.0 m/s | 1 | 7.4 m | 1 | 2 (+0,+1) | 17 mixed | 0.000 | 91/91 | 0.0410, 5 events |
| MASKED_K3 (k=3, 15° mask) / 1.0 | 3 | 29.8 m | 1 | 1 (+0) | **exactly G05 G11 G15 G20 G21 G29** | 0.000 | 92/93 (viol +2) | **0.0000**, 0 exclusions all day |
| blamed-constellation k=3 / 1.0 | 5 | 52.5 m | 2 | 13 (late, +17..) | [] (channels blame E 67/90) | 0.507 | 93/96 | 0.0000 |
| **k=1 / 0.2 m/s** | 5 | **10.5 m** | 2 | **6 (+0..+5)**, Dc ≤ 1.8 m, PL ≤ 7.8 m | G11 G21 G29 (spoofed only) | 0.000 | **95/95** | 0.0410 (rule unchanged) |
Reading: the residual cascade happens under every rule at this walk rate; k=3 isolates the
fault perfectly for one epoch (+3) but the 10-epoch grant hysteresis never lets the corrected
fix use it; constellation blame is a verified non-fix; the gentler walk is the only variant
that alerts inside the corridor AND gives the corrected fix a window. Continuity and
time-to-alert trade through the exclusion rule: k=1 alerts at 7–11 m with FSR 4.1%,
MASKED_K3 alerts at 30 m with FSR 0. **Recommendation:** LOGISTICS ships k=1 at 0.2 m/s
(integrity first — design.md §8's stated asymmetry — with FSR reported as measured); the
rule choice and the thresholds are re-derived at the threshold session on the distributions
above (MASKED_K3 zero-overlap band [0.441, 0.707]; a NOMINAL threshold at 0.70 would give
TTA 2 at 18.6 m with 0/2880 clean epochs below — margin 0.007, thin). Structural fix
(roadmap, ~3 h, ASSUMED): RAIM fault exclusion by solution separation — drop the
worst-residual satellite and re-solve until Track D's chi-square `residual_test` passes,
capped at n−(3+k)−1 drops; run the same 35 s script and look for the spoofed six isolated
through the whole attack.

### What ARBITRAS sees (RE-SCORED, k=1, 0.2 m/s, shipped thresholds — VERIFIED)
Features ≥ 0.30 salience: `cross_constellation` and `cn0_anomaly` at +0 (0.30 / 0.37,
the capture epoch; C/N0 lead-in max 0.31 so never the headline), `pseudorange_residual`
and `code_carrier_divergence` from +1. Explanation headline through the alert: "Range
measurements are drifting from their smoothed track" (claim `features.pseudorange_residual`).
Per epoch (conf, |D|, corrected error, ok, PL, IR, excluded, state): +0 0.87 0 m 0.7 m ok
PL 5 IR 1.00 0 N · +1 0.80 1 m 1.6 ok 5 1.00 0 N · +2 0.75 4 m 1.7 ok 5 0.91 2 N · +3 0.72
6 m 0.6 ok 4 0.87 3 N (G11 G21 G29) · +4 0.65 8 m 0.4 ok 6 0.76 6 N · **+5 0.605 10.5 m
1.8 ok PL 7.8 IR 0.63 7 excluded DEGRADED, nbo "G"** · +6 0.55 13 m — PL 18 > 15 revoked,
11 excluded, DEGRADED · **+7 0.51 15.1 m SURRENDERED**, 12 excluded, IR 0.45 · +8 17 m ·
+9 20 m. IR 0.000 from about +12; 22–25 excluded for the rest of the attack. Attack
confidence p95/p50 0.630/0.295. Zero epochs with ok true and corrected error > PL.
Recovery: R +120 (13:30Z, gate re-granted the same epoch), D +130, N +140 (13:40Z).
Tail 13:40Z–15:15Z ASSUMED NOMINAL (the CLEAN replay has no transition in that window;
VERIFIED on the clean day, not on the regenerated injected stream). Credential: PENDING
idx 270, EXPIRED idx 390 → SURRENDERED by `credential_force` (VERIFIED ON-DISK; scripted,
detector-independent). 1 m/s reference: D +1 (7.4 m), S +2 (18.6 m), same recovery epochs.

### Course correction
"Correct" for a convoy: keep the vehicle on the trafficable corridor using a fix the
integrity monitor can stand behind, or stop. Three tracks (architecture D): TRUE =
`route_point(s)`; BELIEVED = TRUE + D, D = `position − _truth`; CORRECTED = TRUE + D_c,
D_c = `geometry.correction.corrected_position − _truth`, drawn only while
`geometry.correction.correction_ok` is true, with a ring of radius
`geometry.correction.protection_level_m`. The corridor band (half-width `alert_limit_m`)
is the consequence overlay; "beyond alert limit" is judged on |D| exactly as today; the gate
line reads `PL x.x m / AL 15 m`.
Vehicle behaviour per state (presentation frame; depicts the policy, commands nothing;
TRUE advances `speed_m_per_epoch × motion(state)`):
- NOMINAL: motion 1. "Convoy proceeding on GNSS, accepting route updates from the FOB."
- DEGRADED + `correction_ok`: motion 1 on the CORRECTED fix; `speed_scale` shown as text,
  not applied (TRACK_D: "emit it; do not act on it"); advisory names
  `next_best_observation` ("Reweight toward G") — one epoch (+5) on this stream.
  DEGRADED without ok (+6): motion 1, "coasting on inertial; corridor position not verified".
- RESTRICTED: motion 1 to the next phase line only; "no new route segments accepted".
- SURRENDERED: motion 0 — the convoy halts in the corridor; the ghost keeps walking east.
- Credential EXPIRED/REVOKED: motion 0 regardless of signal; uplink drawn broken.

### Presentation frame
Route runs NORTH so the measured east-ish displacement (bearing 80.8°) is cross-corridor
(67.6° off the local heading); today's `ROUTE_ENU` trends east (segment-1 bearing 78°) and
would send the ghost AHEAD along the corridor, not off its edge. Waypoints, ENU metres from
the surveyed USN8 point (segment lengths 161.9/174.6/173.6/162.8/156.3 = **829.2 m**,
VERIFIED): `[(0,0), (-25,160), (15,330), (-20,500), (10,660), (-10,815)]`.
`speed_m_per_epoch` 3.0 under the motion rule (VERIFIED arithmetic): NOMINAL idx 0–64 →
195 m; DEGRADED 65–66 → 201 m; halt at **s = 201 m (−16.0, 198.1)** idx 67–179; RESTRICTED
+ DEGRADED idx 180–199 (60 m) → 261 m = PL AMBER; NOMINAL from idx 200 → reaches the route
end (829 m) at idx 389, the epoch the credential expires — the convoy stands down at RP KILO.
`alert_limit_m` 15.0; `alert_limit_provenance` "stated: single-track unimproved road
half-width; not derived; not yet agreed" (today's PLACEHOLDER, unchanged).
Props: `{"kind":"fob","e":0,"n":0,"label":"FOB · SUPPLY POINT","radius_m":30}`;
`{"kind":"phase_line","e":-2.3,"n":256.5,"label":"PL AMBER","radius_m":60}` (cross-track
line, half-length 60 m, local heading 13.2°); `{"kind":"resupply_point","e":-10,"n":815,
"label":"RP KILO · RESUPPLY POINT","radius_m":25}`. `ground_station_enu` (-45, -20): the
FOB comms mast, source of the TESLA authorisation.
Camera/palette: chase rig as today (fov 62, pitch 14, camDist 17 growing with |D|); the
route heads north so `faceAz` 200 → 20 for the model's nose; HDRI sun az 124.4 / el 47.3
then lights the east (ghost) side; corridor ribbon amber 0xE8A21C, ghost `--accent`
0xE4551F, CORRECTED point in `--truth` 0x7FA8CC with a dashed PL ring; ambience "prairie
noon", ground 0x6B6A5E unchanged; off-frame ghost label as today once past the terrain box.
Beats (VERIFIED epochs; credential beats scripted):
| idx | caption |
|---|---|
| 0 | 12:00Z · Convoy departs the FOB on GNSS. Real USN8 observables, 30 s epochs. NOMINAL. |
| 40 | Corridor half-width 15 m = alert limit. Route and motion are a presentation frame; the receiver is static. |
| 60 | 12:30Z · Carry-off begins: six highest GPS satellites captured at +2 dB. Nothing visible. |
| 63 | +3 · Three GPS satellites dropped (G11 G21 G29). Believed 6 m east. Corrected fix 0.6 m off, PL 4 m. Still NOMINAL. |
| 65 | +5 · DEGRADED: believed 10.5 m east, inside the corridor. Seven satellites dropped, information ratio 0.63. Corrected fix 1.8 m off, PL 7.8 m < 15 m — driving on the trusted subset. |
| 66 | +6 · PL 18 m > 15 m: correction revoked. Coasting; corridor position not verified. |
| 67 | +7 · Believed position crosses the corridor edge (15.1 m). SURRENDERED — convoy halts at 201 m. |
| 100 | Believed track ~90 m east of the halted convoy. Trusted geometry exhausted (ratio 0.00). |
| 149 | Attack ends at 205 m of believed displacement. The convoy never moved. |
| 150 | 13:15Z · Fix snaps back. Recovery: 10 epochs above threshold + 5 dwell per step. |
| 180 | 13:30Z · RESTRICTED: convoy proceeds to PL AMBER only. Corrected fix re-granted. |
| 200 | 13:40Z · NOMINAL: convoy crosses PL AMBER. |
| 270 | 14:15Z · Authorisation PENDING — key not yet disclosed. No override. |
| 390 | 15:15Z · Authorisation EXPIRED: the FOB stopped renewing. Clean sky, perfect fix. SURRENDERED at RP KILO. |

### Stream recipe
`MissionSpec(name="logistics", spoof=CARRY_OFF, kwargs=dict(carrier_rate_error=0.02,
bearing_deg=90.0, walk_off_mps=0.2, target_svs=top_n_by_elevation(6),
duration_s=90*30-1), onset=2026-08-20T12:30:00Z, attack_epochs=90,
credential=[("VALID",0,270),("PENDING",270,390),("EXPIRED",390,510)] (T_int 60 × d 2),
slice=[1440,1950))` → `out/logistics.jsonl`, 510 epochs. **Mandatory change in the shared
path (F-L1):** `cal = fit(clean, floor, resid_panel=residual_panel(clean, nav_xc))` and in
`score_stream` `res = fx.step(ep, resid=(sols.get("all") or {}).get("resid_m"))` with
`sols = solve_per_constellation(ep, nav_xc)` — already computed for the cross-constellation
feature; one solve, both consumers, as `backend/replay.py` does. Provenance rows: feature 2
"measured/injected — all-in-view post-fit residual, per-SV sigma median 0.51 m, saturation
8.4 (median per-SV p99)"; exclusion "k=1 on `pseudorange_residual`; clean day ≥1 exclusion
17.4% of epochs, FSR 0.0410 (5 events) under the shipped thresholds"; walk "0.2 m/s,
venue-tuned (§7: 'tune at the venue and be ready to justify'), chosen so the corridor
crossing and the corrected-fix window are legible at 30 s epochs; stated, not derived; the
1 m/s run is reported alongside (TTA 1, 7.4 m, peak 1027 m)"; displacement "first >1 m +1,
first >15 m +7, peak 205.4 m at +89"; time-to-alert 5 epochs; the "273.5 m" sentence is
regenerated away.

### Console specifics
Props: FOB as a 30 m pad + mast label; PL AMBER as a dashed cross-track line with a flag
label; RP as a 25 m ring that fills when TRUE is inside it — on `INSET_LAYER` so the
satellite-view inset gets them free, and as SVG in `Plot`.
Readouts (each with a contract path): DISPLACEMENT |D| (`position − _truth`, existing);
CORRECTED FIX (`geometry.correction.corrected_position − _truth`, "n/a" when ok false);
PL / AL (`geometry.correction.protection_level_m`, `geometry.correction.alert_limit_m`);
DRIVE ON CORRECTED FIX YES/NO (`correction_ok`); SPEED ADVISORY (`speed_scale`, labelled
advisory); TRUSTED (`geometry.correction.trusted_count` / `satellites_tracked`); NEXT
BEST OBSERVATION (`geometry.next_best_observation`, DEGRADED only); CONVOY MOVING/HOLDING
and ROUTE s/L (presentation frame, captioned).
Explanation phrases (operator language, appended under the verified explanation from a
mission `state_lines` map, no numbers so `verify()` is untouched): NOMINAL "Convoy
proceeding on GNSS. Accepting route updates from the FOB." · DEGRADED "Convoy proceeding
on the trusted-satellite fix; corridor position verified." / "…coasting on inertial;
corridor position not verified." · RESTRICTED "Convoy completes the current leg to the
phase line. No new route segments accepted." · SURRENDERED "Convoy halted in the corridor.
Control with the FOB operator." · EXPIRED "FOB authorisation lapsed. Convoy holding at the
resupply point."
Preview tile: a column of three UGVs (`buildUGV()` primitives, 10 m spacing) driving the
north–south corridor between the FOB pad and PL AMBER; the lead vehicle's ghost peels off
east and exits the tile while the column halts. Driven by `Mission.preview`: 60 samples
`{i, s, De, Dn, state}` every 2nd epoch over idx 40–160 of `out/logistics.jsonl`, served on
`/mission?name=logistics` — real D, real states, small. Caption "preview · displacement from
data · motion a presentation frame".

### Build decomposition
- **F-L1 — feature-2 seam in `backend/demo.py` + `--mission logistics`** (1.0 h). The two
  lines above; `walk_off_mps` per MissionSpec; regenerate. Acceptance: `python -m
  console.replay out/logistics.jsonl` prints →DEGRADED 65, →SURRENDERED 67, →RESTRICTED 180,
  →DEGRADED 190, →NOMINAL 200, →SURRENDERED 390 (credential_force);
  `backend.measurement.displacement` on the 0.2 m/s carry-off prints PASS at every
  arbitrated-NOMINAL epoch; `docs/stream_provenance.md` no longer says 273.5 m;
  `test_ingest.py` passes.
- **F-L2 — `console/missions/logistics.py` + generic `console/mission.py`** (0.75 h).
  Mission object as above; `route_point`/`lateral_offset`/`as_dict` take a route list.
  Acceptance: `/mission?name=logistics` returns `route_length_m` 829.2, 6 waypoints,
  3 props; `test_mission.py` parametrised over the registry, its coverage assertion
  rewritten as `sum(speed × motion(state))` over the mission's replay ≈ route length;
  `route_parity.mjs` passes.
- **F-L3 — consequence overlay** (1.0 h). CORRECTED track + PL ring in `EnvScene`, inset
  and `Plot`; PL/AL gate readouts; state-driven motion (hold in SURRENDERED, phase-line
  stop in RESTRICTED). Acceptance: `window.__envInfo()` at idx 67 shows the ghost beyond the
  band; the CORRECTED marker is drawn at exactly the epochs where `correction_ok` is true
  (0–65 and 180 onward); `veh.s` is 201 from idx 67 to 179.
- **F-L4 — props + phrases** (0.75 h). FOB pad, PL AMBER, RP ring in scene/inset/plot;
  `state_lines` under the explanation bar. Acceptance: three labels in the inset SVG; the
  logistics line changes with state; `verify()` still reports zero failures over the replay.
- **F-L5 — beats + slow-motion window** (0.5 h). Captions on epoch; per-mission
  `rate_schedule` `[(0,15),(60,1.5),(70,15)]` consumed in `server._events` so the onset
  (idx 60–70) takes ~7 s of wall time. Acceptance: each of captions 63/65/66/67 is on
  screen ≥ 0.6 s at the default rate.
- **F-L6 — preview samples + README** (0.5 h). `Mission.preview` from the stream at server
  start; README §10 numbers replaced with the F-L1 output and FSR as a distribution.
  Acceptance: `/mission` carries 60 preview samples; README quotes TTA 5 / 10.5 m /
  PL 7.8 m / crossing +7 / peak 205 m / FSR from the regenerated clean day, and the 1 m/s
  reference line. (Items sum to ≈ 4.5 h.)

### Cut rule
F-L1 is not cuttable — without it the mission is beat 2 with the layer on, and every other
mission's stream inherits the same unscored feature 2; do it first and alone. If the
0.2 m/s walk is rejected at the threshold session, keep 1 m/s: the beats become 61/62
instead of 65/67, the halt moves to s = 186 m, PL AMBER to (−5.7, 241.8), and everything
else stands (VERIFIED). Cut order after F-L1: F-L6 (README lines only) → F-L5 (fall back to
`--rate 3` and captions in the README) → F-L4 (props become inset labels only) → F-L3 (the
PL/AL gate becomes two readouts in the instrument strip; no corrected track). If F-L2 is
cut, `logistics` is today's `console/mission.py` with `ROUTE_ENU` swapped for the
north–south list and speed 3.0 — ten minutes. Every change above is additive, so the other
three missions and the page are untouched by cutting any of it.

### Limitations to state in the README
- The README's §10 numbers (3.67 m vs 14.0 m, TTA 4, FSR 0.0000, the four-state downgrade
  staircase, 273.5 m peak) were fitted to the previous feature 2; the on-disk demo stream
  as of 21:08 detects nothing on signal alone because feature 2 is unscored in
  `backend/demo.py`. Regenerated numbers replace them.
- Residual-magnitude exclusion is detection, not fault isolation: under the carry-off the
  all-in-view fit is dragged by the spoofed subset, authentic satellites are excluded within
  1–3 epochs of the first exclusion, the information ratio reads 0.000 for most of the
  attack, DEGRADED lasts 1–2 epochs and the corrected fix fails closed. MASKED_K3 isolates
  the spoofed six exactly at +3 but alerts at 30 m; solution-separation exclusion is the fix.
- Continuity and time-to-alert trade through the exclusion rule: k=1 alerts inside the
  corridor with clean-day FSR 0.041 (5 events, one clean-sky surrender); MASKED_K3 gives FSR
  0.0000 and alerts at twice the corridor width. The shipped thresholds were fitted to the
  old distribution; on this day the zero-overlap bands are [0.275, 0.379] (k=1) and
  [0.441, 0.707] (MASKED_K3).
- The 0.2 m/s walk is a venue-tuned legibility choice inside §7's "tune at the venue";
  the §7 midpoint (1 m/s) alerts in one epoch at 7.4 m and is reported alongside.
- "Max adversarial displacement" by the §10 definition (epoch before the first transition)
  is 8 m at 0.2 m/s and 0.00 m at 1 m/s (the capture epoch carries no walk); the
  alert-epoch figure and the bound are reported beside it.
- The displacement bearing (~80°) is set by the spoofed subset's geometry; the north–south
  route is a presentation choice so the measured displacement is cross-corridor. The
  receiver never moved; the halt at 201 m and the arrival at RP KILO are the policy
  depicted, not a measurement. The alert limit (15 m) is stated, not derived, not agreed.
- 30 s epochs: capture is one sample; the on-screen slow-motion window is a presentation
  device and is captioned as such. The 205 m / 1027 m peaks are reached after surrender.
  The credential beat is scripted (T_int 60 × d 2, venue-tuned) and detector-independent.
