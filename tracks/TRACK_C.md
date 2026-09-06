# TRACK C — Geometry + Measurement

## CONTRACT EXTENSION REQUESTED BY TRACK B — `geometry.sky`

The console renders a 3D constellation view: satellites at their real sky
positions, going dark as they leave the trusted set. Please emit this from your
real H-matrix pipeline so the fixture can be dropped.

Shape — one entry per satellite above the elevation mask, inside `geometry`:

    "sky": [{"sv": "G07", "az": 143.2, "el": 41.8, "trusted": false}, ...]

* `az` degrees [0,360), `el` degrees [mask, 90], mask default 10.
* `trusted` is false for exactly the satellites in `excluded_sv`, true otherwise.
  The console asserts this; they must not disagree.
* Rows sorted by descending elevation.

`backend/geometry/skyview.py` already does the propagation and is yours to reuse
or replace — `sky_at(datetime)` returns the list above minus `trusted`. It shares
the line-of-sight vectors your information matrix needs, so it may fold straight
into your H build. Two things it learned the hard way:

* **Galileo dedupe must key on the `gnss_sv_id` STRING, not `sv_id`.** georinex
  splits Galileo by nav message type (E14, E14_1, E14_2, E14_3) and
  gnss-lib-py's RinexNav folds that suffix into the numeric sv_id as 14, 141,
  142, 143. Keying on sv_id gives four satellites at identical az/el — which
  looks entirely plausible on a skyplot. It inflated Galileo from 12 to 39.
* **BeiDou does not propagate.** It parses, `sqrtA` is finite, and
  `glp.find_sv_states` returns all-NaN — BeiDou's broadcast time base is BDT,
  not GPS. 37 satellites selected, 0 finite positions. Currently excluded on
  purpose. If you want C in the trusted set, apply the BDT→GPS offset first.
  GLONASS is excluded too: PZ-90 state vectors, not Keplerian elements.

Scope today is G + E, ~16-22 visible at USN8, which is physically right.

---


You own `backend/geometry/` and `backend/measurement/`. You produce the
`geometry` block of the §5 contract. This is the weight-independent half of
the confidence score and it is NOT on the cut list.

## Setup (~10 min, unattended)
    git clone https://github.com/maxzfan/ARBITRAS.git && cd ARBITRAS
    bash bootstrap.sh
    source .venv/bin/activate

## Already done — do not redo
The nav file is resolved and VERIFIED (design.md §16 called this blocking):

    python backend/rinex/nav_prefilter.py    # already run, output committed-ignored
    # -> data/brdc_filtered.rnx

Stripped 164 IRNSS records. Verified loading in georinex:
G 32, E 120, R 26, C 37 SVs, 4,166 epochs, Keplerian params all present
(M0, Eccentricity, sqrtA, Omega0, Io).

## GOTCHA 1 — Galileo SVs are duplicated
georinex returns `E02`, `E02_1`, `E02_2`, `E02_3` — it splits Galileo by nav
message type (I/NAV vs F/NAV), not by satellite. That is why E shows 120 for a
~26-satellite constellation. **Dedupe to the base PRN before building H** or
you will double-count rows and the information matrix will be wrong in a way
that looks plausible.

## GOTCHA 2 — the §6b open question is answered, and the naive form fails
design.md §6b asks: does the determinant ratio stay meaningful as the trusted
subset shrinks? Worked on paper: **no, not as written.** Two reasons, both fixable.

**(a) H is n x (3+k), not n x 4.** k = number of constellations in the trusted
set; each contributes its own clock bias. Exclude every SV of one constellation
and the matrix goes rank-deficient in that constellation's clock state:
det -> 0 discontinuously, for a reason that has nothing to do with position
information. This fires exactly under **meaconing (§7 scenario 3)**, where the
correct detector response is to distrust one whole constellation. The score
would fail hardest on the attack it should handle best.

  Fix: when a constellation's trusted SV count reaches zero, DROP its clock
  column. Reduce the state vector; never carry a rank-deficient matrix.

**(b) The raw ratio decays far too fast to read.** det(HtH) scales with the
(3+k)th power of the geometry. Dropping 2 of 11 SVs can move it an order of
magnitude, and it is exactly 0 the moment trusted SVs < unknowns. On screen in
video beat 3 it reads "0.00" almost immediately.

  Fix: report the **normalised** ratio `(det ratio)^(1/(3+k))`. This is the
  D-optimality form — the ratio of GDOP volumes, the geometric mean of the
  information eigenvalues. Legible across the whole exclusion range, and
  *easier* to defend on a whiteboard, which satisfies the §13 convention that
  the score be derivable in two minutes.

**Confirm this numerically once H exists — ~15 min, not a rebuild.** Sweep
exclusion count 0..7 on a real epoch, plot raw vs normalised. If the normalised
curve is monotone and spans a useful range, the geometry track is unblocked and
Claim 2a survives. That plot is also worth putting in the README.

## 1. Nav -> satellite positions -> line-of-sight matrix H
`gnss-lib-py` (Stanford NavLab) makes this tractable. Rows = tracked SVs,
columns = [3 position, k clock biases].

## 2. Information ratio, normalised, plotted over the clean day
`det(HtH)` on the trusted subset over the full-constellation solution, then the
(1/(3+k)) power. Scaled to [0,1]. No free parameter — that is the whole point.

## 3. Analytic displacement bound + next-best-observation
Next-best falls out of the rank-one update:
`det(G + hh^T) = det(G)(1 + h^T G^-1 h)` — ranking candidates is one quadratic
form each. Borrowed from active view selection (CONVERGE, Stanford); cite it
by name in the README.

## 4. Freeze-at-last-NOMINAL (evening)
Line-of-sight vectors derive from the receiver's own position estimate, which
under attack is the spoofed one. Below NOMINAL, evaluate geometry against the
LOS set held at the last NOMINAL epoch. **Divergence between frozen and live
geometry is itself evidence.** This is the strongest finding in the project —
same structure as the TESLA clock dependency. It belongs in the README.

## 5. Measurement (§10) — sweep harness
FSR as a **distribution over a Dirichlet sample of detector weight vectors**,
not a point estimate. ~40 lines. The Dirichlet varies the four feature weights
and the feature/geometry blend; the geometry term itself stays fixed. Report
**what fraction of the score is weight-sensitive** — that is a better answer to
arXiv 2607.05415 than a wide distribution alone.

Then: empirical swept displacement vs the analytic bound, plotted on one axis.
If the empirical number ever exceeds the bound, the bound is wrong. Run that
check explicitly and say that you ran it.

## Deliverable by 18:30
`geometry` block flowing in the contract: information_ratio, excluded_sv,
displacement_bound_m, next_best_observation.

---

# HANDOFF FROM TRACK B — real data is flowing; thresholds and position are yours
(2026-09-05 ~12:50. Everything below reproduces from the repo; streams are gitignored, regenerate them.)

## Regenerate the real streams (Track A's pipeline over USN8, ~minutes)
    source .venv/bin/activate
    python -m backend.demo            # -> out/clean.jsonl, out/carryoff.jsonl,
    # -> out/clean.jsonl (2880), out/carryoff.jsonl (2880), out/demo.jsonl (240: 12:00–14:00 UTC, onset 12:30)
    python -m console.replay out/demo.jsonl      # arbitras timeline, headless
    python -m console.replay out/clean.jsonl     # FSR on the clean day
Field-by-field provenance: docs/stream_provenance.md.

## 1. THRESHOLDS — §10 steps 1–3 are done; step 4 is the by-hand pick
Placeholders (0.75 / 0.50 / 0.25) fail on real data: NOMINAL sits on the clean median.
    clean day (2880)  p1 0.652  p25 0.717  p50 0.744  p75 0.774  p99 0.831
    attack   (90)     p1 0.365  p25 0.430  p50 0.454  p75 0.481  p99 0.534
    gap +0.118 — zero overlap, d′ ≈ 6.95
    FSR with placeholders: 0.829 (2388/2880 below NOMINAL, 28 events, all DEGRADED)
Any NOMINAL threshold in (0.543, 0.652) gives zero clean false alarms on this day.
Open design question for the pick: attack floor is 0.36, so SURRENDERED < 0.25 never
fires on signal alone for a 1–3 dB carry-off — either raise the lower thresholds into
the attack distribution, or decide that RESTRICTED is the correct ceiling for a subtle
spoofer and only the credential layer forces surrender (§8 supports this reading).
The single swap point is console/arbitras/states.py (THRESHOLDS + THRESHOLD_PROVENANCE —
set provenance off "PLACEHOLDER" to clear the console banner). Hysteresis
(RECOVERY_EPOCHS=10, MIN_DWELL=5) made the bad threshold sticky; re-check FSR after the pick.
Confidence is currently equal-weight, β=1 (weights_tuned=false) — the Dirichlet sweep
(§10, Track A's backend/measurement/sweep.py) should be run at the chosen thresholds.
Caveat for the README: one station, one day, one injector setting (carrier_rate_error
0.02 m/s is a test value, unpicked).

## 2. POSITION — beat 2 is a static dot on real data until this exists
Track A emits position = SURVEYED (position_source:"surveyed"); _truth == position, so
displacement reads 0 m. Needed: WLS single-point solution from code_1 + satellite
positions → believed position; _truth = the clean-run solution at the same epoch so
common-mode atmosphere cancels. This is your H matrix — the same H gives
information_ratio / displacement_bound_m / next_best_observation, all currently null.
Eric's propagator API (backend/rinex/ephemeris.py, pseudorange-validated, has BeiDou):
positions_at(t, svs, nav=None) -> DataFrame ECEF m; elevations_at(t, svs, sta_ecef).
Ours (backend/geometry/skyview.py, G+E) feeds geometry.sky — converge on his for H.

## 3. Not yet merged into track_b: Eric's 2600c8d on main (BDT-offset fix + E+C solvability check).

---

# 18:30 CHECKPOINT — converge these, all three tracks (written ~13:35)

Two people found the same physics independently today, which is good news and a
coordination hazard at once:

1. **Uniform range offset on one constellation = that constellation's clock.**
   Position moves 0.0 m. Eric (262f220) and Track B's WLS agent (2ee0239) both
   measured it. Consequences: Track A's default all-GPS carry-off is a TIMING
   attack; the demo stream uses Eric's `top_n_by_elevation(6)` subset rule so
   the fix actually moves (273 m peak). **Decide the demo attack together** —
   Eric flagged "walk direction is a physics decision"; it is also the thing
   the 21:00 thresholds are fitted to.

2. **Two position solvers.** `backend/rinex/solve.py` (Eric: absolute
   all-in-view, iono-free dual-freq, tropo, 5° mask, 3.9–12.3 m from surveyed)
   and `backend/geometry/solve.py` (Track B: single-band, differential
   clean-vs-injected so atmosphere cancels, 0.73 m median from surveyed).
   Different jobs — his feeds the cross-constellation feature, ours feeds the
   displayed displacement — but they are two definitions of "believed position".
   `backend/demo.py` bypasses `replay.run()` and uses ours; `replay.run()` with
   `xc`+`nav` emits his (`position_source: solution`). Pick one for the stream,
   or state the layering in docs/stream_provenance.md.

3. **Two propagators.** `backend/rinex/ephemeris.py` (Eric, pseudorange-
   validated, G+E+C, transmit-time) and `backend/geometry/skyview.py` (Track B,
   gnss-lib-py, G+E) — the latter still feeds `geometry.sky`. Converge on Eric's.

4. **Cross-constellation feature exists (§6a.4) but is NOT in the demo stream.**
   Console shows `—` for it. Wiring it into `backend/demo.py` changes confidence
   and therefore the threshold distributions — do it BEFORE 21:00 or not until
   after. Eric's numbers: meaconing composite d′ 0.18 → 5.04 with it.

5. **Thresholds** (§10 step 4, by hand): attack percentiles in the handoff above
   are stale (all-GPS); current top-6 stream: attack p50 0.564, clean p1 0.652,
   gap +0.034. Post-fit residual RMS (267 m attack vs 2.1 m clean, 127×) is
   the strongest unused signal — `Fix.residuals_m` in `backend/geometry/solve.py`.

6. `position_source` is in the epoch but not copied onto the SSE payload —
   trivial, `console/server.py decide()`, if the console ever needs to show it.

# CHECKPOINT RESOLUTION (written ~21:30, after the threshold session)

Every numbered item above is settled; refs are commits on track-c.

1. **Demo attack decided:** carry-off on `top_n_by_elevation(6)` is the
   default in `backend/demo.py` (`--target all_gps` keeps the timing-attack
   variant). Peak displacement 273.5 m — after surrender.
2. **Solver layering stated, not collapsed:** Eric's absolute solver feeds
   the cross-constellation feature; the differential solver feeds the
   displayed displacement. Documented in docs/stream_provenance.md (2aba33b).
3. **geometry.sky now comes from the GeometryEngine** on the frozen basis
   (3ba7976), i.e. the oracle-verified propagator. `skyview.py` remains only
   as a tested standalone; it no longer feeds the stream.
4. **Cross-constellation is IN the demo stream** (3a25b00), wired before the
   threshold session, so the thresholds below already include it.
5. **Thresholds measured and shipped** (eea1c47): NOMINAL 0.643 (mid
   zero-overlap band), DEGRADED 0.548 / RESTRICTED 0.518 (attack p75/p25),
   d' 7.18. Provenance string travels with the contract.
6. **position_source is on the SSE payload** (`console/server.py`).

Track C deliverables complete through task 12: sigma_UERE 1.934 m measured,
displacement bound live, Dirichlet sweep run (arbitrated FSR median 0.0000,
detection median 0.944, weight-sensitive 44.6%), empirical-vs-bound PASS
2709/2709 arbitrated-NOMINAL epochs (3.67 m vs 14.0 m). README §15 carries
the numbers and plots (9a83772).
