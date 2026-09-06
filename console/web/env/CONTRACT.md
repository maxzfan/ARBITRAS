# Environment modules -- the contract (Track F, tracks/TRACK_F.md §2)

One file per mission, `console/web/env/<name>.js`, a classic `<script>` (no
ES modules, no bundler -- the console is served as-is and runs offline). It
registers itself:

```js
(function () {
  window.ARBITRAS_ENV = window.ARBITRAS_ENV || {};
  window.ARBITRAS_ENV.recon = { id: 'recon', spec: SPEC, build: build };
  ...
})();
```

The console calls `build(ctx)` once when a mission's scene mounts and uses
the returned handle every frame. The standalone harness
`console/web/env/preview.html?env=<name>` calls it the same way, so a module
that renders in the harness renders in the console.

## `spec` -- static, read before build

```js
const SPEC = {
  hdr: '/vendor/asset-env-recon-sky.hdr',       // lighting only (PMREM); never scene.background
  exposure: 1.3,                                // ACES tone-mapping exposure
  fog: { color: '#6E7A8C', density: 0.0030 },   // FogExp2, matched to the HDR horizon band
  sky: { horizon: '#6E7A8C', mid: '#4E5E7C', zenith: '#2F3C5C' },   // procedural gradient sphere, ~2 stops under the HDR
  sun: { az: 265.0, el: 9.0 },                  // fallback key-light direction until the HDR's brightest pixel overrides it
  palette: { ground: '#4F5A3F', rock: '#5C574D', accent: '#7FA8CC' },
  hero_camera: { az: 200, el: 22, dist: 140, dolly: 0.6 },   // for the hero/tile framing
  // optional -- a rendered equirectangular backdrop as the VISIBLE sky (see below)
  backdrop: { url: '/vendor/asset-backdrop-recon.jpg', sun_u: 0.633, sun_el: 34.0, dim: 0.58 },
};
```

### `spec.backdrop` -- rendered sky, optional

The gradient sphere is first paint and fallback. When `spec.backdrop` is
present the console, the hero/tiles and the harness call
`helpers.loadBackdrop(spec.backdrop, spec.sun.az)` and, when the JPEG lands,
replace the gradient sphere with the pano on a sphere just inside it. The HDR
in `spec.hdr` still lights the scene (PMREM); the backdrop is only what the
viewer sees. Rules:

- The image is a 2:1 equirectangular render of the theatre from the UGV sim
  asset pack (Blender/Cycles, `export_env.py --hires`, 4096x2048), far field
  only: sky, horizon and anything beyond 120 m. Near set dressing stays real
  geometry in the module. It is a render, not a photograph (CLAUDE.md).
- `sun_u` / `sun_el`: the sun disc measured IN THE IMAGE (u across, elevation
  in degrees), never taken from a manifest. The sphere is rotated so that disc
  sits at `spec.sun.az`, the azimuth the module was composed for, and the key
  light takes `sun_el`; the HDR's measured sun no longer re-aims the key.
- `dim`: linear multiplier so the pano's 10-25 deg band lands at the module's
  previous `sky.mid` luminance (EARTH_BACKDROP.md's legibility rule for the
  console's additive sprites). Never above 1.
- `fog.color` becomes ACES^-1 at `spec.exposure` of the backdrop's horizon band
  as seen (times `dim`), so the ground fades into the pano; `sky.*` become the
  pano's own bands so first paint matches. Record the measurements in the
  module header as the existing HDR measurements are.
- Files are committed as `console/web/vendor/asset-backdrop-<mission>.jpg`
  (rendered here, not fetchable; the `asset-env-*` ignore rule does not match
  them). `report({backdrop: 'loaded' | 'fallback'})` like every other asset.

### `spec.relief` and `spec.props` -- the theatre's ground and set dressing, optional

Both come from the same Blender scenes as the backdrops
(`console/web/env/tools/export_env_assets.py`, run inside Blender against
`~/Desktop/UGV_CAD/environments/ENV_<biome>.blend`).

- `relief: { id, scale, blend_m }` -- the theatre's height field
  (`build_env.make_height_fn`, 160x160 over 760 m, int16) served as
  `/vendor/asset-relief-<mission>.js` and loaded before the modules.
  `helpers.relief(spec).at(x, z)` returns metres, oriented to the DISPLAYED
  pano (Blender +X at compass `360 (0.25 - sun_u) + sun.az`, +Y ninety degrees
  clockwise -- the pano is mirrored relative to Blender world, verified from
  the camera frame of all four scenes), centred on the route box, fading to 0
  at the field's edge. The module ADDS it with its own wide flattening band
  (`smooth(hw + 3, hw + 3 + blend_m, lat)`, prop rims `+7`, ground station
  `20 m`), so invariant 1 holds exactly as before; `blend_m` is wide (30-90 m)
  because the relief is metres, not centimetres. Without the asset `at()` is 0.
- `props: { url, seed, margin, groups: [...] }` -- the pack's low-poly
  prototypes (`/vendor/asset-props-<mission>.glb`, Draco; trees decimated to
  ~900 faces, one primitive each, normals only) scattered by
  `helpers.scatterProps(ctx, heightAt, cfg, own)`: one `InstancedMesh` per
  prototype variant, seeded, culled from the corridor (`keepOut.corridor` past
  `hw`), every prop's rim (`keepOut.prop`) and the ground station, admitted by
  an optional `accept(e, n)` (CASEVAC admits by pre-map class) and `density`.
  `tint: {low, high, split, radial}` paints trunk -> canopy per vertex in
  LINEAR RGB; `region: 'near'` scatters in a band beside the route. Async:
  meshes join `own` for the module's dispose and `report({props, props_detail})`
  carries instance, draw-call and triangle counts. `ctx.lite` (the home tiles)
  scales counts by 0.35. Budget still applies: read `props_detail.triangles`.
- `helpers.antiTile(material)` on the ground material blends a second sample of
  the same map at ~1/7 scale and a low-frequency value noise into the albedo
  so a 2k texture repeated across the theatre stops reading as a grid; it
  chains any `onBeforeCompile` the material already has.

## `build(ctx)` -- synchronous first paint, async polish

`ctx`:

| field | what |
|---|---|
| `THREE` | the vendored three.js r147 namespace (examples already attached: `EffectComposer`, `GLTFLoader`, `RGBELoader`, `OrbitControls`, ...) |
| `scene` | the `THREE.Scene` to add to. Do not replace `scene.fog`/`scene.environment` yourself -- set fog from `spec` and let the console install the PMREM environment (it calls `helpers.loadHDR` on `spec.hdr` and reports the sun) |
| `renderer` | for `capabilities.getMaxAnisotropy()` and texture loading |
| `mission` | the `/mission?name=` object: `route[{e,n}]`, `props[{kind,e,n,label,radius_m}]`, `ground_station{e,n}`, `corridor_half_width_m`, `route_length_m`, `scene{...}` |
| `helpers` | see below |
| `terrain` | only when `mission.scene.terrain_map` is true and `/terrain` answered: `{grid: Int16Array(rows*cols), rows, cols, cell_m, origin_enu:[e,n], classes:[names]}`; row index grows NORTH, column index EAST; `-1` = unlabelled |
| `report(status)` | call with `{ground:'loading'|'loaded'|'fallback', vegetation:'loaded'|'skipped', note:'...'}` whenever an asset lands or fails |

`helpers` (from `console/web/env/helpers.js`, extracted from the console --
use these, do not copy them):

| helper | what |
|---|---|
| `makeNoise(seed)(x, y)` | smooth value noise in [-1, 1], deterministic per seed |
| `mulberry32(seed)()` | deterministic RNG in [0, 1) |
| `smooth(a, b, x)` | smoothstep |
| `loadTexture(url, srgb, repeat, aniso)` | Promise<Texture>, RepeatWrapping set |
| `loadHDR(url, renderer)` | Promise<{env, sunDir, sunEl, fog}> -- the console calls this on `spec.hdr`; a module needs it only for a preview |
| `withTimeout(promise, ms, what)` | reject after ms |
| `azel(azDeg, elDeg)` | unit vector, scene frame: x = East, y = Up, z = South |
| `toScene(e, n, y=0)` | ENU metres -> `THREE.Vector3` |
| `lateralOffset(e, n)` | signed metres from the mission route polyline (positive = left of travel) |
| `roundedRect(w, h, r)` | `THREE.Shape` |
| `INSET_LAYER` | layer index 1; **enable it on every static mesh** (`obj.layers.enable(INSET_LAYER)`) so the top-down satellite-view inset sees the ground, rocks and props. Do not enable it on the sky sphere or on vegetation |
| `FALLBACK` | `{hdr, ground:{diff,nor,rough}}` -- the vendored Earth set every module falls back to |

Return a handle:

```js
return {
  heightAt(x, z),        // scene-frame metres -> ground height y. REQUIRED. Deterministic. Cheap (called per frame for vehicle, ghosts, camera).
  bounds: { minX, maxX, minZ, maxZ },
  sunAz, sunEl,          // degrees, from spec.sun until the HDR overrides
  tick(dt, camera),      // optional, per frame, must be cheap (sway, dust)
  dispose(),             // geometry, materials, textures you created
};
```

### Invariants -- the console relies on these

1. **Flat where things stand.** `heightAt` must return the same value (any
   value, but constant) within `corridor_half_width_m + 3` m of the route
   polyline, within `radius_m + 7` m of every prop, and within 20 m of the
   ground station. Use `helpers.lateralOffset` and `helpers.smooth` exactly as
   today's `makeTerrain` does. Vehicles, ghosts, rings and posts are placed on
   `heightAt`; a bump under the route reads as vehicle motion that is not in
   the data.
2. **Frame.** ENU -> scene is `x = e, y = up, z = -n`. North is `-z`.
3. **Nothing that reads as data.** No pins, no tracks, no satellite marks, no
   text. The console draws everything that is measured. Props are drawn by
   the console too (rings, posts, phase lines); the environment only shapes
   the ground under them and may add *set dressing* near them (a wall at a
   CCP, blocks at a FOB) that does not obscure the ring.
4. **No imagery.** Procedural geometry plus CC0 PBR textures and HDRIs.
   No photographs, no satellite tiles, no map tiles.
5. **Static geometry on `INSET_LAYER`.** Ground, rocks, walls, tree trunks:
   yes. Sky sphere, grass/scrub instances: no.
6. **Budget.** ≤ 120 draw calls and ≤ 600 k triangles for the environment
   alone at the chase camera; vegetation as `InstancedMesh` (one draw call
   per kind); textures ≤ 2k diffuse, ≤ 1k normal/roughness. 60 fps at 1080p
   on a laptop with the console's SSAO + bloom on.
7. **Fallback, never black.** Every `loadTexture` / HDR failure falls back to
   `helpers.FALLBACK` and calls `report({... 'fallback'})`. First paint is the
   procedural scene with flat-coloured materials; textures swap in when they
   arrive.
8. **Deterministic.** Seeded RNG only. Two machines render the same scene.

### Assets

New assets are Poly Haven CC0, fetched -- not committed. Add each to
`console/web/env/ASSETS.txt` as `url<TAB>vendor-filename` with the vendor
filename `asset-env-<mission>-<what>.<ext>` (e.g.
`asset-env-recon-sky.hdr`, `asset-env-recon-ground-diff.jpg`). `bootstrap.sh`
fetches the list; `console/web/vendor/asset-env-*` is gitignored. While
developing, `curl -L -o console/web/vendor/<name> <url>` yourself. Direct URL
shape: `https://dl.polyhaven.org/file/ph-assets/HDRIs/hdr/2k/<slug>_2k.hdr`
and `.../Textures/jpg/2k/<slug>/<slug>_diff_2k.jpg` (`_nor_gl_2k.jpg`,
`_rough_2k.jpg`); 1k under `/1k/`. Verify each URL with `curl -sI` before
listing it.

### Preview and verification

`python -m console.server` then open
`http://localhost:8420/env/preview.html?env=<name>&mission=<name>` -- a
fixed chase camera on the route start, OrbitControls, the console's SSAO +
bloom, and `window.__envPreviewInfo()` returning `{drawCalls, triangles,
status, heightSamples, fps}`. Headless screenshot with the venv's
Playwright:

```python
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(args=["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
    pg = b.new_page(viewport={"width": 1600, "height": 900})
    pg.goto("http://localhost:8420/env/preview.html?env=recon&mission=recon")
    pg.wait_for_timeout(8000)
    pg.screenshot(path="recon.png"); print(pg.evaluate("window.__envPreviewInfo()"))
```

Acceptance for a module: the screenshot is recognisably the theatre in
TRACK_F.md §2's table, `__envPreviewInfo().status` shows every asset
`loaded` (or `fallback` with a reason), draw calls and triangles inside the
budget, and `heightAt` returns a constant along the whole route polyline
(the harness samples it and reports `heightSamples.routeSpread`, which must
be 0).
