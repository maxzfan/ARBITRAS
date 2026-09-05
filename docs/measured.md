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
