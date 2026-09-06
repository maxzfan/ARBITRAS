# Verifiable Position for Autonomous Systems

Technical project document — the authoritative one. Everything needed to build is here. Event logistics, judge research and the anticipated-question drill bank live in separate documents and are not needed to write code.

Last updated 3 September 2026. Team of three.

**If you are running your own Claude against this file:** paste it in whole and ask about your section. It is written to be self-contained. Where it says a decision is open, it is genuinely open — argue with it.

---

# 1. WHAT IT IS

A trust layer between a GNSS receiver and an autonomy stack, so a vehicle can tell when it is being lied to about where it is and give up authority before acting on bad data.

**Scenario:** a resupply UGV in a contested corridor. Anchored to the Army's April 2026 Commercial Solutions Opening for a last-tactical-mile UGV — resupply forward, casualty evacuation back, across the segment under the greatest threat from enemy observation and fires. Demonstration event GroundBreaker 1, mid-October 2026, Camp Grafton ND.

**Four components:**
1. Detection — signal features *and* a Fisher-information geometry score over replayed real observables → continuous confidence
2. Arbitration — a state machine mapping confidence to how much authority the vehicle keeps
3. Credentials — TESLA-authenticated broadcast mission authorisation
4. Measurement — integrity risk and continuity risk, reproducibly

---

# 2. THE CLAIM

**Not claimable:** detection (shipped — Septentrio, u-blox, Safran BroadShield); staged degradation (aviation integrity, DHS RPCF levels); trust scoring for PNT (PNTTING); "frameworks lack measurement" (arXiv 2607.05415, June 2026); **geometry-derived integrity** — DOP *is* the Fisher information of the position solution, and ARAIM already reasons over satellite-subset geometry. Say this out loud before a judge says it for you.

**Claim 1 — the coupling.** Positional authority is arbitrated as a joint function of a continuous signal-derived confidence *and* a discrete cryptographic credential state, where credential state can dominate in both directions: a revoked or expired credential forces surrender regardless of signal quality, and an unverified credential caps authority below full autonomy. No known fielded system makes the *provenance* of a platform's authorisation constrain the *positional authority* it may exercise.

**Claim 1a — the honest qualifier.** Credential state dominates, but not unconditionally. TESLA verification depends on loose time synchronisation, and time comes from the receiver under attack. We bound the dependency rather than deny it: the vehicle disciplines its clock to GNSS only while `NOMINAL`, and stops accepting new authorisations below it. Degraded position confidence tightens the credential rules; a failed credential collapses positional authority. The two halves constrain each other.

**Claim 2 — the instrument.** Two paired, reproducible measurements from the same replay, expressed in aviation's certification vocabulary. Together they form a cost curve for a threshold, which the conformance frameworks describe the need for and leave to self-attestation.

**Claim 2a — the displacement bound is derived, not only measured.** Maximum adversarial displacement is reported two ways: swept empirically over injector parameters, *and* bounded analytically from the Fisher information of the trusted satellite subset. The two are plotted together. Agreement is a result; disagreement is a more interesting one, and either way the number stops depending on how many attack variants we had time to run.

**Motto:** *Signal quality is not provenance.*

---

# 3. PRIOR ART

| Work | Relationship |
|---|---|
| **PNTTING** — PNT Trust Inference Engine (DHS S&T / MITRE, Molina-Markham et al.) | **Closest published work.** Probabilistic trust inference for PNT user equipment, explicitly intended to assist automated decisions. More principled than ours. No credential binding; not an authority arbiter with a published false-alarm rate. |
| **DHS Resilient PNT Conformance Framework v2.0** (May 2022) | Four resilience levels, source-agnostic, outcome-based. Our state machine instantiates its behavioural intent. |
| **DHS Resilient PNT Reference Architecture v1.0** (June 2022) | Applies Zero Trust to PNT — "function under the assumption of compromise." Doctrinal parent. |
| **IEEE P1952** (in development) | Standard for Resilient PNT User Equipment. Our instrument is a candidate conformance test. |
| **arXiv 2607.05415** (June 2026) | Makes the measurement-gap argument independently, and shows composite scores are weighting-unstable — re-weighting flips the winner in up to 22% of draws under nominal conditions, ~1% under active denial. Ally and challenge. |
| **Galileo OSNMA** (operational 24 July 2025) | TESLA delayed disclosure, live PKI, Merkle root, OTAR. Upstream: authenticates the message, says nothing about authority. |
| **RAIM / ARAIM** | Independent-fault model, not a coordinated adversary. Answers "can I trust this fix," not "how much may this platform do." Its subset-geometry reasoning is the closest published relative of our geometry score. |
| **Chen, Dai, Adang, Gao, Schwager — CONVERGE** (Stanford) | Fisher Information Gain reduced to a tractable coverage surrogate for active view selection. Different domain, same mathematics: the source of the geometry score in §6b and the observation-selection rule in §8. Cited by name in the README. |
| **Rothmaier, Chen, Lo, Walter — ION GNSS+ 2021** | Spoofing detection via metric combinations. Stanford GPS Lab. Our detector is this class of method. |
| **Psiaki & Humphreys, Proc. IEEE 2016** | Canonical survey. |
| **TEXBAT** (UT Austin, ds1–ds8) / **OAKBAT** (ORNL, os1–os6) | Standard spoofing datasets, both raw IQ. We work in the measurement domain. |
| **Shepard, Humphreys, Fansler 2012** | PMU spoofing — canonical critical-infrastructure demonstration. |
| **ICAO Doc 10169** | SBAS authentication: TESLA, Ed25519, KMAC, compressed certificates. The protocol our credential layer reimplements. |

---

# 4. DATA

Source: NASA CDDIS, IGS daily archive, day-of-year 232 (2026-08-20). Files are distributed to each machine before travel; they live in `data/` in the repo and are gitignored.

| File | Station | Notes |
|---|---|---|
| `USN800USA_R_20262320000_01D_30S_MO.crx.gz` | US Naval Observatory, DC | **Primary.** Septentrio PolaRx5TR (timing variant). Contributes to UTC(USNO). |
| `STFU00USA_S_...` | Stanford | Secondary |
| `GODE00USA_R_...` | NASA Goddard, MD | Secondary |
| `ALGO00CAN_R_...` | Algonquin, ON | Geographic spread |
| `BRDC00IGS_R_20262320000_01D_MN.rnx.gz` | — | Merged broadcast ephemeris. Requires the IRNSS pre-filter. |

All four observation files MD5-verified against the CDDIS manifest.

**USN8:** RINEX 3.03, Hatanaka-compressed, read natively by `georinex`. 30 s interval, 2,880 epochs (00:00:00–23:59:30 GPS time). 109 satellites, 5 constellations. ECEF `[1112161.8802, -4842854.4026, 3985497.3830]`. SBAS observables present (WAAS L1 and L5) — relevant to the roadmap argument.

**Observable codes:**

| System | Codes |
|---|---|
| G (GPS) | C1C L1C S1C, C1W S1W, C2W L2W S2W, C2L L2L S2L, C5Q L5Q S5Q |
| E (Galileo) | C1C L1C S1C, C6C L6C S6C, C5Q L5Q S5Q, C7Q L7Q S7Q, C8Q L8Q S8Q |
| S (SBAS) | C1C L1C S1C, C5I L5I S5I |
| R (GLONASS) | C1C L1C S1C, C1P L1P S1P, C2P L2P S2P, C2C L2C S2C, C3Q L3Q S3Q |
| C (BeiDou) | C2I L2I S2I, C6I L6I S6I |

> **The number everything derives from: measured C/N₀ noise floor 35–50 dB-Hz depending on elevation, epoch-to-epoch variation ≈ ±0.5 dB.** A 1–3 dB sophisticated spoofer is 2–6σ out against it.

**Constraint:** RINEX observation files contain code (C), carrier phase (L), Doppler (D) and signal strength (S) only. AGC is receiver hardware telemetry, not an observable — out of scope by construction. There is a deployment argument for that: a trust layer needing only what a receiver already exports works with any receiver.

---

# 5. INTERFACE CONTRACT

One object per epoch. Backend emits, console consumes. **This is the architectural boundary — nothing crosses it.** The backend never knows about states; the console never touches observables.

```json
{
  "timestamp": "2026-08-20T00:14:30Z",
  "confidence": 0.42,
  "credential_status": "VALID",
  "position": { "lat": 38.9207, "lon": -77.0669, "alt": 58.3 },
  "position_source": "surveyed",
  "features": {
    "cn0_anomaly": 0.71,
    "pseudorange_residual": 0.15,
    "code_carrier_divergence": 0.08,
    "cross_constellation": 0.34
  },
  "geometry": {
    "information_ratio": 0.58,
    "excluded_sv": ["G07", "G13"],
    "displacement_bound_m": 41.2,
    "next_best_observation": "E"
  },
  "satellites_tracked": 11,
  "score_detail": {
    "feature_score": 0.29,
    "geometry_deficit": 0.42,
    "beta": 0.5,
    "geometry_available": true,
    "weights_tuned": false,
    "weights": { "cn0_anomaly": 0.25, "pseudorange_residual": 0.25,
                 "code_carrier_divergence": 0.25, "cross_constellation": 0.25 },
    "weight_sensitive_fraction": 0.5,
    "features_scored": ["cn0_anomaly", "pseudorange_residual",
                        "code_carrier_divergence", "cross_constellation"]
  }
}
```

- `confidence` — float [0,1], 1.0 = fully trusted. Continuous, never binary.
- `credential_status` — `VALID` | `PENDING` | `EXPIRED` | `UNVERIFIED` | `REVOKED`
  - `PENDING` is normal and transient: MAC received, key not yet disclosed. Lasts exactly the disclosure lag.
- `position` — what the receiver *believes*. Under attack this is the spoofed position. That is the point.
- `features` — each [0,1], higher = more anomalous. **Always emitted alongside the composite**, so the explanation layer can name which signal diverged and so we never report a single number alone.
- `position_source` — `"solution"` | `"surveyed"` *(added 5 Sep)*. Until the
  position solution exists the backend reports the station's surveyed position
  flagged `"surveyed"`, so a displacement read off it is visibly zero by
  construction rather than quietly wrong. The console may render either; it
  must not compute displacement from a `"surveyed"` position.
- `score_detail` — *(added 5 Sep)* the decomposition of `confidence`:
  `feature_score` (the tuned half), `geometry_deficit` (1 − information ratio),
  `beta` (the blend actually applied; forced to 1 when `geometry` is null),
  `geometry_available`, `weights_tuned` (**false until the threshold session —
  any number produced while false is a placeholder**), `weights`, and
  `weight_sensitive_fraction` (the share of the composite a re-weighting can
  move; the answer to the arXiv 2607.05415 objection), and `features_scored`
  *(added 5 Sep, Track E)* — the features that actually entered the weighted
  sum this epoch; weights are renormalised over that list, so an absent
  feature is not scored rather than read as zero. Additive: the composite
  is never shipped without the parts that made it.
- `geometry` — the weight-independent half of the score. `information_ratio` ∈ [0,1] is the determinant ratio of the trusted-subset information matrix against the full solution. `excluded_sv` is which satellites the detector stopped trusting and therefore which rows came out. `displacement_bound_m` is the analytic bound at this epoch. `next_best_observation` is the observation that would recover the most information — consumed by the console in `DEGRADED`, ignored elsewhere.

**Transport:** JSON Lines appended to a file; console tails it.
**Replay rate:** file time is 30 s/epoch; demo replays at 10–20 epochs/sec.
**Ownership:** backend produces `confidence`, `features` and `geometry`; console owns the thresholds mapping confidence to state and owns what to do with `next_best_observation`. The console still never touches an observable.

**Stale-epoch behaviour:** a missing or malformed epoch is evidence of degradation, not a no-op. If the stream goes silent the console steps state down on a timeout rather than freezing. Silence is not consent.

---

# 6. DETECTION

Confidence has two halves, computed separately and combined last. The split matters: one half is tuned, one half is derived, and only the tuned half is exposed to the weighting objection.

## 6a. Feature score — tuned

Four features, each normalized to [0,1], combined by weighted sum. Weights tuned at the venue.

1. **C/N₀ anomaly** — per-satellite deviation from its own rolling mean. Strongest and cheapest. *Known weakness:* goes quiet ~10 s after capture once elevated power becomes the new baseline. This is why one feature is insufficient.
2. **Pseudorange residual** — deviation from a smoothed per-satellite baseline. Stays elevated after the C/N₀ signal fades.
3. **Code-minus-carrier divergence** — `C - L` should be near-constant except for ionospheric drift. Breaks when a spoofer manipulates code without perfectly matching carrier.
4. **Cross-constellation disagreement** — independent position solutions from GPS-only vs Galileo-only vs GLONASS-only. Most attackers target one constellation. **Needs the nav file. First to cut.**

The failure modes are complementary *in time*, not just in strength — that is why four.

## 6b. Geometry score — derived

The four features answer *is something wrong with this signal.* The geometry score answers a different question: *given which satellites I have stopped trusting, how much of my position solution is left, and how far can an attacker move me with what remains.*

Build the line-of-sight matrix `H` over tracked satellites. Its information matrix `HᵀH` is the Fisher information of the position solution — this is DOP, written the way it is actually defined. Distrusting a satellite removes a row.

- **Information ratio** = `det(HᵀH)` over the trusted subset, normalised against the full-constellation solution. Scaled to [0,1]. No free parameter.
- **Displacement bound** = the largest position error consistent with the trusted geometry before residuals become inconsistent, computed from the same matrix. This is the analytic companion to the swept empirical number in §10.
- **Next-best observation** = the satellite or constellation whose readmission would recover the most information. Falls directly out of the rank-one update — adding a row is `det(G + hhᵀ) = det(G)(1 + hᵀG⁻¹h)`, so ranking candidates is one quadratic form each.

The formulation is borrowed from active view selection (§3), where the same rank-one determinant update ranks candidate camera poses by information gain. Same mathematics, different sensor.

**What it buys.** Confidence was previously a weighted sum end to end, which is the most attackable design decision in the project (§10). The geometry half has no weights to attack. It also gives `DEGRADED` something to *do* rather than only things to stop doing (§8).

> **This section is a proposal, not a settled design.** It was written from the structural analogy between the rank-one determinant update in active view selection and the GNSS information matrix. Nobody has yet checked it against how `HᵀH` actually behaves when rows are dropped under realistic satellite exclusion. The specific question: **does the determinant ratio stay meaningful as the trusted subset shrinks, or does it degenerate toward zero and stop discriminating?** Twenty minutes on paper answers it. Do that before Saturday, and if the answer is bad, say so early — the whole geometry track rests on it and there is a version of this project that runs fine without it.

**Dependency, and it is a real one.** The geometry score needs satellite positions, which means the nav file. So does the cross-constellation feature. **One file failure now costs two components.** Nav verification on the travel laptop moves from important to blocking — see §16.

*Degraded fallback if the nav file dies:* compute the information ratio from tracked-SV count and observed C/N₀-vs-elevation structure alone. Weaker, no displacement bound, and say so. Do not present the fallback as the real thing.

---

# 7. ATTACK MODEL

From Rothmaier et al., ION GNSS+ 2021.

**Power advantage:** sophisticated 1–3 dB (deliberately low, to capture without triggering AGC jumps); crude 10–20 dB.

**Temporal signature the injector must reproduce:**
1. Onset — power and distortion metrics spike
2. ~10 s later — elevated power becomes the new baseline; power metric drops back as tracking loops lock to the spoofer
3. Lift-off begins — distortion spikes briefly again
4. Steady state — receiver tracks spoofer cleanly; distortion and pseudorange residuals remain slightly elevated

**Three scenarios:**

| Scenario | Power | Behaviour | Purpose |
|---|---|---|---|
| Simplistic | 10–20 dB | Abrupt offset, all SVs at once | Pipeline validation |
| Intermediate carry-off (position) | 1–3 dB | Capture, then walk the believed **position** along a swept horizontal bearing; per-SV offsets are `−e_sv · dp` | **Primary demo** |
| Intermediate carry-off (clock) | 1–3 dB | Capture, then uniform range drift across the captured set — absorbed by the constellation clock, position unmoved | Separates the clock domain from the position domain; exercises feature 4's clock channels |
| Meaconing | rebroadcast | Common bias across one constellation | Exercises cross-constellation |

**Walk-off rate:** ~1 m/s. *(Revised 5 Sep — the original wording, "~1 m/s equivalent range drift", was written for a range-domain model and is wrong for the primary demo.)* The rate is now read in whichever domain the scenario attacks:

- **Position domain** (`carry_off`, the primary demo): **1 m/s is the rate of the commanded horizontal position displacement** along a fixed bearing. Per-satellite offsets are the projections `−e_sv · dp`, so every satellite's range rate is `e_sv · v̂ × 1 m/s` and is *at most* 1 m/s — satellites near the horizon perpendicular to the walk barely move. Horizontal only, by ruling: no vertical component, because VDOP is the weak axis and an unconstrained sweep would find "up" and inflate the headline number with a direction no road-bound vehicle can be walked along. Bearing is a swept parameter (8 bearings, 45° apart from local ENU north); the demo pin is cross-corridor east. **The bearing is never derived from detector response or from H** — the injector must not be a function of the thing it attacks.
- **Clock domain** (`clock_carry_off`, `meaconing`, `simplistic`): 1 m/s of *uniform* range drift across the captured set. Kept as its own scenario because it is a different attack, not a worse version of the same one — see the table note below.

Slow enough to stay inside tracking loop bandwidth, fast enough to displace within the demo window. Tune at the venue and be ready to justify.

> **Measured, and it is why the two domains are separate scenarios:** a range offset applied *uniformly* to every satellite of one constellation is indistinguishable from that constellation's clock. The least-squares absorbs all of it and **the believed position does not move at all** (0.0 m under a 300 m meaconing bias). A clock-domain attack corrupts *time*; only a position-domain walk moves the vehicle's believed position. "The position looks fine" is not the same as "nothing is wrong."

---

# 8. TRUST STATE MACHINE

| State | Confidence | Vehicle behaviour |
|---|---|---|
| `NOMINAL` | ≥ 0.75 | Full autonomy. Navigates on GNSS, accepts new waypoints. |
| `DEGRADED` | 0.50–0.75 | Continues mission, coasts position on inertial. Flags discrepancy. **Actively seeks information** — see below. |
| `RESTRICTED` | 0.25–0.50 | Completes current leg only. Refuses new waypoints. |
| `SURRENDERED` | < 0.25 | Holds position / safe stop. Control returned to operator. |

**These thresholds are placeholders. Replace with measured separation points Saturday evening.** Round numbers without a reason are the most attackable thing in the project.

**Four invariants:**

1. **Authority is monotone non-increasing** on any single epoch's evidence. Downgrades fire immediately on threshold crossing; degradation may skip states (hard failure → straight to `SURRENDERED`).
2. **Recovery walks up one state at a time**, gated on confidence sustained above the higher threshold for 10 consecutive epochs, with a 5-epoch minimum dwell to prevent flapping. No single epoch can restore authority.
3. **Credential state dominates** — so the safety property does not depend on the detector being correct.
4. **Clock coupling.** Below `NOMINAL` the vehicle stops disciplining its clock to GNSS and stops accepting new credentials. It rides out the authorisation it holds, then expires.
5. **`DEGRADED` is an active state.** Losing authority is not the same as doing nothing. On entry the vehicle begins acquiring the observations that would most restore its position information.

Rationale for the asymmetry: an unnecessary downgrade costs convenience; a premature upgrade means acting on a spoofed position.

## Why `DEGRADED` acts

Every state below `NOMINAL` used to be a subtraction — coast, refuse, halt. That makes staged degradation a slower shutdown and invites the obvious operator objection: *why not just hand off immediately?*

With the geometry score there is an answer. `next_best_observation` names the observation that recovers the most information, and in `DEGRADED` the vehicle pursues it:

- **Reweight** toward the constellation with the most independent remaining geometry. Cheap, always available, no vehicle motion.
- **Advise a lateral offset.** A spoofer transmitting from a fixed position has a geometry that a moving receiver breaks. The console emits this as an advisory to the autonomy stack; **we do not command motion.** A vehicle that half-trusts its position should not be steering itself on that basis.

This is the difference between a circuit breaker and a circuit breaker with a way back, and it is the honest answer to mentor question 1 in §16.

**Credential override:**

| Status | Effect |
|---|---|
| `REVOKED` | force `SURRENDERED` regardless of confidence |
| `EXPIRED` | force `SURRENDERED` |
| `UNVERIFIED` | cap at `DEGRADED` |
| `PENDING` | no override — the previously verified credential is still inside its window |
| `VALID` | no override |

---

# 9. CREDENTIAL LAYER — TESLA

A short-lived mission authorisation broadcast to the vehicle and authenticated with TESLA. A mission ticket, renewed continuously, that lapses on its own if renewal stops.

## Why TESLA

The deployment shape is authenticated broadcast over a one-way, low-bandwidth, degraded link to many receivers with no per-receiver handshake. That is the SBAS problem, and TESLA is the scheme aviation chose for it. Asymmetric signatures per message are bandwidth-expensive; TESLA gets asymmetry from delayed disclosure of symmetric keys.

Same protocol class implemented for aviation under ICAO Doc 10169 over the summer — rebuilt in Python, for a different link, this weekend.

## Protocol

**Setup, at mission issuance:**
- Ground generates a hash chain: `K_n` random, `K_i = H(K_{i+1})`, anchor `K_0 = H^n(K_n)`
- Anchor is Ed25519-signed and loaded on the vehicle at mission start. One asymmetric operation per mission; everything after is symmetric.
- Time divided into intervals of duration `T_int`, with disclosure lag `d` intervals

**Per interval `i`:**
- Ground broadcasts `{vehicle_id, mission_id, corridor_id, interval_index, validity}` with a MAC keyed by `K_i`
- At interval `i + d`, ground discloses `K_i`
- Vehicle buffers the MAC on arrival, marks `PENDING`. On receiving `K_i`, verifies `H^i(K_i)` chains to the anchor, verifies the buffered MAC, marks `VALID`.

**Security condition:** the vehicle must confirm `K_i` had *not yet been disclosed* when the MAC arrived. That requires loose time synchronisation.

**Parameters (tune at venue):** `T_int` = 10 epochs of file time, `d` = 2 intervals, SHA-256 chain, HMAC-SHA256 tag. *README note:* Doc 10169 specifies KMAC; HMAC substitutes here because the hard rule is library crypto only.

## What TESLA buys

**Revocation stops being a distributed-systems problem.** No CRL, no revocation message. Ground stops MACing assertions for that vehicle and the credential lapses within one interval. **Fail-closed on silence is a protocol property, not a policy bolt-on.** *We don't propagate revocation — absence of renewal is revocation.*

**Bounded, principled latency.** A credential is verifiable exactly `d` intervals after its assertion arrives.

**It makes the clock dependency visible instead of hidden.**

## The clock coupling

**TESLA's security depends on loose time sync. Time comes from GNSS. GNSS is what is being spoofed.**

A spoofer who drags the receiver's clock forward makes the vehicle believe a key has already been legitimately disclosed; the attacker replays a captured key and forges an assertion. The credential layer — the supposed independent override — has a dependency on the thing we don't trust.

**How we bound it:**
- Vehicle disciplines its clock to GNSS **only while `NOMINAL`**. Below that it free-runs on the local oscillator, as a real timing receiver does in holdover. (USN8 is a PolaRx5TR *timing* variant — the analogy is not decorative.)
- Below `NOMINAL` the vehicle **stops accepting new credentials entirely**.
- A key arriving too early by the vehicle's own clock → `UNVERIFIED`, capping authority at `DEGRADED`.

Net effect: degraded position confidence tightens the credential rules, and a failed credential collapses positional authority.

## The same pattern, twice

The geometry score has the identical structure. Line-of-sight vectors are computed from broadcast ephemeris *and the receiver's own position estimate* — which under attack is the spoofed one. A spoofer that displaces the vehicle also slowly corrupts the geometry the vehicle would use to notice. The information ratio can look healthy the whole time, because it is computed in the attacker's frame.

Two independent instances stop being a quirk and become a stated principle:

> **Any trust layer that consumes state from the system it audits inherits that system's compromise.** The credential layer needs time. The geometry layer needs position. Both come from the receiver under attack.

The response is the same in both cases and it is the reason the architecture holds together: **freeze the borrowed state at the last trusted epoch.** Below `NOMINAL`, the clock free-runs and the geometry is evaluated against the line-of-sight set held at the last `NOMINAL` epoch, not the live one. Divergence between frozen and live geometry is itself evidence.

This is the strongest technical finding in the project. It belongs in the README and in the answer to any judge who asks what was actually learned here.

## In `SURRENDERED`

Holds position, stops accepting waypoints, hands control back, keeps reporting. Does not return to base autonomously and does not self-destruct — both are *actions*, and a vehicle that cannot trust its position should not take actions.

## Known weaknesses

- **DoS on the credential channel** forces expiry and stops the vehicle without touching GPS. Mitigation is honest rather than complete: it converts a covert attack into an overt, attributable one, and interval length and disclosure lag become operational parameters tuned against comms reliability — where continuity risk is the number that says whether you tuned it right.
- **The clock dependency.** Bounded, not eliminated.
- **Anchor distribution** out of scope. We assume the signed anchor reaches the vehicle at mission start over a trusted channel — the same assumption SBAS makes about its root certificate.

## Build tiers

| Tier | Time | What |
|---|---|---|
| **T1** | ~90 min | Hash chain, signed anchor, interval scheduler, MAC buffer-and-verify, `PENDING` state |
| **T2** | ~45 min | Clock-coupling rule: clock discipline gated on `NOMINAL`, refuse new credentials below it, too-early disclosure → `UNVERIFIED` |
| **T3** | ~1h 45m | Red-team agent attacking both surfaces (position *and* clock), fixed budget of 20–30 attempts, maximizing displacement before authority is revoked. Result to hope for: it discovers the clock route to forging a credential on its own. Yields demo beat 4b. |

**T1 + T2 are protected hours. T3 is expendable.**

---

# 10. MEASUREMENT

**These are aviation's metrics, ported — not invented.**

| Aviation term | Our instrument |
|---|---|
| **Integrity risk** — probability of an undetected hazardous error | Maximum adversarial displacement |
| **Continuity risk** — probability of an unnecessary loss of function | False surrender rate |
| **Time-to-alert** — latency from hazard onset to declaration | Epochs from injection start to first transition out of `NOMINAL` |
| **Alert limit** — error beyond which the situation is hazardous | Pick one for the corridor (road width or standoff distance), justify it, report whether displacement stays under it |

## Definitions — fixed, do not vary

**False surrender rate (continuity risk):**

> FSR = (epochs in a clean replay on which the arbitrated state is below `NOMINAL`) ÷ (total epochs in the clean replay), over the full 2,880-epoch day, reported as a **distribution over a Dirichlet sample of detector weight vectors**, with a breakdown by state.

Embedded decisions, each defensible: **epoch-weighted** (what an operator cares about is time under unnecessary restriction, not count of annoyances — but be ready to report event count too); **any downgrade counts**, not only `SURRENDERED`; **measured on real clean data**, which is why we went to CDDIS for USN8; **always reported with the weights and thresholds that produced it**.

**Maximum adversarial displacement (integrity risk):**

> Greatest position error, in metres, between believed and true position at the epoch immediately preceding the first transition out of `NOMINAL`, swept over injector parameters (walk-off rate, power advantage, spoofed SV subset size).

**Reported alongside the analytic bound** from §6b, computed from the trusted-subset information matrix at the same epoch. Plot both on one axis. The empirical number says *what our injector achieved*; the analytic bound says *what any attacker could achieve against this geometry*. The second is the stronger claim and does not depend on how many attack variants there was time to run. If the empirical number ever exceeds the bound, the bound is wrong — that check is worth running explicitly and mentioning that it was run.

## Why the Dirichlet sweep is not optional

arXiv 2607.05415 showed re-weighting a composite PNT score flips the winner in up to 22% of draws under nominal conditions. A point estimate invites exactly that objection. Sampling weight vectors from a Dirichlet and reporting distributions is ~40 lines and converts the most attackable design decision into a headline result.

**The sweep now covers the tuned half only.** The geometry score (§6b) has no weights, so the Dirichlet sample varies the four feature weights and the feature/geometry blend, and the geometry term itself stays fixed. Worth reporting explicitly: *how much of the confidence score is weight-sensitive* is a better answer to arXiv 2607.05415 than a wide distribution alone, because it shows the sensitivity was reduced rather than just characterised.

## Threshold procedure — by hand, ~90 minutes

Claude will fabricate a confident number if asked. Do not ask.

1. Run the clean day through the detector; get the empirical distribution of composite confidence over 2,880 epochs.
2. Run the injected day; get the distribution during the attack window.
3. Plot both. Look at the overlap.
4. Pick thresholds at separation points; report the separation (d-prime or overlap fraction).
5. Never a round number without a reason. If the data says 0.61, use 0.61.

---

# 11. DEMO AND SUBMISSION VIDEO

## 11a. The four beats — never varied

The order is the argument. Do not reorder.

1. **Clean run**, briefly. "Real observables, US Naval Observatory, 20 August, 30-second epochs. NOMINAL."
2. **Spoofed, layer OFF.** Start it, then say nothing for four seconds. Let them watch the true track and the believed track separate. Then: intermediate carry-off, 1–3 dB, N metres off route, nothing has alarmed.
3. **Same attack, layer ON.** Confidence falls, the state machine steps down, the system surrenders authority and explains in plain language which signal diverged. **Show the geometry panel alongside** — satellites dropping out of the trusted set, the information ratio falling, the displacement bound widening in metres. The bound is the number a judge can hold onto; put it on screen in text.
4. **Credential close.** Ground stops renewing. No revocation message is sent — none exists. Within one TESLA interval the credential lapses and the vehicle stands down with a clean sky and a perfect fix.
   - **4b, if T3 got built:** the spoofer drags the clock to forge a credential. Confidence is already degraded, so the vehicle stopped disciplining its clock to GNSS and stopped accepting new authorisations. The forgery is rejected. *Best moment available — the two halves defending each other.*

Requires a **hard reset under five seconds.** This runs three times.

---

## 11b. The submission video

**The stage demo may use only submitted materials.** We do not get to run the system live in front of judges. Whatever is in the video *is* the product as far as judging is concerned. It gets the protected 08:00–09:30 Sunday block in §14 and that block does not compress no matter what is unfinished.

| | |
|---|---|
| Absolute cap | 3:00 |
| Target | 2:00–2:30 |
| Music | None |
| Captions | Burned in, always |
| Content | Only what actually ran. No mockups, no fake data, no re-enactment. |

That last row is not a stylistic preference. Several judges build this class of system for a living and will spot a staged capture. One fabricated frame costs more than every real one earns.

### The reference

We are aiming at the register of Palantir's product videos. **Before writing or recording anything, go watch two or three of their public demo videos and write down five rules you observe yourself.** The description below is a characterisation of a genre from memory; your own rules from watching will be better.

What matters, and is transferable:

- Real time spent on the problem before any product appears.
- Narration in the operator's language, not the builder's. "The vehicle stops accepting waypoints," not "the state machine transitions to RESTRICTED."
- The system doing work in one continuous take, at the speed it actually runs. Not a montage of features.
- Willingness to be quiet. Silence over a screen where something is going wrong is the most persuasive thing in the format.
- Flat, declarative tone. No adjective doing work the demonstration should do.

What is downstream — dark low-chrome interface, deliberate cursor movement, sparse sentence-case overlays, no music.

**The honest risk.** Palantir people are judging. Copying the surface without the substance makes the gap more visible to them, not less. If we have to choose — and at 08:00 Sunday we will — spend the time on the operational narration and the continuous take, and let the styling be plain. **Do not** reproduce their branding, colour signature, logo, typeface or assets, and do not phrase anything that implies affiliation. Take the register, not the trade dress.

### Timing

| # | Beat | Time | On screen |
|---|---|---|---|
| 0 | Setup | 0:00–0:20 | Static frame or slow pan over the corridor map. Nothing running. |
| 1 | Clean run | 0:20–0:35 | Console, NOMINAL, real observables replaying |
| 2 | Spoofed, layer OFF | 0:35–1:05 | Believed and true track separating. **Silence for four seconds.** |
| 3 | Same attack, layer ON | 1:05–1:50 | Confidence falling, geometry panel, staged step-down, explanation |
| 4 | Credential close | 1:50–2:15 | Clean sky, perfect fix, vehicle stands down anyway |
| 5 | Numbers | 2:15–2:30 | Both numbers as static text. Hold. |

**Beat 2 is the whole video.** Everything before is setup, everything after is resolution. If the edit runs long, take time out of beats 1, 4 and 5 before touching beat 2. The four seconds of silence will feel unbearable in the edit and correct to a viewer — time it with a stopwatch, not by feel.

### Narration — draft to react to

Rewrite in the narrator's own voice. Read aloud before recording; anything that snags in the mouth gets cut. Roughly 300 words, unhurried. Shorter beats faster.

> **[0:00 — corridor map, static]**
>
> A resupply ground vehicle runs the last tactical mile. Forward with supplies, back with casualties, across the segment most exposed to enemy observation.
>
> It navigates on GPS. GPS is unauthenticated. Anyone with a few hundred dollars of hardware can tell that vehicle it is somewhere it is not.
>
> **[0:20 — console, clean run]**
>
> This is real data. Observations from the US Naval Observatory receiver in Washington, the twentieth of August, thirty-second epochs. Nothing is wrong. The vehicle has full authority.
>
> **[0:35 — spoofing begins, trust layer off]**
>
> A spoofer is now transmitting. One to three decibels above the real signal — low enough that the receiver never notices the handover.
>
> *[SILENCE — four seconds. Let the two tracks separate.]*
>
> The vehicle is off route. It is reporting a confident, plausible position and nothing has alarmed. This is what a spoofed autonomous system looks like from the operator's side. It looks like nothing.
>
> **[1:05 — same attack, trust layer on]**
>
> Same data. Same attack. Trust layer running.
>
> Confidence falls. Satellites drop out of the trusted set and the position solution loses information — the bound on how far an attacker could move us widens, in metres, on screen.
>
> The vehicle does not stop. It steps down. It coasts on inertial, refuses new waypoints, and tells the operator in plain language which signal diverged.
>
> **[1:50 — credential close]**
>
> Now the ground station stops renewing the vehicle's mission authorisation. No revocation message is sent. None exists.
>
> Clean sky. Perfect fix. Every signal nominal.
>
> The vehicle stands down anyway, because its authorisation lapsed.
>
> Signal quality is not provenance.
>
> **[2:15 — numbers]**
>
> Maximum adversarial displacement before detection: *[N]* metres. False surrender rate on a clean day: *[M]* percent.

**Delivery:** flat, unhurried, not performed. If it sounds like a trailer, start over. The motto lands once and never repeats. Say what the vehicle does, not "our system" or "we built." Record audio separately from screen capture, never live. Record the whole script three or four times end to end and pick the best take rather than punching in on lines — consistency of tone beats any single sentence.

### On-screen text

- Burned-in captions throughout, full transcript, sentence case, bottom third.
- `Trust layer: OFF` / `Trust layer: ON` in a fixed corner, same position both times so the viewer can compare.
- **Both numbers as static text at the end.** A judge watching with sound off must still leave with them.
- Hard cuts between beats. No animated transitions, no logo, no title card over two seconds.

### Capture

- 1920×1080, 30fps, screen capture only
- Console in dark mode, browser chrome hidden, no notifications, no dock, clean desktop
- **Confirm the replay rate reads as intelligible on video.** 10–20 epochs/sec is a development setting; beats 2 and 3 may need it slower. Test this Saturday evening, not Sunday morning.
- One continuous take per beat. Cuts between beats, never within.
- At least three clean takes of beat 2 before moving on.

The five-second hard reset in §11a matters here too — each beat gets run several times, and slow reset turns a two-hour block into three.

### Roles — assign Saturday night, not Sunday morning

- **Operator** drives the screen. Rehearses the cursor path per beat until it is muscle memory. Slow and deliberate beats accurate and fast.
- **Narrator** records audio separately in the quietest space available. A phone voice memo in a stairwell beats a laptop mic in a hackathon hall.
- **Editor** assembles, burns captions, checks on-screen numbers against the numbers on paper, exports, verifies playback.

One person can hold two. Nobody holds all three.

### Video cut order, if behind

1. Corridor map opening — start cold on the console
2. Beat 5 as a separate hold — overlay the numbers on beat 4 instead
3. Beat 1 down to five seconds
4. Beat 4 down to the stand-down moment alone
5. Captions to key lines only rather than full transcript

**Do not cut beat 2, and do not cut the silence inside it.** If the geometry panel is not ready, beat 3 still works on confidence and the state machine alone — narrate what is shown, never a panel that is not on screen.

### Before calling it done

- [ ] Under 3:00, timed not estimated
- [ ] Every number on screen matches the number on paper
- [ ] Nothing mocked, staged or re-enacted
- [ ] Makes the argument with sound off
- [ ] Plays on a machine that is not the one it was edited on
- [ ] No file names, notifications or personal information in any frame
- [ ] No partner branding, assets or implied affiliation
- [ ] Uploaded and confirmed playing from a logged-out browser

### Open — decide together, do not default

1. **Cold open on the console, or corridor map first?** The map costs fifteen seconds and buys operational framing. Watch it both ways once there is footage.
2. **Does the geometry panel share the frame in beat 3, or cut between?** Split screen is more information and less legible. Test it.
3. **Name the Army last-tactical-mile CSO explicitly, or leave it implied?** Naming it is more credible to a defence judge and less legible to an investor judge. Depends who is in the room.


---

# 12. OUT OF SCOPE

- No SDR, no raw IQ, no signal-domain processing. Measurement domain only.
- **No GNSS transmission of any kind.** Federal offense; the design never needs it. No transmit-capable hardware in the bag.
- No live OSNMA verification — needs sky view, incompatible with injecting attacks on archived observables.
- AGC-based detection — not in RINEX.
- C509/CBOR certificate compression — solved a bandwidth constraint this system doesn't have. Prior work, not rebuilt.
- **Cameras, imagery, visual odometry, SLAM, any sensor fusion involving vision.** No capture hardware and no image data tied to these observations. The Fisher-information formulation transfers; the sensor does not. Do not let the pitch imply otherwise.
- Physical rover — **cut.**

---

# 13. ENVIRONMENT

**Conda env `dnhacks`, Python 3.12.13.** Run `conda activate dnhacks` before anything, including in any terminal Claude Code spawns. (Base conda is 3.13; `gnss-lib-py` caps at <3.13.)

- `georinex` 1.16.1 — built from source due to a dependency pin; confirmed working on the Hatanaka files
- `gnss-lib-py` 1.0.4 — Stanford NavLab. Makes the cross-constellation feature tractable.

**Claude Code** 2.1.247, Claude Pro. Watch usage limits. Drop to Sonnet with `/model` for plumbing *early, not late*; save Opus for injector physics and threshold reasoning. Codex credits (09:00 Saturday) are a second agent on a separate worktree — good for the sweep harness and T3.

## Verified — observations

```python
import georinex as gr
from datetime import datetime
obs = gr.load('USN800USA_R_20262320000_01D_30S_MO.crx.gz',
              use={'G'},
              tlim=(datetime(2026,8,20,0,0), datetime(2026,8,20,0,10)))
print(obs['S1C'].to_pandas().iloc[:3, :4])
```

```
sv                     G03    G04    G07    G08
time
2026-08-20 00:00:00  42.25  49.50  35.75  38.75
2026-08-20 00:00:30  41.75  49.25  38.25  39.25
2026-08-20 00:01:00  42.50  49.00  36.25  39.25
```

georinex emits a FutureWarning per epoch from xarray. Harmless. Suppress with `2>/dev/null` for clean output — but not when debugging, since it also swallows tracebacks.

## Nav file — resolved, unverified on travel machine

`BRDC00IGS_R_20262320000_01D_MN.rnx.gz` failed in georinex due to a **field-count mismatch in IRNSS (System I) records**. Fix: pre-filter stripping IRNSS records before loading, preserving G/E/R/C.

**Verify end-to-end on the travel laptop before flying.** Load, print the satellite list, confirm G/E/R/C present with Keplerian parameters (`M0`, `Eccentricity`, `sqrtA`, `Omega0`, `Io`). If it fails there, cross-constellation is cut and the project runs on three features — a fine answer stated out loud, a bad surprise at 23:00 Saturday.

## What Claude can and cannot do

**Can:** RINEX parsing, the injector (once physics are specified), feature extraction, state machine, TESLA implementation with standard libraries, console, explanation layer, sweep harness. ~90% of the code.

**Cannot:** set the detection thresholds, or specify the attack physics. Ask for a C/N₀ deviation that indicates spoofing and you get a confident fabricated number.

## Repo layout — create at the venue, not before

```
ARBITER/
├── CLAUDE.md
├── docs/design.md
├── data/                   ← gitignored
├── backend/                ← detection side
│   ├── rinex/
│   ├── injector/
│   ├── detection/
│   ├── geometry/           ← information matrix, ratio, bound, next-best
│   ├── credentials/        ← TESLA: chain, anchor, scheduler, verifier
│   └── measurement/        ← FSR + displacement sweep
└── console/                ← arbitration + display side
```

## CLAUDE.md — first commit Saturday

```markdown
# Verifiable Position for Autonomous Systems

Trust layer between a GNSS receiver and an autonomy stack. Detects spoofed
positioning, scores confidence continuously, and degrades the vehicle's
authority in stages as confidence falls. Credential state can override
signal quality in both directions.

Built at DNHacks, 5-6 Sept 2026. 26-hour build.

## Environment
Run `conda activate dnhacks` first. Python 3.12. georinex 1.16.1,
gnss-lib-py 1.0.4.

## Read first
`docs/design.md` — authoritative. When this file and design.md disagree,
design.md wins.

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

## Layout
- `backend/` — RINEX parsing, spoofing injector, detection features,
  confidence scoring, credential layer (TESLA), measurement.
- `backend/geometry/` — information matrix, information ratio, analytic
  displacement bound, next-best-observation ranking.
- `console/` — trust state machine, operator console, natural-language
  explanation layer, explanation verifier.
- Boundary is the interface contract in design.md. Do not cross it.

## State machine note
DEGRADED is an active state, not a slower shutdown. On entry the console
consumes `geometry.next_best_observation` and pursues it — reweighting toward
the constellation with the most independent geometry, or emitting a lateral
offset advisory. Advisory only. Never command vehicle motion.

Below NOMINAL, both borrowed inputs freeze: the clock free-runs, and geometry
is evaluated against the line-of-sight set held at the last NOMINAL epoch.

## Data
`data/` holds four IGS daily observation files plus merged broadcast
ephemeris, 2026-08-20, 30s interval, 2880 epochs. Primary station USN8
(US Naval Observatory). Five constellations. Measured C/N0 noise floor:
±0.5 dB. Nav file requires the IRNSS pre-filter before georinex. Gitignored.

## Conventions
- Commit frequently. Git history is the record of when work happened.
- Thresholds derive from observed data. Never hardcode a guessed number.
- Never report the composite confidence without the four sub-scores and the
  geometry block.
- Report FSR as a distribution over sampled weight vectors, not a point.
- The geometry score must be derivable on a whiteboard in two minutes. If an
  implementation makes it un-explainable, the implementation is wrong.
- Keep functions testable against both clean and injected datasets.
```

---

# 14. BUILD PLAN

## Saturday

Three people, three tracks. **A** = detection and injector. **B** = arbitration, console, explanation. **C** = geometry and measurement. Tracks share only the interface contract.

| Hours | A — detection | B — arbitration | C — geometry |
|---|---|---|---|
| 10:00–10:30 | Repo, CLAUDE.md, design.md, first commit. Timestamp everything. All three present. | | |
| 10:30–12:00 | RINEX loader → per-epoch dataframe. Sonnet. | Console skeleton against a hand-written fixture epoch | Nav file → satellite positions → line-of-sight matrix H |
| 13:30–15:30 | Injector. **Physics specified by hand; Claude writes the code.** | State machine + hysteresis + credential override | Information ratio, normalised, plotted over the clean day |
| 15:30–18:00 | Three features (C/N₀, residual, code-minus-carrier) | Explanation layer, templated first | Analytic displacement bound + next-best-observation ranking |
| 18:00–18:30 | **Integration checkpoint. All three.** Full contract flowing end to end including the `geometry` block. Non-negotiable — if it isn't flowing by 18:30 that's a problem, not a task. | | |
| 18:30–21:00 | Cross-constellation feature | `DEGRADED` active behaviour; consume `next_best_observation` | Freeze-at-last-NOMINAL geometry; live-vs-frozen divergence |
| 21:00–22:30 | **THRESHOLDS. A and C together, by hand, not delegated.** Both distributions, plot, separation points. The most important 90 minutes of the weekend. | Console hardening, hard-reset path under 5 s | (with A) |
| 22:30–00:45 | TESLA T1 + T2 | Explanation verifier — every quantitative claim checked against the epoch record before display | Sweep harness: Dirichlet weights → FSR + displacement distributions |
| 00:45–01:30 | Time-to-alert. **Empirical-vs-analytic displacement plot** — the §10 consistency check. | | |
| 01:30–03:30 | Sleep, first shift (two down, one on) | | |
| 03:30–06:00 | Second shift. **T3 only if the person on shift is functional.** Run once, trace saved to disk, on Codex credits, never live during judging. If fried, skip it and sleep. | | |

**Three people means three sleep shifts, not less sleep.** The failure mode with a bigger team is everyone staying up because someone else is.

## Sunday

| Time | Target |
|---|---|
| 06:00 | **FEATURE FREEZE** |
| 06:00–08:00 | Sweeps finish, plots exported, numbers written on paper |
| 08:00–09:30 | **Record the submission video.** Full spec in §11b — read it Saturday, not Sunday. Roles assigned the night before. |
| 09:30–10:15 | Screenshots, slideshow (6–7 slides), title, description |
| 10:15–10:45 | README with limitations section. Push. Verify repo link from a logged-out browser. |
| 10:45–11:15 | **Submit.** Verify the video plays. |

## Cut order

1. Rover — already cut
2. **T3** — red-team agent, time-shift attack, beat 4b
3. `DEGRADED` lateral-offset advisory → keep the reweighting, cut the motion advisory
4. Cross-constellation feature → run three, say so out loud
5. Analytic displacement bound → report the empirical sweep alone, say the bound was scoped out
6. Live sweep → precompute Saturday night, show the plot
7. Explanation-layer polish → templated strings still read fine

**The information ratio is not on the cut list.** It is the weight-independent half of the confidence score and the answer to the strongest objection in §10. If it goes, the project is back to a tuned composite and Claim 2a evaporates.

**TESLA T1 + T2 are not on the cut list.** If they don't get built, the credential layer reverts to a static signed assertion with an expiry check — works, but throws away the best answer in the project.

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Claude Code hits Pro limits mid-build | **High** | Sonnet early. Codex as a second agent on a separate worktree. |
| Submission rushed, video weak | **High** | Video owns 08:00–09:30 Sunday. |
| Overtiredness → bad Sunday | **High** | Sleep shifts are not optional. |
| Console not ready Saturday night | Medium | Backend emits JSON; minimal matplotlib visualisation carries the demo |
| Demo crashes during judging | Medium | Pre-recorded 60 s clean run as backup. Never lead with it. |
| Venue wifi dies | Medium | Everything runs offline. Explanation layer falls back to templated strings. |
| **Nav file fails on travel laptop** | Low (fixed, unverified) — **but now costs two components** | Verify before flying, on two machines. Fallback: degraded information ratio from SV count and C/N₀-vs-elevation, no displacement bound, stated plainly. |
| Three tracks drift; contract breaks silently | Medium | 18:30 integration checkpoint is all-hands. Fixture epoch committed hour one so B and C never block on A. |
| Organisers judge a three-person team more rigorously than a two | Medium | Stated policy, unavoidable. Handled by all three tracks producing something a judge can point at — §6b, §9 and §11b each stand alone. |

---

# 15. README STRUCTURE

1. One paragraph — what it is, plain English
2. Motto: *Signal quality is not provenance.*
3. Run it — three commands that work from a clean checkout
4. Results — the two numbers, with plots, with the weights and thresholds that produced them
5. **How the thresholds were set** — distributions, separation, reasoning. This section is what makes a technical reader believe you.
6. Prior art, named honestly — the table from §3
7. **Limitations**, written properly and not softened:
   - Attack is synthetic; absolute detection numbers not comparable to signal-domain results on TEXBAT
   - Credential channel is a DoS surface; interval length and disclosure lag are untuned operational parameters
   - TESLA depends on loose time sync, and time comes from the receiver under attack — bounded, not eliminated
   - **The geometry score has the same structure of dependency:** line-of-sight vectors derive from the receiver's own position estimate, which under attack is the spoofed one. Frozen at the last `NOMINAL` epoch, not eliminated. State the principle (§9) rather than hiding the instance.
   - The analytic displacement bound assumes the trusted-subset residuals are the only constraint on the attacker; a spoofer with a better propagation model may do better
   - Thresholds fit on one station, one day, static, open sky; generalisation untested
   - The feature half of confidence is a weighted sum and single-number scores are known to be weighting-sensitive; hence the distribution. The geometry half is weight-free — report what fraction of the score that covers.
   - No live OSNMA verification
   - Anchor distribution assumed
   - 26 hours
8. Philosophy — Stoic *phantasia* (the impression) and *sunkatathesis* (the assent): a spoofed vehicle's error is not in perceiving but in assenting, and the trust layer is a discipline of assent. Long (1877) and Carter (1758), both public domain. Keep attributions clean.

---

# 16. STATUS

## Done

- Four observation files downloaded and MD5-verified
- Nav file IRNSS parse failure diagnosed; pre-filter written
- `dnhacks` env created, both libraries verified
- RINEX observation loading verified end to end
- Claude Code installed and authenticated
- Design locked: interface contract, four features, attack model, state machine, TESLA protocol, measurement definitions
- Novelty claim scoped against PNTTING, RPCF, arXiv 2607.05415, OSNMA, RAIM

## Open — before Friday

- [ ] **Verify the nav file parses on the travel laptop.** Now blocking two components, not one. Highest priority by a clear margin.
- [ ] Re-verify all five data files present on the travel machine; confirm a second machine can also load them
- [ ] **Propagate the contract change** — new `PENDING` status, the new `geometry` block, and the clock-coupling rule. All three people build against this; it has to be settled before Saturday, not at 18:30.
- [ ] Commit a fixture epoch (one hand-written contract object) so tracks B and C are never blocked on A
- [ ] **All three environments set up and both libraries importing before travel.** Everyone reads §5, §6b, §8, §9, §11.
- [ ] **Answer the open question in §6b** — does the determinant ratio hold up under realistic SV exclusion? Twenty minutes on paper, before Saturday. This gates the geometry track.
- [ ] Decide the feature/geometry blend and be able to justify it — this is now a threshold-class decision, not a detail
- [ ] Schedule the Sunday demo video explicitly — it is a two-hour job and cannot be squeezed
- [ ] Pick the alert limit for the corridor and be able to justify it
- [ ] Have one real ICAO integrity-risk or time-to-alert figure ready, correctly quoted

## Two questions to put to mentors and judges

Neither can be answered from a lab, and both change the design:

1. **Does staged authority degradation match how operators actually want a system to behave, or is a hard handoff preferable?**
2. **What does an unnecessary halt cost on a resupply run in a contested corridor?** That number is the denominator of continuity risk. If someone answers it, quote it back in Sunday's demo narration.
3. **Would an operator accept a vehicle that manoeuvres to improve its own position confidence while it is already unsure of its position?** The lateral-offset advisory (§8) is the one place the design edges toward action under uncertainty. If the answer is no, cut it and keep the reweighting — which is why it sits at position 3 in the cut order.
