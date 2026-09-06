# Earth backdrop for `?view=env` — spec for the index.html owner

Goal: the environment must read as **Earth, temperate northern plains** (Camp Grafton, ND — design.md §1),
not another planet. The combo this was written against (the NASA Perseverance glb + `rocky_terrain_02` +
`belfast_sunset_puresky`) read as Mars. The vehicle has since been replaced by the 4x4 UGV
(`/vendor/asset-ugv.draco.glb`, own CAD, Draco-compressed; see `ENV.model` in index.html).
Assets below are vendored and committed. The older `asset-sky.hdr` / `asset-ground-*` set is still live: RECON lights with `asset-sky.hdr`, and `asset-ground-*` is the rock texture (`ENV.rockTex`, `helpers.rock`).

## Assets (all Poly Haven, CC0; flat in /vendor/ — server handler is basename-only)

| replaces                     | new file                           | what                                              |
|------------------------------|------------------------------------|---------------------------------------------------|
| `asset-sky.hdr`              | `asset-earth-sky.hdr`              | `kloofendal_48d_partly_cloudy_puresky` 2k, 5.2 MB |
| `asset-ground-diff.jpg`      | `asset-earth-ground-diff-2k.jpg`   | `grass_path_2` diffuse 2k (sRGB, 2.7 MB)          |
| `asset-ground-nor_gl.jpg`    | `asset-earth-ground-nor_gl.jpg`    | normal, OpenGL +Y (Three.js default, no flip)     |
| `asset-ground-rough.jpg`     | `asset-earth-ground-rough.jpg`     | roughness (linear)                                |

Why these: kloofendal is a real blue sky with defined cumulus and a flat horizon (blue is the Earth cue;
`sunflowers_puresky` measured cream-tinted, `wasteland_clouds` is sunset-orange = the Mars problem again).
`grass_path_2` is a trodden dirt track with grass tufts and small stones — a prairie road; every other candidate
(`gravel_road`, `forest_ground_04`, `dry_ground_01`, `dirt_aerial_02`, `brown_mud_dry`) is uniform brown clay.

## Measured from the HDR (pure-Python RGBE decode; 1k and 2k agree)

Sun = centroid of pixels above the 99.99th-percentile luminance.
- image (u, v) = (0.5955, 0.2372) → **elevation 47.3°**; azimuth **+34.4° east of image centre**
- horizon band (v = 0.50 ± 0.03) mean linear RGB = (0.417, 0.449, 0.546)
- sky above 20° mean linear RGB = (1.286, 1.352, 1.477)
- cos-weighted, solid-angle-weighted mean radiance of the upper hemisphere L̄ = 1.524

### Sun direction in the scene frame — no envmap rotation available in r147, so match the light to the HDRI
Scene frame (from `azel()` in index.html): **x = East, y = Up, z = South (North = −z)**,
`azel(az,el) = (sin az·cos el, sin el, −cos az·cos el)`, az clockwise from N.
Three.js equirect lookup: `u = atan2(dir.z, dir.x)/2π + 0.5`, so **u = 0.5 is +x (East)** and u grows toward +z (South).
Sun at u = 0.5955 → 34.4° from +x toward +z → **compass az = 124.4° (ESE), el = 47.3°** — a mid-morning sun at a
northern site, which is fine.

```js
// unit vector FROM the scene origin TOWARD the sun; matches the HDRI as loaded (no rotation)
const SUN_DIR = azel(124.4, 47.3);            // ≈ (0.559, 0.735, 0.383)
sun.position.copy(SUN_DIR).multiplyScalar(300); sun.target.position.set(0,0,0);
sun.color.set(0xFFF4E0); sun.intensity = 2.2; // warm-white key; HDRI supplies fill + reflections
```
Shadows then fall toward the WNW, consistent with the bright patch in the HDRI and with the dome compass.

### Exposure
Method: a Lambertian mid-grey ground (ρ = 0.18) under the HDRI re-emits L_out = ρ·L̄; we want that to land
at 0.18 linear before ACES, so `toneMappingExposure = 1/L̄ = 0.656`. Treat as the starting point; ACES adds
its own shoulder. (For reference the belfast sunset HDR needed a much higher value — do not carry it over.)

### Colours (linear → sRGB at exposure 0.656)
- **fog colour** (horizon band): `#8F94A1` — cool grey-blue. `scene.fog = new THREE.FogExp2(0x8F94A1, 0.0022)`
- **sky tint** (above 20°): `#EDF2FC` — near-white. See the legibility note below before using it as-is.

### Ground material
`diff` colorSpace sRGB (`texture.encoding = THREE.sRGBEncoding` in r147); `nor_gl`, `rough` linear.
`wrapS/T = RepeatWrapping`; `repeat = terrainSize / 3.5` (one tile ≈ 3.5 m); `anisotropy = renderer.capabilities.getMaxAnisotropy()`.
`normalScale ≈ (0.8, 0.8)`; `roughness` map as-is; `metalness = 0`. Rocks: same material family, different repeat.
The 2k diffuse is worth it in the chase-cam view; the 1k normal/rough are fine.

## Recommendation on `scene.background` — read this before switching the HDRI on

**Use the HDRI for `scene.environment` (PBR lighting + reflections). Do NOT put it straight into `scene.background`
in `?view=env` at the physical exposure.** The measured daytime sky averages `#EDF2FC` — near-white — and the
satellite dome's additive glow sprites and thin LOS rays will disappear against it. The env view exists to show
satellites going dark over the vehicle; a sky that swallows them defeats the view.

Recommended: keep the procedural sky, tint it from the measured colours but **~2 stops darker** so it reads as
late afternoon over the same terrain and lighting — zenith `#5F7FB5`, horizon `#8F94A1` (the measured fog),
with the HDRI still doing the lighting. Dim the dome graticule to `#1C222A`-class hairlines, keep sprites
additive; they stay legible.

If you want the real sky visible anyway (it *is* the strongest Earth cue): `scene.background = hdrTexture`
(equirect mapping, r147 has no `backgroundIntensity`), then compensate: sprite size ×1.4, add a 1-px dark
outline to sprites and labels, LOS rays 2 px with a dark halo, graticule off. Verify a screenshot at 1920
with six excluded satellites before committing to it.

Either way the dome's N/E/S/W and the ground compass are unchanged — the sun placement above keeps shadows
consistent with the HDRI.

## Attribution (HUD corner line, replace the current one)
`Vehicle: 4x4 UGV, own CAD (Blender) · Sky, ground: Poly Haven, CC0`

## What a viewer should now perceive
Open temperate plains under a blue, partly-cloudy mid-morning sky, a dirt track through short dry grass and
scattered stones, long-ish shadows to the WNW. Northern Plains in late summer — not a desert, not Mars.
