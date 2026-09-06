# Mission overlays -- the contract (Track F)

A mission's correction mechanism has a backend half (a post-pass over the
stream that adds an additive block, `backend/missions.py register_augment`)
and a console half: an overlay module that draws that block and any
mission-specific HUD, `console/web/overlays/<mission>.js`. Classic script:

```js
(function () {
  window.ARBITRAS_OVERLAY = window.ARBITRAS_OVERLAY || {};
  window.ARBITRAS_OVERLAY.recon = { id: 'recon', mount: mount };
})();
```

The console calls `mount(ctx)` once when the 3D scene for that mission is
built, then `handle.epoch(payload, frame)` on every stream epoch and
`handle.tick(dt, cam)` every animation frame. It draws all the measured
things itself (true vehicle, believed ghost, satellites, corridor, props);
the overlay draws ONLY what its mechanism adds, and every number it shows
carries a contract path.

## `mount(ctx)`

| field | what |
|---|---|
| `THREE` | three.js r147 namespace |
| `scene` | the console's scene; add your objects to it; frame x = East, y = Up, z = South |
| `mission` | the `/mission?name=` object (route, props, gain, behaviour, attack, beats, scene) |
| `helpers` | `ARBITRAS_HELPERS.forMission(mission)`: `toScene(e,n,y)`, `routePoint(s)`, `lateralOffset(e,n)`, `azel`, `INSET_LAYER`, ... |
| `heightAt(x, z)` | terrain height in scene frame (from the environment module) |
| `latLonToEnu(lat, lon)` / `enuToLatLon(e, n)` | route.js conversions bound to the mission |
| `labels` | an absolutely positioned HTML layer you may append `div.skl`-styled labels to; position them with `project` |
| `project(v3)` | scene point -> `[x, y, visible]` in CSS px of the pane |
| `colors` | `{truth:'#7FA8CC', believed:'#E4551F', corrected:'#6FCF97', amber:'#E8A21C', red:'#D62119', dim:'#8794A2'}` |
| `glowTexture()` | the console's sprite texture |
| `report(note)` | a short status string shown in the scene key line |

Return a handle:

```js
return {
  epoch(payload, frame) {},   // REQUIRED. payload = the SSE decision object; frame = {truePos, ghostPos, D, Dc, sCur, headingAz, state}
  tick(dt, cam) {},           // optional, cheap
  hud() {},                   // optional -> {title, lines:[{k, v, path}]} rendered in the strip's MISSION cell (max 5 lines)
  drive(dt, st) {},           // optional (takeover only): -> {e, n, headingAz} to move the TRUE vehicle in the presentation frame, or null
  dispose() {},
};
```

`payload` is what `/events` streams: `state`, `previous_state`, `reason`,
`confidence`, `credential_status`, `features`, `geometry` (with
`correction`, and any mission block the backend post-pass added, e.g.
`geometry.correction.stationary`), `terrain` (when the channel is on, with
any `terrain.route_fix` the post-pass added), `position`, `_truth`,
`explanation`, `epoch_index`, `advisory`.

`frame` is the console's presentation-frame state at that epoch:
`truePos` (scene `Vector3` of the TRUE vehicle), `ghostPos` (BELIEVED,
scene), `D` (`{e, n, mag}` metres, measured), `Dc` (corrected displacement
or null), `sCur` (arc length), `headingAz` (compass degrees), `state`.

## Rules

1. **Every number you draw has a path.** Label text like `ANCHOR · 1.2 M ·
   geometry.correction.stationary.agreement_m`. No derived numbers the
   backend did not emit, except pure frame conversions (ENU of a lat/lon).
2. **Nothing that commands motion.** `drive()` exists only for the operator
   takeover overlay, where a human's input moves the vehicle in the
   presentation frame; a mechanism overlay never moves the vehicle.
3. **Own your objects; dispose them.** Do not touch objects you did not add.
4. **Captions.** Anything scripted or simulated says so in its label
   (`SENSOR · SIMULATED`, `preview · scripted`).
5. **Cheap.** `epoch()` may allocate; `tick()` must not.

## Verifying

Start a private server (`python -m console.server --port 84xx`), open
`/?mission=<name>&post=0&shadow=0&env=0&rate=40` headless (venv Playwright,
launch args `--use-gl=angle --use-angle=swiftshader --enable-unsafe-swiftshader
--ignore-gpu-blocklist`), wait for the epochs you care about (`?rate=` sets
epochs per second; the replay ends with `replay complete`), screenshot, and
read `window.__envInfo()` (the console's audit hook; `overlay` in it reports
`handle.status()` if you provide one). Look at the screenshot.
