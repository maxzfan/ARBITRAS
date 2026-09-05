# Measured quantities — USN8, 2026-08-20 (DOY 232)

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

# `excluded_sv`: the rule is built, unset, and its clean-day cost measured

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
