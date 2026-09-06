/* RECON environment -- dusk hills, one ridge with a tree line (tracks/TRACK_F.md §2).
 *
 * Built against console/web/env/CONTRACT.md the same way logistics.js is. The
 * scout's loop sits on a flat bench (invariant 1: recon's corridor_half_width_m
 * is 100 m, so a 206 m band along the whole loop and the loop's interior are
 * one constant level); the ground falls away to a shallow valley south of the
 * bench and rises north of it to ONE ridge whose crest carries a tree line of
 * low-poly trunks + cones. Tall grass and scrub are instanced, flag-and-berm
 * mounds sit beside each OP outside its ring. Everything here is set dressing;
 * the console draws every measured thing (vehicle, ghosts, rings, posts).
 *
 * Sky: belfast_sunset_puresky (Poly Haven, CC0), vendored as /vendor/asset-sky.hdr
 * (TRACK_F.md §2 assigns it to RECON). Measured by helpers.loadHDR at load and
 * reported by the harness as status.hdr_measured = {sunAz 126.0, sunEl 2.6,
 * horizon #f9d4cc}; cross-checked with a pure-Python RGBE decode
 * (scratchpad/recon_sectors.py), 1024x512:
 *   brightest pixel (u 0.600, v 0.485) -> sun az 126.0 deg, el 2.6 deg
 *   (the HDR puts the low sun ESE, not west: r147 has no envmap rotation, so the
 *   key light follows the HDRI and long shadows fall toward the WNW);
 *   horizon band 0.5-4 deg, lum<4, all azimuths: linear (0.947, 0.664, 0.605)
 *   = #F9D4CC at unit exposure; sun sector +-45 deg (2.45, 1.50, 1.06) vs the
 *   anti-sun sector (0.33, 0.32, 0.41), so the haze is warm toward the sun and
 *   blue-grey away from it; glow band 6-14 deg (2.19, 1.82, 1.88); sky 14-30 deg
 *   (1.30, 1.20, 1.58); zenith 55-90 deg (0.85, 0.91, 1.42); cos-weighted
 *   upper-hemisphere mean radiance L = 1.08 -> physical exposure 1/L = 0.92
 *   (EARTH_BACKDROP.md method).
 * Colours below are those bands 2 stops under the HDR (linear / 4 -> sRGB), the
 * rule EARTH_BACKDROP.md sets so additive sprites stay legible: horizon #86716C
 * (= hdr_measured.horizon / 4), glow band #C3B4B6 as sky.mid, zenith #7F83A1.
 * fog.color == sky.horizon so the bench fades into the sky. Exposure 1.1 is
 * ~1.2x physical (logistics runs 2.3x its physical value at noon); the ground
 * lands about one stop under logistics, which is the point of dusk.
 *
 * Ground: brown_mud_leaves_01 (Poly Haven, CC0), 2k diffuse + 1k normal/rough,
 * fetched by bootstrap.sh from ASSETS.txt; falls back to the Earth set.
 */
(function () {
  const SPEC = {
    hdr: '/vendor/asset-sky.hdr',                         // belfast_sunset_puresky, lighting only (PMREM)
    exposure: 1.1,
    // Visible sky: the "forest" theatre rendered in Blender/Cycles (UGV sim asset pack): misty
    // jungle, far field only (a distant tree line in haze; our ridge and trees stay real geometry).
    // The HDR above still lights the scene. sun_u/sun_el measured in the image (glare centroid
    // u 0.633; el 34.0 = the pack's manifest, the disc is lost in the mist). dim 0.58: the pano is
    // near-white mist (10-25 deg band luminance 0.823) brought to the previous sky.mid target
    // (0.476) so sprites stay legible. Mid-morning mist replaces dusk: the key light rises to el 34.
    backdrop: { url: '/vendor/asset-backdrop-recon.jpg', sun_u: 0.633, sun_el: 34.0, dim: 0.58 },
    // Relief: the forest theatre's Blender height field (asset-relief-recon.js, +-6 m) at 0.7, added
    // under the module's own bench / valley / ridge, blended over 45 m from the corridor edge.
    relief: { id: 'recon', scale: 0.7, blend_m: 45 },
    // Set dressing from the pack's prototypes (asset-props-recon.glb): jungle trees (24-32 m), ferns and
    // tufts in a band beside the loop, rocks; the module's own ridge tree line stays.
    props: { url: '/vendor/asset-props-recon.glb', seed: 7, margin: 400, groups: [
      { proto: 'Tree', n: 200, scale: [0.75, 1.35], sink: 0.05, inset: true, keepOut: { corridor: 30, prop: 18 },   // 200 x ~1 k tris keeps the theatre under the 600 k budget with the ridge line
        tint: { low: [0.08, 0.06, 0.045], high: [0.045, 0.095, 0.028], split: [0.45, 0.60], radial: [0.25, 0.45] } },
      { proto: 'Fern', n: 400, region: 'near', near_m: 140, scale: [0.6, 1.7], sink: 0.15, castShadow: false, keepOut: { corridor: 6, prop: 8 },
        tint: { low: [0.035, 0.08, 0.02], high: [0.07, 0.14, 0.04], split: [0.1, 0.9], radial: [0.9, 1.0] } },
      { proto: 'Tuft', n: 500, region: 'near', near_m: 120, scale: [0.7, 1.9], castShadow: false, keepOut: { corridor: 4, prop: 6 },
        tint: { low: [0.05, 0.07, 0.025], jitter: 0.2 } },
      { proto: 'Rock', n: 120, scale: [0.5, 3.4], sink: 0.25, inset: true, color: '#34312E', keepOut: { corridor: 8, prop: 12 } },
    ] },
    fog: { color: '#9C9C9D', density: 0.0019 },           // ACES^-1 at exposure 1.1 of the backdrop's horizon as seen (#B2B2B3)
    sky: { horizon: '#B2B2B3', mid: '#B6B8BA', zenith: '#B4B7BB' },   // first paint and fallback: the backdrop's bands at dim 0.58 (uniform mist)
    sun: { az: 126.0, el: 2.6 },                          // az: measured from the HDR, the aim for the backdrop's sun; el from backdrop.sun_el at load
    palette: { ground: '#4A4633', rock: '#34312E', accent: '#7FA8CC' },   // olive-brown, dark rock, cold blue
    attribution: 'Ground: Poly Haven, CC0 · sky: rendered scene (Blender), lit by a Poly Haven HDR',
    // Harness convention (preview.html): the camera sits at compass bearing
    // (180 - az) from the route box centre, dist metres away at elevation el, and
    // OrbitControls aim it at the route start. az 294 -> camera ~(-40, -80) ENU,
    // 30 m up, SW of the patrol base looking NE over its berms and flag, the loop
    // beyond and the ridge's tree line as the skyline; the sun is camera-right.
    hero_camera: { az: 294, el: 7, dist: 255, dolly: 0.6 },
  };
  const GROUND = {
    diff: '/vendor/asset-env-recon-ground-diff.jpg',
    nor: '/vendor/asset-env-recon-ground-nor_gl.jpg',
    rough: '/vendor/asset-env-recon-ground-rough.jpg',
  };

  function build(ctx) {
    const T = ctx.THREE, H = ctx.helpers, m = ctx.mission, scene = ctx.scene;
    const hw = m.corridor_half_width_m, gs = m.ground_station;
    const noise = H.makeNoise(23), rnd = H.mulberry32(41);
    const aniso = ctx.renderer.capabilities.getMaxAnisotropy();
    const own = [];                                         // everything we must dispose

    // -- the ridge crest: a polyline ~190 m north of the loop's northern arc (ENU).
    //    Distance to it and the arc-length along it shape the ridge; the crest is
    //    where the tree line stands. ---------------------------------------------
    const CREST = [[-60, 175], [40, 218], [150, 255], [230, 275], [250, 290], [310, 310], [400, 292], [480, 258], [580, 200]];
    const crestL = [0]; for (let i = 1; i < CREST.length; i++) crestL.push(crestL[i-1] + Math.hypot(CREST[i][0]-CREST[i-1][0], CREST[i][1]-CREST[i-1][1]));
    const CREST_LEN = crestL[CREST.length - 1];
    const crestInfo = (e, n) => {                           // {d: distance m, t: arc length m, north: +1 if north of the crest}
      let best = null;
      for (let i = 0; i + 1 < CREST.length; i++) {
        const ae = CREST[i][0], an = CREST[i][1], de = CREST[i+1][0] - ae, dn = CREST[i+1][1] - an, L2 = de*de + dn*dn;
        let t = ((e - ae)*de + (n - an)*dn) / L2; t = Math.min(Math.max(t, 0), 1);
        const pe = ae + t*de, pn = an + t*dn, d = Math.hypot(e - pe, n - pn);
        if (best === null || d < best.d) best = {d, t: crestL[i] + t*Math.sqrt(L2), north: (de*(n - pn) - dn*(e - pe)) >= 0 ? 1 : -1};
      }
      return best;
    };
    const crestPoint = t => {                               // ENU point at arc length t along the crest, plus the unit normal to the north
      for (let i = 0; i + 1 < CREST.length; i++) {
        if (t <= crestL[i+1] || i === CREST.length - 2) {
          const u = Math.min(1, Math.max(0, (t - crestL[i]) / (crestL[i+1] - crestL[i])));
          const de = CREST[i+1][0] - CREST[i][0], dn = CREST[i+1][1] - CREST[i][1], L = Math.hypot(de, dn);
          return {e: CREST[i][0] + u*de, n: CREST[i][1] + u*dn, ne: -dn/L, nn: de/L};
        }
      }
    };
    const RIDGE_H = 26, RIDGE_W = 150;                      // crest height m, half-width to the foot m

    // -- terrain: rolling hills that grow with distance, a valley to the south, the
    //    ridge to the north; FLAT along the corridor (hw + 3), inside every prop's
    //    rim (+7) and around the ground station (20 m) -- contract invariant 1. ----
    const R = H.relief(SPEC);                               // Blender relief (spec.relief); at() is 0 without the asset
    const heightAt = (x, z) => {
      const n = -z;
      const lat = Math.abs(H.lateralOffset(x, n));
      const hills = (3.2*noise(x/120 + 3.1, z/120 + 1.7) + 1.1*noise(x/38, z/38) + 0.28*noise(x/9, z/9)) * (1 + 2.5*H.smooth(220, 520, lat));
      const valley = -9 * H.smooth(-150, -380, n);
      const c = crestInfo(x, n);
      const env = RIDGE_H * H.smooth(0, 140, c.t) * H.smooth(0, 140, CREST_LEN - c.t) * (0.85 + 0.15*noise(c.t/45, 0.5));
      const ridge = env * (1 - H.smooth(0, RIDGE_W, c.d)) * (1 + 0.12*noise(x/45 + 7, z/45));
      const clear = H.propClearance(x, n), gsd = Math.hypot(x - gs.e, n - gs.n);
      return (hills + valley + ridge) * H.smooth(hw + 3, hw + 45, lat) * H.smooth(7, 20, clear) * H.smooth(20, 34, gsd)
           + R.at(x, z) * H.smooth(hw + 3, hw + 3 + R.blend, lat) * H.smooth(7, 7 + R.blend*0.6, clear) * H.smooth(20, 44, gsd);
    };
    const ext = H.extent(450);
    const TW = ext.maxE - ext.minE, TD = ext.maxN - ext.minN, TCx = (ext.minE + ext.maxE)/2, TCz = -(ext.minN + ext.maxN)/2;
    const groundMat = H.antiTile(new T.MeshStandardMaterial({color: new T.Color(SPEC.palette.ground), roughness:1, metalness:0, envMapIntensity:1.0}));
    {
      const geo = new T.PlaneGeometry(TW, TD, Math.min(Math.round(TW/6), 180), Math.min(Math.round(TD/6), 180));
      const pa = geo.attributes.position;
      for (let i = 0; i < pa.count; i++) pa.setZ(i, heightAt(pa.getX(i) + TCx, -pa.getY(i) + TCz));
      geo.computeVertexNormals();
      const ground = new T.Mesh(geo, groundMat);
      ground.rotation.x = -Math.PI/2; ground.position.set(TCx, 0, TCz); ground.receiveShadow = true;
      ground.layers.enable(H.INSET_LAYER); scene.add(ground); own.push(ground);
    }

    // Merge a few primitives into one vertex-coloured triangle soup (one draw
    // call per kind through InstancedMesh). parts: [{g, c:[r,g,b], x, y, z, s}]
    // Every InstancedMesh below sets frustumCulled = false: r147 culls an
    // InstancedMesh on its one-instance geometry bounds at the origin, so a
    // chase camera that has left the base would drop the whole kind at once.
    const stack = parts => {
      const pos = [], nor = [], col = [];
      for (const p of parts) {
        const g = p.g.index ? p.g.toNonIndexed() : p.g.clone();
        if (p.s) g.scale(p.s[0], p.s[1], p.s[2]);
        g.translate(p.x || 0, p.y || 0, p.z || 0);
        const P = g.attributes.position, N = g.attributes.normal;
        for (let i = 0; i < P.count; i++) { pos.push(P.getX(i), P.getY(i), P.getZ(i)); nor.push(N.getX(i), N.getY(i), N.getZ(i)); col.push(p.c[0], p.c[1], p.c[2]); }
        g.dispose(); p.g.dispose();
      }
      const out = new T.BufferGeometry();
      out.setAttribute('position', new T.Float32BufferAttribute(pos, 3));
      out.setAttribute('normal', new T.Float32BufferAttribute(nor, 3));
      out.setAttribute('color', new T.Float32BufferAttribute(col, 3));
      return out;
    };
    const m4 = new T.Matrix4(), q = new T.Quaternion(), eu = new T.Euler(), pv = new T.Vector3(), sv = new T.Vector3(), cv = new T.Color();

    // -- rocks: instanced, seeded, dark; off the track and off the props ----------
    const rockMat = new T.MeshStandardMaterial({color: new T.Color(SPEC.palette.rock), roughness:1.0, metalness:0.0, envMapIntensity:0.6});
    {
      const rockGeo = new T.DodecahedronGeometry(1, 1);
      const pa = rockGeo.attributes.position, rv = new T.Vector3();
      for (let i = 0; i < pa.count; i++) { rv.fromBufferAttribute(pa, i);
        rv.multiplyScalar(1 + 0.30*noise(rv.x*1.7 + 5.2, rv.y*1.7 + rv.z*0.9)); pa.setXYZ(i, rv.x, rv.y*0.75, rv.z); }
      rockGeo.computeVertexNormals();
      const rocks = new T.InstancedMesh(rockGeo, rockMat, 240);
      let n = 0, tries = 0;
      while (n < 240 && tries++ < 10000) {
        const x = TCx + (rnd()-0.5)*(TW-300), z = TCz + (rnd()-0.5)*(TD-300);
        if (Math.abs(H.lateralOffset(x, -z)) < 25) continue;
        if (H.propClearance(x, -z) < 12) continue;
        if (rnd() > 0.3 + 0.7*(noise(x/40, z/40)+1)/2) continue;
        const s = 0.35 + rnd()*rnd()*1.6;
        eu.set(rnd()*Math.PI, rnd()*Math.PI, rnd()*Math.PI); q.setFromEuler(eu);
        sv.set(s*(0.8+rnd()*0.5), s*(0.55+rnd()*0.5), s*(0.8+rnd()*0.5));
        m4.compose(pv.set(x, heightAt(x, z) - 0.22*s, z), q, sv); rocks.setMatrixAt(n++, m4);
      }
      rocks.count = n; rocks.castShadow = true; rocks.receiveShadow = true; rocks.layers.enable(H.INSET_LAYER); rocks.frustumCulled = false;
      scene.add(rocks); own.push(rocks);
    }

    // -- tall grass: one InstancedMesh, five tapered blades per clump, vertex-tinted
    //    dark olive -> straw, swayed in the vertex shader (uTime). Kept off the
    //    track (8 m) and off prop rings. Not on the inset layer. -----------------
    const timeU = {value: 0};
    let grass = null; const GRASS_N = 7500;
    {
      const verts = [], cols = [];
      const blade = (phi, h, r) => {
        const lx = Math.cos(phi), lz = Math.sin(phi), wx = -Math.sin(phi), wz = Math.cos(phi);
        const bx = lx*r, bz = lz*r, wb = 0.055, wm = 0.035, ym = 0.55*h, lm = 0.10, lt = 0.30*h;
        const P = [[bx - wx*wb, 0, bz - wz*wb], [bx + wx*wb, 0, bz + wz*wb],
                   [bx + wx*wm + lx*lm, ym, bz + wz*wm + lz*lm], [bx - wx*wm + lx*lm, ym, bz - wz*wm + lz*lm],
                   [bx + lx*lt, h, bz + lz*lt]];
        const C = [[0.06, 0.07, 0.03], [0.06, 0.07, 0.03], [0.17, 0.17, 0.07], [0.17, 0.17, 0.07], [0.36, 0.31, 0.15]];
        for (const tri of [[0, 1, 2], [0, 2, 3], [3, 2, 4]]) for (const k of tri) { verts.push(...P[k]); cols.push(...C[k]); }
      };
      for (let k = 0; k < 6; k++) blade(k*1.0472 + 0.4, 0.8 + 0.2*Math.cos(k*2.1), 0.2);
      const g = new T.BufferGeometry();
      g.setAttribute('position', new T.Float32BufferAttribute(verts, 3));
      g.setAttribute('color', new T.Float32BufferAttribute(cols, 3));
      g.computeVertexNormals();
      const mat = new T.MeshStandardMaterial({vertexColors:true, roughness:1, metalness:0, side:T.DoubleSide});
      mat.onBeforeCompile = sh => {
        sh.uniforms.uTime = timeU;
        sh.vertexShader = sh.vertexShader
          .replace('#include <common>', '#include <common>\nuniform float uTime;')
          .replace('#include <begin_vertex>', '#include <begin_vertex>\n'
            + '#ifdef USE_INSTANCING\n vec2 ip = instanceMatrix[3].xz;\n#else\n vec2 ip = vec2(0.0);\n#endif\n'
            + 'float sw = sin(uTime*1.3 + ip.x*0.15 + ip.y*0.11)*0.6 + sin(uTime*2.3 + ip.x*0.31 - ip.y*0.07)*0.3;\n'
            + 'transformed.x += sw * 0.22 * position.y * position.y;\n');
      };
      grass = new T.InstancedMesh(g, mat, GRASS_N);
      let n = 0, tries = 0;
      while (n < GRASS_N && tries++ < GRASS_N*8) {
        let x, z;
        if (rnd() < 0.75) {                                // most clumps on and around the bench the loop sits on
          const p = H.routePoint(rnd()*H.routeLength()), a = rnd()*Math.PI*2, d = 8 + rnd()*rnd()*190;
          x = p.e + Math.sin(a)*d; z = -(p.n + Math.cos(a)*d);
        } else { x = TCx + (rnd()-0.5)*(TW-250); z = TCz + (rnd()-0.5)*(TD-250); }
        if (Math.abs(H.lateralOffset(x, -z)) < 8) continue;
        if (H.propClearance(x, -z) < 3) continue;
        if (rnd() > 0.35 + 0.65*(noise(x/30 + 9, z/30)+1)/2) continue;
        eu.set(0, rnd()*Math.PI*2, 0); q.setFromEuler(eu); const s = 0.8 + rnd()*0.9;
        sv.set(s, s*(0.85 + rnd()*0.7), s);
        m4.compose(pv.set(x, heightAt(x, z), z), q, sv); grass.setMatrixAt(n, m4);
        grass.setColorAt(n++, cv.setRGB(0.8 + rnd()*0.3, 0.85 + rnd()*0.25, 0.9 + rnd()*0.2));
      }
      grass.count = n; grass.castShadow = false; grass.receiveShadow = true; grass.frustumCulled = false;
      scene.add(grass); own.push(grass);
    }

    // -- scrub: low dark bushes, instanced, off the track (25 m) and off the props --
    {
      const lobe = (seed) => {                              // one lumpy lobe of a bush
        const g = new T.IcosahedronGeometry(1, 1), pa = g.attributes.position, rv = new T.Vector3();
        for (let i = 0; i < pa.count; i++) { rv.fromBufferAttribute(pa, i);
          rv.multiplyScalar(1 + 0.25*noise(rv.x*2.3 + seed, rv.y*2.3 + rv.z)); pa.setXYZ(i, rv.x, Math.max(rv.y, -0.2)*0.75, rv.z); }
        g.computeVertexNormals(); return g;
      };
      const g = stack([
        {g: lobe(1.1), c: [0.030, 0.052, 0.030], y: 0.55, s: [1.0, 1.0, 1.0]},
        {g: lobe(4.7), c: [0.036, 0.060, 0.034], x: 0.8, y: 0.4, z: 0.25, s: [0.8, 0.7, 0.8]},
      ]);
      const scrub = new T.InstancedMesh(g, new T.MeshStandardMaterial({vertexColors:true, roughness:1, metalness:0}), 480);
      let n = 0, tries = 0;
      while (n < 480 && tries++ < 8000) {
        let x, z;
        if (rnd() < 0.6) { const p = H.routePoint(rnd()*H.routeLength()), a = rnd()*Math.PI*2, d = 25 + rnd()*200;
          x = p.e + Math.sin(a)*d; z = -(p.n + Math.cos(a)*d); }
        else { x = TCx + (rnd()-0.5)*(TW-250); z = TCz + (rnd()-0.5)*(TD-250); }
        if (Math.abs(H.lateralOffset(x, -z)) < 25) continue;
        if (H.propClearance(x, -z) < 10) continue;
        if (rnd() > 0.25 + 0.75*(noise(x/55 + 3, z/55 + 8)+1)/2) continue;
        const s = 0.9 + rnd()*1.3;
        eu.set(0, rnd()*Math.PI*2, 0); q.setFromEuler(eu); sv.set(s*(0.9 + rnd()*0.5), s*(0.7 + rnd()*0.5), s*(0.9 + rnd()*0.5));
        m4.compose(pv.set(x, heightAt(x, z) - 0.15, z), q, sv); scrub.setMatrixAt(n, m4);
        scrub.setColorAt(n++, cv.setRGB(0.8 + rnd()*0.4, 0.85 + rnd()*0.3, 0.8 + rnd()*0.4));
      }
      scrub.count = n; scrub.castShadow = true; scrub.receiveShadow = true; scrub.frustumCulled = false;
      scene.add(scrub); own.push(scrub);
    }

    // -- tree line along the ridge crest: trunk + two cones merged, one InstancedMesh,
    //    scattered +-45 m about the crest with a thinner spill down the north flank.
    //    Static: on the inset layer. -------------------------------------------------
    {
      const treeGeo = stack([
        {g: new T.CylinderGeometry(0.22, 0.40, 3.0, 6, 1, true), c: [0.12, 0.08, 0.055], y: 1.5},
        {g: new T.ConeGeometry(2.5, 4.8, 7, 1, true), c: [0.030, 0.062, 0.046], y: 5.3},
        {g: new T.ConeGeometry(1.7, 3.8, 7, 1, true), c: [0.036, 0.070, 0.050], y: 8.4},
      ]);
      const TREE_N = 950;
      const trees = new T.InstancedMesh(treeGeo, new T.MeshStandardMaterial({vertexColors:true, roughness:1, metalness:0}), TREE_N);
      let n = 0;
      const plant = (e, nn) => {
        const x = e, z = -nn;
        if (Math.abs(H.lateralOffset(x, nn)) < hw + 15 || H.propClearance(x, nn) < 20) return;
        const s = 0.75 + rnd()*0.6;
        eu.set(0, rnd()*Math.PI*2, 0); q.setFromEuler(eu); sv.set(s*(0.85 + rnd()*0.3), s, s*(0.85 + rnd()*0.3));
        m4.compose(pv.set(x, heightAt(x, z) - 0.25, z), q, sv); trees.setMatrixAt(n, m4);
        trees.setColorAt(n++, cv.setRGB(0.8 + rnd()*0.4, 0.85 + rnd()*0.35, 0.85 + rnd()*0.35));
      };
      for (let t = 20; t < CREST_LEN - 20 && n < TREE_N; t += 2.2 + rnd()*1.8) {
        const p = crestPoint(t), off = (rnd() + rnd() + rnd() - 1.5) * 30;          // ~normal, sd ~ 15 m
        if (rnd() < 0.6 + 0.4*(noise(t/70, 2.5)+1)/2) plant(p.e + p.ne*off, p.n + p.nn*off);
        if (n < TREE_N && rnd() < 0.35) { const o2 = 40 + rnd()*75; plant(p.e + p.ne*o2, p.n + p.nn*o2); }   // north-flank spill
      }
      trees.count = n; trees.castShadow = true; trees.receiveShadow = true; trees.layers.enable(H.INSET_LAYER); trees.frustumCulled = false;
      scene.add(trees); own.push(trees);
    }

    // -- set dressing at the OPs and the base: three berm mounds in an arc on the
    //    north (observed) side of each ring, outside it (r + 1 .. r + 12), and one
    //    pole with a small cold-blue pennant on the middle mound (the base flies one
    //    too, which also puts a flag in the harness's top/chase cameras). Never inside the
    //    ring; the console draws the ring, post and mast. Static: inset layer. ----
    {
      const dressed = m.props.filter(p => ['obs_point', 'observation_post', 'patrol_base'].includes(p.kind));
      const berms = new T.InstancedMesh(new T.SphereGeometry(1, 10, 7),
        new T.MeshStandardMaterial({color: new T.Color('#4A4436'), roughness:1, metalness:0}), dressed.length*3);
      const flagGeo = stack([
        {g: new T.CylinderGeometry(0.04, 0.05, 4.2, 5, 1, true), c: [0.35, 0.35, 0.33], y: 2.1},
        {g: (() => { const g = new T.BufferGeometry();
          g.setAttribute('position', new T.Float32BufferAttribute([0, 4.2, 0, 0.95, 4.02, 0, 0, 3.75, 0,  0, 4.2, 0, 0, 3.75, 0, 0.95, 4.02, 0], 3));
          g.computeVertexNormals(); return g; })(), c: [0.22, 0.40, 0.62], y: 0},
      ]);
      const flags = new T.InstancedMesh(flagGeo, new T.MeshStandardMaterial({vertexColors:true, roughness:0.8, metalness:0.1, side:T.DoubleSide}), dressed.length);
      let nb = 0, nf = 0;
      for (const p of dressed) {
        const r = (p.radius_m || 15) + 6.5;
        for (const a of [-0.55, 0, 0.55]) {
          const e = p.e + Math.sin(a)*r, nn = p.n + Math.cos(a)*r, x = e, z = -nn;
          eu.set(0, -a, 0); q.setFromEuler(eu); sv.set(5.0 + rnd(), 1.15 + rnd()*0.3, 2.4 + rnd()*0.4);
          m4.compose(pv.set(x, heightAt(x, z) - 0.35, z), q, sv); berms.setMatrixAt(nb++, m4);
          if (a === 0) {
            eu.set(0, rnd()*Math.PI*2, 0); q.setFromEuler(eu); sv.set(1, 1, 1);
            m4.compose(pv.set(x, heightAt(x, z) + 1.0, z), q, sv); flags.setMatrixAt(nf++, m4);
          }
        }
      }
      berms.count = nb; berms.castShadow = berms.receiveShadow = true; berms.layers.enable(H.INSET_LAYER); berms.frustumCulled = false;
      flags.count = nf; flags.castShadow = true; flags.layers.enable(H.INSET_LAYER); flags.frustumCulled = false;
      scene.add(berms); scene.add(flags); own.push(berms, flags);
    }

    // -- textures: the recon ground set, else the Earth set, else flat colour;
    //    report either way (contract invariant 7) ------------------------------------
    ctx.report({ground:'loading', vegetation:'loaded'});
    const loadSet = (set, tile, tint) => Promise.all([
      H.loadTexture(set.diff, true, 1, aniso), H.loadTexture(set.nor, false, 1, aniso), H.loadTexture(set.rough, false, 1, aniso),
    ]).then(([map, nor, rough]) => {
      for (const t of [map, nor, rough]) { t.repeat.set(TW/tile, TD/tile); t.needsUpdate = true; own.push(t); }
      Object.assign(groundMat, {map, normalMap:nor, roughnessMap:rough, color:new T.Color(tint), envMapIntensity:1.0});
      groundMat.normalScale.set(0.9, 0.9); groundMat.needsUpdate = true;
    });
    loadSet(GROUND, 3.0, 0xCFD4C8).then(() => ctx.report({ground:'loaded'}))
      .catch(() => loadSet(H.FALLBACK.ground, 3.5, 0xB9BDB0).then(() => ctx.report({ground:'fallback', note:'recon ground set missing; Earth set (grass_path_2) in its place'}))
        .catch(() => ctx.report({ground:'fallback', note:'ground textures failed; flat colour'})));

    H.scatterProps(ctx, heightAt, SPEC.props, own);       // async set dressing; meshes join `own`

    return {
      heightAt,
      bounds: {minX: ext.minE, maxX: ext.maxE, minZ: -ext.maxN, maxZ: -ext.minN},
      sunAz: SPEC.sun.az, sunEl: SPEC.sun.el,
      tick(dt) { timeU.value += dt; },
      dispose() {
        for (const o of own) {
          if (o.isTexture) { o.dispose(); continue; }
          scene.remove(o);
          if (o.geometry) o.geometry.dispose();
          if (o.material) (Array.isArray(o.material) ? o.material : [o.material]).forEach(mm => mm.dispose());
        }
      },
    };
  }

  window.ARBITRAS_ENV = window.ARBITRAS_ENV || {};
  window.ARBITRAS_ENV.recon = { id: 'recon', spec: SPEC, build };
})();
