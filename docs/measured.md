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
