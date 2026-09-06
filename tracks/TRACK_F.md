# TRACK F -- Mission console (scoped 2026-09-05, NOT BUILT)

**Status: scoped, not started.** One scrolling page: a hero that says what
ARBITER is, a four-way mission selector, and the existing console parametrised
by mission. Each mission is a different UGV task in a different environment,
a different attack from the design.md §7 model, and a different meaning of
"course correct". The §5 contract does not change. Nothing on screen claims
more than it did before: the route is still a presentation frame, the receiver
at USN8 still never moved, and only the measured displacement (and now the
corrected displacement) separates the tracks.

Four mission plans were produced by four separate planning agents against one
shared brief, each verified on the real USN8 day where the plan says VERIFIED:

| Mission | File | Attack (design.md §7) | Course correction |
|---|---|---|---|
| LOGISTICS | `tracks/TRACK_F_LOGISTICS.md` | Intermediate carry-off, position domain, cross-corridor, top-6 GPS, code/carrier mismatch | RAIM re-solve on the trusted subset; corridor band; PL vs 15 m alert limit gates driving |
| RECON | `tracks/TRACK_F_RECON.md` | Meaconing: GPS rebroadcast, 300 m common delay, 8 dB | Constellation swap (drop GPS, reweight to Galileo); trust tag on each observation report |
| CASEVAC | `tracks/TRACK_F_CASEVAC.md` | Sophisticated carrier-coherent carry-off, along-track | Arrival gate: "at CCP" only on the corrected fix with PL inside the ring |
| COMBAT | `tracks/TRACK_F_COMBAT.md` | Crude 15 dB abrupt position step on all GPS, then operator REVOKES authorisation | Hold; fire-control position input withheld; credential dominates under a clean sky |

Read first: design.md §5, §7, §8, §11a/b; CLAUDE.md hard constraints (no
imagery -- every backdrop is a rendered scene, not a photograph);
console/mission.py (the presentation frame), console/server.py,
backend/demo.py, docs/stream_provenance.md; then the four mission files.

## Prerequisites found while scoping -- fix before any mission (F-0)

All four planners hit the same wall independently. These are not Track F
work; they are defects in what exists, and the page cannot be honest until
they are fixed.

**F-0a -- feature 2 is unscored in every stream `backend/demo.py` writes
(VERIFIED four times).** `score_stream` calls `fx.step(ep)` with no `resid=`
and `main()` fits with no `resid_panel`, so since feature 2 became the
post-fit pseudorange residual (commit 76293c9) it reads 0.0 on every epoch,
`distrusted()` excludes nothing, `information_ratio` is 1.0 and attack-window
confidence bottoms at 0.72-0.77, above NOMINAL 0.643. The stream regenerated
at 21:08 tonight (`out/demo.jsonl`) has exactly one transition: the EXPIRED
force at epoch 390. `python -m backend.measurement.displacement
out/carryoff.jsonl` FAILS 2,792 of 2,880 epochs. `backend/replay.py` already
has the correct wiring; it is four lines:

    # backend/demo.py main()
    cal = fit(clean, floor, resid_panel=residual_panel(clean, nav_xc))
    # backend/demo.py score_stream(), one solve shared by features 2 and 4
    sols = solve_per_constellation(ep, nav_xc)
    res = fx.step(ep, resid=(sols.get("all") or {}).get("resid_m"))
    feats["cross_constellation"] = xc.score(ep, sols)["value"]

Half an hour. Do it first and alone; regenerate; re-run `console.replay` and
the §10 displacement check. Nothing below is built on a stream without it.

**F-0b -- the thresholds pre-date the feature-2 rewrite.**
`console/arbiter/states.py` (14:06) was fit on the old feature; with F-0a in,
the clean day at 0.643 gives **FSR 4.10 %** (118 of 2,880 epochs, 5 events,
one false SURRENDERED at 06:57 with 12 satellites excluded). The demo window
12:00-15:05 is clean-event-free, so demos run meanwhile, but the README's
FSR 0.0000 / TTA 4 / 3.67 m / staircase / 273.5 m peak are all stale (peak is
1,027 m). Redo the §10 threshold session on the wired pipeline and include
meaconing epochs (RECON's signature straddles 0.643).

**F-0c -- residual-magnitude exclusion is detection, not fault isolation.**
Under a subset carry-off or a step, the all-in-view fit is dragged toward the
spoofed position, the *authentic* satellites carry the large residuals, and
the k=1 rule cascades: 6 → 15 → 23 → 26-30 exclusions within five epochs,
information ratio 0.000, bound null, corrected fix fails closed, NOMINAL →
SURRENDERED in two epochs with DEGRADED lasting one. Three of the four
missions show it. Measured options: `MASKED_K3` isolates exactly the six
spoofed satellites at +3 with clean-day FSR 0.0000 but alerts at 30 m
(outside the corridor); a gentler 0.2 m/s walk under k=1 alerts at 10.5 m and
keeps the corrected fix valid for six epochs (LOGISTICS's choice); the
constellation-level rule fixes RECON exactly and is a verified non-fix for
carry-off. The structural fix is RAIM fault exclusion by solution separation
(roadmap, ~3 h). Decide at the threshold session; each mission file states
its own choice and its cost.

**F-0d -- the DEGRADED advisory misreads `next_best_observation`.** Under a
GPS drop the field correctly names "G" (readmission recovers the most
information, §6b), and `console/arbiter/machine.py` prints "Reweight toward
G" -- the spoofed constellation. RECON's F-R2 adds a derived
`geometry.reweight_toward` (argmax over trusted constellations of their own
rows' information, one determinant each: "E" on 90/90 meaconing epochs) and
names `next_best_observation` as the readmission target. Benefits every
mission.

**F-0e -- the causal baselines stay polluted after an attack.** Feature 2's
40-epoch trailing median and feature 4's gated baseline hold attack-level
values for 20-21 epochs after the spoofer stops (28 satellites still
distrusted on clean data), so recovery is attack end + 20, then the
10-epoch staircase per step: RESTRICTED +30, DEGRADED +40, NOMINAL +50. A
measured limitation, not a bug; every mission's tail length is chosen around
it and the beats say so.

## Build order -- importance = demo payoff × certainty ÷ risk

| Order | Item | Why here | Planner's verdict |
|---|---|---|---|
| 0 | **F-0a**, then page plumbing (F-P1..P3) | Nothing else is honest without F-0a; everything hangs off the registry | all four: F-0a first |
| 1 | **LOGISTICS** | design.md §1 is this scenario; stream, scene, beats and provenance exist end to end; the first hour is a repair, not a build | keep #1 |
| 2 | **RECON** | The beat no other mission has: displacement 0.000 m and something is still wrong. The only mission where DEGRADED is *active* through the whole attack -- GPS dropped, information ratio 0.72, corrected fix on Galileo within 1.3 m, `correction_ok` 100 % -- because the constellation rule excludes exactly what was spoofed. Needs F-R1b/c (12 lines, 0/2,880 clean false fires) | keep #2 on condition F-R1b/c lands within the first hour; else swap with CASEVAC |
| 3 | **CASEVAC** | The sophisticated attacker: code-minus-carrier blind by construction, caught in two epochs by residual-fed geometry; the most legible failure on the page (believed pin inside the ring, vehicle 240 m short); only a three-line arrival rule is new | argues for #2: verified end to end, nothing new to build |
| 4 | **COMBAT** | Invariants 1 and 2 (skip to SURRENDERED, staircase up) and REVOKED forcing surrender at confidence 0.85 -- the one beat that shows credential dominance in the direction no signal can override. Needs a 10-line injector extension; least staged detection story | agrees #4, cut first |
| 5 | **Environments + hero + selector** (F-P4..P7) | Visual, high payoff for the video; the console is the argument. Build after two missions run | -- |

Swap rule between 2 and 3: RECON stays at #2 only if its constellation
exclusion rule is in by the end of its first hour; CASEVAC then needs nothing
new and takes the slot. Cut rule for the whole track: a mission that is not
built appears in the selector as `SCOPED · NOT BUILT` with its attack card and
no tile animation. Never a fake tile.

## 1. The page -- three layers, one scroll

### Hero (layer 1)

Full viewport, dark. Black nav bar: wordmark `ARBITER` left in the display
face; centre `+ PLATFORM  + MISSIONS  + METHOD  + LIMITS` (in-page anchors:
hero, selector, console, footer); right `RESET` (reload = hard reset, as
today).

**Backdrop -- a rendered scene, not imagery.** The `EnvScene` stack (terrain,
Poly Haven HDRI, vendored rover GLB, satellite sprites, LOS rays, SSAO +
bloom) reused as a `HeroScene`: a high oblique camera on a slow dolly over the
LOGISTICS environment (§2) with a small fleet of UGVs on scripted routes -- a
three-vehicle convoy on the track, a lone scout climbing the ridge, one
vehicle returning toward the depot -- and satellite rays converging on the
lead vehicle's antenna. Clones via `SkeletonUtils.clone` (vendored). Left
third darkened by a gradient for the headline; radial vignette; no
depth-of-field pass (not vendored). Corner caption, small: `RENDERED SCENE ·
ILLUSTRATIVE`. The loop pauses when scrolled out of view (IntersectionObserver)
so it never competes with the console for the GPU.

**Text.** Headline, two lines, condensed black caps, the motto:

    SIGNAL QUALITY
    IS NOT PROVENANCE

Sub-line, sans: "A trust layer between a GNSS receiver and an autonomy stack.
It detects spoofed positioning, scores confidence continuously, and degrades
the vehicle's authority in stages -- full autonomy, coast, finish the leg,
hold." Mono line: `AUTHENTICATED RANGING · BOUNDED INTEGRITY · TIERED
EXECUTION RIGHTS`. Bottom-left, mono: live `DD.MON.YYYY` and `HH:MM:SS ZULU`
from the browser clock in UTC. Boxed `↓` at right, scrolls to the selector.

**Spec block**, bottom-right, mono, every line a measured result with its
source, served from `console/web/numbers.json` (hand-maintained, one `source`
per entry, rendered beside the value; changes in the same commit as the
README). **Populate it from the F-0a regeneration, not from today's README
-- every headline number there is stale (F-0b).** Shape:

    MAX ADVERSARIAL DISPLACEMENT   <regen> M   readme §results
    ANALYTIC BOUND (SAME EPOCH)    <regen> M   readme §results
    FALSE SURRENDER RATE           <regen>     readme §results, 2880 clean epochs
    TIME TO ALERT                  <regen>     readme §results
    STATION                        USN8 · 2026-08-20 · 30 S · 5 CONSTELLATIONS

### Selector (layer 2)

Tagline, sans: "Four missions. Four attacks. One arbiter." Then four equal
columns, `▸ RECON` · `▸ LOGISTICS` · `▸ CASEVAC` · `▸ COMBAT`, each a tile.

**Tiles are live, from ONE renderer.** A single `<canvas>` spans the row;
`renderer.setScissorTest(true)` and per tile `setViewport` + `setScissor`,
then render that tile's mini-scene with its own camera. Each mini-scene is
its mission's environment (§2) at tile scale: ground, route ribbon, props,
one vehicle clone driving, and the ghost doing what that mission's attack
does -- peeling off toward the corridor edge; arriving at the ring first; a
whole constellation of sprites going dark overhead; a 98 m jump across a
phase line. Where the mission file provides `Mission.preview` samples (real D
and states from the stream, LOGISTICS does), the tile is driven by data and
captioned so; otherwise it is scripted and captioned `preview · scripted`.
Loops every ~6-12 s; 30 fps cap; paused when off screen.

**Hover:** `▸` becomes `▾`, the tile brightens, a two-line caption slides in:
mission · attack · correction (the registry's attack card). **Click:**
`selectMission(name)` -- pushes `?mission=name` to history, mounts or
remounts the console with that mission, smooth-scrolls to it. A not-built
mission shows the card and `SCOPED · NOT BUILT`; click does nothing.

### Console (layer 3)

The existing `App` becomes `<Console mission=...>`. Unchanged: the instrument
strip's claims, the stage, the explanation bar, `?view=`, `?lock=1`, `?hz=`,
`?rate=`, `?layer=off`, press R. New:

- **Mission header row** above the strip: `LOGISTICS · CONVOY RESUPPLY` | the
  attack card | beat indicator `1 CLEAN · 2 ATTACK · 3 CORRECT · 4 CREDENTIAL`
  lit from the registry's `beats` and the epoch index | `LAYER ON/OFF` and
  `RESTART`.
- **Correction cell.** The Track D block (`geometry.correction`) is emitted
  today and displayed nowhere; "course correct" needs it on screen. Sixth
  strip cell: protection level as the big number against the alert limit,
  the five gate checks as five dots (null = hollow), verdict `DRIVING ON
  CORRECTED FIX` / `CORRECTED FIX WITHHELD`. Dropped-SV chips move into the
  geometry cell; strip floor stays ~1275 px.
- **Third track: CORRECTED.** Map and 3D scene: a second ghost in
  `--corrected: #6FCF97` at TRUE + D_c, `D_c = ENU(corrected_position) -
  ENU(_truth)`, drawn only while `correction_ok` (all four planners agreed
  on this), with a ring of radius `protection_level_m`. Third pin in the
  satellite-view inset; one more key line.
- **Props by kind** (union of the four mission files): `depot`/`fob`,
  `resupply_point`, `checkpoint`, `phase_line` (perpendicular to the route
  heading, half-length `radius_m`), `objective`, `ccp` (ring), `litter`,
  `aid_station`, `patrol_base`, `obs_point`, `observation_post`,
  `hold_marker` (state-driven). Geometry only -- boxes, rings, posts, dashed
  lines -- labelled in the mono face; on `INSET_LAYER` so the inset gets
  them free; SVG twins in `Plot`.
- **State-driven motion in the presentation frame.** TRUE advances
  `speed_m_per_epoch × gain(state)` with `gain` from the mission file
  (LOGISTICS and COMBAT halt in SURRENDERED; CASEVAC and RECON keep the
  constant frame). Captioned as policy depicted, never a measurement.
- **Mission readouts and phrases** per mission file: corridor gate,
  arrival status, report tag + clock holdover, fire-control input.
  `explain(d, behaviour=mission.behaviour)` overrides `BEHAVIOUR` strings
  only; headlines and claims untouched so `verify()` stays green.
- **Consequence overlay** per mission: corridor breach, arrival ring,
  observation stamp, hold marker.
- **`?solo=1`** renders only the console layer (capture); `?mission=`
  without `solo` opens scrolled to the console.

## 2. Environments -- one per mission, rendered in three.js at high quality

The four missions are four theatres, not one prairie with different labels.
Each has its own terrain, light, ground, vegetation and haze, so a viewer
knows which mission is running from the backdrop alone, and the hero and the
tiles inherit them. All rendered, all captioned as illustrative; no
photographs, no satellite imagery, no camera (CLAUDE.md).

| Mission | Time / sky (HDRI, Poly Haven CC0) | Terrain | Ground | Vegetation and props | Haze / palette |
|---|---|---|---|---|---|
| LOGISTICS | Noon, partly cloudy -- `kloofendal_48d_partly_cloudy` (vendored today) | Northern prairie, gentle undulation (today's `makeTerrain`), corridor flattened | `grass_path_2` dirt track with grass tufts and stones (vendored) | Short dry grass tufts (instanced), scattered stones, FOB pad of stacked blocks + mast, resupply-point ring | Thin, warm-neutral; ground `#6B6A5E`, corridor amber |
| RECON | Dusk, low sun in the west -- `belfast_sunset_puresky` (vendored as `asset-sky.hdr`) | Rolling hills with one ridge line the loop climbs; OPs on rises | Darker mixed grass/soil set (one new 2k set, e.g. `brown_mud_leaves_01` or `forest_leaves_02`) | Tall grass and scrub (instanced crossed quads), a tree line of low-poly cones/billboards along the ridge, flag-and-berm OPs, OP mast | Cooler blue haze, long shadows; ground olive-brown, accent cold blue |
| CASEVAC | Overcast, flat light, light rain -- one new HDRI (`kloofendal_overcast_puresky` or `overcast_soil_puresky`, 2k ≈ 5 MB) | Valley floor with a dry riverbed cut across the outbound leg; berm at the CCP | Wet look: darker diffuse, roughness lowered to 0.35 for puddle sheen; gravel set (`gravel_road` or `aerial_beach_01`) | Sparse scrub, a low stone wall at the CCP, red-cross panel and litter, tent at the aid station | Grey-blue, fog density ×2 (0.0045), desaturated; ring amber/green |
| COMBAT | Dawn, blue hour, sun on the horizon -- one new HDRI (`kiara_1_dawn`, 2k) | Broken ground, berms and shell-scrape depressions, a rise with the objective compound | Dry cracked earth (`dry_ground_01` or `brown_mud_dry`), dust-tinted | Almost none; phase-line posts with dashed lines, objective compound of boxes on the rise, hold marker | Warm dust haze at the horizon, cold shadows; accent red `#E4551F` on phase lines |

**Implementation.** `Mission.scene` becomes a full environment spec consumed
by a `makeEnvironment(spec, mission)` factory that today's `EnvScene`
constants (`ENV`) collapse into:

    scene = {
      "hdr": "/vendor/asset-sky-<name>.hdr",     # lighting only; procedural sky ~2 stops darker (EARTH_BACKDROP.md)
      "sun": {"az": 124.4, "el": 47.3},          # measured from the HDR at load, as today (loadHDR reads it)
      "exposure": 1.5, "fog": {"color": "#8F94A1", "density": 0.0022},
      "sky": {"horizon": "#8F94A1", "mid": "#7891B0", "zenith": "#5F7FB5"},
      "ground": {"diff": ..., "nor": ..., "rough": ..., "tile_m": 3.5, "tint": "#FFFFFF", "roughness_scale": 1.0},
      "terrain": {"amplitude": 2.4, "octaves": [70, 22, 6], "ridge": {"bearing_deg": 40, "height_m": 18, "width_m": 120} | null,
                  "valley": {...} | null, "flatten": ["corridor", "props"]},
      "vegetation": {"kind": "grass_tufts" | "tall_grass" | "scrub" | "none", "density_per_ha": 800, "treeline": {...} | null},
      "palette": {"ground": "#6B6A5E", "rock": "#5C574D", "accent": "#E8A21C"},
      "hero_camera": {"az": 200, "el": 22, "dist": 140, "dolly": 0.6}
    }

`makeTerrain` is generalised to a sum of noise octaves plus optional ridge
and valley terms, still flattened along the corridor and under props (the
two invariants today's terrain keeps). Vegetation is `InstancedMesh` of a
few triangles per instance with a procedural alpha gradient (no texture
downloads), placed by the same seeded RNG, never inside the corridor or
within a prop's radius, LOD-culled beyond 250 m. Ground sets are the same
diff/nor/rough triple the loader already handles.

**Quality bar, stated so it can be checked.** 2k HDRI lighting with the
sun direction read from the pixels (as today); PBR ground with 2k diffuse
and 1k normal/roughness, anisotropy at the device maximum; PCF soft shadows
at 2048; SSAO + bloom + gamma through the composer (as today); terrain up
to 200 × 200 segments over the route box; 2-5 k vegetation instances;
device pixel ratio capped at 2; fog matched to the HDR horizon band. Target
60 fps at 1080p on the demo laptop with the console mounted, and 30 fps
with the hero and four tiles on screen; `__envInfo()` reports draw calls,
triangles and the loaded/fallback status per asset, and the acceptance test
reads it.

**Assets.** Three new HDRIs (≈ 15 MB at 2k) and two new ground sets
(≈ 8 MB) are fetched by `bootstrap.sh` from Poly Haven's direct CC0 URLs
(no login) into `console/web/vendor/`, gitignored like the RINEX data; the
existing vendored set stays committed and is the fallback for every
environment (`status.sky = 'fallback'` / `status.ground = 'fallback'` are
already reported in the HUD). A mission whose assets are missing still
renders, one notch plainer, and says so.

**Hero.** The hero uses the LOGISTICS environment at hero camera settings;
the four tiles use their own. The tile renderer shares environment
factories, not GL contexts, with the console.

## 3. Plumbing -- registry, streams, server

### Console mission registry (`console/missions/`)

    @dataclass(frozen=True)
    class Mission:
        name: str; title: str; tagline: str
        route_enu: tuple[tuple[float, float], ...]
        speed_m_per_epoch: float
        gain: dict[str, float]           # state -> presentation-frame motion multiplier
        alert_limit_m: float; alert_limit_provenance: str
        ground_station_enu: tuple[float, float]
        props: tuple[dict, ...]          # {"kind", "e", "n", "label", "radius_m"?}
        stream: str                      # out/<name>.jsonl
        attack: dict                     # {"name", "mechanism", "watch", "response"}
        beats: tuple[tuple[int, str], ...]
        behaviour: dict[str, str]        # per-state operator sentence (explain() override)
        scene: dict                      # environment spec, §2
        preview: str | None              # sample source for the tile, or None = scripted
        built: bool

`REGISTRY = {"logistics", "recon", "casevac", "combat"}`; `get(name)` raises
on unknown. The pure kinematics in `console/mission.py` move to
`console/frame.py` as functions of an explicit route; `console/mission.py`
keeps its module-level names bound to the logistics mission so
`console/tests/test_mission.py` and `route_parity.mjs` pass unchanged.
`as_dict(mission)` adds the new keys. `route.js` already takes the
`/mission` object: no change.

### Server

`/mission?name=<name>` (default `logistics`); `/events?mission=<name>` picks
`mission.stream` unless `--source` was given (tail mode keeps working for
Track A live). Unknown mission: 404 listing the registry. One `Arbiter` per
connection as today. Optional per-mission `rate_schedule`
(`[(epoch, eps), ...]`) consumed in `_events` so an onset can play in slow
motion (LOGISTICS F-L5); captioned as a presentation device.

### Backend streams (`backend/missions.py` + `backend/demo.py --mission`)

    @dataclass(frozen=True)
    class MissionSpec:
        name: str
        spoof: Callable[[datetime], Spoof]     # partial over the §7 constructor
        onset: datetime; attack_epochs: int
        pre_epochs: int; post_epochs: int
        credential: Callable[[int], str]       # index in slice -> status
        exclusion: str                         # "k1" | "masked_k3" | "k1+constellation"
        alert_limit_m: float
        notes: str                             # provenance paragraph

`python -m backend.demo --mission all` loads, calibrates **with the residual
panel (F-0a)**, fits cross-constellation, loads nav tables and measures
sigma_UERE once (9 s with the loader cache), then per mission: inject, solve
positions, score, slice, write `out/<name>.jsonl` and `out/<name>_truth.csv`,
append a provenance section to `docs/stream_provenance.md`. `out/demo.jsonl`
stays as an alias of `out/logistics.jsonl` until the README stops naming it.
Score from `onset - pre_epochs - WARMUP` with `WARMUP = max(FeatureConfig
windows, CrossCal.window) + min_history` (40 + 5, read from the configs) and
drop the warm-up from the file, so four missions cost four demo windows.

### Tests

- `console/tests/test_missions.py`: every registry entry has a route of
  600-1,100 m, non-empty `alert_limit_provenance`, `props[*].kind` in the
  allowed set, beats with strictly increasing epochs, a `stream` path, a
  `scene` with `hdr` and `ground` keys, and `as_dict()` round-trips through
  `route.js` parity (parametrise `route_parity.mjs` over `?name=`).
- `test_server.py`: `/mission?name=x` 404s; `/events?mission=recon` serves
  `out/recon.jsonl`; `?mission` absent serves logistics.
- `test_ingest.py`: each `out/<name>.jsonl` record satisfies the §5
  required-key subset; no record carries a mission field.
- Headless check per mission (`?mission=<name>&solo=1&lock=1`): the console
  mounts, the state timeline equals `console.replay`, the corrected pin is
  present exactly when `correction_ok`, `__envInfo()` reports the mission's
  HDR and ground as loaded (or fallback, never silent).

## 4. Page build decomposition -- F-P1..P7

**F-P1 -- registry + frame + server** (1.5 h). `console/frame.py`,
`console/missions/{__init__,logistics}.py`, compat shim, `/mission?name=`,
`/events?mission=`. Acceptance: existing tests green; `/mission?name=logistics`
equals today's `/mission` plus the new keys.

**F-P2 -- `--mission` streams** (1.5 h + regeneration, after F-0a).
`backend/missions.py`, load-once / per-mission refactor, warm-up slicing,
provenance sections. Acceptance: `out/logistics.jsonl` replays through
`console.replay` with the transitions its mission file lists; `test_ingest`
green; provenance no longer says 273.5 m.

**F-P3 -- console takes a mission** (3 h). `<Console>`, header row, beat
indicator, correction cell, corrected ghost + pin + key line, props by kind,
state-driven motion, `explain(behaviour=)`, `?solo=1`. Acceptance:
`?mission=logistics&solo=1` is today's console plus the correction cell and
the corrected track; `__envInfo()` reports |D_c|; strip floor unchanged.

**F-P4 -- environments** (3 h). `makeEnvironment`, generalised terrain,
vegetation instancing, wet-ground and dust variants, asset fetch in
`bootstrap.sh` with fallback, the four `scene` specs, tuning on the demo
machine. Acceptance: the four missions are told apart from a screenshot of
the backdrop alone; `__envInfo()` shows loaded assets and ≥ 60 fps in the
console for each; a missing asset degrades to the vendored set with the
HUD note, never a black frame.

**F-P5 -- hero** (2.5 h). `HeroScene` on the LOGISTICS environment, fleet,
headline, clock, spec block from `numbers.json` (post-regeneration values),
arrow, pause-when-hidden. Acceptance: 60 fps with the console mounted below;
every spec-block value has a source string; no raster imagery in the page.

**F-P6 -- selector** (2 h). Scissor renderer, four mini-scenes on their own
environments (built missions only), data-driven preview where the mission
provides samples, hover caption, click → mount + scroll, deep link, `SCOPED
· NOT BUILT` fallback. Acceptance: one WebGL context for four tiles
(`renderer.info` via `__selectorInfo()`); click reaches the console with the
right stream within a second.

**F-P7 -- tests, README, provenance** (1 h). The tests above; README gains a
"Missions" section with the build-order table and, per built mission, the
VERIFIED numbers from its file; the stale §Results numbers replaced from the
regeneration; `docs/stream_provenance.md` has one section per stream.

About 14.5 h for the page including environments; F-0a is 0.5 h before any
of it; each mission file carries its own 4.4-5.75 h decomposition. Three
people: F-0a and F-P1..P3 first on one machine while LOGISTICS's owner does
F-L1..L2 and RECON's owner does F-R1b/c; environments, hero and selector
once two missions replay end to end.

## 5. What this track does not change

- The §5 contract and the backend/console boundary. Mission data rides on
  `/mission`, never in the stream.
- The arbiter's invariants. Thresholds change only through the §10 session
  (F-0b), in `states.py`, with provenance.
- What "measured" means: D and D_c from the stream; everything else framed
  and captioned. The receiver never moved.
- CLAUDE.md's rules: no imagery (every backdrop is rendered), no motion
  commands (all advisories; the presentation-frame halts depict policy), no
  guessed numbers (each mission's alert limit and standoff carries
  provenance, PLACEHOLDER where it is stated from doctrine and unverified).
