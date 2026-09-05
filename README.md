# HOLDFAST

Spoof detection is solved. What happens in the ninety seconds after detection
is not.

HOLDFAST sits between a GNSS receiver and an autonomy stack and continuously
scores positional trust, then degrades the vehicle's authority in stages —
full autonomy, coast on inertial, finish the leg, hold position — rather than
making one binary trust decision.

**Signal quality is not provenance.**

Built at DNHacks, 5-6 September 2026.

## Run it
    bash bootstrap.sh          # python 3.12 venv + libraries + data (~10 min)
    source .venv/bin/activate
    python -m backend.demo && python -m console.server   # then open the console

## Results

Real observables: USN8 (US Naval Observatory), 2026-08-20, 2,880 epochs at
30 s, five constellations. Attack: measurement-domain carry-off injected on
the six highest-elevation GPS satellites, 1 m/s walk-off, onset 12:30.
Detector weights equal (flagged untuned), feature/geometry blend β = 0.5,
thresholds as derived below. The two §10 numbers:

**Integrity — maximum adversarial displacement: 3.67 m** (believed vs true
position at the epoch immediately preceding the first transition out of
NOMINAL), against an **analytic bound of 14.0 m** computed from the same
epoch's trusted geometry. The bound's only empirical input is σ_UERE =
1.934 m, the measured clean-day post-fit residual RMS. The explicit §10
check was run: empirical ≤ bound at **2,709 of 2,709** epochs whose
arbitrated state was NOMINAL — the bound claims nothing once the system has
flagged, and it was never beaten while the system still trusted the signal
(`python -m backend.measurement.displacement out/carryoff.jsonl`).

![empirical vs bound](docs/plots/displacement_empirical_vs_bound.png)

**Continuity — false surrender rate: 0.0000** (0 of 2,880 clean epochs below
NOMINAL through the arbiter, hysteresis included). Reported per convention as
a distribution over 1,000 Dirichlet draws of the four feature weights and the
feature/geometry blend, never a point: raw-confidence FSR min/median/max
**0.000 / 0.000 / 0.282**; arbitrated FSR median **0.0000**, max 0.460 in the
extreme-blend tail. Attack-window detection fraction min/median/max
0.000 / 0.944 / 0.989 — the min is the β→0 (geometry-only) corner, where the
per-SV exclusion rule alone does not push confidence under NOMINAL.
**44.6% of epochs are weight-sensitive** under this deliberately hostile draw
(β swept over the full unit interval); at the shipped weights the verdict
sits in the robust mass of both histograms.

![FSR distribution](docs/plots/fsr_distribution.png)

Supporting numbers: time-to-alert 4 epochs (2 min at the 30 s cadence) from
onset to first downgrade; the injector reached 273.5 m of displacement at
2,660 m of range offset — after the system had already surrendered. The
demo replay traverses NOMINAL → DEGRADED → RESTRICTED → SURRENDERED on
signal alone, recovers up the staircase when the attack stops, then drops to
SURRENDERED by credential force when the TESLA chain lapses under a clean
sky (`python -m console.replay out/demo.jsonl`).

The geometry half behaves as derived, on real data:

- Normalised information ratio over the clean day: 0.887–0.938, no
  exclusions ([plot](docs/plots/information_ratio_clean_day.png)).
- The raw determinant ratio decays exponentially with exclusions while the
  normalised (D-optimality) form stays legible; dropping all of Galileo
  reads 0.653, not 0.00
  ([plot](docs/plots/det_ratio_raw_vs_normalised.png)).
- Freeze-at-last-NOMINAL costs almost nothing: frozen-vs-live ratio gap
  0.0007 at 30 min, ≤ 0.015 over a 2 h freeze
  ([plot](docs/plots/freeze_divergence.png)).

## How the thresholds were set

By hand, from distributions, per design.md §10 — never a round number
without a reason.

1. Clean day through the detector: 2,880 confidence values —
   min 0.6591, p1 0.7315, median 0.8679.
2. Attack window (the injector's own 90-epoch truth log): p95 0.6268,
   p75 0.5478, median 0.5338, p25 0.5182. Separation d′ = 7.18.
3. The only overlap is the attack's first ~4 ramp epochs (range offset
   ≤ 110 m, position displacement ≤ ~7 m) against clean's deepest tail —
   those epochs set time-to-alert, not the threshold.
4. **NOMINAL = 0.643**, the midpoint of the zero-overlap band
   [attack p95 0.6268, clean min 0.6591]: zero clean epochs below it,
   ≥ 95% of attack epochs under it.
5. **DEGRADED = 0.548 (attack p75)** and **RESTRICTED = 0.518 (attack
   p25)** — deliberately raised *into* the attack distribution so the
   state machine traverses the full staircase on signal alone and the
   deep-attack quartile reads SURRENDERED.

The provenance string is stamped into every emitted decision
(`console/arbiter/states.py` is the single swap point). The Dirichlet sweep
above is the answer to "you picked the weights that make this work":
arXiv 2607.05415 showed composite PNT scores flip winners in up to 22% of
re-weighting draws; we report the whole distribution and the exact fraction
of epochs where weighting decides.

## Prior art, named honestly

- **PNTTING** (DHS S&T / MITRE, Molina-Markham et al.) — closest published
  work: probabilistic trust inference for PNT user equipment. More
  principled than ours; no credential binding, no authority arbiter with a
  published false-alarm rate.
- **DHS Resilient PNT Conformance Framework v2.0** (2022) and **Reference
  Architecture v1.0** — our state machine instantiates their behavioural
  intent; "function under the assumption of compromise" is the doctrinal
  parent. **IEEE P1952** is the standard in development; our instrument is
  a candidate conformance test.
- **RAIM / ARAIM** — the closest published relative of the geometry score's
  subset reasoning; independent-fault model, not a coordinated adversary.
  Our planned course-correction track (tracks/TRACK_D.md) is weighted RAIM
  by name.
- **Chen, Dai, Adang, Gao, Schwager — CONVERGE (Stanford)** — Fisher
  Information Gain reduced to a tractable surrogate for active view
  selection. Different domain, same mathematics: the rank-one determinant
  identity behind `geometry.next_best_observation` is theirs.
- **arXiv 2607.05415** (June 2026) — the weighting-instability result the
  Dirichlet sweep answers. Ally and challenge.
- **Galileo OSNMA** (operational July 2025) / **ICAO Doc 10169** — the
  TESLA-based protocols our credential layer reimplements at toy scale.
- **Rothmaier, Chen, Lo, Walter (ION GNSS+ 2021)**; **Psiaki & Humphreys
  (Proc. IEEE 2016)**; **TEXBAT / OAKBAT**; **Shepard, Humphreys, Fansler
  (2012)** — the detection lineage; our detector is that class of method.

## Limitations

Stated properly, not softened:

1. **The attack is synthetic.** Our injector writes the carry-off into real
   RINEX observables; absolute detection numbers are not comparable to
   signal-domain results on TEXBAT. The analytic bound is the defence
   against "your injector was easy": it does not depend on which attacks we
   had time to run.
2. **One station, one day, one injector, static, open sky.** Thresholds are
   measured separation points for USN8/2026-08-20 and generalise no further
   than that sentence.
3. **The geometry score's line-of-sight vectors derive from the receiver's
   own position estimate** — under attack, the spoofed one. Below NOMINAL
   they are frozen at the last trusted epoch: the dependency is bounded
   (divergence ≤ 0.015 over 2 h, logged as evidence), not eliminated.
4. **TESLA depends on loose time sync, and time comes from the receiver
   under attack.** Bounded by the clock-coupling invariant (below NOMINAL
   the clock free-runs and new credentials are not accepted), not
   eliminated. The credential channel is also a DoS surface; interval
   length and disclosure lag are venue-tuned operational parameters.
5. **The displacement bound assumes trusted-subset residual consistency is
   the only constraint on the attacker.** A spoofer with a better
   propagation model attacks the calibration, not the residuals.
6. **The feature half is a weighted sum** and single-number scores are
   weighting-sensitive — hence the distribution, and the honest 44.6%
   weight-sensitive fraction under a full-unit-interval blend sweep. The
   geometry half is weight-free; β = 0.5 of the composite is covered by it.
7. **GLONASS is excluded from the H matrix** (state-vector ephemeris, not
   Keplerian; propagating it didn't fit the build) — it still contributes
   to the feature half. Which constellations entered H is recorded per
   epoch.
8. **Two position solvers coexist** (absolute per-constellation WLS feeding
   the cross-constellation feature; differential WLS feeding the displayed
   displacement and σ_UERE). Layering is documented in
   `docs/stream_provenance.md`; it is a seam, and seams are where bugs
   live.
9. **No live OSNMA verification; anchor distribution assumed.**
10. **26 hours.**

## Philosophy

The Stoics distinguish the impression (*phantasia*) from the assent
(*sunkatathesis*). A spoofed vehicle's error is not in perceiving — the
spoofed signal is genuinely there — but in assenting to it. HOLDFAST is a
discipline of assent: perception continues under attack; authority is what
gets withdrawn. (Epictetus, tr. Carter 1758; Marcus Aurelius, tr. Long
1877 — both public domain.)

## Read first
- `CLAUDE.md` — working agreement and hard constraints
- `docs/design.md` — the authoritative technical document
- `docs/stream_provenance.md` — what is measured, what is injected, what is
  scripted, per stream field
- `tracks/TRACK_{A,B,C,D}.md` — per-person work packets (D is the deferred
  weighted-RAIM course-correction track)
