# TRACK F / COMBAT -- mission simulation (scoped 2026-09-05, NOT BUILT)

One of the four mission plans of tracks/TRACK_F.md, produced by a separate
planning agent against the shared brief and verified on the real USN8 day
where it says VERIFIED. Read TRACK_F.md first: the F-0 prerequisites (feature 2
unscored in backend/demo.py, stale thresholds, residual-exclusion cascade) apply
to this mission and are fixed once, page-wide, before it is built. Environment
(terrain, sky, ground, vegetation) is assigned in TRACK_F.md §2 and overrides
the palette hints below.

## COMBAT — armed recon: a crude 15 dB step throws the fix 98 m past a phase line, ARBITRAS holds; the operator then withdraws authority under a clean sky

Experiment: `scratchpad/combat_exp.py` (+ `clean_wired_fsr.py`), USN8 2026-08-20, slice
[1440,1700) = 260 epochs 12:00:00→14:09:30, onset 12:30:00 (idx 60), 20-epoch attack, five
spoofed sets × two pipelines; a 40-epoch re-run; a full clean-day arbitration (logs
`combat_run2/3.log`, `clean_wired.log`). VERIFIED unless marked ASSUMED. No repo edits.

**Finding that outranks this mission (VERIFIED):** `backend/demo.py` calls `fx.step(ep)`
and `fit(clean, floor)` with no post-fit residual (`backend/replay.py` lines 81–86 and
151–154 pass it), so in every stream demo.py writes `pseudorange_residual` is 0.0,
`excluded_sv` is `[]`, `information_ratio` is 1.0 and attack confidence bottoms at
0.72–0.77, above NOMINAL 0.643. `console.replay out/demo.jsonl` (21:08 regeneration)
shows ONE transition: the EXPIRED force at epoch 390. No mission leaves NOMINAL on signal
until the wiring below lands (the on-disk demo.py still lacks it). Cost at the CURRENT
thresholds: clean-day FSR 4.10 % (118/2880, 5 events, one false SURRENDERED 06:57–07:11
with 12 SVs excluded, conf 0.450); clean conf min 0.379 / p1 0.607 / p50 0.888; no event
inside 12:00–15:05. states.py (14:06) pre-dates the feature-2 rewrite (14:57): redo §10.

    # backend/demo.py score_stream()            # backend/demo.py main()
    sols = solve_per_constellation(ep, nav_xc)   cal = fit(clean, floor,
    res = fx.step(ep, resid=(sols.get("all")               resid_panel=residual_panel(clean, nav_xc))
                    or {}).get("resid_m"))       # panel is cached: 0 s
    feats["cross_constellation"] = xc.score(ep, sols)["value"]

### Rank
Agree, keep COMBAT at #4 and cut it first: its detection story is the least staged of the
four (NOMINAL → SURRENDERED at offset 0, geometry half 0.000, no DEGRADED-as-active beat
on the way down), and its two unique beats — skip-to-SURRENDERED and REVOKED forcing
surrender at confidence 0.85 — cost about four hours on top of the logistics plumbing.
But the demo.py residual wiring above is a page-wide blocker to schedule before any
mission, including #1; COMBAT is the mission whose experiment exposed it.

### The mission
An armed UGV advances from a line of departure through phase lines PL AMBER and PL RED
toward an objective, in the segment PROJECT.md §1 calls the most exposed to enemy
observation and fires. Its reported position does two things no other mission's does: it
is the origin of every target grid it lases and reports, and the basis on which it reports
crossing a phase line — the control measure that coordinates fires and manoeuvre behind
it. A 98 m error is a wrong grid and a false "crossed PL AMBER" report.

The operator's question is PROJECT.md §1's, sharpened: *may this vehicle's position feed
fire control right now, and who says so?* ARBITRAS answers with two independent inputs
(PROJECT.md §2, Claim 1): a signal-derived confidence and a cryptographic credential.
COMBAT alone shows the credential dominate in the direction no signal can override — the
operator withdraws authorisation under a clean sky and the vehicle holds anyway.

### The attack
design.md §7 row 1 — "Simplistic, 10–20 dB, abrupt offset, all SVs at once" — read in the
POSITION domain. A crude single-transmitter spoofer (a GPS L1 simulator) captures the
whole GPS set at +15 dB (§7 midpoint, as `SIMPLISTIC`) and presents ranges consistent with
a receiver 250 m east of the true one: per-SV offsets `−e_sv·dp`. 250 m is stated, not
derived — unmistakable at a glance, exactly as `SIMPLISTIC`'s docstring says; the 0.014
m/s code/carrier mismatch is carried from `SIMPLISTIC` (stated, not derived).

Injector extension (additive, `backend/injector/spoof.py`; the 0.0 default leaves every
existing truth log byte-identical; `inject.py` unchanged; the record additionally copies
`_attack.commanded_displacement_m`, which inject() already logs):

    class Spoof:                                         # one field, after carrier_rate_error
        # Abrupt position-domain offset applied from the first attack epoch (the
        # CAPTURE epoch, as SIMPLISTIC's common_bias_m). Position mode only.
        step_displacement_m: float = 0.0
        def displacement_m(self, t):
            if self.walk_mode != "position": return 0.0
            st, _ = self.stage(t)
            if st == CLEAN: return 0.0
            return self.step_displacement_m + self.walk_off_mps * self.walked_s(t)

    def SIMPLISTIC_POSITION(onset, bearing_deg=EAST_BEARING_DEG, target_svs="all_gps", **kw):
        """§7 row 1 in the position domain. 15 dB = §7 midpoint. 250 m is chosen to be
        unmistakable at a glance and is not derived from anything (stated, not derived)."""
        return replace(Spoof(name="simplistic_position", power_db=15.0, walk_off_mps=0.0,
                             onset=onset, walk_mode="position", bearing_deg=bearing_deg,
                             svs="G", target=target_svs, step_displacement_m=250.0,
                             carrier_rate_error=0.014, liftoff_delay_s=0.0), **kw)
    SCENARIOS["simplistic_position"] = SIMPLISTIC_POSITION
    POSITION_DOMAIN = ("carry_off", "simplistic_position")

What happens to the fix (VERIFIED, 45 SVs tracked, G+E differential WLS):

| spoofed set | SVs | \|D\| first epoch → +19 | achieved ENU at onset | G clock absorbed |
|---|---|---|---|---|
| top-6 GPS by elevation | 6 | 92.5 → 96.2 m | E +91.3, N +14.7 | −83.6 m |
| **all GPS** (recommended) | 12 (8 in solve) | **98.5 → 103.0 m** | E +97.5, N +13.5 | −53.4 m |
| top-8 GPS | 8 | 98.5 → 103.0 m (= all GPS: the solver uses 8 GPS) | same | same |
| top-4 GPS | 4 | 52.1 → 54.6 m | E +51.4, N −8.1 | −14.9 m |
| all 45 SVs (G E C R S) | 45 | **250.0 m exactly**, every epoch | E +249.9, N 0.0 | 0 |

The fix jumps on the FIRST attack epoch (the CAPTURE epoch, where `SIMPLISTIC`'s bias
also applies) and snaps back to 0.000 m the epoch after the attack ends. All-GPS answer:
a position-domain step on all GPS is NOT absorbed by the clock column — the joint G+E
solve is pulled 98.5 m (39 % of commanded; Galileo anchors it; the G clock takes 53 m of
the common part; believed residual RMS 68.9 m vs 0.9 m clean). Recommend **all_gps**:
displacement and wired-pipeline detection match top-6, but C/N0 saturates at 1.00 (top-6
reads 0.61 because `aggregate()` averages over the 12 tracked GPS) — the "15 dB against a
±0.5 dB floor" signature — and it is the literal §7 "all SVs at once" for a spoofer that
generates a constellation. Fall back to top-6 only if RECON takes the all-GPS step.

### What ARBITRAS sees
Feature-2-wired pipeline, all_gps (top-6 in brackets where different). Offsets from onset.

| off | stage | \|D\| m | conf | cn0 | resid | cmc | xc | IR | excl | bound | corr_ok | state |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| −1 | clean | 0.0 | 0.868 | 0.19 | 0.16 | 0.36 | 0.35 | 1.000 | 0 | 15.7 | True | NOMINAL |
| 0 | CAPTURE | 98.5 | 0.092 [0.143] | 1.00 [0.61] | 1.00 [0.98] | 0.26 | 1.00 | 0.000 | 28 [26] | null | False | **SURRENDERED** |
| +1 | WALK | 98.6 | 0.012 [0.096] | 1.00 [0.53] | 1.00 | 0.90 [0.71] lift-off | 1.00 | 0.000 | 28 | null | False | SURRENDERED |
| +19 | WALK | 103.0 | 0.194 [0.198] | 0.21 | 0.94 [0.90] | 0.30 | 1.00 | 0.000 | 27 [25] | null | False | SURRENDERED |
| +20 | clean | 0.0 | 0.072 [0.276] | 1.00 (15 dB drop) | 1.00 [0.89] | 1.00 [0.69] | 0.42 | 0.000 [0.145] | 29 [22] | null [36] | False | SURRENDERED |
| +40 | clean | 0.0 | 0.281 [0.412] | 0.18 | 0.92 [0.90] | 0.18 | 0.47 [0.14] | 0.000 [0.175] | 24 [18] | null [37] | False | SURRENDERED |
| +41 | clean | 0.0 | 0.848 [0.887] | 0.26 | 0.25 | 0.22 | 0.48 [0.16] | 1.000 | 0 | 12.5 | False (grant run) | SURRENDERED, recovery run starts |
| +50 | clean | 0.0 | 0.881 [0.921] | 0.11 | 0.18 | 0.23 | 0.43 | 1.000 | 0 | 12.2 | True | **RESTRICTED** (recovery) |
| +60 | clean | 0.0 | 0.869 [0.905] | 0.14 | 0.17 | 0.30 | 0.44 | 1.000 | 0 | 12.0 | True | **DEGRADED** (recovery) |
| +70 | clean | 0.0 | 0.821 [0.896] | 0.15 | 0.31 | 0.21 | 0.76 | 1.000 | 0 | 11.7 | True | **NOMINAL** (recovery) |
| +190 | clean, REVOKED | 0.0 | 0.854 [0.883] | 0.23 | 0.24 | 0.28 | 0.41 | 1.000 | 0 | 10.3 | True | **SURRENDERED** (credential_force) |

- **Time-to-alert 0 epochs.** Invariant 1 exactly: NOMINAL → SURRENDERED on the first
  attack epoch, DEGRADED and RESTRICTED skipped. Attack confidence 0.012–0.194 [0.096–0.198].
- **Exclusions:** 28 of 45 tracked (G 10, E 11, C 7) [26: G 9, E 11, C 6]. The per-SV
  post-fit residual of the joint solve spreads the 98 m inconsistency over every row, so
  it distrusts Galileo and BeiDou that were never spoofed. The trusted G+E remainder is
  rank-deficient → `information_ratio` 0.000, `displacement_bound_m` null, Track D fails
  closed (`corrected_position`/`protection_level_m` null, `redundancy` False). Honest
  reading: nothing left to stand on — hold.
- **Track D gate** revokes on the first attack epoch (both pipelines) and re-grants at
  attack end +30; lead-in availability 0.85 = 51/60 (10-epoch grant warm-up).
- **Explanations (verified=True, every claim sourced):** onset "Signal strength has moved
  off its own baseline." [top-6: "The constellations no longer agree on position."] +
  "Holding position. Control is with the operator. 28 satellites dropped out of the
  trusted set (…). The clock is free-running and no new authorisation will be accepted…".
  Recovery: "Signals are back within their normal range. Authority is restored one step
  at a time." REVOKED: "Mission authorisation was revoked. Signal quality did not enter
  into this. Holding position. Control is with the operator. An attacker could move us up
  to 11 m…". The first NOMINAL epoch names feature 2 (0.31 vs floor 0.30) — cosmetic.
- **Recovery staircase (invariant 2):** feature 2's 40-epoch trailing median stays
  polluted for 20 epochs after the spoofer stops (0.92 at +40, 0.25 at +41): exclusions
  clear at attack end +20, confidence re-crosses 0.643 at +21, then exactly 10 epochs per
  step (dwell 5 met inside the run): RESTRICTED +30, DEGRADED +40, NOMINAL +50 after
  attack end. Same offsets for a 40-epoch attack.
- **40-epoch caveat:** at attack offsets +21…+25 the residual baseline had adapted:
  exclusions 2–4, IR 0.83–0.90, confidence 0.644–0.715, five `recovery_gated` epochs — five
  more and the staircase up would begin at 97 m. Cross-constellation held 1.00. Keep 20.
- **As-is pipeline (no residual):** all_gps → DEGRADED at +1 (0.637) for 10 epochs, then
  NOMINAL at +11 while displaced 100 m; top-6 → NOMINAL throughout (min 0.720).
- **All-45-SV step:** 250.0 m; cn0 1.00 and cmc 0.99 fire, resid 0.15, xc 0.30, IR 1.000,
  `correction_ok` stays **True** on a spoofed fix, confidence min 0.695 → NOMINAL
  throughout. Invisible to three features and the geometry half. README limitation.

### Course correction
"Correct" in COMBAT is not a position correction: there is no corrected fix to offer
(null through the attack, fail-closed) and the vehicle must not act on a grid it cannot
stand behind. The honest behaviour is a HOLD plus an explicit statement of what fire
control may consume. Fields: `state`, `reason`, `credential_status`, `geometry.correction.
correction_ok` / `.protection_level_m` / `.alert_limit_m`, `geometry.excluded_sv`,
`position − _truth`. Per state, advisory only — nothing commanded:

| state | vehicle (presentation-frame progress gain) | fire-control position input readout |
|---|---|---|
| NOMINAL, VALID | advances, 1.0 × speed | TRUSTED |
| DEGRADED, `correction_ok` | 0.5 × speed on the corrected fix | CONDITIONAL · PL n m < AL 15 m (never occurs in this stream) |
| DEGRADED, not ok | 0.5 × speed, inertial | NOT TRUSTED |
| RESTRICTED | 0.25 × speed, current bound only | NOT TRUSTED |
| SURRENDERED by confidence | 0 — hold marker at the true position | NOT TRUSTED · hold |
| SURRENDERED by credential_force | 0 — hold marker | AUTHORITY WITHDRAWN · confidence 0.85 |

Shared tracks drawn as usual (TRUE, BELIEVED = true + D, CORRECTED absent while null).
Consequence overlay: hold marker on PL AMBER at the true position; believed pin 98 m past
PL AMBER labelled "REPORTS PL AMBER CROSSED · FALSE"; readout "target grid offset 98 m".

### Presentation frame
Route ENU (metres from the surveyed USN8 origin; 933.0 m; heading ENE so the +97.5 m east
step lands ahead of the vehicle): `[(0,0), (150,20), (320,40), (480,110), (640,130),
(800,200), (880,260)]`. Speed 3.0 m/epoch (framing choice, captioned); progress is the
cumulative sum of speed × gain(state). Alert limit 15 m; provenance: target-location-error
category II upper bound (JP 3-60 / CJCSI 3505.01 TLE: I ≤ 6, II ≤ 15, III ≤ 30, IV ≤ 91,
V ≤ 305 m) — ASSUMED from doctrine memory, verify before the README; equals
`gate.py ALERT_LIMIT_M`, so `test_alert_limit_matches_console_mission` stays green.
The 98 m offset is TLE CAT V: no precision fire.

Props (`{"kind","e","n","label","radius_m"}`; phase lines drawn perpendicular to the
route heading at the crossing point, half-length `radius_m`):
- `phase_line` "LD" at (0.0, 0.0), radius 80; ground station (authorisation source) at
  (−90, 80), behind the LD
- `phase_line` "PL AMBER" at route_point(180) = (178.5, 23.4), radius 80 — the vehicle is
  exactly here at onset (60 × 3.0); the ghost sits at (276.0, 36.9), 98.4 m along-track
  beyond it, 3 m off-axis
- `phase_line` "PL RED" at route_point(562.5) = (544.9, 118.1), radius 80 — the vehicle is
  exactly here at the REVOKED epoch: 180 + 10×0.75 + 10×1.5 + 120×3.0 = 562.5
- `objective` "OBJ HAWK" at (880, 260), radius 60 — never reached
Camera: the existing over-the-shoulder rig (its ghost bias already looks at the believed
pin). `scene: {"ambience": "dusk", "phase_line_color": "#E4551F"}`; uplink BROKEN on REVOKED.

Beats (stream epoch → caption; indices per the recipe below):

| epoch | caption |
|---|---|
| 0 | Advance from LD toward PL AMBER. Real observables, USN8, 20 Aug, 30 s epochs. NOMINAL. Fire-control position input: TRUSTED. |
| 60 | Crude spoofer: 15 dB, all GPS, abrupt. Believed position jumps 98 m past PL AMBER on the first epoch. SURRENDERED — no intermediate state. Hold at PL AMBER. Fire-control input: NOT TRUSTED. |
| 61 | Which signal: strength 1.00, range residuals 1.00, constellations disagree 1.00. 28 of 45 satellites dropped; no trusted geometry; no corrected fix. Hold. |
| 80 | Spoofer off. Believed = true again. The hold continues: the residual baseline needs 20 epochs to clear. |
| 110 | RESTRICTED — first step up after 10 consecutive clean epochs. Completing the current bound. |
| 120 | DEGRADED. Advancing at reduced speed. |
| 130 | NOMINAL. Fire-control position input: TRUSTED. Advance to PL RED. |
| 250 | Operator withdraws the mission authorisation. Clean sky, confidence 0.85. SURRENDERED. Hold at PL RED. Fire-control input: WITHDRAWN. Signal quality did not enter into this. |
| 369 | End. Objective not reached; without the credential, authority is never restored. |

### Stream recipe
`MissionSpec(name="combat", spoof=SIMPLISTIC_POSITION, kwargs=dict(bearing_deg=90.0,
target_svs="all_gps"), onset=datetime(2026,8,20,12,30), attack_epochs=20
(duration_s=20*30−1=599), pre_epochs=60, post_epochs=290, credential=lambda j: "REVOKED"
if j >= 250 else "VALID", out="out/combat.jsonl")`. Slice [1440, 1810) = 370 epochs,
12:00:00 → 15:04:30; attack 12:30:00–12:39:30; RESTRICTED 12:55:00, DEGRADED 13:00:00,
NOMINAL 13:05:00 (held 120 epochs ≥ LEGIBLE_EPOCHS); REVOKED 14:05:00, held 120 epochs
(confidence there 0.854, VERIFIED at idx 250). At 15 epochs/s: lead-in 4.0 s, attack
1.3 s, hold 3.3 s, staircase 1.3 s, NOMINAL 8 s, REVOKED 8 s; recommend `?rate=10`.
Shared load / floor / calibration (with `resid_panel`) / xc / nav / sigma_UERE (item C);
`xc.reset()` and `corrector.reset()` before the run.

Provenance rows (docs/stream_provenance.md, combat section):
- `_attack.commanded_displacement_m` 250 — **stated, not derived** (§7 row 1 gives no size);
  `_attack.power_db` 15 — §7 crude midpoint; `_attack.cmc_divergence_m` 0.014 m/s × walked s, carried from SIMPLISTIC, stated
- `_solution.displacement_m` 98.5–103.0 m — **measured**, differential WLS, 39 % of commanded (Galileo anchors the joint solve)
- `credential_status` REVOKED from 14:05:00 — **scripted**; models an authenticated withdrawal assertion (validity = 0 for this vehicle_id) verified after the disclosure lag; TESLA has no revocation message (§9); T1 not built
- `geometry.correction` null through the attack — fail-closed, redundancy False; thresholds: states.py values pre-date the residual feature, clean-day FSR with it wired 4.10 % at 0.643 (§10 re-measure pending), the 12:00–15:05 window is clean-event-free

### Console specifics
- Props: `Plot` (SVG) draws a phase line through (e,n) perpendicular to the `routePoint`
  heading, length 2·radius, labelled; the objective as a ring. `EnvScene` adds two posts
  + a dashed line on `INSET_LAYER` (the satellite-view inset gets them free) and a ring.
- Hold marker: when gain(state) = 0, a pulsing ring at the true position labelled
  `HOLD · <nearest phase line>`; the true pin stops (progress accumulator in `App`).
- Readout cell "FIRE CONTROL · POSITION INPUT": value from the table above; paths on
  hover `state`, `credential_status`, `geometry.correction.correction_ok`,
  `geometry.correction.protection_level_m`. Line 2 `target grid offset <|D|> m`; line 3
  `phase-line report: believed CROSSED PL AMBER · true SHORT` (frame-derived, captioned).
- Explanation phrases: `Mission.behaviour` overrides `BEHAVIOUR` via one optional kwarg
  `explain(d, behaviour=…)` in `decide()`. NOMINAL "Full autonomy. Advancing on GNSS;
  position feeds fire control." DEGRADED "Advancing at reduced speed on inertial.
  Fire-control position input flagged." RESTRICTED "Completing the current bound only.
  Fire-control input withheld." SURRENDERED "Holding at the phase line. Fire-control
  input withheld. Control is with the operator." Headlines unchanged.
- Preview tile (shared renderer, scissor viewport): the UGV halted at a phase-line post,
  ghost 98 m beyond it; loops onset → hold → snap-back → REVOKED hold at the second post.
  Fixed-corner caption: route, phase lines and halting are a presentation frame; the
  receiver at USN8 never moved; D is measured.

### Build decomposition
- **F-C0 (page owner, 0.3 h, blocker for all missions):** the residual wiring in demo.py /
  `backend/missions.py`. Acceptance: `console.replay out/demo.jsonl` shows a transition
  out of NOMINAL inside the attack window.
- **F-C1 (0.5 h):** `step_displacement_m` + `SIMPLISTIC_POSITION` in spoof.py;
  `_attack.commanded_displacement_m` in the record. Acceptance: `displacement_m(onset)` =
  250, 0 at onset − 30 s and onset + 600 s; injector tests unchanged; `python -m
  backend.demo` output byte-identical.
- **F-C2 (0.5 h):** combat `MissionSpec`. Acceptance: `out/combat.jsonl` has 370 records;
  `console.replay` prints exactly 60 NOMINAL→SURRENDERED, 110 →RESTRICTED, 120 →DEGRADED,
  130 →NOMINAL, 250 →SURRENDERED (credential_force); `_solution.displacement_m` at idx 60
  within 98.5 ± 0.5 m and 0.000 at idx 80.
- **F-C3 (1.0 h):** `console/missions/combat.py` Mission (route, props, alert limit +
  provenance, ground station, behaviour, beats, attack card, scene). Acceptance: route
  length 933 ± 1 m; `route_point(180)` = (178.5, 23.4) ± 0.1; `/mission?name=combat`
  serves it; `test_mission.py` passes parametrised over missions.
- **F-C4 (1.5 h):** state-gated progress, phase-line/objective props in Plot + EnvScene,
  hold marker, fire-control readout, re-captioned displacement readout, behaviour kwarg.
  Acceptance (`?mission=combat`): idx 60 true pin on PL AMBER, ghost "BELIEVED · 98 M",
  readout NOT TRUSTED; idx 250 hold at PL RED, readout AUTHORITY WITHDRAWN beside
  confidence 0.85, bar "Signal quality did not enter into this."; `explanation_verified`
  true on every epoch.
- **F-C5 (0.5 h):** preview tile loop. Acceptance: ≥ 30 fps in the shared renderer with
  the other three tiles; no second WebGL context.
- **F-C6 (0.4 h):** provenance rows, README paragraph, limitations. Total ≈ 4.4 h after F-C0.

### Cut rule
F-C1 defaults to 0.0 and F-C2 is a registry entry: additive, cannot affect the other
missions. If F-C4 is not done three hours in, ship COMBAT with the generic corridor
rendering (no props, no readout) — stream, states and captions still play. Without F-C0,
COMBAT cannot be shown at all: mark the tile "not built".

### Limitations to state in the README
- Commanded 250 m, achieved 98.5 m: the G+E joint solve is anchored by Galileo. A GPS-only
  receiver would take the full 250 m (ASSUMED; not run — the solver is G+E).
- A step applied consistently to all 45 tracked SVs moves the fix exactly 250 m and is
  invisible to the residual, cross-constellation and geometry halves; only C/N0 and the
  lift-off CMC transient fire, confidence stays ≥ 0.695, `correction_ok` stays true on a
  spoofed fix. Not caught on signal at the current weights; credential and terrain (Track
  E) are the answers.
- The per-SV post-fit residual cannot attribute the fault: 28 of 45 satellites excluded,
  including 11 Galileo and 7 BeiDou never spoofed; information ratio 0.000, bound null,
  corrected fix null. The response is a hold, not a correction. RAIM attribution: roadmap.
- The causal residual baseline (40-epoch trailing median) adapts to a sustained attack:
  21–25 epochs into a 40-epoch attack confidence rose to 0.715 and a recovery run began
  at 97 m displacement; cross-constellation alone held. After the spoofer stops, the hold
  outlasts the attack by 20 epochs plus a 30-epoch staircase.
- states.py thresholds pre-date the residual feature; with it wired, clean-day FSR at
  0.643 is 4.10 % with one false surrender (06:57). Re-measure per §10 before quoting.
- REVOKED is scripted. TESLA has no revocation message; the modelled mechanism is an
  authenticated withdrawal assertion verified after the disclosure lag — T1 protocol
  logic, not built. The demo shows the arbitras's response, not the protocol.
- Phase lines, objective, hold and halting are a presentation frame; the receiver never
  moved; the TLE-category alert-limit provenance is doctrine from memory, unverified.
