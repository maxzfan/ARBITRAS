# TRACK E -- Terrain channel (scoped 2026-09-05, NOT BUILT)

**Status: scoped, not started, not in the Saturday plan, not on the Sunday cut
list.** Nothing in this file is running. If it is picked up it sits below Track
D in priority and every number it produces is stamped `source: "simulated"`
until real sensor data exists. It does not appear in the submission video
(design.md §11b: only what actually ran) unless the caption in the fixed corner
says the sensor is simulated. Recommendation: keep it off the video, put the
plots in the README under a roadmap heading.

Origin: a conversation with someone at Mach Industries, who said this is what
they are actively working on -- a small sensor that classifies the terrain
under the platform, so that a vehicle with a pre-mapped operating area can say
whether the ground it is on agrees with the ground the map has at the position
it believes it holds.

You would own `backend/terrain/` and the `terrain` block of the §5 contract,
plus one feature name. You consume the believed position (Track B's solver),
the trusted-subset covariance (Track C's `solve_context()`), and a land-cover
raster. You produce one feature, one map-derived bound, one advisory input, and
one gate check. **You never decide state.**

Read first: design.md §5, §6, §7, §8, §9 ("The same pattern, twice"), §10;
TRACK_D.md (the contract-extension pattern and the cut rule are the template);
CLAUDE.md hard constraints, in particular the imagery rule.

## Why this track exists

Every evidence channel ARBITER has today enters through the antenna. C/N0,
residuals, code-minus-carrier, cross-constellation, the information ratio, the
protection level -- all of them are functions of the same RF input the spoofer
controls. The credential layer is independent but says nothing about
position. **The terrain channel is the first evidence about position that does
not come through the antenna.** A spoofer with perfect carrier coherence
(`carrier_rate_error = 0.0`, which the injector supports and under which
features 2 and 3 see nothing by construction) still has to move the believed
position across ground the map knows about.

It is also exactly complementary to the clock-domain blindness already
measured: simplistic, meaconing and clock carry-off displace the fix by
0.000 m, so this channel sees nothing there and says so. It is a
position-domain check, only.

## What the sensor is, and what it is not

Abstractly: at each epoch the sensor emits a posterior over K terrain classes
for the patch of ground under the platform. That is the whole interface. What
physics produces the posterior is the vendor's business; the contract carries
the posterior, not the physics.

**Not a camera.** CLAUDE.md rules out imagery, visual odometry and SLAM, and
this track does not reopen that. The sensor emits a class label with a
probability, never a frame; nothing is matched against imagery. Realisations
that satisfy the rule, for reference only:

- ground-contact vibration spectrum on a UGV (wheel-terrain interaction; the
  standard terrain-classification approach for rovers)
- single-point downward radar or lidar backscatter class
- point spectrometer or mm-wave radiometer on an aerial platform

Two sensor parameters enter the design and both come from a datasheet or a
calibration run, never from a guess: the **confusion matrix** `M[c_true,
c_read]`, and the **footprint radius** `rho_s` the sensor physically
integrates over (near zero for a wheel; altitude-dependent for a downward
radar).

## The pre-map

A raster of class labels over the operating area, cell size `delta`, K
classes, with an id, a checksum and an age. For USN8 the natural source is a
public land-cover product: ESA WorldCover (10 m, 11 classes, global, open
licence) or NLCD (30 m, 16 classes, US). The Naval Observatory grounds are
tree cover and lawn inside built-up Washington, so there are real class
boundaries within the 273 m the carry-off reached -- how far, on which
bearings, is measured from the raster in E1, not asserted here.

**The map is a credential-class input.** It is loaded at mission issuance and
signed with the same Ed25519 key that signs the TESLA anchor (library crypto
only; one more signature at mission start, no new primitive). A map the
vehicle did not receive signed is not consulted: `terrain.available: false`.
Map poisoning is otherwise the cheapest attack on this channel.

Before use the map is **collapsed to sensor-separable classes**: any two map
classes whose confusion rows are not separable at the detector's own
saturation are merged. This is a preprocessing step driven by `M` and it is
what makes the bound below honest against a mediocre sensor.

## Assumptions made in place of questions

1. **Sensor-agnostic categorical output** with a calibrated posterior. If Mach's
   sensor emits something else (a continuous roughness scalar, say), the
   posterior is the vendor's classifier over it and the contract is unchanged.
2. **The demo uses the route presentation frame** (`console/mission.py`): the
   true point moves along the scripted route, the sensor is simulated at the
   true point, the map is looked up at the believed point. Everything on
   screen then derives from the measured displacement `D`, the raster and the
   sensor model -- and the caption says so.
3. **This is roadmap, not the weekend.** Scope, simulator and contract are
   worth having in the repo; the sensor does not exist.
4. **Map source: a public raster.** No survey, no imagery. Fetched by
   `bootstrap.sh` like the RINEX files, gitignored.

## The math -- derivable on a whiteboard in two minutes

**1. What the map says is under the reported position.** The believed position
`p_hat` has horizontal covariance `Sigma = sigma_UERE^2 * inv(G)[0:2,0:2]`,
with `G` from Track C's `solve_context()` (frozen below NOMINAL, same rule as
everything else that borrows H). Dilate by the sensor footprint `rho_s`.
Weight the cells under that footprint:

    w(x)  ~ exp(-0.5 (x - p_hat)' inv(Sigma) (x - p_hat)),  |x - p_hat| <= 3 sigma + rho_s
    q(c)  = sum_x w(x) [ map(x) == c ] / sum_x w(x)          map posterior over classes

**2. What the sensor says.** `p_s(c)`, the sensor's own posterior.

**3. Match likelihood.** The overlap of the two:

    L      = sum_c q(c) p_s(c)
    L_max  = max_c p_s(c)                 the best any position could score
    mismatch_t = 1 - L / L_max            in [0, 1], higher = more anomalous

On a correct position with a decent sensor `q` is a point mass on the true
class and `L = L_max`, so mismatch is 0. Walk the believed position across a
boundary into class `c'` and `L` falls to `p_s(c')`, the sensor's confusion
between the two classes -- which is exactly the quantity that should drive it.

**4. The feature.** Causal, like the other four: a trailing-window mean of
`mismatch_t` over `W` epochs, normalised by its saturation on the clean
calibration run (p99, same rule as `cn0_anomaly`), clipped to [0, 1].
`features.terrain_mismatch`. `W` is chosen at the threshold session against
the confusion noise, by hand, from the clean-run distribution.

**5. The map-derived displacement bound.** Flood-fill the (collapsed) raster
from the cell under `p_hat`; the connected same-class region is everywhere the
attacker can put the believed position without this channel noticing:

    E(p_hat) = max { |x - p_hat| : x in the same-class connected component of p_hat }

`terrain.consistent_extent_m`. If the component touches the edge of the map
window, `E` is None -- the map makes no claim, and None is alarming, correctly,
same convention as `displacement_bound_m`.

**6. Combining the bounds.** An undetected attack must stay inside the
residual-consistent ellipse AND inside the class-consistent region. The
largest displacement in an intersection is at most the smaller of the two
maxima:

    displacement_bound_m = min(residual_bound_m, E)        geometry.bound_source names which

No weight, no free parameter. The §10 empirical-vs-bound check runs on the
combined number unchanged.

**7. The terrain next-best observation.** The nearest cell of a different
class, its distance and bearing, and what the sensor should read there:

    r_T, theta_T, c_beyond = argmin over x with map(x) != map(p_hat) of |x - p_hat|

`terrain.nearest_boundary`. This is a prediction the mission tests for free:
the planned route is a schedule of class transitions, and each one the sensor
confirms is an epoch of evidence that the reported position is right.

**8. Terrain time-to-alert by bearing, predicted before anything runs.** The
carry-off walks the believed position along bearing `theta` at `v` m/s (1 m/s,
§7). The first epoch this channel can fire is when the walk crosses a
boundary:

    TTA_terrain(theta) = r_T(theta) / v + W epochs

computed from the raster alone for all eight sweep bearings, then measured on
the injected run. Predicted vs measured on one polar plot is this channel's
version of the Claim 2a pairing.

## Three ways to plug it in

**A. Fifth feature only.** Add `terrain_mismatch` to `FEATURE_NAMES`, one
extractor, one explanation phrase. Cheapest by far. Throws away the derived
half: no bound, no advisory, no gate check, and the Dirichlet sweep becomes the
only thing that says how much the channel matters.

**B. A `terrain` block mirroring the two-halves structure -- recommended.** The
feature enters the tuned half (it IS a noisy calibrated statistic, which is
what the tuned half is for, and the sweep reports its weight-sensitivity
automatically). The extent enters the derived half through the `min` rule (no
weight). The nearest boundary gives DEGRADED a second thing to pursue. The
overlap gives the correction gate a sixth check. Each entry point is one
derivation from the block above. This is the same shape as Track D's
extension and costs about the same.

**C. Trajectory terrain-referenced navigation.** Run a particle filter over
position on the class sequence plus odometry, produce an independent position
posterior, compare it with GNSS. This is TERCOM/SITAN with classes instead of
elevation and it is where the channel is genuinely powerful -- but it needs an
odometry input ARBITER does not have, and a static receiver yields one
observation forever. Roadmap. Step 3 above is its measurement model, so B is
the first step toward C, not a detour.

## Contract extension -- settle before touching code

Additive. Illustrative values, fixture-style, like `fixtures/epoch.json`.

    "features": {
      "cn0_anomaly": 0.71, "pseudorange_residual": 0.15,
      "code_carrier_divergence": 0.08, "cross_constellation": 0.34,
      "terrain_mismatch": 0.62,
      "by_sv": { ... }
    },
    "terrain": {
      "available": true,
      "sensed":   {"class": "tree_cover", "p": {"tree_cover": 0.84, "grassland": 0.11, "built_up": 0.05}},
      "map_at_position": {"class": "built_up", "p": {"built_up": 0.93, "grassland": 0.07}},
      "match_likelihood": 0.10,
      "consistent_extent_m": 210.0,
      "nearest_boundary": {"distance_m": 38.0, "bearing_deg": 47.0, "class_beyond": "grassland"},
      "map":    {"id": "worldcover-2021-v200-<tile>", "cell_m": 10, "classes": 11,
                 "collapsed_classes": 6, "signed": true, "age_days": 41},
      "sensor": {"id": "sim-confusion-v0", "source": "simulated",
                 "footprint_m": 0.5, "window_epochs": 4}
    },
    "geometry": {
      ...,
      "displacement_bound_m": 14.0,
      "bound_source": "residual",
      "residual_bound_m": 14.0,
      "correction": { "checks": { ..., "terrain_consistent": true } }
    },
    "score_detail": { ..., "features_scored": ["cn0_anomaly", "pseudorange_residual",
                                               "code_carrier_divergence", "cross_constellation",
                                               "terrain_mismatch"] }

Rules:

- `terrain.available: false` (no sensor, no signed map) means: no
  `terrain_mismatch` key, `terrain_consistent: null`, `bound_source:
  "residual"`. **A sensorless run must score identically to today.**
- `terrain.sensor.source` is `"simulated"` or `"measured"`, never absent.
- `geometry.displacement_bound_m` keeps its name and its meaning (the tightest
  bound the backend can stand behind) so beat 3 and the §10 check are
  untouched; `residual_bound_m` preserves the pure geometry number.
- The console never computes any of it. It renders both bounds, highlights the
  binding one, and consumes `nearest_boundary` in DEGRADED only.

## Where it touches the existing code

| Component | Change | Owner today |
|---|---|---|
| `backend/detection/features.py` | `FEATURE_NAMES` gains `terrain_mismatch`; extractor is a new module, not in `FeatureExtractor` | A |
| `backend/detection/confidence.py` | `anomaly()` must renormalise weights over **scored** features. Today a missing key reads `0.0`, so a sensorless run would silently score as if the terrain agreed -- the exact failure feature 2's NaN rule exists to prevent | A |
| `backend/geometry/engine.py` or the emitter | `min` rule, `bound_source`, `residual_bound_m`. One function; settle which at integration, as Track D did for PL | C |
| `backend/correction/gate.py` | check 6 `terrain_consistent`: corrected-position overlap `L` above the floor fit on the clean run. `null` when unavailable; the AND is unchanged | D |
| `console/arbiter/machine.py` | `Decision` passes `terrain` through like `geometry`; DEGRADED advisory gains one sentence from `nearest_boundary`. Advisory only, never a motion command | B |
| `console/arbiter/explain.py` | `FEATURE_PHRASE["terrain_mismatch"] = "the ground under the vehicle does not match the map at the reported position"`; claims carry `terrain.match_likelihood` | B |
| `backend/measurement/displacement.py` | none -- reads `displacement_bound_m` | C |
| `backend/measurement/sweep.py` | Dirichlet over five weights when the feature is scored; `weight_sensitive_fraction` unchanged in meaning | C |
| `docs/stream_provenance.md` | three rows: map (measured, public raster, checksum), sensor (simulated, `M` swept), extent/boundary (derived from the map) | — |
| `fixtures/epoch.json` | the block above | — |

## Attack model -- what this channel does and does not see

| Scenario | Believed position moves? | Terrain channel |
|---|---|---|
| Simplistic (uniform offset) | 0.000 m | blind, by construction; says so |
| Clock carry-off | 0.000 m | blind |
| Meaconing | 0.000 m | blind |
| Position carry-off (primary demo) | up to 273 m on the demo | fires when the walk crosses a sensor-separable boundary; latency `TTA_terrain(theta)` from the raster |
| Carrier-coherent carry-off (`carrier_rate_error = 0`) | same | **fires when nothing in features 2 and 3 can** -- the case that justifies the channel |

**The map-aware adversary.** A spoofer who knows the map walks the believed
position inside the same-class region -- along the road, inside the wood. That
attacker is invisible to this channel *and that is precisely what `E` reports*:
the bound holds against a map-aware adversary; only the time-to-alert
prediction assumes a map-naive one. Say both. Finer sensor-separable classes
(road surface vs. verge) shrink `E`; that is the sensor vendor's lever, and the
sweep in E5 shows what it buys.

**Where the terrain is uniform** -- and Camp Grafton is prairie -- `E` is large
or None and the channel contributes little. That is the bound doing its job,
not a defect; the honest README line is that class-based terrain evidence is
worth exactly as much as the boundary density of the operating area.

## The borrowed-state principle, applied here

design.md §9: any trust layer that consumes state from the system it audits
inherits its compromise. Two of this channel's inputs are borrowed and one is
not:

- **The believed position is the hypothesis under test, not the evaluator.**
  It is NOT frozen: the whole point is to look the live claim up on the map.
  This differs from the geometry case, where position built the H that judged
  position.
- **The footprint covariance `Sigma` is borrowed** from the trusted-subset `G`.
  Below NOMINAL it is the frozen `G`, same rule as everything else.
- **The sensor posterior is independent.** It is the only input in ARBITER
  that the RF channel cannot reach. Its own attack surface is physical (change
  the ground) or supply-chain (poison the map, hence the signature).

## Measurement plan -- every number as a function of the sensor model

There is no observed data for a theoretical sensor. The observed inputs are
the raster (real) and the confusion matrix `M` (from the vendor, or swept).
Until a measured `M` exists, **nothing is reported at a single `M`**: every
figure is a curve over the diagonal of `M`, and every record says
`"simulated"`.

1. **Calibration run** (clean day, static receiver, simulated sensor at the
   antenna): distribution of `mismatch_t`, saturation at p99, `W` chosen by
   hand, FSR contribution through the arbiter. Note the believed position
   jitters 0.73 m median / 2.19 m max on the clean day (stream provenance),
   which the footprint absorbs -- if the clean-run mismatch is not pure
   confusion noise, the footprint is wrong.
2. **Predicted terrain time-to-alert** for the eight sweep bearings from the
   raster alone, before any injected run.
3. **Injected runs** (carry-off, eight bearings, `carrier_rate_error` at the
   demo value and at 0.0): measured terrain time-to-alert vs. predicted;
   empirical displacement <= `min` bound at every arbitrated-NOMINAL epoch
   (`backend.measurement.displacement`, unchanged). If ever violated, the
   bound is wrong -- run the check, say it was run.
4. **Sensor-quality sweep**: diagonal of `M` over a range, detection fraction
   and FSR per value. This is the curve a sensor vendor actually wants: how
   good does the sensor have to be for the channel to earn its weight.
5. **Map-resolution sweep**: `delta` at 10 m and 30 m, `E` and `r_T`
   histograms over the map window. Shows what the raster costs.

## Simulator -- and the honesty rule

`backend/terrain/simulate.py`: given the true point (the antenna on the real
replay; `route_point(s)` in the presentation frame), read the true class from
the raster, draw a reading from `M[c_true, :]`, emit the posterior. Seeded.
Its truth log (true class, drawn reading) goes into the `_terrain` replay
metadata beside `_truth` and `_attack`, and the detector never sees it.

design.md §11b forbids fake data on the video and this is a simulated sensor.
The plots go in the README under a roadmap heading with the sensor model
stated in the caption. If the team decides to show the panel anyway, the
fixed-corner caption reads `Terrain sensor: SIMULATED` for the whole take.

## Build decomposition -- E1 through E5

**E1 -- raster + map maths** (`backend/terrain/rastermap.py`, ~2 h). Load the
raster window around USN8, collapse classes under a supplied `M`, footprint
posterior `q`, flood-fill extent `E`, nearest boundary. Pure numpy. Acceptance:
on a hand-drawn 20x20 fixture raster, `E` and `r_T` match values computed by
hand; `E` is None when the component touches the edge; `r_T(theta)` for the
eight bearings printed for the real window.

**E2 -- simulator** (`backend/terrain/simulate.py`, ~1 h). As above.
Acceptance: with `M = I` the mismatch is exactly 0 at the true position on
every epoch; with a walked believed position it is exactly `1 - p_s(c') / max_c p_s(c)`
on the far side of a boundary (1.0 under `M = I`).

**E3 -- feature + contract** (`backend/terrain/emit.py`, `features.py`,
`confidence.py`, ~2 h). Window, saturation, block assembly, `min` rule,
`features_scored`, weight renormalisation. Acceptance: a run with
`available: false` produces byte-identical `confidence` to today's stream;
the extended fixture round-trips `console.replay` with zero verifier fires.

**E4 -- consumers** (arbiter passthrough, advisory sentence, explanation
phrase, gate check 6, ~1.5 h). Acceptance: `test_machine.py` invariants
untouched; the advisory appears in DEGRADED only; check 6 `null` leaves
`correction_ok` unchanged.

**E5 -- measurement plots** (`backend/terrain/validate.py`, ~2.5 h). The five
items above. README-bound with `"simulated"` in every caption.

About nine hours for one person, none of them this weekend unless the team
says so.

## Cut rule

Off by default. `terrain.available` is false unless a signed map and a sensor
posterior are both present, and in that state nothing anyone else built
changes -- E3's byte-identical acceptance test is the guarantee. If picked up
and E1 does not print sensible `r_T` values for the real window in two hours,
stop: the raster is the risk, not the maths.

## What goes in the README limitations, if it lands

- The sensor is simulated; every terrain figure is conditional on a confusion
  matrix nobody has measured. Reported as a curve over it, never a point.
- Class-based terrain evidence is bounded by boundary density. Prairie gives
  little; the extent says exactly how little.
- The bound holds against a map-aware spoofer; the time-to-alert does not.
- The map is a supply-chain surface. Signed at mission issuance; staleness
  (seasons, construction, flooding) is folded into `M` and is untested.
- The footprint covariance is borrowed from the trusted subset and frozen
  below NOMINAL: bounded, not eliminated, same sentence as limitation 3.
- A static receiver yields one terrain observation forever; the trajectory
  form (approach C) is untested.

## Roadmap -- where this goes after the weekend

- **Trajectory TRN** (approach C): the particle filter over the class sequence
  plus odometry, giving an independent position posterior. For an aerial
  platform moving at tens of metres per second this is where the value is; the
  per-epoch check above is its measurement model.
- **Aerial footprint**: `rho_s` from altitude, a coarser map, a larger K from
  backscatter. Contract unchanged.
- **Sequential test**: replace the windowed mean with a CUSUM on the
  log-likelihood ratio against the map's class prior. Same inputs, better
  latency, one more threshold to derive.

## Open questions -- for the team and for Mach

1. What does the sensor actually emit: a posterior over classes, a hard label,
   or a continuous descriptor? The contract assumes the first.
2. Is there any measured confusion matrix, even from a bench test? A single
   real `M` retires the sweep in E5.
3. Which platform: the sensor's footprint and the map's resolution follow
   from it.
4. Does an operator want a terrain prediction on screen ("expect water 38 m
   north-east") or only the verdict? Mentor question 3 in design.md §16 asks
   the same thing about the lateral offset advisory; the answer applies here.

# TRACK RESOLUTION (written 2026-09-05, after the build)

Built as specified, E1 through E5, on branch `track_b`; refs are commits on
that branch. Plan: `docs/superpowers/plans/2026-09-05-terrain-channel.md`.
Nothing here ran for the submission video. Every terrain record is stamped
`sensor.source: "simulated"`.

1. **E1 landed** (e820799, 3565c14): `backend/terrain/rastermap.py` —
   footprint posterior, class-consistent extent (flood fill, unlabelled cells
   absorbed, None at the window edge), nearest boundary, ray-march boundary
   distance, class collapse, checksum, npz round-trip. Acceptance on the
   hand-drawn 20×20 fixture (`fixtures/terrain_fixture.json`): extent
   127.28 + 7.07 m by hand, boundaries 35/80/85/100 m on the four cardinal
   rays, all matched. Metres-per-degree pinned equal to `console/mission.py`.
2. **E2 landed** (1eb3c4a): confusion-matrix sensor, seeded, Bayes posterior
   over the drawn column. Identity sensor is one-hot; reset replays the draw
   sequence, so clean and injected replays differ only through the believed
   position.
3. **E3 landed** (09dbb91, 38d12d8, 34ddf0f): `OPTIONAL_FEATURE_NAMES`,
   weights renormalised over scored features, `score_detail.features_scored`
   (design.md §5 updated), `record(terrain=)`, the channel, calibration
   (saturation = clean p99 of the windowed mismatch, gate floor = clean p0.1
   of L), the min bound rule with `bound_source`. An epoch with no map lookup
   makes **no claim** (feature absent) rather than repeating the last value —
   found on the first real run, where a stale 0 read as agreement after the
   walk left the map. Sensorless records are byte-identical apart from the
   additive `features_scored`.
4. **Consumers landed** (9fb9b6d, acfaa91): `extra_checks` hook so check 6
   enters the correction gate before hysteresis; `Decision.terrain`
   passthrough; DEGRADED advisory sentence with an eight-point compass;
   explanation phrase and a verifiable `terrain.match_likelihood` claim.
5. **Signed map** (1294586): Ed25519 via `cryptography` (added to
   `bootstrap.sh`); unsigned or tampered maps are not consulted.
6. **The raster is OpenStreetMap, not WorldCover** (931b885): no GeoTIFF
   reader in the environment. `backend/terrain/fetch_map.py` rasterises
   1,936 OSM elements within 600 m of the antenna at 5 m (7 classes,
   priority order, buffered highways; relation inner rings ignored). Cut
   rule passed: antenna cell labelled (*building*), boundary distance
   12.5–72.5 m on all eight bearings. **37.7% of cells are unlabelled**, and
   the conservative treatment makes the extent at the antenna 268 m — the
   map-derived bound never binds against a 10–16 m residual bound. Map
   completeness is the lever, not sensor quality.
7. **The sensor is simulated at the static antenna**, not on the route
   presentation frame (assumption 2 revised): the route lives in the
   console and never enters the channel; the real replay's believed fix is
   looked up on the map and that is all the channel sees.
8. **E5 measured** (`python -m backend.terrain.validate`, rescoring the
   shipped streams; `out/terrain_validation.json`, four README-bound plots).
   Terrain first fires at attack epoch 2–3 (31 m at bearing 80°, predicted
   2.7 epochs on the 90° ray). At W = 1 the clean-day fire rate is the
   misread rate (0.40 → 0.01 across diag 0.6 → 0.99); attack fire rate
   0.85–0.92 over the 58% of attack epochs the channel could score (the walk
   leaves the map, and passes over a second *building* — the class-collision
   blind spot, as predicted); d′ 1.2 → 3.9 at W = 1, 4.6 at W = 4, diag 0.99.
   Gate check 6 stays true through the attack (2,879/2,880 carry-off epochs):
   the corrected fix is on the antenna and the sensor agrees with it while
   disagreeing with the believed fix — the signature of a good correction.
   With single-epoch L and a symmetric confusion sensor the check cannot read
   false (floor = misread level); a windowed check is the next iteration.
9. **Composite effect is bounded by the untuned weights**: a saturated
   terrain feature moves confidence by ≤ 0.1 at equal weights over five
   features, β 0.5. Its weight is the threshold session's call; d′ is the
   input to that call.
10. **Pre-existing state exposed, not caused**: the shipped `out/*.jsonl`
    (21:08) carry `pseudorange_residual = 0.0` throughout — `backend/demo.py`
    calls the extractor without the post-fit residual panel that
    `backend/replay.py` supplies — so nothing is excluded, the arbiter never
    leaves NOMINAL under the carry-off, and `backend.measurement.displacement`
    reads 2,792/2,880 FAIL against the residual bound with or without
    terrain. README §Results predates those streams. Track A/B seam; left
    untouched by this track.
11. **Web console rendering landed on `track_f`** (2026-09-06, not in
    E1–E5): while the epoch carries a block, the strip shows a fifth feature
    bar (`terrain · sim`), the displacement bound's source
    (`geometry.bound_source`) with the non-binding bound beside it, and a
    `Terrain · simulated` cell — sensed class at the receiver, map class at the
    believed fix, match likelihood, the nearest boundary as the testable
    prediction (`test …` in DEGRADED, `next …` otherwise) and gate check 6 on
    the signed map — every value tooltipped with its contract path. With the
    trust layer off, `console/server.py` strips the block and the advisory
    like every other layer output. **Still not built**: the map-resolution
    sweep at 10 m; the trajectory (particle-filter) form; the CUSUM
    sequential test. Those remain roadmap.
