# TRACK D -- Course Correction (trusted-subset fix, protection level, gate)

You own `backend/correction/` and the `correction` sub-block of `geometry` in
the §5 contract. You consume Track A's observables and Track C's H matrix. You
produce a corrected position, a protection level, and a boolean that says
whether the vehicle may drive on it. **You never decide state.** The console
decides what to do with `correction_ok`; you only say whether the math holds.

Read first, in this order: design.md §5, §6b, §8, §10; TRACK_C.md (GOTCHA 2 is
load-bearing for you); TRACK_B.md "Decisions made" (the arbiter is pure Python
and you must not break that).

## Why this track exists

Every state below NOMINAL is currently a subtraction: coast, refuse, halt. The
vehicle's best case under attack is standing still. This track gives DEGRADED a
way to keep moving: re-solve position from the satellites it still trusts, bound
how wrong that fix could be, and drive on it only while the bound is inside the
corridor. Aviation does exactly this -- RAIM fault exclusion gives a fix, the
protection level says whether you may fly on it. We are porting that, not
inventing it. Name it that way in the README.

The design principle does not change: **the vehicle acts on a corrected fix
only inside the alert limit.** That is a threshold with a derivation, not a
policy bolt-on.

## Setup (~10 min, unattended)
    git clone https://github.com/maxzfan/ARBITER.git && cd ARBITER
    bash bootstrap.sh
    source .venv/bin/activate
    python -m pytest console/tests -q      # must be green before you start

## Dependencies -- what you take from the other tracks

| From | What | Where |
|---|---|---|
| Track A | per-epoch dataframe: pseudorange (C1C / C1C), C/N0, per-SV feature scores | `backend/detection/` |
| Track C | H (n x (3+k)), LOS vectors, satellite ECEF positions, trusted set, clock-column handling | `backend/geometry/` |
| Track B | `console.replay.arbitrate()` for replay, `_truth` for validation | `console/` |

**You build against fixtures until 18:30.** `fixtures/epoch.json` plus a
hand-written `fixtures/epoch_obs.parquet` (one epoch of pseudoranges and LOS
vectors, ~12 rows) is enough for D1 through D3. Commit it in your first hour.

## CONTRACT EXTENSION REQUESTED -- two items, settle at 18:30

**1. From Track A: per-satellite anomaly, not only per-feature aggregates.**
The §5 `features` block is four numbers over all satellites. Half-trust needs
one number per satellite. Requested shape, inside `features`:

    "by_sv": {"G07": 0.91, "G13": 0.88, "E11": 0.04, ...}

Max over the three per-SV features (C/N0 anomaly, pseudorange residual,
code-minus-carrier), each already normalised [0,1]. Cross-constellation is a
solution-level feature and does not enter `by_sv`.

**2. To the contract: `geometry.correction`.** Emitted by you, consumed by the
console and the vehicle controller.

    "correction": {
      "corrected_position": {"lat": 38.9207, "lon": -77.0669, "alt": 58.3},
      "protection_level_m": 6.4,
      "alert_limit_m": 12.0,
      "weights": {"G07": 0.0, "G13": 0.0, "G03": 1.0, "E11": 1.0, ...},
      "trusted_count": 9,
      "checks": {
        "pl_under_al": true,
        "redundancy": true,
        "residual_test": true,
        "continuity": true,
        "cross_constellation": null
      },
      "correction_ok": true
    }

`checks` values are `true`, `false`, or `null` (not evaluated, e.g. cross-
constellation before it exists). `correction_ok` is the AND of every non-null
check **plus hysteresis** (below). The console must never recompute it.

## The math -- derivable on a whiteboard in two minutes, per §13

Per-satellite trust weight, from `features.by_sv[sv]` = a:

    w = 1                      if a <= theta_low
    w = (theta_high - a) / (theta_high - theta_low)   between
    w = 0                      if a >= theta_high

Binary exclusion is theta_low == theta_high. Track C's `excluded_sv` must equal
`{sv : w == 0}` -- assert it, same as the `sky.trusted` assertion.

Weighted solve, W = diag(w), H from Track C (already deduped, already with the
per-constellation clock columns, already with empty-constellation columns
dropped -- do not rebuild any of that):

    G   = H^T W H                 (3+k) x (3+k)
    S   = G^-1 H^T W              (3+k) x n
    dx  = S @ d_rho               d_rho = observed pseudorange - predicted range
    P   = I - H @ S               n x n residual projection
    r   = P @ d_rho               post-fit residuals

`corrected_position = x_lin + dx[0:3]` where `x_lin` is the position **frozen at
the last NOMINAL epoch** (Track C item 4). Not the live believed position. Under
attack the live one is the attacker's frame.

`d_rho` must have the satellite clock correction from ephemeris removed
(gnss-lib-py does this; use it). Ignore iono/tropo -- they are near common-mode
at one site and the clean-day residual floor absorbs them. Say so in the README.

Protection level (single-fault, weighted RAIM slope). For satellite i, a bias b
that the detector has not caught keeps its residual under tau:

    slope_i = || S[0:3, i] || / sqrt(P_ii)        (metres of displacement per metre of residual)
    PL      = tau * max_i slope_i                 (single fault)
    PL_k    = tau * sum of the k largest slope_i  (k-fault, k from §7 scenario)

`tau` is the per-satellite residual threshold Track A sets at 21:00. Until then
it is `THRESHOLD_PROVENANCE = "PLACEHOLDER"` and the console banner fires, same
mechanism as Track B. **PL is `displacement_bound_m` computed on the corrected
solution.** Reconcile with Track C at 18:30 so the field is not produced twice
with two definitions; the slope form should replace the eigenvalue form if Track
C has not already done it, and the README should call it by its RAIM name.

Satellites with w near 0 have near-zero slope: excluding them shrinks PL. A
satellite with small P_ii (the others cannot check it) has a large slope even
when it looks clean. Put both facts on screen; they explain the decision.

## The gate -- five checks, then hysteresis

1. `pl_under_al`: PL < alert limit. The alert limit is corridor half-width. It
   is a chosen number with a written justification (design.md §16 open item).
   `ALERT_LIMIT_M` lives in one constant with a provenance string, Track B style.
2. `redundancy`: at least 5 satellites with w > 0.5 **and** n_eff = sum(w) >= 3+k+1.
   With no redundancy P is zero, the slope is infinite, and the test means nothing.
3. `residual_test`: r^T W r / (n_eff - (3+k)) below the chi-square cutoff fit on
   the clean day at 21:00. A trusted subset that disagrees with itself is not trusted.
4. `continuity`: corrected fix agrees with dead reckoning from the last NOMINAL
   position within the DR drift bound. **This is the defence against a majority
   spoof** -- a self-consistent captured subset passes check 3 and fails this.
   On the replay, DR is "still at the antenna" with drift 0 + noise floor. On the
   RC car, DR is integrated commanded wheel speeds. On a real UGV, the IMU.
5. `cross_constellation`: single-constellation fixes overlap the corrected fix
   within their own covariances. `null` until Track A's fourth feature exists.

Hysteresis, matching the arbiter's asymmetry: `correction_ok` turns **on** only
after 10 consecutive epochs of all checks passing, turns **off** on the first
failing epoch. One epoch can revoke, no single epoch can grant.

Speed advisory, for the controller: `speed_scale = clamp((AL - PL) / AL, 0, 1)`.
Emit it; do not act on it. Advisory only, same rule as the lateral offset.

## What the console does with it (Track B, one consumer)

In DEGRADED with `correction_ok == true`: controller and map use
`corrected_position`; on-screen text reads "Driving on trusted satellites,
bound N m, corridor M m." In DEGRADED with `correction_ok == false`, and in every
other state: existing behaviour, untouched. RESTRICTED and SURRENDERED never
consult the correction block. Credential override still dominates everything.

This is one `if` in the controller and one overlay on the map. Track B owns it.

## Subagent decomposition -- five agents, D1 through D3 run in parallel

Each agent gets this file, design.md, and TRACK_C.md. Each agent's first action
is to run the console tests and confirm green. Each agent commits to its own
worktree; the owner merges.

**D1 -- weighted solver** (`backend/correction/solve.py`)
Inputs: H, w, d_rho, x_lin. Outputs: dx, S, P, r, G. Pure numpy, no I/O.
Acceptance: with w = all ones, `dx` and `G` match Track C's unweighted solve to
1e-9. With w = binary exclusion, matches Track C's trusted-subset solve. Unit
tests for both, plus rank-deficiency: dropping every SV of one constellation
must raise, not return garbage (Track C GOTCHA 2a).

**D2 -- protection level** (`backend/correction/protection.py`)
Inputs: S, P, tau, k. Outputs: per-SV slope, PL, PL_k. Acceptance: on the
fixture epoch, hand-compute slope for one satellite and match. On a synthetic
H with one satellite nearly collinear with another, its slope is the largest.
Property test: PL is monotone non-increasing when a satellite's w goes to 0.

**D3 -- gate + hysteresis** (`backend/correction/gate.py`)
Inputs: the five check inputs, epoch history. Outputs: `checks`, `correction_ok`,
`speed_scale`. Pure, deterministic, no wall clock -- Track C's Dirichlet sweep
replays it and Track B's arbiter rule applies: **no time inside the gate.**
Acceptance: 10-epoch grant, 1-epoch revoke, verified in tests the way Track B
verified invariant 2.

**D4 -- contract + emitter** (`backend/correction/emit.py`, after D1-D3 merge)
Wires D1-D3 into the per-epoch pipeline, emits `geometry.correction`, asserts
`weights == 0` set equals `excluded_sv`, asserts `corrected_position` is finite.
Extends `fixtures/epoch.json`. Acceptance: fixture stream round-trips through
`console.replay` with zero verifier-banner fires.

**D5 -- validation plots** (`backend/correction/validate.py`, after D4)
Four plots, README-bound, from the real replay:
1. Clean day: corrected fix vs `_truth`, all 2,880 epochs. Should sit on the
   antenna within a few metres. If it wanders, the linearisation or satellite
   clock handling is wrong -- stop and fix before anything else.
2. Clean day: PL over time. Small, stable. This is the first defensible
   alert-limit argument; the alert limit should sit well above it.
3. Injected day: believed, corrected, `_truth` on one axis, with the epoch the
   exclusions land and the epoch `correction_ok` turns on marked.
4. Injected day: empirical displacement of the **corrected** fix vs PL. If the
   empirical number ever exceeds PL, PL is wrong. Run the check, say you ran it.

## Build order -- each step independently demoable

| When | What | Gate to next step |
|---|---|---|
| Hour 1 | Fixtures committed; D1, D2, D3 started in parallel | Tests green |
| +2 h | D1 matches Track C unweighted to 1e-9 | Do not proceed if it does not |
| +2 h | D2, D3 tests green | |
| 18:30 | Checkpoint: `by_sv` from A, H from C, `correction` block agreed | Contract frozen |
| +1 h | D4: block flowing through `console.replay` | Zero banner fires |
| 21:00-22:30 | Thresholds with A and C: tau, chi-square cutoff, theta_low, theta_high, alert limit. **By hand.** | `THRESHOLD_PROVENANCE` off PLACEHOLDER |
| +1.5 h | D5 plots 1 and 2 (clean day) | Corrected fix on the antenna |
| +1 h | D5 plots 3 and 4 (injected day) | Empirical <= PL |
| Night | Track B wires the consumer; video beat 3 gains the "keeps driving" close | |

## Cut rule

This track sits at cut-order position 4, between the lateral-offset advisory
and cross-constellation. If plot 1 does not put the corrected fix on the
antenna by 23:30, **cut D4 and D5, keep D1-D3 in the repo, and say in the README
that correction was built and validated on fixtures but not on the real
replay.** The `correction` block then emits with `correction_ok: false` on every
epoch and the console never consults it. Nothing anyone else built is touched.

Do not ship a corrected fix that has not been checked against `_truth` on the
clean day. A wrong correction driven onto the map in beat 3 is worse than no
correction, and the judges who build this for a living will see it.

## What goes in the README limitations

- The corrected fix is only as good as the exclusion. A self-consistent majority
  spoof passes the residual test; only continuity and cross-constellation stand
  in the way, and continuity on the replay is trivially satisfied by a static
  receiver. State this plainly.
- Single-fault PL is a lower bound on multi-fault exposure. Report PL_k and k.
- theta_low, theta_high, tau, chi-square cutoff, alert limit: fit on one station,
  one day, static, open sky.
- Iono/tropo ignored as common-mode; the clean-day residual floor absorbs them
  at one site and would not at scale.
- Acting on the corrected fix inside the alert limit is a policy choice made
  explicit, not a proof that acting is safe. It is the same choice aviation
  makes with RAIM, and it is the honest answer to mentor question 3 in §16.

## What it looks like on video, if it lands

Beat 3 no longer ends with the vehicle stopped. Confidence falls, satellites go
dark on the sky view, the bound circle appears on the map and shrinks as the
exclusions land, `correction_ok` flips, and the vehicle slows but keeps moving
down the corridor on the corrected fix while the believed marker drifts into the
building. Narration: "It knows how much it can trust what is left. It drives on
exactly that much." Then the credential close, as before.

If the RC car exists: the OFF car hits the wall; the ON car slows, wobbles,
and finishes the corridor.

# TRACK RESOLUTION (written 2026-09-05, after the fit)

Built as specified, D1 through D5; refs are commits on main.

1. **D1-D3 landed** (0dbf0fa): weighted solve on Track C's H as-is (LOS
   columns negated internally, `corrected_position = x_lin + dx[0:3]`),
   slope-form PL, five-check gate with 10-grant/1-revoke hysteresis. One
   acceptance criterion was false as written: PL is NOT monotone
   non-increasing as any weight → 0 (information loss inflates the other
   slopes; G18 sweep moved PL 1.09 → 1.50 m/m). The true form — the
   downweighted SV's own slope → 0 — is asserted, counterexample pinned in
   `test_protection.py`.
2. **D4 landed** (3ad9bfc): `geometry.correction` and `features.by_sv` flow
   through `backend.replay` and `backend.demo`; the extended fixture
   round-trips `console.replay` with zero verifier-banner fires. Weights are
   the binary fallback (theta_low == theta_high) from `excluded_sv`, with
   `{sv: w==0} == excluded_sv` asserted.
3. **Thresholds fit, PLACEHOLDER retired** (590962d): tau 5.187 m, chi-square
   cutoff 7.703 m², DR drift bound 20.672 m — all p99.9 of the clean day
   (`python -m backend.correction.validate`, distributions in
   out/correction_thresholds.json). ALERT_LIMIT_M 15.0 still carries
   PLACEHOLDER provenance pending team sign-off.
4. **Cut rule: passed.** Corrected fix on the antenna horizontally — p50
   0.95 m, max 3.97 m over 2,880 clean epochs. The 3D error (p50 15.2 m) is
   the unmodelled single-frequency vertical atmosphere, stated in the README.
5. **§10-style integrity check run: zero violations in all four scenarios**
   (correction_ok true while attack-induced displacement > PL). Carry-off
   revokes 1 epoch after onset. Uniform offsets (simplistic, meaconing)
   displace the corrected fix 0.000 m — absorbed exactly by the clock
   columns; timing attacks, not position attacks.
6. **Known holes, stated:** the demo distrust rule never fires under any
   scenario (the residual test does all the revoking — saturation threshold
   deserves a look); 14 clean epochs show fault-free error > PL (no K·sigma
   nominal term — README limitation 11); check 5 (cross-constellation
   per-solution overlap) remains a None seam.
