/* COMBAT environment -- dawn over broken ground (tracks/TRACK_F.md §2,
 * tracks/TRACK_F_COMBAT.md "Presentation frame", console/web/env/CONTRACT.md).
 *
 * Blue hour with the sun just on the horizon: cold shadows, a warm dust haze
 * at the horizon. The 933 m route runs ENE from the line of departure across
 * dry cracked earth broken by berms (low linear mounds) and shell-scrape
 * depressions, toward a rise beyond OBJ HAWK carrying a walled compound of
 * low buildings. Everything here is set dressing: the console draws the phase
 * lines, their posts, the objective ring and every measured thing on top.
 *
 * Assets (Poly Haven, CC0; fetched by bootstrap.sh from env/ASSETS.txt):
 *   asset-env-combat-sky.hdr      kiara_2_sunrise 2k   -- lighting (PMREM), never the background
 *   asset-env-combat-ground-*.jpg dry_ground_01 2k diffuse, 1k normal (GL +Y), 1k roughness
 * kiara_1_dawn was measured first and rejected: its sun is still below the
 * horizon, so the HDR's brightest pixel (which helpers.loadHDR aims the key
 * light at) is a lit cloud at el 44 / az 31 -- mid-morning shadows on a dawn
 * scene. kiara_2_sunrise has the sun disc on the horizon.
 *
 * Measured from asset-env-combat-sky.hdr (pure-Python RGBE decode, the
 * EARTH_BACKDROP.md method; helpers.loadHDR agrees at load):
 *   sun          brightest pixel (u 0.6165, v 0.4771) -> az 131.9 (SE), el 4.1
 *   horizon band (0.5-4 deg, lum < 4) mean linear RGB (0.359, 0.299, 0.204)
 *   sky 8-18 deg  linear (0.29, 0.34, 0.50); sky 40-80 deg linear (0.19, 0.24, 0.42)
 *   cos-weighted upper-hemisphere mean radiance 0.725 (1/L = 1.38)
 * Fog is the horizon band through ACES at spec.exposure; the procedural sky
 * keeps that colour at the horizon and is ~2 stops under the HDR above it so
 * the console's sprites stay legible (EARTH_BACKDROP.md).
 */
(function () {
  const SPEC = {
    hdr: '/vendor/asset-env-combat-sky.hdr',
    exposure: 1.6,
    // Visible sky: the "dunes" theatre rendered in Blender/Cycles (UGV sim asset pack): a sand sea
    // at dawn, far field only (our berms, scrapes and the compound stay real geometry). The HDR
    // above still lights the scene. sun_u/sun_el measured in the image (u 0.125, el 12.9; the pack's
    // manifest agrees). dim 1.0: the pano's 10-25 deg band (0.362) already sits under the previous
    // sky.mid target (0.448), so it is shown as rendered. The sun rises from el 4 to el 13.
    backdrop: { url: '/vendor/asset-backdrop-combat.jpg', sun_u: 0.125, sun_el: 12.9, dim: 1.0 },
    // Relief: the dunes theatre's Blender height field (asset-relief-combat.js, -6..+14 m) at 0.55 under
    // the berms and scrapes, blended over 90 m from the corridor edge so the dunes rise gently.
    relief: { id: 'combat', scale: 0.8, blend_m: 90 },
    // Set dressing from the pack's prototypes (asset-props-combat.glb): rocks and dead tufts. A sand sea
    // is meant to be empty; the relief carries this theatre.
    props: { url: '/vendor/asset-props-combat.glb', seed: 13, margin: 420, groups: [
      { proto: 'Rock', n: 180, scale: [0.5, 3.3], sink: 0.25, inset: true, color: '#6E655A', keepOut: { corridor: 8, prop: 12 } },
      { proto: 'Tuft', n: 500, region: 'near', near_m: 200, scale: [0.7, 1.9], castShadow: false, keepOut: { corridor: 4, prop: 6 },
        tint: { low: [0.20, 0.15, 0.08], jitter: 0.25 } },
    ] },
    // fog is mixed before tone mapping: this is ACES^-1 at exposure 1.6 of the backdrop's horizon
    // band as seen (#A79D92), so the cracked earth fades into the pano without a seam.
    fog: { color: '#7A7269', density: 0.0015 },
    // The gradient sphere (first paint, fallback) is not tone-mapped: the backdrop's own bands.
    sky: { horizon: '#A79D92', mid: '#92A4B6', zenith: '#64758C' },
    sun: { az: 131.9, el: 4.1 },                            // az: the HDR's sun (SE), the aim for the backdrop's sun; el from backdrop.sun_el at load
    palette: { ground: '#8C7C63', rock: '#6E655A', accent: '#E4551F' },
    attribution: 'Ground: Poly Haven, CC0 · sky: rendered scene (Blender), lit by a Poly Haven HDR',
    // Harness convention: the camera sits at compass bearing (180 - az) from the theatre centre and
    // is aimed by the harness. az 288 / dist 543 puts it ~120 m behind the line of departure, 40 m
    // up, so LD, the advance and the objective on its rise all lie ahead of it; sun to the right.
    hero_camera: { az: 288, el: 4, dist: 543, dolly: 0.6 },
  };

  function build(ctx) {
    const T = ctx.THREE, H = ctx.helpers, m = ctx.mission, scene = ctx.scene;
    const hw = m.corridor_half_width_m, gs = m.ground_station;
    const noise = H.makeNoise(23), rnd = H.mulberry32(41);
    const aniso = ctx.renderer.capabilities.getMaxAnisotropy();
    const own = [];                                         // everything we must dispose

    // -- frame: where the objective is and which way the advance is heading ------
    const route = m.route, last = route[route.length - 1], prev = route[route.length - 2];
    let hdE = last.e - prev.e, hdN = last.n - prev.n; { const l = Math.hypot(hdE, hdN) || 1; hdE /= l; hdN /= l; }
    const obj = m.props.find(p => p.kind === 'objective') || { e: last.e, n: last.n, radius_m: 60 };
    const objR = obj.radius_m || 60;
    // The rise: a cosine dome centred beyond the objective along the axis of
    // advance; the compound sits on its crown, outside the ring's flat pad.
    const HILL = { x: obj.e + hdE * (objR + 120), z: -(obj.n + hdN * (objR + 120)), R: 300, h: 20 };

    // Route-box in scene metres (x = e, z = -n): the region that gets fine ground cells.
    const es = route.map(w => w.e).concat(m.props.map(p => p.e), [gs.e, HILL.x - 60, HILL.x + 60]);
    const ns = route.map(w => w.n).concat(m.props.map(p => p.n), [gs.n, -HILL.z - 60, -HILL.z + 60]);
    const FINE = { minX: Math.min(...es) - 80, maxX: Math.max(...es) + 80, minZ: -Math.max(...ns) - 80, maxZ: -Math.min(...ns) + 80 };

    // Distance to the nearest phase line (the console draws them as posts + a
    // dashed line through the prop, perpendicular to the route): scrub and
    // rocks keep clear of them so the line is never obscured.
    const phaseLines = m.props.filter(p => p.kind === 'phase_line').map(p => {
      let best = null;
      for (let i = 0; i + 1 < route.length; i++) {
        const de = route[i + 1].e - route[i].e, dn = route[i + 1].n - route[i].n, L2 = de * de + dn * dn || 1;
        let t = ((p.e - route[i].e) * de + (p.n - route[i].n) * dn) / L2; t = Math.min(1, Math.max(0, t));
        const d = Math.hypot(p.e - route[i].e - t * de, p.n - route[i].n - t * dn);
        if (!best || d < best.d) best = { d, ux: de / Math.sqrt(L2), un: dn / Math.sqrt(L2) };
      }
      return { e: p.e, n: p.n, pe: -best.un, pn: best.ux, r: p.radius_m || 80 };
    });
    const phaseLineDist = (e, n) => {
      let best = Infinity;
      for (const L of phaseLines) {
        const t = Math.max(-L.r, Math.min(L.r, (e - L.e) * L.pe + (n - L.n) * L.pn));
        best = Math.min(best, Math.hypot(e - (L.e + t * L.pe), n - (L.n + t * L.pn)));
      }
      return best;
    };

    // -- berms and shell scrapes: analytic terms of heightAt, seeded, kept out
    //    of every flat zone so nothing is clipped by the flattening. -----------
    const inBox = (x, z, pad) => x > FINE.minX + pad && x < FINE.maxX - pad && z > FINE.minZ + pad && z < FINE.maxZ - pad;
    const clearOf = (x, z, lat, clr) => Math.abs(H.lateralOffset(x, -z)) > hw + lat && H.propClearance(x, -z) > clr
                                          && Math.hypot(x - gs.e, -z - gs.n) > 36 + clr;
    const BERMS = [], SCRAPES = [];
    {
      // Berms run roughly across the axis of advance (dug facing the objective).
      const across = Math.atan2(-hdN, hdE) + Math.PI / 2;        // scene-frame angle of the perpendicular
      let tries = 0;
      while (BERMS.length < 26 && tries++ < 6000) {
        const x = FINE.minX + rnd() * (FINE.maxX - FINE.minX), z = FINE.minZ + rnd() * (FINE.maxZ - FINE.minZ);
        const len = BERMS.length < 4 ? 70 + rnd() * 50 : 22 + rnd() * 40;
        const a = across + (rnd() - 0.5) * 1.1, c = Math.cos(a), s = Math.sin(a);
        const x0 = x - c * len / 2, z0 = z - s * len / 2, x1 = x + c * len / 2, z1 = z + s * len / 2;
        const w = 4 + rnd() * 3;                              // half-width: 8-14 m across, 2-4 ground cells
        if (!inBox(x, z, 40)) continue;
        let ok = true;
        for (const [px, pz] of [[x0, z0], [x, z], [x1, z1]]) if (!clearOf(px, pz, 20 + w, 26 + w)) { ok = false; break; }
        if (!ok) continue;
        if (Math.hypot(x - HILL.x, z - HILL.z) < 130) continue;
        if (BERMS.some(b => Math.hypot(b.cx - x, b.cz - z) < 55)) continue;
        BERMS.push({ x0, z0, x1, z1, cx: x, cz: z, w, h: 1.5 + rnd() * 1.5 });
      }
      tries = 0;
      while (SCRAPES.length < 44 && tries++ < 6000) {
        // half of them cluster on the approach between PL RED and the objective
        const cl = SCRAPES.length % 2 === 0;
        const x = cl ? obj.e - 120 - rnd() * 360 : FINE.minX + rnd() * (FINE.maxX - FINE.minX);
        const z = cl ? -obj.n + 60 - rnd() * 260 : FINE.minZ + rnd() * (FINE.maxZ - FINE.minZ);
        const r = 3.5 + rnd() * 3.5;                          // 7-14 m across, 2-4 ground cells
        if (!inBox(x, z, 30) || !clearOf(x, z, 12 + r * 1.4, 12 + r * 1.4)) continue;
        if (BERMS.some(b => Math.hypot(b.cx - x, b.cz - z) < 40)) continue;
        if (SCRAPES.some(s => Math.hypot(s.x - x, s.z - z) < 20)) continue;
        SCRAPES.push({ x, z, r, d: 0.7 + rnd() * 0.6 });
      }
    }
    const segDist = (x, z, b) => {
      const dx = b.x1 - b.x0, dz = b.z1 - b.z0, L2 = dx * dx + dz * dz || 1;
      let t = ((x - b.x0) * dx + (z - b.z0) * dz) / L2; t = Math.min(1, Math.max(0, t));
      return Math.hypot(x - b.x0 - t * dx, z - b.z0 - t * dz);
    };

    // -- heightAt: broken ground + berms + scrapes + the rise, FLAT along the
    //    corridor, inside every prop's rim and under the ground station
    //    (contract invariant 1). The flattening uses helpers.lateralOffset and
    //    helpers.smooth exactly as the logistics reference does; the rise takes
    //    a wider transition so it climbs gently from the objective's pad. -------
    const R = H.relief(SPEC);                               // Blender relief (spec.relief); at() is 0 without the asset
    const heightAt = (x, z) => {
      const e = x, n = -z;
      const lat = Math.abs(H.lateralOffset(e, n)), clear = H.propClearance(e, n);
      const fGs = H.smooth(20, 36, Math.hypot(e - gs.e, n - gs.n));
      const fNear = H.smooth(hw + 3, hw + 22, lat) * H.smooth(7, 32, clear) * fGs;   // flat by hw+3 / r+7; eased, not rimmed
      if (fNear <= 0) return 0;
      let base = 1.6 * noise(x / 64 + 3.1, z / 64 + 1.7) + 0.55 * noise(x / 19, z / 19) + 0.16 * noise(x / 5.5, z / 5.5);
      for (const b of BERMS) {
        if (Math.abs(x - b.cx) > 80 || Math.abs(z - b.cz) > 80) continue;
        const d = segDist(x, z, b); if (d >= b.w) continue;
        const t = 1 - (d / b.w) * (d / b.w); base += b.h * t * t;
      }
      for (const s of SCRAPES) {
        const lim = s.r * 1.35; if (Math.abs(x - s.x) > lim || Math.abs(z - s.z) > lim) continue;
        const t = Math.hypot(x - s.x, z - s.z) / s.r; if (t >= 1.35) continue;
        base += t < 1 ? -s.d * (1 - t * t) : s.d * 0.3 * Math.sin(Math.PI * (t - 1) / 0.35);   // bowl, then a spoil lip
      }
      let h = base * fNear
            + R.at(x, z) * H.smooth(hw + 3, hw + 3 + R.blend, lat) * H.smooth(7, 7 + R.blend * 0.6, clear) * fGs;
      const dh = Math.hypot(x - HILL.x, z - HILL.z);
      if (dh < HILL.R) h += HILL.h * 0.5 * (1 + Math.cos(Math.PI * dh / HILL.R))
                          * H.smooth(hw + 3, hw + 80, lat) * H.smooth(7, 120, clear) * fGs;   // climbs ~10 deg from the pad
      return h;
    };

    // -- ground: one graded grid, 3.75 m cells over the route box (berms and
    //    scrapes are sized to resolve on them), growing geometrically to the
    //    500 m margin. ~140 k triangles: the console's SSAO composer renders the
    //    scene three times, so the 600 k budget is ~175 k of base geometry.
    //    Vertex colour and a UV warp break the tile repeat: dust-pale on crests,
    //    dark in scrapes. -----------------------------------------------------------
    const ext = H.extent(500);
    const groundMat = H.antiTile(new T.MeshStandardMaterial({ color: new T.Color(SPEC.palette.ground), vertexColors: true, roughness: 1, metalness: 0, envMapIntensity: 1.0 }));
    {
      const axis = (lo, hi, flo, fhi, step) => {
        const pre = []; let v = flo, s = step * 1.4;
        while (v - s > lo) { v -= s; pre.push(v); s *= 1.4; } pre.push(lo); pre.reverse();
        const out = pre.slice(); for (let u = flo; u < fhi - 1e-6; u += step) out.push(u); out.push(fhi);
        v = fhi; s = step * 1.4; while (v + s < hi) { v += s; out.push(v); s *= 1.4; } out.push(hi);
        return out;
      };
      const CELL = 3.75;
      const xs = axis(ext.minE, ext.maxE, FINE.minX, FINE.maxX, CELL), zs = axis(-ext.maxN, -ext.minN, FINE.minZ, FINE.maxZ, CELL);
      const W = xs.length, D = zs.length, N = W * D, TILE = 4.0;
      const pos = new Float32Array(N * 3), uv = new Float32Array(N * 2), col = new Float32Array(N * 3);
      for (let j = 0, k = 0; j < D; j++) for (let i = 0; i < W; i++, k++) {
        const x = xs[i], z = zs[j], y = heightAt(x, z);
        pos[3 * k] = x; pos[3 * k + 1] = y; pos[3 * k + 2] = z;
        // UVs in tiles, warped by slow noise (< ~10 % local stretch) so the 4 m repeat never lines up in rows
        uv[2 * k] = x / TILE + 0.45 * noise(x / 27 + 3, z / 27 + 8); uv[2 * k + 1] = -z / TILE + 0.45 * noise(x / 31 + 5, z / 31 + 2);
        let c = 1 + 0.09 * noise(x / 140 + 11, z / 140 + 5) + 0.14 * noise(x / 38 + 7, z / 38) + 0.07 * noise(x / 9, z / 9 + 4);
        if (y > 0.5) c += Math.min(0.12, 0.05 * (y - 0.5));          // dust-pale crests and the rise
        if (y < -0.15) c -= Math.min(0.18, 0.3 * (-0.15 - y));       // darker in the scrapes
        c = Math.min(1.24, Math.max(0.64, c));
        col[3 * k] = c; col[3 * k + 1] = c * 0.975; col[3 * k + 2] = c * 0.93;
      }
      const idx = new Uint32Array((W - 1) * (D - 1) * 6);
      for (let j = 0, k = 0; j < D - 1; j++) for (let i = 0; i < W - 1; i++) {
        const a = j * W + i, b = a + W, c = a + 1, d = b + 1;
        idx[k++] = a; idx[k++] = b; idx[k++] = c; idx[k++] = b; idx[k++] = d; idx[k++] = c;
      }
      const geo = new T.BufferGeometry();
      geo.setAttribute('position', new T.BufferAttribute(pos, 3)); geo.setAttribute('uv', new T.BufferAttribute(uv, 2));
      geo.setAttribute('color', new T.BufferAttribute(col, 3)); geo.setIndex(new T.BufferAttribute(idx, 1));
      geo.computeVertexNormals();
      const ground = new T.Mesh(geo, groundMat);
      ground.receiveShadow = true; ground.layers.enable(H.INSET_LAYER); scene.add(ground); own.push(ground);
    }

    // -- merged boxes: one BufferGeometry per group (compound, sandbags) so each
    //    is a single draw call. yaw: local +x -> (cos yaw, 0, -sin yaw). ----------
    const mergeBoxes = items => {
      const P = [], Nn = [], U = [], C = [], m4 = new T.Matrix4(), q = new T.Quaternion(), e3 = new T.Euler(), v = new T.Vector3(), nm = new T.Matrix3();
      for (const it of items) {
        const g = new T.BoxGeometry(it.w, it.h, it.d).toNonIndexed();
        e3.set(0, it.yaw || 0, 0); q.setFromEuler(e3); m4.compose(v.set(it.x, it.y, it.z), q, new T.Vector3(1, 1, 1)); nm.getNormalMatrix(m4);
        const p = g.attributes.position, n = g.attributes.normal, u = g.attributes.uv, cc = new T.Color(it.color);
        for (let i = 0; i < p.count; i++) {
          v.fromBufferAttribute(p, i).applyMatrix4(m4); P.push(v.x, v.y, v.z);
          v.fromBufferAttribute(n, i).applyMatrix3(nm).normalize(); Nn.push(v.x, v.y, v.z);
          U.push(u.getX(i), u.getY(i)); C.push(cc.r, cc.g, cc.b);
        }
        g.dispose();
      }
      const geo = new T.BufferGeometry();
      geo.setAttribute('position', new T.Float32BufferAttribute(P, 3)); geo.setAttribute('normal', new T.Float32BufferAttribute(Nn, 3));
      geo.setAttribute('uv', new T.Float32BufferAttribute(U, 2)); geo.setAttribute('color', new T.Float32BufferAttribute(C, 3));
      return geo;
    };
    const addStatic = (geo, mat) => { const o = new T.Mesh(geo, mat); o.castShadow = o.receiveShadow = true; o.layers.enable(H.INSET_LAYER); scene.add(o); own.push(o); return o; };
    const jitter = (hex, amt) => { const c = new T.Color(hex); const k = 1 + (rnd() - 0.5) * amt; c.r *= k; c.g *= k; c.b *= k; return c.getHex(); };

    // -- the compound on the rise: a walled yard with a gate facing the objective,
    //    four low buildings and a corner tower. Outside the ring, obviously "the
    //    objective". Local frame: u = right, v = toward the objective. -----------
    {
      const fx = -hdE, fz = hdN;                             // scene-frame direction from the compound toward the objective
      const yaw = Math.atan2(fx, fz), rx = fz, rz = -fx;     // rotation.y that maps local +z to (fx, fz); local +x -> (rx, rz)
      const world = (u, v) => [HILL.x + u * rx + v * fx, HILL.z + u * rz + v * fz];
      const boxes = [];
      const wall = (u0, v0, u1, v1) => {                    // a wall piece between two local points, base fitted to the ground
        const [ax, az] = world(u0, v0), [bx, bz] = world(u1, v1), len = Math.hypot(u1 - u0, v1 - v0);
        let lo = Infinity, hi = -Infinity;
        for (let t = 0; t <= 1; t += 0.25) { const y = heightAt(ax + (bx - ax) * t, az + (bz - az) * t); lo = Math.min(lo, y); hi = Math.max(hi, y); }
        const h = 2.4 + (hi - lo);
        boxes.push({ w: len, h, d: 0.5, x: (ax + bx) / 2, y: lo - 0.2 + h / 2, z: (az + bz) / 2,
                     yaw: Math.atan2(-(bz - az), bx - ax), color: jitter('#A99B83', 0.08) });
      };
      const HU = 32, HV = 24, GATE = 3.5;
      wall(-HU, -HV, HU, -HV); wall(-HU, -HV, -HU, HV); wall(HU, -HV, HU, HV);
      wall(-HU, HV, -GATE, HV); wall(GATE, HV, HU, HV);
      const bld = (u, v, w, d, h, dyaw, color) => {
        const [x, z] = world(u, v); const y = heightAt(x, z);
        boxes.push({ w, h: h + 0.5, d, x, y: y - 0.5 + (h + 0.5) / 2, z, yaw: yaw + dyaw, color: jitter(color, 0.06) });
      };
      bld(-15, -9, 14, 9, 4.2, 0, '#B7AA92'); bld(14, -11, 10, 8, 3.6, 0.06, '#B2A58C');
      bld(16, 8, 9, 7, 3.2, -0.14, '#BBAE96'); bld(-13, 9, 12, 7, 3.8, 0.09, '#B5A88F');
      bld(26.5, -18.5, 4, 4, 7.5, 0, '#9E927B');            // corner tower
      bld(-24, 2, 3, 3, 1.6, 0.3, '#8F8570'); bld(6, -18, 5, 2.2, 1.4, -0.2, '#8F8570');   // a store and a low shelter
      const compoundMat = new T.MeshStandardMaterial({ vertexColors: true, color: 0xFFFFFF, roughness: 0.95, metalness: 0, envMapIntensity: 0.8 });
      addStatic(mergeBoxes(boxes), compoundMat);
    }

    // -- sandbag positions behind the line of departure: three courses of bags
    //    per wall, either side of the corridor, on the LD pad (already flat);
    //    back < 0 puts a wall a few metres past the line. ----------------------
    {
      const P0 = H.routePoint(0), c = Math.cos(P0.heading), s = Math.sin(P0.heading);   // heading: 0 = east, CCW
      const bags = [];
      const wallAt = (back, lateral, len, skew) => {
        // centre = P0 - back*dir + lateral*left (ENU); bags run along the phase line (perpendicular to the route)
        const ce = P0.e - back * c - lateral * s, cn = P0.n - back * s + lateral * c;
        const ang = P0.heading + Math.PI / 2 + skew;         // ENU angle of the wall's long axis
        const ux = Math.cos(ang), uz = -Math.sin(ang);       // scene-frame unit vector along the wall
        const yaw = Math.atan2(-uz, ux), nb = Math.round(len / 0.7), y0 = heightAt(ce, -cn);
        // bags overlap their pitch slightly (no gaps, no sliver faces) so the wall reads as one mass at range
        for (let course = 0; course < 3; course++) for (let i = 0; i < nb - (course % 2); i++) {
          const t = (i - (nb - 1 - (course % 2)) / 2) * 0.7;
          bags.push({ w: 0.76, h: 0.3, d: 0.48, x: ce + ux * t, y: y0 + 0.15 + course * 0.29, z: -cn + uz * t,
                      yaw, color: jitter('#77704F', 0.10) });
        }
      };
      wallAt(-28, 23, 7.5, 0.10); wallAt(-34, -22, 6.5, -0.12); wallAt(8, 38, 5.5, 0.55); wallAt(10, -40, 6, -0.45);
      const bagMat = new T.MeshStandardMaterial({ vertexColors: true, color: 0xFFFFFF, roughness: 1, metalness: 0, envMapIntensity: 0.7 });
      addStatic(mergeBoxes(bags), bagMat);
    }

    // -- rocks and rubble: instanced, seeded, small; a rubble skirt at the compound --
    const rockMat = new T.MeshStandardMaterial({ color: new T.Color(SPEC.palette.rock), roughness: 0.94, metalness: 0.02, envMapIntensity: 0.55 });
    {
      const rockGeo = new T.DodecahedronGeometry(1, 0);      // 36 triangles a rock; the noise displacement does the rest
      const pa = rockGeo.attributes.position, rv = new T.Vector3();
      for (let i = 0; i < pa.count; i++) { rv.fromBufferAttribute(pa, i);
        rv.multiplyScalar(1 + 0.38 * noise(rv.x * 1.9 + 5.2, rv.y * 1.9 + rv.z * 0.9)); pa.setXYZ(i, rv.x, rv.y * 0.7, rv.z); }
      rockGeo.computeVertexNormals();
      const rocks = new T.InstancedMesh(rockGeo, rockMat, 320);
      const m4 = new T.Matrix4(), q = new T.Quaternion(), e = new T.Euler(), pv = new T.Vector3(), sv = new T.Vector3();
      let n = 0, tries = 0;
      const put = (x, z, s) => {
        e.set(rnd() * 0.5, rnd() * Math.PI, rnd() * 0.5); q.setFromEuler(e);
        sv.set(s * (0.8 + rnd() * 0.5), s * (0.5 + rnd() * 0.4), s * (0.8 + rnd() * 0.5));
        m4.compose(pv.set(x, heightAt(x, z) - 0.18 * s, z), q, sv); rocks.setMatrixAt(n++, m4);
      };
      while (n < 250 && tries++ < 12000) {
        const x = FINE.minX + rnd() * (FINE.maxX - FINE.minX), z = FINE.minZ + rnd() * (FINE.maxZ - FINE.minZ);
        if (Math.abs(H.lateralOffset(x, -z)) < hw + 4) continue;
        if (phaseLineDist(x, -z) < 6 || Math.hypot(x - obj.e, -z - obj.n) < 14) continue;
        if (Math.hypot(x - HILL.x, z - HILL.z) < 48) continue;
        if (rnd() > 0.3 + 0.7 * (noise(x / 45 + 2, z / 45) + 1) / 2) continue;
        put(x, z, 0.25 + rnd() * rnd() * 1.3);
      }
      for (let i = 0; i < 60; i++) {                         // rubble skirt around the compound walls
        const a = rnd() * Math.PI * 2, d = 38 + rnd() * 18;
        put(HILL.x + Math.sin(a) * d, HILL.z + Math.cos(a) * d, 0.3 + rnd() * 0.7);
      }
      rocks.count = n; rocks.castShadow = true; rocks.receiveShadow = true; rocks.layers.enable(H.INSET_LAYER);
      scene.add(rocks); own.push(rocks);
    }

    // -- dead scrub: one InstancedMesh of five crossed dark blades, seeded, sparse,
    //    off the corridor and clear of the phase lines. Not on the inset layer. ----
    let scrub = null; const SCRUB_N = 1400;
    {
      const g = new T.BufferGeometry(); const verts = [], cols = [];
      const blade = (rot, h, lean) => {
        const c = Math.cos(rot), s = Math.sin(rot), w = 0.065;
        verts.push(-w * c, 0, -w * s, w * c, 0, w * s, lean * s, h, -lean * c);
        cols.push(0.11, 0.09, 0.07, 0.11, 0.09, 0.07, 0.23, 0.19, 0.14);   // dead, dark: never brighter than the ground
      };
      blade(0, 0.55, 0.12); blade(1.1, 0.42, -0.10); blade(2.2, 0.62, 0.08); blade(0.6, 0.30, 0.16); blade(1.7, 0.48, -0.14);
      g.setAttribute('position', new T.Float32BufferAttribute(verts, 3));
      g.setAttribute('color', new T.Float32BufferAttribute(cols, 3));
      g.computeVertexNormals();
      const mat = new T.MeshStandardMaterial({ vertexColors: true, roughness: 1, metalness: 0, side: T.DoubleSide });
      scrub = new T.InstancedMesh(g, mat, SCRUB_N);
      const m4 = new T.Matrix4(), q = new T.Quaternion(), e = new T.Euler(), pv = new T.Vector3(), sv = new T.Vector3();
      let n = 0, tries = 0;
      while (n < SCRUB_N && tries++ < SCRUB_N * 8) {
        const x = FINE.minX + rnd() * (FINE.maxX - FINE.minX), z = FINE.minZ + rnd() * (FINE.maxZ - FINE.minZ);
        if (Math.abs(H.lateralOffset(x, -z)) < hw + 2) continue;
        if (phaseLineDist(x, -z) < 5 || Math.hypot(x - obj.e, -z - obj.n) < 10) continue;
        if (Math.hypot(x - HILL.x, z - HILL.z) < 44) continue;
        if (rnd() > 0.25 + 0.75 * (noise(x / 30 + 9, z / 30) + 1) / 2) continue;
        e.set(0, rnd() * Math.PI * 2, 0); q.setFromEuler(e);
        const s = rnd() < 0.04 ? 2.4 + rnd() * 0.9 : 0.7 + rnd() * 0.9;
        sv.set(s, s * (0.8 + rnd() * 0.5), s);
        m4.compose(pv.set(x, heightAt(x, z), z), q, sv); scrub.setMatrixAt(n++, m4);
      }
      scrub.count = n; scrub.castShadow = false; scrub.receiveShadow = true; scrub.frustumCulled = false;
      scene.add(scrub); own.push(scrub);
    }

    // -- textures: the combat set first, the vendored Earth set as the fallback;
    //    report either way. UVs are in tiles already, so repeat = 1. -------------
    ctx.report({ ground: 'loading', vegetation: 'loaded' });
    const groundSet = (diff, nor, rough) => Promise.all([
      H.loadTexture(diff, true, 1, aniso), H.loadTexture(nor, false, 1, aniso), H.loadTexture(rough, false, 1, aniso),
    ]).then(([map, norm, rgh]) => {
      own.push(map, norm, rgh);
      Object.assign(groundMat, { map, normalMap: norm, roughnessMap: rgh, color: new T.Color(0xF2E6D2), envMapIntensity: 1.0 });
      groundMat.normalScale.set(0.9, 0.9); groundMat.needsUpdate = true;
    });
    groundSet('/vendor/asset-env-combat-ground-diff.jpg', '/vendor/asset-env-combat-ground-nor_gl.jpg', '/vendor/asset-env-combat-ground-rough.jpg')
      .then(() => ctx.report({ ground: 'loaded' }))
      .catch(() => groundSet(H.FALLBACK.ground.diff, H.FALLBACK.ground.nor, H.FALLBACK.ground.rough)
        .then(() => ctx.report({ ground: 'fallback', note: 'combat ground set missing; Earth ground set' }))
        .catch(() => ctx.report({ ground: 'fallback', note: 'ground textures failed; flat colour' })));
    Promise.all([
      H.loadTexture(H.FALLBACK.rock.diff, true, 1.6, aniso), H.loadTexture(H.FALLBACK.rock.nor, false, 1.6, aniso),
      H.loadTexture(H.FALLBACK.rock.rough, false, 1.6, aniso),
    ]).then(([rm, rn, rr]) => {
      Object.assign(rockMat, { map: rm, normalMap: rn, roughnessMap: rr, color: new T.Color(0xB9AE9C), envMapIntensity: 0.8 });
      rockMat.needsUpdate = true; own.push(rm, rn, rr);
    }).catch(() => {});

    H.scatterProps(ctx, heightAt, SPEC.props, own);       // async set dressing; meshes join `own`

    return {
      heightAt,
      bounds: { minX: ext.minE, maxX: ext.maxE, minZ: -ext.maxN, maxZ: -ext.minN },
      sunAz: SPEC.sun.az, sunEl: SPEC.sun.el,
      // no tick: dead scrub in still dawn air. (A whole-mesh rotation sway would pivot about the
      // world origin and lift instances a kilometre away by metres.)
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
  window.ARBITRAS_ENV.combat = { id: 'combat', spec: SPEC, build };
})();
