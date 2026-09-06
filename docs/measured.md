# Measured quantities — USN8, 2026-08-20 (DOY 232)

> **SUPERSEDED:** every d' figure recorded in this document before commit
> `5780797` is stale — that run is the first with the 5° mask, the post-fit
> residual feature 2, and a correctly-keyed cross-constellation calibration.
> Use the "Items 4 and 5, measured on the masked detector" section at the end.

Everything a threshold or an injected magnitude is expressed against. Measured,
not assumed (CLAUDE.md: *thresholds derive from observed data*). Regenerate with
`python -m backend.rinex.report_floor`.

Station USN8 (US Naval Observatory), 2,880 epochs, 30 s, 00:00:00–23:59:30 GPS
time, 109 satellites across G/E/R/C/S. All figures are robust (MAD-derived)
epoch-to-epoch scatter on the **first difference** along time, per satellite,
pooled by median — differencing removes the elevation trend in C/N0 and the
ionospheric ramp in code-minus-carrier and leaves the noise.

## C/N₀

| | dB-Hz |
|---|---|
| epoch-to-epoch σ, median over 109 SV | **0.262** |
| σ, p90 over SV | 0.524 |
| level, min / median / max | 37.50 / 46.75 / 53.50 |
| **file quantisation step** | **0.250** |

design.md §4 states ±0.5 dB. Measured, that is the p90; the median satellite is
tighter at 0.26 dB-Hz.

**But 0.262 is a ceiling, not a measurement.** 1.4826 × 0.25 / √2 = 0.262 exactly
— the median satellite's differenced MAD *is* one quantisation step. The receiver
reports C/N₀ to 0.25 dB, so the true epoch-to-epoch noise is at or below the
resolution of the file and cannot be resolved from it. Consequences:

- Quoting detection sensitivity against 0.262 dB-Hz **overstates** it. The
  detector floors its per-satellite σ at the quantisation-limited value rather
  than trusting anything smaller.
- design.md's ±0.5 dB is the conservative number and the one to say out loud.
  Against it a 1–3 dB spoofer is **2–6σ**, which is the §4 claim, and it holds.

## Code-minus-carrier

Epoch-to-epoch σ, metres, band 1, median over satellites:

| System | n SV | σ (m) |
|---|---|---|
| G | 31 | 0.184 |
| E | 28 | 0.138 |
| C | 23 | 0.217 |
| R | 24 | 0.365 |
| S | 3 | 1.085 |
| **pooled** | 109 | **0.204** |

## GLONASS channel numbers — a bug this measurement caught

GLONASS is FDMA: G1 = 1602.0 MHz + k × 562.5 kHz, k ∈ −7..6, per satellite. The
channel number is in the **nav** file, not the observation file, so the loader's
first version assumed the nominal k = 0.

Code-minus-carrier is `C − λL`. A fractional wavelength error ε turns the
satellite's own range rate into fake divergence at `ε × range_rate`. GLONASS
range rate reaches ~800 m/s, so assuming k = 0 when k = ±7 injects tens of metres
per epoch. Measured: **R median CMC σ was 13.98 m** against 0.18 m for GPS —
i.e. feature 3 was structurally unusable on GLONASS and would have read as a
permanent spoof.

`backend/rinex/glonass.py` recovers k with no nav file: sweep all fourteen
channels, keep the one minimising differenced CMC scatter. Result:

- all 24 GLONASS SV fitted, winner-to-runner-up margin **7×–17×**
- **all 24 agree exactly with the frequency numbers in the broadcast ephemeris**
- R median CMC σ **13.98 m → 0.365 m**
- fitted channels reproduce the known antipodal-pair reuse (R01/R05 = +1,
  R02/R06 = −4, …), which is independent structure the variance sweep was not
  told about

Kept as a from-observations fit rather than a nav lookup: it costs Track A no
dependency on the nav file, and §6b already notes one file failure costs two
components.

## SBAS

S CMC σ 1.085 m, ~5× the other constellations. Geostationary, so this is not a
channel-frequency artefact. Not chased — SBAS is not in the trusted set for
positioning and is present for the roadmap argument in §4.

---

# Detection — clean vs injected, USN8 full day

> **Staleness note (5 Sep, ruling):** the carry-off rows below were produced
> with the pre-ruling divergence model (2σ/epoch ≈ 0.014 m/s equivalent). The
> parameter is now `carrier_rate_error` with the demo pin pending; carry-off
> figures will be regenerated when the pin lands. Clean-day, simplistic and
> meaconing figures in this section are unaffected by that ruling but
> superseded by the four-feature results in the cross-constellation section
> below.

Calibration fitted on the **clean** replay only and reused for every injected
one; fitting on injected data would let the attack define normal and make the
false-surrender rate meaningless. Saturating |z|, median per-satellite p99:
C/N₀ 15.8, pseudorange residual 12.7, code-minus-carrier 5.1.

Regenerate with `python -m backend.replay`.

## Feature separation, d′ against the clean day

Sustained window = 30–90 min after onset. Onset window = first 10 epochs.

| Scenario | C/N₀ onset | C/N₀ sustained | Residual sustained | CMC sustained |
|---|---|---|---|---|
| simplistic (15 dB, all SV) | **21.9** | 0.60 | **11.0** | 3.5 |
| carry-off (2 dB, GPS subset) | 1.6 | 0.10 | **11.0** | 1.8 |
| meaconing (8 dB, GPS bias) | **10.5** | 0.44 | 0.70 | 0.13 |

The §6a claims reproduce, including the ones that are admissions:

- **C/N₀ fires hard and then goes quiet.** d′ 21.9 → 0.60 on the crude attack.
  §6a.1 predicts this. In this pipeline the fade takes `cn0_window` = 20 epochs,
  because that is how long the trailing mean takes to absorb the step — the
  receiver's own 10 s loop dynamics are not observable at 30 s sampling. Quote
  the window, not the 10 s.
- **The residual carries the sustained detection**, d′ 11.0 on both walk-off
  attacks, saturating at 1.000. §6a.2's stated role, confirmed.
- **Meaconing is invisible to features 2 and 3** — d′ 0.70 and 0.13, i.e. not
  detected at all once the C/N₀ transient passes. Correct by construction: a
  repeater re-radiates the authentic signal, so the code/carrier relationship
  survives intact. This is the concrete argument that cross-constellation
  (§6a.4) is needed rather than optional, and it is worth weighing against its
  position on the §14 cut list.

## Composite confidence — and the placeholder thresholds are wrong

Equal feature weights, β = 1.0 (no geometry half yet, so the whole score is
weight-sensitive; this drops to β once Track C lands).

| | confidence |
|---|---|
| clean day, mean | 0.745 |
| clean day, σ | 0.042 |
| clean day, p1 / p50 / p99 | 0.652 / 0.744 / 0.831 |
| clean day, min / max | 0.605 / 1.000 |
| carry-off, attack window mean | 0.487 |
| carry-off, **d′ vs clean** | **6.88** |
| simplistic, d′ vs clean | 4.85 |
| meaconing, d′ vs clean | 0.18 |

> **design.md §8's placeholder NOMINAL threshold of 0.75 sits almost exactly on
> the clean day's median of 0.744. On clean data it produces a false surrender
> rate of 55.6%** — 1,602 of 2,880 epochs downgraded to DEGRADED with nothing
> wrong. Nothing reaches RESTRICTED or SURRENDERED.

§8 already says the thresholds are placeholders to be replaced with measured
separation points. This is how wrong they are, measured, and it is the starting
input to the threshold session (§10, TRACK_A.md 21:00–22:30). For scale: a
NOMINAL threshold at the clean p1 of 0.652 would put FSR at 1% while still
sitting 3.9σ above the carry-off attack mean of 0.487.

**Do not read that 0.652 as the answer.** It is one distribution against one
scenario; the session sets thresholds against both distributions with the
overlap plotted, and reports the separation it chose (§10 step 4).

## Open, carried into integration

- **Features 2 and 3 are two statistics of one observable** — the level and the
  rate of code-minus-carrier — not two independent observables. A true
  pseudorange residual needs the position solution, which needs Track C's H.
  Cheap upgrade once it lands, and it makes the two independent. Stated in
  `backend/detection/features.py` rather than presented as three.
- **Clean features sit at 0.20–0.31, not near zero.** Consequence of saturating
  at a measured tail rather than at the noise. Harmless for separation, and
  deliberately not corrected here — rescaling is a threshold-session decision
  made with both distributions in view.
- **β = 1.0 until geometry arrives**, so `weight_sensitive_fraction` currently
  reads 1.0. That figure is the answer to arXiv 2607.05415 and it only improves
  once the derived half is in.

---

# Cross-constellation feature (§6a.4) and the position solver

Gate for this feature was the E+C solvability check: solvable every epoch of
the demo window, GDOP ≤ 2.05, zero degenerate epochs.

## Solver (`backend/rinex/solve.py`)

Iono-free dual-frequency code, broadcast ephemeris at **transmit time**, SV
clock af0+af1, Sagnac, standard-atmosphere tropo, 5° mask. All-in-view vs
USN8 surveyed ECEF at 00:30/06:30/12:30/18:30: **3.9–12.3 m** (residual RMS
4–8 m). Per-constellation solutions 11–44 m (weak single-system geometry;
only *changes* in disagreement are scored). Two propagation bugs were caught
by measurement on the way: reception-time evaluation (±50 m prefit spread —
the satellite moves ~270 m during signal flight) and the BDT 14 s offset
misplaced into the earth-rotation term (~1 mrad node error, 0.4–5 km on every
BDS MEO).

## The finding that shaped the feature

**A range offset applied uniformly to every satellite of one constellation is
indistinguishable from that constellation's clock.** The solver absorbs it
entirely; the believed position moves 0.0 m (measured, meaconing 300 m). Two
consequences:

1. A position-only cross-constellation comparison is blind to meaconing *and*
   to the current carry-off injection. The feature therefore scores
   **inter-system clock offsets** (dt_G−dt_E, dt_G−dt_C, dt_E−dt_C) alongside
   pairwise position disagreement — the 300 m meaconing bias lands on clk_GE
   at its full size.
2. **The injector's walk-off currently walks the receiver's clock, not its
   position.** Making beat 2's believed track actually move needs per-SV
   offsets `e_sv·Δp` toward a spoofed position — a physics decision (walk
   direction) that is not Claude's to make.

## Scoring design — three measured failure modes, then the one that works

| Design | Clean day | Attack |
|---|---|---|
| global clean-day centre | idles at 0.655 (drift read as anomaly) | caught |
| trailing baseline, level-gated | latches: 10% of clean pinned at 1.0 (rise/set jumps refused forever) | caught, sustained |
| + relearn on SV-set change | clean healthy | 11 h meaconing **adopted as baseline** at first rise/set: d′ 0.09 |
| **+ measured-step compensation, increment-gated** | **mean 0.229, p99 0.644** | **meaconing 1.000 sustained 11.5 h, d′ 6.18** |

The working form: when a constellation's tracked set changes, both epochs are
re-solved on the common subset and the baseline is shifted by the *measured*
geometry step (an attacker's standing bias cancels out of it; an attack step
cannot hide in it). Absorption into the trailing window is gated on the
per-epoch **increment** (clean p99.9, per channel) — slow drift (cm/epoch) is
followed, attack steps and walks (30–300 m/epoch) are refused forever.

## Full-day results, four features, believed position from the solver

| | clean | simplistic | meaconing |
|---|---|---|---|
| cross_constellation mean | 0.229 | 0.222 | **1.000** (sustained) |
| cross_constellation d′ | — | 0.04 | **6.18** |
| composite confidence mean | 0.751 | 0.526 | 0.541 |
| composite d′ | — | 4.52 | **5.04** (was 0.18 with 3 features) |

Clean confidence: mean 0.751, σ 0.051, min 0.533. `position_source:
"solution"` on all 2,880 epochs. Simplistic's xc d′ 0.04 is the stated blind
spot (an all-sky bias shifts every clock together), owned by features 1–2 at
composite d′ 4.52.

Carry-off is not in this table: the demo pin for `carrier_rate_error` is
pending and `backend.replay` skips it by design until the pin is given.


---

# Feature 2's residual is not a solution residual — stated before the sweep

Asked and answered before the position-walk injector was written, because it
changes what the sweep output means.

**Neither all-in-view nor per-constellation. There is no position solution in
feature 2 at all.** `backend/detection/features.py` computes
`pseudorange_residual` as the deviation of **code-minus-carrier** from its own
per-satellite trailing baseline: `(C1 - lambda*L1)` against a 40-epoch median,
one satellite at a time, no receiver position and no H matrix anywhere in it.
Feature 3 is the epoch-to-epoch rate of the same quantity. This is documented
in that module already ("two statistics of one observable, not two independent
observables") and it is the honest reason feature 2 has never needed the nav
file.

The consequence for a coordinated position walk is sharper than "they go
quiet", and the precise statement matters:

> **At `carrier_rate_error > 0`, features 2 and 3 respond to the spoofer's
> incoherence at magnitude `carrier_rate_error x t` — a quantity that is
> INDEPENDENT of how far the vehicle has been displaced. They detect
> ADVERSARY ERROR, not ATTACK EFFECT.**

Two consequences that follow directly, and they are the ones to say out loud:

- **A coherent spoofer displacing the vehicle 500 m is invisible to them.**
- **A clumsy one displacing it 2 m is not.**

The feature magnitude is a measure of how badly the attacker built their
transmitter, not of how much danger the vehicle is in. Nothing in features 1-3
is a function of the displacement:

| feature | responds to | function of displacement? |
|---|---|---|
| 1 C/N₀ anomaly | the capture power step, fading over `cn0_window` | no |
| 2 residual (as built) | code-minus-carrier level = `rate x t` | **no** |
| 3 divergence | code-minus-carrier rate = `rate` | **no** |
| 4 cross-constellation | authentic E/C disagreeing with spoofed G | yes |

A per-constellation solution residual would go to zero for a coordinated
GPS-only walk because the walk lies in the position columns of H — that is a
geometric blindness. Features 2 and 3 have a *different* and broader
blindness: they never see geometry at all, at any rate.

This is why §10 must record residuals on the spoofed and authentic subsets
separately: the residual that appears under a coordinated walk is
cross-constellation disagreement, not GPS-internal inconsistency.

# `excluded_sv`: SUPERSEDED -- see the ExclusionRule section at the end

`detection.flagged_sv` names the satellites whose worst per-SV score reaches
`k x` its calibrated saturation. `k` has **no default**; unset, the list is
empty. Measured on the clean day, the cost of each candidate:

| rule | clean epochs flagging >= 1 SV | mean SV flagged |
|---|---|---|
| k = 1.0 | **78.9%** | 1.77 |
| k = 1.5 | 34.6% | 0.43 |
| k = 2.0 | 12.7% | 0.14 |
| k = 3.0 | 2.7% | 0.03 |
| k = 5.0 | 0.5% | 0.00 |

Every one of those is a false exclusion on a clean sky: a satellite the console
paints as distrusted during demo beat 1, and a row removed from Track C's H.
The heavy tail is the same low-elevation multipath structure the C/N0 section
describes. Numbers printed; `k` is a threshold-session decision and is not
chosen here.


---

# Sweep harness — three axes, both domains. BUILT, GATED, NOT RUN.

`python -m backend.measurement.sweep` refuses without `--run`. Grid:

| axis | values |
|---|---|
| domain | position, clock |
| subset size | 12 / 8 / 6 / 4 (top-N by elevation at capture, frozen) |
| `carrier_rate_error` | 0.0 / 0.005 / 0.01 / 0.02 / 0.05 / 0.1 m/s |
| bearing | 8 directions 45° apart (position domain only) |

## Verified on a 2-cell x 6-epoch build check, not the sweep

The numbers below come from a deliberately tiny configuration written to the
scratchpad, run to prove the machinery executes and the recorded quantities
mean what the field names say. **The sweep itself has not been run.**

| commanded | achieved | resid all | resid spoofed | resid authentic | **resid G-only** |
|---|---|---|---|---|---|
| 0 m | 0.00 m | 3.98 | 5.39 | 2.98 | **5.35** |
| 20 m | 8.18 m | 6.49 | 8.56 | 5.08 | **5.00** |
| 50 m | 20.47 m | 14.79 | 19.32 | 11.73 | **4.73** |
| 80 m | 32.75 m | 22.94 | 30.11 | 18.06 | **5.31** |
| 110 m | 45.05 m | 30.86 | 40.43 | 24.35 | **4.77** |
| 140 m | 57.35 m | 40.01 | 52.42 | 31.58 | **4.85** |

Three things this establishes:

1. **Achieved is 0.41 x commanded**, steady. The authentic E and C
   observations pull the least-squares back toward truth, so a spoofer that
   commands 140 m of displacement achieves 57 m. That factor is what the
   third constellation buys, and it is why §10 must measure achieved from the
   solved position and never report commanded.
2. **A spoofed-constellation-only solve is blind to its own walk.** Its
   residual sits at 4.7–5.4 m across the whole range while commanded goes
   0 → 140 m. The walk lies in the position columns of H, so a GPS-only
   solution absorbs it into its own position estimate. **Every metre of
   residual in the all-in-view solution is cross-constellation
   disagreement**, not GPS-internal inconsistency — which is the same
   conclusion the feature-2 note reaches from the other direction.
3. **The clock domain achieves 0.0 m**, as its own scenario should.

## A bug the consistency check caught

The first version of the residual split recomputed post-fit residuals inside
the sweep instead of taking the solver's. It omitted the Sagnac rotation and
read **19 m where the solver read 4 m** — and the partitioned numbers were
inconsistent with the pooled RMS they were supposed to decompose, which is
what made it visible. `solve()` now returns `resid_m` per satellite from its
own final iteration and the duplicate implementation is deleted. Two
implementations of one quantity is a bug factory; there is now one.


---

# `carrier_rate_error` demo pin: 0.0136 m/s

Ruled by hand from the printed arithmetic at k = 2σ, t = 30 s:
2 × 0.204 / 30 = **0.0136 m/s**. Written into
`backend.injector.DEMO_CARRIER_RATE_ERROR` and the default of
`backend.replay --carrier-rate-error`.

**Chosen for demo legibility, not as a physical claim. The reported
displacement bound is measured at `carrier_rate_error = 0`.**

At the pin, the divergence reaches 2σ of the measured clean code-minus-carrier
noise by the first observable epoch after lift-off, which is what makes the
step visible on screen. It says nothing about how coherent a real spoofer's
carrier would be — and by the finding above, the feature magnitude it produces
measures the adversary's workmanship, not the vehicle's exposure.


---

# Item 1 investigation: are features 2 and 3 the same channel?

Asked by hand, measured before anything was changed. Regenerate with the
script under the session scratchpad.

## What each computes today

| | formula | statistic |
|---|---|---|
| feature 2 `pseudorange_residual` | `\|cmc(t) − mean(trailing 40)\| / σ_cmc` | **level** of code-minus-carrier |
| feature 3 `code_carrier_divergence` | `\|cmc(t) − cmc(t−1)\| / (σ_cmc·√2)` | **rate** of code-minus-carrier |

`cmc = C1 − λL1`. **Same observable, same channel; one integrated, one
differenced.** Feature 2 is not the post-fit pseudorange residual §6a.2
specifies — there is no position solution in it.

## Correlation of the two feature series

| replay | Pearson | Spearman |
|---|---|---|
| clean full day | **+0.113** | +0.093 |
| carry-off, rate 0.0136 | **+0.424** | +0.347 |
| carry-off, rate 0 | +0.113 | +0.093 |
| meaconing | +0.113 | +0.093 |

## Composite d′ with feature 2 removed entirely

| scenario | d′ (4 features) | d′ (no feature 2) | change |
|---|---|---|---|
| carry-off, rate 0.0136 | 5.23 | 5.42 | **+0.19** |
| carry-off, rate 0 | 5.09 | 5.21 | **+0.12** |
| meaconing | 5.04 | 5.07 | **+0.03** |

## Per-feature d′, attack window vs clean

| scenario | C/N₀ | residual | divergence | **cross-const** |
|---|---|---|---|---|
| carry-off, rate 0.0136 | 0.03 | 2.04 | 0.57 | **6.01** |
| carry-off, rate 0 | 0.03 | 0.77 | 0.01 | **6.01** |
| meaconing | 0.15 | 0.77 | 0.01 | **6.18** |

## What this shows — and the hypothesis it does *not* support

**The 5.04 was not counting one channel twice.** The two features are drawn
from the same observable but they are nearly uncorrelated (+0.11 on clean
data), because a level and its own first difference are close to orthogonal
for a drifting signal. Removing feature 2 does not cut the composite; it
raises it slightly. So the redundancy framing is not what the numbers say.

**What they say instead is dilution.** The composite d′ of 5.04 is *lower than
its own best feature*: cross-constellation alone reads 6.18. Three features
that barely move (0.15, 0.77, 0.01) are averaged with equal weight against one
that separates cleanly, and the average drags the good one down. That is a
weighting result, and it is a direct input to the threshold session — it says
the equal-weight placeholder is actively costing separation, not merely
untuned.

**Feature 2 as built earns nothing on the scenarios that matter**: d′ 0.77
against both meaconing and a coherent walk, rising to 2.04 only when the
spoofer is incoherent — which by the finding above is a measure of adversary
workmanship, not attack effect. It is the weakest feature in the set and it is
blind to geometry.

So the case for reimplementing it as the post-fit residual from `solve()`
stands, but on the original reason rather than on redundancy: **nothing in
features 1–3 carries geometry, so nothing in features 1–3 can see a
coordinated position walk.** Feature 4 is currently the only channel that can,
and a single channel carrying the entire detection is a fragile place to be.


---

# Item 1 fix: feature 2 reimplemented as the post-fit residual

`pseudorange_residual` is now the per-satellite post-fit residual of the
all-in-view least-squares solution (`solve()['resid_m']`) against its own
trailing median, normalised by a per-satellite sigma measured on the clean day
(median **0.51 m**). This is what §6a.2 specified. Saturating |z| moved from
12.7 to 8.4.

## Per-feature d′, before and after

| scenario | feature 2 before | feature 2 after |
|---|---|---|
| carry-off, incoherence pin | 2.04 | **8.59** |
| carry-off, **coherent (rate 0)** | 0.77 | **8.59** |
| meaconing (clock domain) | 0.77 | **0.02** |

The middle row is the whole point: **8.59 at `carrier_rate_error = 0`.** The
old feature 2 could only see the spoofer's incoherence, so it read 0.77
against a coherent walk however far the vehicle moved. The post-fit residual
sees the *displacement*, so a perfectly coherent spoofer no longer hides from
it. Detection of a position walk no longer rests on feature 4 alone.

## Composite d′, before and after

| scenario | before | after | without feature 2 |
|---|---|---|---|
| carry-off, pin | 5.23 | **8.48** | 5.42 |
| carry-off, coherent | 5.09 | **8.32** | 5.21 |
| meaconing | 5.04 | **3.84** | 5.07 |

Feature 2 went from dead weight to load-bearing on the position walk:
removing it now costs 3.07 of d′ where before it gained 0.19.

**And the meaconing row got worse, which is correct and worth stating.** A
uniform clock-domain bias is absorbed by the constellation clock unknown, so
the post-fit residual is ~zero by construction — feature 2 correctly sees
nothing (d′ 0.02). But under equal weights its silence is averaged in, and the
meaconing composite falls 5.04 → 3.84. Dropping feature 2 would restore it to
5.07.

So the dilution finding from the investigation now cuts both ways: **the same
feature is the strongest signal on one scenario and pure dilution on another.**
No single fixed weight vector is right for both. That is the sharpest input
this track can hand the threshold session, and it is an argument for the
§10 Dirichlet sweep being reported per scenario rather than pooled.

Clean composite confidence with the new feature 2: mean **0.768**, σ 0.061,
p1 0.598. Correlation between features 2 and 3 fell to +0.098 (clean) and
+0.001 under a coherent walk — they are now genuinely different channels.

## Dependency taken on, stated

Feature 2 now needs satellite positions, so it needs the nav file. §6b warned
one file failure costs two components; it now costs three. The fallback is
explicit rather than silent: with no residual supplied, feature 2 is **not
scored at all** (NaN, excluded from the aggregate) rather than quietly reading
zero, and the calibration prints `resid sigma UNSET (feature 2 not scored)`.

## A cache-collision hazard closed while doing this

`residual_panel` was keyed on the replay span and epoch count. A clean replay
and an injected one cover the same span with the same count, so the second
would have silently received the first's residuals — calibrating an attack
against its own injected data. The key now fingerprints the observables
themselves. Nothing shipped with the collision; it is recorded because the
class of bug (span-keyed caches over mutated data) applies to the other caches
in the loader too.


---

# Item 4: exclusion rate with the right denominator, and the elevation floor

Both requested measurements, taken on the clean full day **after** feature 2
became the post-fit residual (the earlier table was computed with the old
feature and is superseded).

## P(at least one SV excluded in an epoch)

Per epoch is the denominator that matters, because it is epochs that lose H
rows. The per-SV-epoch rate is given for scale and is ~40x smaller, since the
station tracks 39.9 satellites per epoch.

| k | **P(≥1 SV excluded)** | mean SV excluded | P(per SV-epoch) |
|---|---|---|---|
| 1.0 | **61.1%** | 0.92 | 2.30% |
| 1.5 | **22.6%** | 0.25 | 0.63% |
| 2.0 | **9.5%** | 0.10 | 0.25% |
| 3.0 | **2.2%** | 0.02 | 0.06% |
| 5.0 | **0.3%** | 0.00 | 0.01% |

To be precise about the earlier figures: they were **already** per-epoch, not
per-SV — the 78.9% quoted before was P(≥1 SV excluded per epoch) with the old
feature 2. Replacing that feature cut the rate by roughly a fifth at every k
(78.9% → 61.1% at k=1.0, 2.7% → 2.2% at k=3.0) as a side effect.

## Is the floor elevation-driven? Yes, essentially entirely.

Exclusion rate binned by satellite elevation, sampled every 8th epoch (359
epochs, 10,177 SV-epochs with ephemeris):

| elevation bin | n | excl% (k=3) | share of exclusions (k=3) | excl% (k=5) |
|---|---|---|---|---|
| 0–15° | 2640 | **0.08%** | **100.0%** | 0.00% |
| 15–30° | 2359 | 0.00% | 0.0% | 0.00% |
| 30–60° | 3386 | 0.00% | 0.0% | 0.00% |
| 60–90° | 1792 | 0.00% | 0.0% | 0.00% |

At k=3 the surviving exclusions sit at a **median elevation of 0.4°** (p90
0.8°, max 0.9°) — they are satellites on the horizon, not satellites being
spoofed. Above 15° the clean rate is zero in every bin at both k values.

**Caveat:** elevations come from Keplerian broadcast ephemeris, which covers
G/E/C only. GLONASS and SBAS — about a quarter of the tracked sky — are absent
from the bin table. The k-rate table above covers all five constellations.

## Implemented

`detection.ExclusionRule`:

- **`MASKED_K3`** — the ruled rule, `k = 3.0` plus a low-elevation mask, with
  the cutoff **UNSET**. `armed` is False and it emits nothing until a cutoff
  is given. This is the default in `backend.replay`.
- **`FLAT_K5`** — the configured fallback, `k = 5.0`, no mask, usable without
  elevations.

A satellite below the mask cutoff is **not** excluded: the mask means "this
satellite's score is not trustworthy evidence of spoofing", which is the
opposite of "this satellite is spoofed". Whether a low-elevation satellite
should be dropped from H for its own noise is a separate question and Track
C's.

The elevation-binned distribution is printed above. **The cutoff is not
chosen here.**


---

# STALENESS WARNING for the README results section

The README (added on `track_b`/`track_c`) publishes §10 numbers computed
against the **previous** feature 2 — maximum adversarial displacement 3.67 m
vs a 14.0 m bound, FSR 0.0000, and the derived thresholds NOMINAL 0.643 /
DEGRADED 0.548 / RESTRICTED 0.518, with clean confidence min 0.6591 and attack
p95 0.6268.

Feature 2 is now the post-fit residual, which changes the composite it is
derived from:

- clean composite mean 0.751 → **0.768**, σ 0.061, p1 0.598
- coherent carry-off composite d′ 5.09 → **8.32**
- meaconing composite d′ 5.04 → **3.84**

**Every threshold, FSR figure and displacement number in the README was fitted
to the old distribution and needs regenerating before it is quoted anywhere.**
The direction of change is favourable for the carry-off demo (better
separation) and unfavourable for meaconing, so this is not a uniform shift
that leaves the thresholds valid.

Not edited here: those numbers and that section belong to the tracks that
produced them, and silently rewriting another track's published results is
worse than flagging them. Regeneration is a threshold-session task, since
step 4 of the §10 procedure has to be redone on the new distributions.


---

# Elevation mask: 5 degrees, applied upstream (ruling 2026-09-05)

**5 degrees is the standard aviation/ARAIM mask angle, taken from convention
and NOT fitted to the exclusion bins it controls** — a cutoff tuned on the data
it governs is a threshold in disguise. The measured bins (100% of clean-day
exclusions in 0–15°, median 0.4°) confirm 5° is a safe place to cut; they did
not choose it.

## Applied as a mask, not an exemption

Satellites below 5° are dropped from the epoch **entirely**, before feature
scoring, before the position solution, and before the geometry block
(`rinex.solve.masked_epoch`). After masking they are neither trusted, nor
excludable, nor usable as cover.

The rejected alternative — exempting low-elevation satellites from exclusion
eligibility while still solving on them — creates a **safe harbour**:
satellites that can never be flagged are exactly the ones an attacker would
choose to capture. `ExclusionRule` now has no elevation term at all, and a
test asserts `flagged_sv` takes no elevation argument so the path cannot come
back.

## The denominator, coordinated with Track C

The information ratio's denominator `det(HᵀH_all)` must be taken over the
masked set too, or the ratio moves for reasons unrelated to any attack.

Track C's engine already masks its own basis — but at **10°**, its default,
against the ruled 5°. Left alone, the position solution would have stood on a
5° set while the geometry ratio stood on a 10° one. Resolved by making the mask
angle a single definition (`rinex.solve.EL_MASK_DEG`) that `backend.replay`
pushes into the geometry engine via a new `set_el_mask_deg`, added in the same
style as their existing `set_sigma_uere`. Verified end to end: the minimum
elevation appearing in the emitted geometry block is exactly 5.00°.

**Validated on three constellations out of five.** The elevation bin table
needs Keplerian broadcast ephemeris, which covers G/E/C only, so GLONASS and
SBAS — about a quarter of the tracked sky — were not part of the validation.
Satellites without usable ephemeris are *kept* rather than masked, deliberately:
they are absent from H either way, so masking them would silently remove
GLONASS from the C/N₀ feature while claiming to be a geometry decision.

`satellites_tracked` now counts **usable** satellites (post-mask) rather than
raw signals present in the file. That is the number that matters for a trust
layer — how many satellites the solution is standing on.

---

# Limitation: the temporal signature is reproduced ~60x slow

Decision 3 approved as proposed — flat `+power_db` from capture onward, with
the fade emerging from the detector's rolling-mean memory rather than from any
injected decay constant.

**The timescale does not survive the data source, and this is a limitation of
the archive, not a defect of the injector.** §7's stage-2 fade is ~10 s. Our
epochs are 30 s, and the C/N₀ trailing mean is 20 epochs, so the emergent fade
sits at **~10 minutes**. We reproduce the *shape* of the four-stage §7
signature — onset spike, fade to baseline, lift-off, elevated steady state — at
a timescale roughly **60× longer than the physical one**, because 30 s archived
observables cannot resolve a 10 s transient. Nothing in the measurement domain
can; resolving it needs the raw IQ that §12 puts out of scope by construction.

Say the shape is right and the clock is stretched. Do not present the ~10
minute fade as the receiver's loop dynamics.

---

# Injected transients: demo only, never in measurement

Decision 5 values approved — capture jitter 3σ of measured clean C/N₀ noise,
lift-off transient 6σ of measured clean code-minus-carrier, one epoch each —
and split by config per the ruling:

| config | transients |
|---|---|
| demo (`Spoof.transients` default) | **ON** |
| every sweep and measurement run | **OFF** (forced in `run_sweep`, asserted by test) |

Both are recorded per epoch in the injector's truth log (`transients` column)
and in every sweep record, so no reported number is ambiguous about which
setting produced it.

Verified: with transients off, the lift-off CMC spike disappears from exactly
one epoch (1.226 m = 6 × 0.204 m) and the capture C/N₀ jitter from exactly the
capture epoch (0.91 dB rms ≈ 3 × 0.262 dB); every other epoch is bit-identical
between the two runs.

A bug this split caught: the capture jitter initially ignored the flag
entirely. It was invisible in a naive diff because both runs draw from the same
seed, so the jitter cancelled and the difference read 0.000 dB — which looked
like "the flag works" rather than "the flag does nothing".

---

# Meaconing 300 m: a lower bound, not decomposed

Decision 4 approved as written. 300 m ≈ 1 μs of excess path, the minimum for
any repeater with physically separated antennas. It is labelled a **lower
bound** and deliberately not decomposed into antenna-to-antenna path plus
amplifier/hardware group delay: an honest decomposition needs a repeater
geometry and hardware we have not specified, and inventing both to arrive back
at the same number would dress a guess up as a derivation. A real meaconer adds
unmodelled hardware delay, so the true bias is ≥ 300 m.


---

# Items 4 and 5, measured on the masked detector

All figures below are the CURRENT numbers and supersede every earlier d′ in
this document: they are the first taken with the 5° mask, the post-fit-residual
feature 2, and a correctly-keyed cross-constellation calibration (see the cache
note at the end). Attack scenarios scored on a 2 h window centred on onset with
60 epochs of causal warm-up; clean baseline and FSR denominator are the full
2,880-epoch day.

## Item 4 — are we detecting our own injected spike? No.

Composite and per-feature d′ at `carrier_rate_error = 0`:

| transients | C/N₀ | residual | divergence | cross-const | **composite** |
|---|---|---|---|---|---|
| **ON** | 0.17 | 7.62 | 0.37 | 7.55 | **10.38** |
| **OFF** | 0.18 | 7.62 | 0.37 | 7.55 | **10.22** |

Sustained detection does not depend on the transients: composite d′ moves by
0.16 (1.6%) and no per-feature figure moves at all. **We were not partly
detecting our own artefact.** Transients stay ON for the demo and OFF for every
measurement, and the setting is recorded per epoch either way.

## Item 5 — weighted_sum vs max, both printed, neither picked

| scenario | d′ (sum) | d′ (max) | per-feature d′ (cn0 / resid / cmc / cross) |
|---|---|---|---|
| carry-off, coherent | **10.22** | 9.20 | 0.18 / 7.62 / 0.37 / 7.55 |
| carry-off, pin | **10.47** | 9.20 | 0.18 / 7.62 / 1.54 / 7.55 |
| clock-domain walk | **9.08** | 8.62 | 0.18 / 4.47 / 0.37 / 12.38 |
| meaconing | 4.47 | **9.55** | 0.20 / 0.76 / 0.37 / **28.85** |
| simplistic | 0.91 | **1.79** | 0.28 / 0.76 / 3.99 / 0.28 |

Clean-day composite distribution:

| mode | mean | σ | p1 | p0.1 | min |
|---|---|---|---|---|---|
| weighted_sum | 0.8001 | **0.0425** | 0.6568 | 0.5441 | 0.5000 |
| max | 0.6768 | 0.1002 | 0.2241 | 0.0470 | 0.0000 |

False surrender rate against a **swept** NOMINAL threshold (none picked), with
the coherent carry-off's detection fraction alongside:

| NOMINAL | FSR sum | FSR max | det sum | det max |
|---|---|---|---|---|
| 0.50 | **0.0000** | 0.0514 | 0.9889 | 1.0000 |
| 0.55 | 0.0014 | 0.0698 | 0.9944 | 1.0000 |
| 0.60 | 0.0024 | 0.1187 | 0.9944 | 1.0000 |
| 0.65 | **0.0080** | **0.2403** | 0.9944 | 1.0000 |
| 0.70 | 0.0312 | 0.5069 | 0.9944 | 1.0000 |
| 0.75 | 0.1010 | 0.8469 | 1.0000 | 1.0000 |
| 0.80 | 0.4205 | 0.9875 | 1.0000 | 1.0000 |

### What the two rules actually buy

**Neither dominates, and the split is along scenario lines.**

- `weighted_sum` wins where several features move together: the position walk
  (10.22 vs 9.20) and the clock-domain walk (9.08 vs 8.62). Averaging helps
  when there is more than one live channel.
- `max` wins decisively where exactly one feature carries everything:
  **meaconing 4.47 → 9.55**. Cross-constellation alone reads 28.85 there; the
  equal-weight average drags that to 4.47, and max recovers a bit over twice
  the separation without being told which scenario it is in. It also doubles
  the simplistic case (0.91 → 1.79).
- **The dilution is not fixed by either rule.** On meaconing even max reaches
  only 9.55 against the 28.85 its best channel achieves alone, because max's
  own clean distribution is 2.4× wider (σ 0.1002 vs 0.0425) — d′ divides by
  that spread, so max gives back in variance much of what it gains in mean.

**The cost lands exactly where predicted: false alarms.** At a 0.65 threshold,
FSR is 0.0080 for sum and 0.2403 for max — a 30× increase for a detection
fraction that was already 0.9944. Max buys its meaconing separation with a
clean day that pins at zero confidence on 1 epoch in 20 (p0.1 = 0.047,
min 0.0).

Both are implemented (`confidence.COMBINE_MODES`); `weighted_sum` remains the
shipped default and **neither is picked here**. One property worth carrying to
the threshold session: `max` has no weights at all, so it would drive
`weight_sensitive_fraction` to 0.0 and make the tuned half weight-free — a
second answer to arXiv 2607.05415, bought at that FSR.

Scenario-conditional weights were **not** implemented: the runtime detector
does not know which scenario it is in, so it could not carry them.

## A cache defect that cost a set of measurements

The first run of this measurement reported cross-constellation d′ of 2.36–2.70
and concluded that `max` failed to recover any separation. Both were wrong.
`fit_cross`'s calibration cache was keyed on epoch span and count, which are
**identical for a masked and an unmasked clean day**, so the feature was
calibrated on unmasked channel values while being scored on masked ones. With
the key fingerprinting the observables, cross-constellation d′ on meaconing is
28.85, not 2.70.

This is the same defect fixed in `rinex.solve.residual_panel` one session
earlier and **missed here**. The generalisable lesson, recorded because it will
recur: *span-keyed caches over mutated epoch streams are unsafe in this
codebase* — clean vs injected, masked vs unmasked, and any future filtered
variant all collide. Both caches now fingerprint the observables; any new one
must too.


---

# Thresholds ruled 2026-09-06, and the state-level continuity cost

    NOMINAL    0.8247   measured clean p1 of THIS detector
    DEGRADED   0.50     carried from design.md §8
    RESTRICTED 0.25     carried from design.md §8

Split provenance, stamped into every emitted decision: NOMINAL is measured on
this detector; DEGRADED and RESTRICTED are carried, and their justification is
not a fit — both sit below the measured clean minimum of 0.7500, so neither can
contribute a false surrender on this day. They partition the attack
distribution, not the clean one.

## FSR denominator: epoch 0 excluded (ruled)

Epoch 0 has no causal baseline, so every per-SV feature is unscored, the
anomaly reads 0, and confidence is exactly 1.0000 — an artefact of having no
history, not a measurement of a clean sky. It is dropped from the
false-surrender denominator (2,880 → 2,879 epochs).

**The 0.750017 epoch is kept.** It is the clean-day minimum, it looks like a
round 0.75 and is not, and it is data: `cross_constellation` saturated at 1.0
on clean sky with the geometry deficit at zero.

## Per-epoch FSR understates the cost by ~4x

design.md §8 makes recovery asymmetric on purpose: a downgrade fires on one
epoch's evidence, an upgrade needs 10 sustained epochs above the higher
threshold plus 5 epochs' dwell. So one bad epoch does not cost one epoch of
authority — it costs one epoch plus the whole climb back. A per-epoch
threshold sweep has no memory and cannot see this.

Measured through the arbiter on the clean day (`python -m
backend.measurement.continuity`):

| | NOMINAL 0.8247 (ruled) | NOMINAL 0.80 |
|---|---|---|
| scored epochs | 2,879 | 2,879 |
| distinct downgrade events | **8** | 3 |
| episodes below NOMINAL | 8 | 3 |
| epochs below NOMINAL | **125** | 36 |
| **fraction of day not NOMINAL** | **0.0434** | **0.0125** |
| longest / median episode | 26 / 15.0 epochs | 16 / 10.0 epochs |
| states visited | DEGRADED, NOMINAL | DEGRADED, NOMINAL |

> **State-level FSR at the ruled threshold is 4.34%, against a per-epoch
> figure of 1.01% at the same value — a 4.3x amplification.** Eight isolated
> clean-day excursions become 125 epochs (62 minutes) of reduced authority,
> because each one carries a ~15-epoch recovery. The same ratio holds at 0.80
> (1.25% state-level against 0.28% per-epoch, 4.5x).
>
> Report the state-level number. The per-epoch one is not wrong, it is
> answering a question no operator asks: what an operator experiences is time
> under unnecessary restriction, which is what §10 says it intends to measure.

Neither threshold ever reaches RESTRICTED or SURRENDERED on clean data — the
clean distribution never comes near 0.50.

## Time to alert, and whether authority returns

Same arbiter, same hysteresis, at the ruled thresholds:

| scenario | epochs to first downgrade | min state reached | recovers before window ends |
|---|---|---|---|
| carry-off coherent | **1** (30 s) | SURRENDERED | no |
| carry-off at the pin | **1** (30 s) | SURRENDERED | no |
| clock-domain walk | 2 (60 s) | SURRENDERED | no |
| meaconing | **0** | DEGRADED | no |

Meaconing alerts at the onset epoch itself because its confidence (0.7854
± 0.0349) already sits below 0.8247 — the threshold was placed at the clean p1
precisely where the meaconing distribution begins. It reaches DEGRADED and
stops there, correctly: 0.7854 is nowhere near the 0.50 carried threshold. The
walks cross the whole staircase within a couple of epochs.

None recover, which is the intended behaviour: all four attacks run to the end
of their window, so there is nothing to recover from yet.


---

# README §10 figures regenerated on the current detector (2026-09-06)

Streams regenerated with `python -m backend.demo` after repairing the
generator (see the defect note below), then:

    python -m backend.measurement.displacement out/carryoff.jsonl
    python -m backend.measurement.weight_sweep out/clean.jsonl \
        out/carryoff.jsonl --nominal 0.8247 --n-draws 1000 --arbitrate

## The generator was scoring with feature 2 dead

`backend/demo.py` called `fit(clean, floor)` with no `resid_panel` and never
masked. Once feature 2 became the post-fit residual it *requires* the
solution, so in that generator it returned nothing and scored **0.0 on every
epoch**. The previously published §10 numbers were therefore not merely fitted
to a stale distribution — they came from a detector whose strongest channel was
silent. This was a consequence of the feature 2 change that should have been
traced into the generator when it was made and was not.

Repaired: `demo.py` masks upstream, builds the residual panel, calibrates with
it, threads per-epoch residuals into scoring (one solve per epoch, shared with
the cross-constellation feature), and takes the demo pin from its single
definition instead of the `0.02` test literal it still carried.

Solver sanity on the regenerated clean stream: horizontal p50 **0.73 m**, p95
1.67 m against the surveyed position, GDOP p50 1.63, zero unsolvable epochs.

## Integrity — maximum adversarial displacement: 0.00 m

At the ruled thresholds the arbiter leaves NOMINAL **one epoch after onset**,
and the epoch preceding that transition is the onset epoch itself, where the
walk has not yet displaced the solution. So the attacker achieves **0.00 m**
before authority is reduced, against an analytic bound of **12.6 m** at the
same epoch.

**Two caveats, both of which matter more than the number.**

1. **The figure is quantised by the 30 s epoch.** "0.00 m" means the attacker
   got less than one epoch of walk-off, not that displacement is impossible.
   Detection is faster than the sampling interval, so the measurement floor
   and the result coincide. The attack goes on to reach **1027.0 m** inside
   the window — long after the vehicle has surrendered.
2. **§10's literal wording no longer picks the attack.** It says the epoch
   preceding *the first* transition out of NOMINAL. With NOMINAL at the clean
   p1 there are 8 clean-day downgrade events, the first at **00:26:30** — ten
   hours before onset — so the literal definition selects a clean epoch and
   returns 0.00 m for a reason that has nothing to do with the attack. Both
   scopings happen to give 0.00 m here, so nothing is currently misreported,
   but they agree by coincidence. **Scoping the definition to the first
   transition after onset is a measurement decision and is not taken here.**

Claim 2a check: empirical <= bound at **2,649 of 2,649** arbitrated-NOMINAL
epochs — PASS.

## Continuity — false surrender rate

Reported as a distribution over 1,000 Dirichlet draws, never a point (§10):

| | min | median | max |
|---|---|---|---|
| **arbitrated FSR** (hysteresis, the §10 headline) | 0.0000 | **0.0344** | 0.2858 |
| downgrade events per draw | — | 5 | 54 |
| raw per-epoch FSR | 0.013 | 0.059 | 0.979 |
| attack detection fraction | 0.978 | 0.989 | 1.000 |

**Weight-sensitive epochs: 96.2%**, against 44.6% in the superseded README.
That is a worse answer to arXiv 2607.05415 and it should be reported as one.
The likely reason is mechanical rather than mysterious: feature 2 is now
load-bearing on the walks (d' 8.59) and silent on the clock-domain attack
(0.02), so a re-weighting moves the composite much further than it did when
feature 2 was weak everywhere. A single fixed weight vector is doing more work
than before, so its choice matters more.

At the ruled weights the arbitrated FSR median of 0.0344 sits close to the
directly measured 0.0434 for the equal-weight vector; the spread is the answer
to the objection, not the median alone.
