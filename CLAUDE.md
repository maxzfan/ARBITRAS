# HOLDFAST

Trust layer between a GNSS receiver and an autonomy stack. Detects spoofed
positioning, scores confidence continuously, and degrades the vehicle's
authority in stages as confidence falls. Credential state can override
signal quality in both directions.

Built at DNHacks, 5-6 Sept 2026. 26-hour build.

## Environment
No conda. Python 3.12 + venv:

    bash bootstrap.sh              # once per machine
    source .venv/bin/activate      # every terminal, including Claude Code spawns

georinex 1.16.1, gnss-lib-py 1.0.4. Python 3.13/3.14 will NOT work —
gnss-lib-py caps at <3.13.

## Read first
`docs/design.md` — authoritative. When this file and design.md disagree,
design.md wins, EXCEPT for the environment section above (design.md §13
specifies a conda env that does not exist on our machines).

## Hard constraints
- Measurement domain only. Input is RINEX observables. No SDR, no raw IQ,
  no signal-domain processing.
- No GNSS transmission of any kind.
- AGC is not available in RINEX. Do not propose AGC-based detection.
- Library crypto only (PyNaCl / cryptography / cbor2). Never implement
  primitives from scratch. The TESLA hash chain and disclosure schedule
  are protocol logic and are ours to write; SHA-256, HMAC and Ed25519
  come from `cryptography`.
- **No imagery, no camera, no visual odometry, no SLAM.** We have no capture
  hardware and no image data tied to these observations. Do not propose
  vision-based cross-checks or camera fusion.
- Python backend, React console.

## Confidence has two halves
1. **Feature score** — C/N0 anomaly, pseudorange residual, code-minus-carrier,
   cross-constellation. Weighted sum, weights tuned against data.
2. **Geometry score** — Fisher information over the line-of-sight matrix H.
   Distrusting a satellite removes a row; the score is a determinant ratio
   against the full solution. It is derived. Do not replace it with a
   heuristic, a lookup table, or another weighted sum.

The displacement bound is computed from the geometry score, not only measured.
Both halves are always emitted; never report the composite alone.

### Geometry score — two corrections to design.md §6b
design.md §6b flags an open question. It has been worked on paper and the
naive form degenerates. Build the corrected form:

- **H is n x (3+k), not n x 4.** k = number of constellations in the trusted
  set; each contributes its own clock bias. When a constellation's trusted SV
  count reaches zero, DROP that clock column — do not carry a rank-deficient
  matrix. Otherwise meaconing (design.md §7 scenario 3), whose correct response
  is distrusting one whole constellation, zeroes the score by construction.
- **Report the normalised ratio** `(det ratio)^(1/(3+k))`, not the raw
  determinant ratio. The raw form decays with the (3+k)th power and reads 0.00
  on screen after two exclusions. The normalised form is the D-optimality /
  GDOP-volume ratio — the geometric mean of the information eigenvalues — and
  stays legible across the exclusion range.

Both are more defensible on a whiteboard than the original, which satisfies the
convention below.

## Layout
- `backend/` — RINEX parsing, spoofing injector, detection features,
  confidence scoring, credential layer (TESLA), measurement.
- `backend/geometry/` — information matrix, information ratio, analytic
  displacement bound, next-best-observation ranking.
- `console/` — trust state machine, operator console, natural-language
  explanation layer, explanation verifier.
- `fixtures/epoch.json` — one hand-written contract object. Tracks B and C
  build against this and are never blocked on A.
- Boundary is the interface contract in design.md §5. Do not cross it.

## State machine note
DEGRADED is an active state, not a slower shutdown. On entry the console
consumes `geometry.next_best_observation` and pursues it — reweighting toward
the constellation with the most independent geometry, or emitting a lateral
offset advisory. Advisory only. Never command vehicle motion.

Below NOMINAL, both borrowed inputs freeze: the clock free-runs, and geometry
is evaluated against the line-of-sight set held at the last NOMINAL epoch.

## Data
`data/` holds four IGS daily observation files plus merged broadcast
ephemeris, 2026-08-20 (DOY 232), 30s interval, 2880 epochs. Primary station
USN8 (US Naval Observatory). Five constellations. Measured C/N0 noise floor:
+/-0.5 dB. Nav file requires the IRNSS pre-filter before georinex. Gitignored.

Source is the **BKG mirror**, which needs no login. CDDIS requires an Earthdata
account — do not burn time on it. `bootstrap.sh` pulls everything.

## Conventions
- Commit frequently. Git history is the record of when work happened.
- Thresholds derive from observed data. Never hardcode a guessed number.
- Never report the composite confidence without the four sub-scores and the
  geometry block.
- Report FSR as a distribution over sampled weight vectors, not a point.
- The geometry score must be derivable on a whiteboard in two minutes. If an
  implementation makes it un-explainable, the implementation is wrong.
- Keep functions testable against both clean and injected datasets.
