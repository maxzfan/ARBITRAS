/* CASEVAC environment -- overcast, wet valley floor; the ground IS the data
 * (tracks/TRACK_F.md §2, tracks/TRACK_F_CASEVAC.md "Presentation frame").
 *
 * The mission's automatic correction is terrain-referenced (Track E): a signed
 * OSM-rasterised pre-map around the USN8 antenna, 240 x 240 cells of 5 m,
 * classes grass / shrub / tree_cover / bare / water / paved / building, -1
 * unlabelled. The console hands it over as ctx.terrain and this module renders
 * the real classes as ground zones, so a viewer sees the boundaries the wheel
 * sensor will cross: every terrain vertex sits on a cell centre, samples that
 * cell's class and drives the ground material (vertex colour + per-vertex
 * roughness on one PBR gravel set). tree_cover gets instanced low-poly trees,
 * water a flat dark sheen a few cm below grade, building simple seeded blocks.
 * A dry riverbed cuts across the outbound leg midway; it fades to flat inside
 * corridor_half_width_m + 3 of the route (contract invariant 1), so the cue
 * is on both sides of the track, never under it. Class boundaries continue
 * under the route on purpose -- the route is supposed to cross them.
 *
 * Assets (Poly Haven, CC0; fetched by bootstrap.sh from ASSETS.txt):
 *   asset-env-casevac-sky.hdr        kloofendal_overcast_puresky 2k
 *   asset-env-casevac-ground-*.jpg   gravel_road diff 2k, nor_gl 1k, rough 1k
 * Fallback: helpers.FALLBACK (the vendored Earth set) and flat colour.
 *
 * HDR measured (pure-Python RGBE decode, 2k): brightest patch at compass
 * az 123.0, el 22.7 (99.99th-percentile centroid; brightest pixel az 119.4,
 * el 23.1) -- a weak sun behind cloud, ESE and low; horizon band (0.5-4 deg)
 * mean linear RGB (0.669, 0.708, 0.790) = #D6DBE6 at exposure 1; sky above
 * 20 deg (1.15, 1.26, 1.51). spec.sun/fog/sky below are that sky ~2 stops
 * under, per EARTH_BACKDROP.md's legibility rule; the harness re-measures
 * the sun at load and overrides spec.sun.
 */
(function () {
  const SPEC = {
    hdr: '/vendor/asset-env-casevac-sky.hdr',
    exposure: 1.25,
    fog: { color: '#7E8894', density: 0.0045 },            // horizon band ~2 stops under, blue pushed; 2x the prairie density
    sky: { horizon: '#7E8894', mid: '#8A929D', zenith: '#75808F' },   // flat overcast dome: brightest just above the horizon
    sun: { az: 123.0, el: 22.7 },                          // measured from the HDR; overridden at load
    palette: { ground: '#5A544C', rock: '#57544E', accent: '#E8A21C' },
    attribution: 'Sky, ground: Poly Haven, CC0',
    hero_camera: { az: 228, el: 16, dist: 230, dolly: 0.6 },   // low oblique from the NW, over the aid station down the outbound leg
  };

  // Linear albedo each class should READ as on screen (wet, overcast), and its
  // roughness as a multiple of the 0.35 wet base. The gravel texture is strongly
  // brown (gravel_road_diff_2k mean linear 0.193/0.099/0.051, measured), so the
  // vertex tint is target / texture-mean: the texture supplies detail, the class
  // supplies the colour. Before the texture lands the material colour is the
  // texture mean, so the flat first paint has the same average look.
  const TEX_MEAN = [0.193, 0.099, 0.051];
  const STYLE = {
    unlabelled: { c: [0.072, 0.064, 0.048], r: 1.9 },     // neutral wet soil, warm grey-brown
    grass:      { c: [0.046, 0.096, 0.028], r: 2.2 },     // wet green
    shrub:      { c: [0.064, 0.078, 0.032], r: 2.3 },     // olive, scrub instances on top
    tree_cover: { c: [0.032, 0.056, 0.024], r: 2.2 },     // dark forest floor, trees on top
    bare:       { c: [0.150, 0.122, 0.082], r: 1.4 },     // pale tan
    water:      { c: [0.016, 0.028, 0.038], r: 0.3 },     // basin under the water sheet
    paved:      { c: [0.036, 0.040, 0.047], r: 0.8 },     // wet asphalt: cool dark grey, sheen
    building:   { c: [0.088, 0.084, 0.080], r: 1.7 },     // concrete footprint under the blocks
  };
  const GRAVEL = { c: [0.215, 0.198, 0.172], r: 1.4 };    // riverbed: pale washed gravel
  const DAMP   = { c: [0.050, 0.050, 0.047], r: 0.6 };    // the damp streak along the bed's axis

  function build(ctx) {
    const T = ctx.THREE, H = ctx.helpers, m = ctx.mission, scene = ctx.scene, terrain = ctx.terrain || null;
    const hw = m.corridor_half_width_m, gs = m.ground_station;
    const noise = H.makeNoise(23), rnd = H.mulberry32(29);
    const aniso = ctx.renderer.capabilities.getMaxAnisotropy();
    const own = [];                                         // everything we must dispose
    const lin = hex => new T.Color().setRGB(((hex >> 16) & 255)/255, ((hex >> 8) & 255)/255, (hex & 255)/255).convertSRGBToLinear();

    // -- the pre-map: class lookup at a point, bilinear fields over cell centres --
    const rows = terrain ? terrain.rows : 0, cols = terrain ? terrain.cols : 0;
    const cell = terrain ? terrain.cell_m : 5, oe = terrain ? terrain.origin_enu[0] : 0, on = terrain ? terrain.origin_enu[1] : 0;
    const grid = terrain ? terrain.grid : null;
    const K = {}; if (terrain) terrain.classes.forEach((nm, i) => { K[nm] = i; });
    const cid = nm => (nm in K ? K[nm] : -99);
    const NAME = []; if (terrain) terrain.classes.forEach((nm, i) => { NAME[i] = nm; });
    const classAt = (e, n) => { if (!grid) return -1;
      const j = Math.floor((e - oe)/cell), i = Math.floor((n - on)/cell);
      return (i < 0 || j < 0 || i >= rows || j >= cols) ? -1 : grid[i*cols + j]; };
    const styleOf = c => STYLE[NAME[c]] || STYLE.unlabelled;
    const maskField = mask => (e, n) => {                   // continuous 0..1, bilinear between cell centres
      if (!mask) return 0;
      const u = (e - oe)/cell - 0.5, v = (n - on)/cell - 0.5, j0 = Math.floor(u), i0 = Math.floor(v), fu = u - j0, fv = v - i0;
      const g = (i, j) => (i < 0 || j < 0 || i >= rows || j >= cols) ? 0 : mask[i*cols + j];
      return (g(i0, j0)*(1 - fu) + g(i0, j0 + 1)*fu)*(1 - fv) + (g(i0 + 1, j0)*(1 - fu) + g(i0 + 1, j0 + 1)*fu)*fv;
    };
    const WATER = cid('water'), TREE = cid('tree_cover'), SHRUB = cid('shrub'), GRASS = cid('grass'), BUILDING = cid('building');
    let waterMask = null;
    if (grid) { waterMask = new Uint8Array(rows*cols); for (let k = 0; k < rows*cols; k++) waterMask[k] = grid[k] === WATER ? 1 : 0; }
    const waterField = maskField(waterMask);

    // -- dry riverbed: runs perpendicular to the outbound leg, crossing it midway,
    //    meandering; ~48 m wide, 1.8 m deep, gravel-tinted. -------------------------
    const r0 = m.route[0], r1 = m.route[1] || r0;
    const legL = Math.hypot(r1.e - r0.e, r1.n - r0.n) || 1;
    const de = (r1.e - r0.e)/legL, dn = (r1.n - r0.n)/legL;       // unit vector along the outbound leg
    const RCe = r0.e + de*legL*0.5, RCn = r0.n + dn*legL*0.5;      // the crossing point
    const RIVER_HALF = 30;
    const riverDist = (e, n) => {                                 // distance from the meandering bed axis
      const pe = e - RCe, pn = n - RCn, t = -pe*dn + pn*de, s = pe*de + pn*dn;   // t along the bed, s across it
      return Math.abs(s - 16*noise(t/120 + 4.2, 0.7) - 6*noise(t/38 + 1.3, 2.9));
    };
    const riverProfile = (e, n) => H.smooth(RIVER_HALF, RIVER_HALF*0.35, riverDist(e, n));   // 1 in the channel, 0 beyond RIVER_HALF
    const dampProfile = (e, n) => H.smooth(9, 2, riverDist(e, n));                           // 1 on the axis, 0 beyond 9 m

    // -- terrain height: valley-floor undulation + riverbed + water basins, FLAT
    //    along the corridor, inside every prop's rim and around the ground station
    //    (contract invariant 1: same smooth() bands as makeTerrain). ---------------
    const relief = (x, z) => {
      const base = 2.2*noise(x/80 + 3.1, z/80 + 1.7) + 0.6*noise(x/24, z/24) + 0.15*noise(x/6, z/6);
      const rp = riverProfile(x, -z);
      return base - 2.2*rp - 0.3*dampProfile(x, -z) + 0.10*rp*noise(x/2.5 + 7, z/2.5) - 0.5*waterField(x, -z);
    };
    const flat = (x, z) => {
      const lat = Math.abs(H.lateralOffset(x, -z)), clear = H.propClearance(x, -z), dg = Math.hypot(x - gs.e, -z - gs.n);
      return H.smooth(hw + 3, hw + 16, lat) * H.smooth(7, 20, clear) * H.smooth(20, 34, dg);
    };
    const heightAt = (x, z) => relief(x, z) * flat(x, z);
    const inCorridor = (e, n, pad) => Math.abs(H.lateralOffset(e, n)) < hw + pad;
    const nearProp = (e, n, pad) => H.propClearance(e, n) < pad || Math.hypot(e - gs.e, n - gs.n) < 20 + pad;

    // -- ground: two meshes on one material. The inner mesh has one vertex per 5 m
    //    cell centre over the route box + 230 m, aligned to the pre-map so each
    //    vertex owns exactly one class; a 25 m skirt (dropped 0.35 m so it never
    //    fights the inner mesh) carries the same classes out to the 500 m extent,
    //    where the fog has them anyway. Budget: ~57 k triangles for the ground.
    //    (Budget note: the console renders the scene three times per frame --
    //    RenderPass, SSAO beauty, SSAO normals -- and each re-renders the shadow
    //    map, so shadow casters cost 6x their triangles and everything else 3x.) --
    const ext = H.extent(500), inner = H.extent(200);
    const groundMat = new T.MeshStandardMaterial({vertexColors:true, color:new T.Color().setRGB(TEX_MEAN[0], TEX_MEAN[1], TEX_MEAN[2]),
                                                  roughness:0.35, metalness:0, envMapIntensity:1.0});
    // per-vertex roughness: multiply the roughness factor by the aRough attribute
    groundMat.onBeforeCompile = sh => {
      sh.vertexShader = sh.vertexShader
        .replace('#include <common>', '#include <common>\nattribute float aRough; varying float vRough;')
        .replace('#include <begin_vertex>', '#include <begin_vertex>\nvRough = aRough;');
      sh.fragmentShader = sh.fragmentShader
        .replace('#include <common>', '#include <common>\nvarying float vRough;')
        .replace('#include <roughnessmap_fragment>', '#include <roughnessmap_fragment>\nroughnessFactor *= vRough;');
    };
    groundMat.customProgramCacheKey = () => 'casevac-ground-vrough';
    const TILE_M = 3.5;                                        // one texture tile; UVs are in tiles so both meshes agree
    const makeGround = (box, sp, drop) => {
      const E0 = oe + cell/2 + sp*Math.floor((box.minE - oe - cell/2)/sp), N0 = on + cell/2 + sp*Math.floor((box.minN - on - cell/2)/sp);
      const NJ = Math.ceil((box.maxE - E0)/sp) + 1, NI = Math.ceil((box.maxN - N0)/sp) + 1;
      const nv = NI*NJ, pos = new Float32Array(nv*3), col = new Float32Array(nv*3), rgh = new Float32Array(nv), uv = new Float32Array(nv*2);
      for (let i = 0; i < NI; i++) for (let j = 0; j < NJ; j++) {
        const e = E0 + j*sp, n = N0 + i*sp, x = e, z = -n, k = i*NJ + j;
        pos[3*k] = x; pos[3*k + 1] = heightAt(x, z) - drop; pos[3*k + 2] = z;
        const st = styleOf(classAt(e, n)), rp = riverProfile(e, n), dp = dampProfile(e, n), mottle = 1 + 0.14*noise(e/9 + 2, n/9 + 5);
        for (let c = 0; c < 3; c++) { const g = st.c[c]*(1 - rp) + GRAVEL.c[c]*rp; col[3*k + c] = (g*(1 - dp) + DAMP.c[c]*dp) * mottle / TEX_MEAN[c]; }
        rgh[k] = (st.r*(1 - rp) + GRAVEL.r*rp)*(1 - dp) + DAMP.r*dp;
        uv[2*k] = e/TILE_M; uv[2*k + 1] = n/TILE_M;
      }
      const idx = new Uint32Array((NI - 1)*(NJ - 1)*6); let q = 0;
      for (let i = 0; i < NI - 1; i++) for (let j = 0; j < NJ - 1; j++) {
        const a = i*NJ + j, b = a + 1, c = a + NJ, d = c + 1;
        idx[q++] = a; idx[q++] = b; idx[q++] = c;  idx[q++] = b; idx[q++] = d; idx[q++] = c;
      }
      const geo = new T.BufferGeometry();
      geo.setAttribute('position', new T.BufferAttribute(pos, 3)); geo.setAttribute('color', new T.BufferAttribute(col, 3));
      geo.setAttribute('aRough', new T.BufferAttribute(rgh, 1)); geo.setAttribute('uv', new T.BufferAttribute(uv, 2));
      geo.setIndex(new T.BufferAttribute(idx, 1)); geo.computeVertexNormals();
      const ground = new T.Mesh(geo, groundMat); ground.receiveShadow = true;
      ground.layers.enable(H.INSET_LAYER); scene.add(ground); own.push(ground);
    };
    makeGround(inner, cell, 0);
    makeGround(ext, 5*cell, 0.35);

    // -- water: one sheet of quads over the water cells, a few cm below grade,
    //    dark and near-mirror; the ground under it is basined by heightAt. --------
    if (grid && waterMask) {
      const verts = [], idx = []; let nq = 0;
      const level = (e, n) => (relief(e, -n) + 0.5*waterField(e, n) - 0.08) * flat(e, -n) - 0.02;
      for (let i = 0; i < rows; i++) for (let j = 0; j < cols; j++) {
        if (!waterMask[i*cols + j]) continue;
        const e0 = oe + j*cell, n0 = on + i*cell;
        for (const [e, n] of [[e0, n0], [e0 + cell, n0], [e0, n0 + cell], [e0 + cell, n0 + cell]]) verts.push(e, level(e, n), -n);
        const a = nq*4; idx.push(a, a + 1, a + 2, a + 1, a + 3, a + 2); nq++;
      }
      if (nq) {
        const geo = new T.BufferGeometry();
        geo.setAttribute('position', new T.Float32BufferAttribute(verts, 3)); geo.setIndex(idx); geo.computeVertexNormals();
        const water = new T.Mesh(geo, new T.MeshStandardMaterial({color:lin(0x24343C), roughness:0.10, metalness:0, envMapIntensity:1.4}));
        water.receiveShadow = true; water.layers.enable(H.INSET_LAYER); scene.add(water); own.push(water);
      }
    }

    // -- trees: instanced low-poly trunks + two canopy kinds, only in tree_cover
    //    cells, never in the corridor or on a prop. Trunks on the inset layer,
    //    canopies not (contract invariant 5). -----------------------------------
    const TREE_CAP = 1000;                                    // ~35 k triangles for all trees (36 + 10 per broadleaf, 14 + 10 per conifer)
    {
      const trunkGeo = new T.CylinderGeometry(0.14, 0.24, 1, 5, 1, true); trunkGeo.translate(0, 0.5, 0);
      const leafGeo = new T.DodecahedronGeometry(1, 0);
      { const pa = leafGeo.attributes.position, v = new T.Vector3();
        for (let i = 0; i < pa.count; i++) { v.fromBufferAttribute(pa, i); v.multiplyScalar(1 + 0.16*noise(v.x*2.3 + 1, v.y*2.3 + v.z)); pa.setXYZ(i, v.x, v.y*1.2, v.z); }
        leafGeo.computeVertexNormals(); }
      const coneGeo = new T.ConeGeometry(1, 1, 7, 1, true); coneGeo.translate(0, 0.5, 0);
      const trunkMat = new T.MeshStandardMaterial({color:lin(0x4A3E33), roughness:0.9, metalness:0});
      const leafMat = new T.MeshStandardMaterial({color:lin(0x42583A), roughness:0.9, metalness:0, flatShading:true});
      const coneMat = new T.MeshStandardMaterial({color:lin(0x2F452E), roughness:0.9, metalness:0, flatShading:true});
      const trunks = new T.InstancedMesh(trunkGeo, trunkMat, TREE_CAP), leaves = new T.InstancedMesh(leafGeo, leafMat, TREE_CAP), cones = new T.InstancedMesh(coneGeo, coneMat, TREE_CAP);
      const m4 = new T.Matrix4(), q = new T.Quaternion(), eu = new T.Euler(), pv = new T.Vector3(), sv = new T.Vector3(), tint = new T.Color();
      let nt = 0, nl = 0, nc = 0;
      const treeCells = []; if (grid) for (let k = 0; k < rows*cols; k++) if (grid[k] === TREE) treeCells.push(k);
      const P = treeCells.length ? Math.min(1, 950/treeCells.length) : 0;
      for (const k of treeCells) {
        if (nt >= TREE_CAP || rnd() > P) continue;
        const i = Math.floor(k/cols), j = k - i*cols;
        const e = oe + (j + 0.5)*cell + (rnd() - 0.5)*4.2, n = on + (i + 0.5)*cell + (rnd() - 0.5)*4.2;
        if (inCorridor(e, n, 4) || nearProp(e, n, 8)) continue;
        const x = e, z = -n, y = heightAt(x, z) - 0.1, conifer = rnd() < 0.42;
        const th = conifer ? 1.2 + rnd()*1.2 : 1.8 + rnd()*1.6, tr = 0.9 + rnd()*0.8;
        eu.set(0, rnd()*Math.PI*2, 0); q.setFromEuler(eu);
        m4.compose(pv.set(x, y, z), q, sv.set(tr, th + (conifer ? 1.5 : 1.0), tr)); trunks.setMatrixAt(nt++, m4);
        if (conifer) {
          const h = 5 + rnd()*5, r = 1.6 + rnd()*1.3;
          m4.compose(pv.set(x, y + th, z), q, sv.set(r, h, r)); cones.setMatrixAt(nc, m4);
          cones.setColorAt(nc++, tint.setRGB(0.75 + rnd()*0.35, 0.8 + rnd()*0.3, 0.75 + rnd()*0.35));
        } else {
          const r = 2.0 + rnd()*1.8, ry = r*(0.9 + rnd()*0.4);
          m4.compose(pv.set(x, y + th + ry*0.75, z), q, sv.set(r, ry, r*(0.85 + rnd()*0.3))); leaves.setMatrixAt(nl, m4);
          leaves.setColorAt(nl++, tint.setRGB(0.75 + rnd()*0.4, 0.8 + rnd()*0.3, 0.75 + rnd()*0.35));
        }
      }
      trunks.count = nt; leaves.count = nl; cones.count = nc;
      for (const im of [trunks, leaves, cones]) { im.castShadow = true; im.receiveShadow = true; im.frustumCulled = false; scene.add(im); own.push(im); }
      trunks.castShadow = false;                              // canopies carry the shadow; trunks would cost 6x for nothing visible
      trunks.layers.enable(H.INSET_LAYER);
    }

    // -- buildings: building cells (outside the corridor and prop rims) merged into
    //    rectangles of up to 8 x 8 cells, each a seeded 4-9 m block. --------------
    if (grid) {
      const mask = new Uint8Array(rows*cols);
      for (let i = 0; i < rows; i++) for (let j = 0; j < cols; j++) {
        const k = i*cols + j; if (grid[k] !== BUILDING) continue;
        const e = oe + (j + 0.5)*cell, n = on + (i + 0.5)*cell;
        mask[k] = (inCorridor(e, n, 3) || nearProp(e, n, 5)) ? 0 : 1;
      }
      const rects = [], used = new Uint8Array(rows*cols), MAXC = 10;
      for (let i = 0; i < rows; i++) for (let j = 0; j < cols; j++) {
        if (!mask[i*cols + j] || used[i*cols + j]) continue;
        let w = 1; while (j + w < cols && w < MAXC && mask[i*cols + j + w] && !used[i*cols + j + w]) w++;
        let h = 1, ok = true;
        while (i + h < rows && h < MAXC && ok) { for (let jj = j; jj < j + w; jj++) if (!mask[(i + h)*cols + jj] || used[(i + h)*cols + jj]) { ok = false; break; } if (ok) h++; }
        for (let ii = i; ii < i + h; ii++) for (let jj = j; jj < j + w; jj++) used[ii*cols + jj] = 1;
        rects.push([i, j, h, w]);
      }
      if (rects.length) {
        const mat = new T.MeshStandardMaterial({color:lin(0x67625C), roughness:0.78, metalness:0});
        const blocks = new T.InstancedMesh(new T.BoxGeometry(1, 1, 1), mat, rects.length);
        const m4 = new T.Matrix4(), q = new T.Quaternion(), pv = new T.Vector3(), sv = new T.Vector3(), tint = new T.Color();
        rects.forEach(([i, j, h, w], r) => {
          const ce = oe + (j + w/2)*cell, cn = on + (i + h/2)*cell, we = w*cell - 0.8, wn = h*cell - 0.8;
          let base = Infinity;
          for (const [fe, fn] of [[-1, -1], [1, -1], [-1, 1], [1, 1], [0, 0]]) base = Math.min(base, heightAt(ce + fe*we/2, -(cn + fn*wn/2)));
          const hgt = 4 + rnd()*5, g = 0.85 + rnd()*0.3;
          m4.compose(pv.set(ce, base - 0.3 + hgt/2, -cn), q, sv.set(we, hgt + 0.3, wn)); blocks.setMatrixAt(r, m4);
          blocks.setColorAt(r, tint.setRGB(g*(0.95 + rnd()*0.1), g, g*(0.95 + rnd()*0.1)));
        });
        blocks.castShadow = true; blocks.receiveShadow = true; blocks.layers.enable(H.INSET_LAYER); scene.add(blocks); own.push(blocks);
      }
    }

    // -- scrub: low bushes, dense in shrub cells, sparse elsewhere; outside the
    //    corridor; not on the inset layer. ---------------------------------------
    {
      const SCRUB_CAP = 800, geo = new T.IcosahedronGeometry(1, 0); geo.scale(1, 0.6, 1); geo.translate(0, 0.35, 0);
      const scrub = new T.InstancedMesh(geo, new T.MeshStandardMaterial({color:lin(0x4F5A36), roughness:0.95, metalness:0, flatShading:true}), SCRUB_CAP);
      const m4 = new T.Matrix4(), q = new T.Quaternion(), eu = new T.Euler(), pv = new T.Vector3(), sv = new T.Vector3();
      let n = 0;
      const put = (e, nn) => {
        if (n >= SCRUB_CAP || inCorridor(e, nn, 2) || nearProp(e, nn, 4)) return;
        eu.set(0, rnd()*Math.PI*2, 0); q.setFromEuler(eu); const s = 0.5 + rnd()*0.9;
        m4.compose(pv.set(e, heightAt(e, -nn) - 0.05, -nn), q, sv.set(s*(0.8 + rnd()*0.5), s*(0.7 + rnd()*0.5), s*(0.8 + rnd()*0.5))); scrub.setMatrixAt(n++, m4);
      };
      if (grid) for (let k = 0; k < rows*cols; k++) {
        const c = grid[k], tries = c === SHRUB ? 2 : (c === GRASS ? (rnd() < 0.03 ? 1 : 0) : (c === -1 ? (rnd() < 0.012 ? 1 : 0) : 0));
        if (!tries) continue;
        const i = Math.floor(k/cols), j = k - i*cols;
        for (let t = 0; t < tries; t++) put(oe + (j + rnd())*cell, on + (i + rnd())*cell);
      } else {
        for (let t = 0; t < 1200; t++) put(ext.minE + rnd()*(ext.maxE - ext.minE), ext.minN + rnd()*(ext.maxN - ext.minN));
      }
      scrub.count = n; scrub.castShadow = false; scrub.receiveShadow = true; scrub.frustumCulled = false; scene.add(scrub); own.push(scrub);
    }

    // -- riverbed stones: instanced, seeded, along the bed, never in the corridor --
    const rockMat = new T.MeshStandardMaterial({color:new T.Color(SPEC.palette.rock), roughness:0.9, metalness:0.02, envMapIntensity:0.6});
    {
      const rockGeo = new T.DodecahedronGeometry(1, 0);      // 36 triangles each
      { const pa = rockGeo.attributes.position, v = new T.Vector3();
        for (let i = 0; i < pa.count; i++) { v.fromBufferAttribute(pa, i); v.multiplyScalar(1 + 0.28*noise(v.x*1.7 + 5.2, v.y*1.7 + v.z*0.9)); pa.setXYZ(i, v.x, v.y*0.7, v.z); }
        rockGeo.computeVertexNormals(); }
      const rocks = new T.InstancedMesh(rockGeo, rockMat, 280);
      const m4 = new T.Matrix4(), q = new T.Quaternion(), eu = new T.Euler(), pv = new T.Vector3(), sv = new T.Vector3();
      let n = 0;
      for (let t = -560; t <= 560 && n < 280; t += 3.5) {
        if (rnd() > 0.7) continue;
        const across = (rnd() - 0.5)*2*RIVER_HALF*0.9 + 16*noise(t/120 + 4.2, 0.7) + 6*noise(t/38 + 1.3, 2.9);
        const e = RCe - t*dn + across*de, nn = RCn + t*de + across*dn;
        if (inCorridor(e, nn, 5) || nearProp(e, nn, 8)) continue;
        const s = 0.3 + rnd()*rnd()*1.1;
        eu.set(rnd()*Math.PI, rnd()*Math.PI, rnd()*Math.PI); q.setFromEuler(eu);
        m4.compose(pv.set(e, heightAt(e, -nn) - 0.2*s, -nn), q, sv.set(s*(0.8 + rnd()*0.5), s*(0.6 + rnd()*0.5), s*(0.8 + rnd()*0.5))); rocks.setMatrixAt(n++, m4);
      }
      rocks.count = n; rocks.castShadow = true; rocks.receiveShadow = true; rocks.layers.enable(H.INSET_LAYER); scene.add(rocks); own.push(rocks);
    }

    // -- set dressing: stone wall + red-cross panel outside the CCP ring, a tent
    //    by the aid station. Outside every rim; the console draws the rings. -------
    const D2R = Math.PI/180;
    const bearingPt = (p, brg, r) => ({e: p.e + Math.sin(brg*D2R)*r, n: p.n + Math.cos(brg*D2R)*r});
    const addMesh = (mesh) => { mesh.castShadow = mesh.receiveShadow = true; mesh.layers.enable(H.INSET_LAYER); scene.add(mesh); own.push(mesh); return mesh; };
    for (const p of m.props) {
      const R = (p.radius_m || 0);
      if (p.kind === 'ccp') {
        // low dry-stone wall: two courses of blocks, 14 m, tangent to the ring on its south side
        const stoneMat = new T.MeshStandardMaterial({color:lin(0x6B675F), roughness:0.92, metalness:0});
        const wall = new T.InstancedMesh(new T.BoxGeometry(1, 1, 1), stoneMat, 20);
        const m4 = new T.Matrix4(), q = new T.Quaternion(), eu = new T.Euler(), pv = new T.Vector3(), sv = new T.Vector3();
        const c = bearingPt(p, 172, R + 7.5), tang = (172 + 90)*D2R, te = Math.sin(tang), tn = Math.cos(tang);
        let n = 0;
        for (let course = 0; course < 2; course++) for (let k = 0; k < 9; k++) {
          const along = (k - 4 + (course ? 0.5 : 0))*1.55, e = c.e + te*along, nn = c.n + tn*along;
          eu.set(0, -tang + (rnd() - 0.5)*0.12, 0); q.setFromEuler(eu);
          m4.compose(pv.set(e, heightAt(e, -nn) + 0.28 + course*0.5, -nn), q, sv.set(1.45 + rnd()*0.2, 0.5, 0.62 + rnd()*0.1)); wall.setMatrixAt(n++, m4);
        }
        wall.count = n; wall.castShadow = wall.receiveShadow = true; wall.layers.enable(H.INSET_LAYER); scene.add(wall); own.push(wall);
        // red-cross panel on two posts, facing the approach (the route arrives from the north-west)
        const pc = bearingPt(p, 250, R + 6), face = 315*D2R, y0 = heightAt(pc.e, -pc.n);
        const panel = new T.Group(); panel.position.set(pc.e, y0, -pc.n); panel.rotation.y = -face + Math.PI/2;
        const white = new T.MeshStandardMaterial({color:lin(0xE9E9E4), roughness:0.55, metalness:0});
        const red = new T.MeshStandardMaterial({color:lin(0xC2242A), roughness:0.6, metalness:0});
        const postMat = new T.MeshStandardMaterial({color:lin(0x4A4540), roughness:0.9, metalness:0});
        const board = new T.Mesh(new T.BoxGeometry(0.08, 2.2, 2.2), white); board.position.set(0, 2.2, 0); panel.add(board);
        const barV = new T.Mesh(new T.BoxGeometry(0.03, 1.5, 0.5), red); barV.position.set(0.055, 2.2, 0); panel.add(barV);
        const barH = new T.Mesh(new T.BoxGeometry(0.03, 0.5, 1.5), red); barH.position.set(0.055, 2.2, 0); panel.add(barH);
        for (const s of [-0.9, 0.9]) { const post = new T.Mesh(new T.BoxGeometry(0.1, 3.2, 0.1), postMat); post.position.set(-0.06, 1.6, s); panel.add(post); }
        panel.traverse(o => { if (o.isMesh) { o.castShadow = o.receiveShadow = true; o.layers.enable(H.INSET_LAYER); } });
        scene.add(panel); own.push(panel);
      }
      if (p.kind === 'aid_station') {
        // a ridge tent, 6 x 7 m, on the flat band just outside the rim, away from both legs
        const tc = bearingPt(p, 250, R + 6), tri = new T.Shape();
        tri.moveTo(-3, 0); tri.lineTo(3, 0); tri.lineTo(0, 2.6); tri.closePath();
        const geo = new T.ExtrudeGeometry(tri, {depth:7, bevelEnabled:false}); geo.translate(0, 0, -3.5);
        const tent = new T.Mesh(geo, new T.MeshStandardMaterial({color:lin(0x4E5540), roughness:0.9, metalness:0, side:T.DoubleSide}));
        tent.position.set(tc.e, heightAt(tc.e, -tc.n) - 0.05, -tc.n); tent.rotation.y = 0.35; addMesh(tent);
        const fly = new T.Mesh(new T.BoxGeometry(6.6, 0.06, 7.6), new T.MeshStandardMaterial({color:lin(0x3E4434), roughness:0.9}));
        fly.position.set(tc.e, heightAt(tc.e, -tc.n) + 0.02, -tc.n); fly.rotation.y = 0.35; fly.castShadow = false; fly.receiveShadow = true; scene.add(fly); own.push(fly);
      }
    }

    // -- textures: the casevac gravel set, else the Earth fallback, else flat colour --
    ctx.report({ground:'loading', vegetation:'loaded', terrain: terrain ? 'loaded' : 'missing',
                note: terrain ? undefined : 'no terrain map: whole floor rendered as unlabelled'});
    const GROUND = {diff:'/vendor/asset-env-casevac-ground-diff.jpg', nor:'/vendor/asset-env-casevac-ground-nor_gl.jpg', rough:'/vendor/asset-env-casevac-ground-rough.jpg'};
    const loadSet = set => Promise.all([H.loadTexture(set.diff, true, 1, aniso), H.loadTexture(set.nor, false, 1, aniso), H.loadTexture(set.rough, false, 1, aniso)]);
    const apply = ([map, nor, rough], status, note) => {
      for (const t of [map, nor, rough]) own.push(t);        // UVs are already in tiles of TILE_M; repeat stays 1
      Object.assign(groundMat, {map, normalMap:nor, roughnessMap:rough, color:new T.Color(0xFFFFFF)});
      groundMat.normalScale.set(0.7, 0.7); groundMat.needsUpdate = true;
      ctx.report(note ? {ground:status, note} : {ground:status});
    };
    loadSet(GROUND).then(t => apply(t, 'loaded'))
      .catch(() => loadSet(H.FALLBACK.ground).then(t => apply(t, 'fallback', 'casevac gravel set failed; Earth grass_path set (class tints approximate)'))
      .catch(() => ctx.report({ground:'fallback', note:'ground textures failed; flat colour'})));
    Promise.all([H.loadTexture(H.FALLBACK.rock.diff, true, 1.6, aniso), H.loadTexture(H.FALLBACK.rock.nor, false, 1.6, aniso), H.loadTexture(H.FALLBACK.rock.rough, false, 1.6, aniso)])
      .then(([rm, rn, rr]) => { Object.assign(rockMat, {map:rm, normalMap:rn, roughnessMap:rr, color:new T.Color(0x9A948A)}); rockMat.needsUpdate = true; own.push(rm, rn, rr); })
      .catch(() => {});

    return {
      heightAt,
      bounds: {minX: ext.minE, maxX: ext.maxE, minZ: -ext.maxN, maxZ: -ext.minN},
      sunAz: SPEC.sun.az, sunEl: SPEC.sun.el,
      dispose() {
        for (const o of own) {
          if (o.isTexture) { o.dispose(); continue; }
          scene.remove(o);
          o.traverse ? o.traverse(c => {
            if (c.geometry) c.geometry.dispose();
            if (c.material) (Array.isArray(c.material) ? c.material : [c.material]).forEach(mm => mm.dispose());
          }) : null;
        }
      },
    };
  }

  window.ARBITER_ENV = window.ARBITER_ENV || {};
  window.ARBITER_ENV.casevac = { id: 'casevac', spec: SPEC, build };
})();
