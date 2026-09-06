/* Environment helpers -- shared by the console's EnvScene, the hero, the
 * selector tiles and the env/preview.html harness (console/web/env/CONTRACT.md).
 *
 * Extracted from index.html so that every environment module builds against
 * ONE implementation of noise, texture/HDR loading and the scene frame.
 * Classic script: defines window.ARBITRAS_HELPERS. Requires window.THREE and
 * window.Route (route.js) to be loaded first.
 *
 * Scene frame everywhere: x = East, y = Up, z = South (North is -z).
 */
(function (root) {
  const D2R = Math.PI / 180;
  const INSET_LAYER = 1;                      // static geometry the top-down inset renders

  // Deterministic noise: identical on every machine and every take.
  const mulberry32 = a => () => { a |= 0; a = a + 0x6D2B79F5 | 0;
    let t = Math.imul(a ^ a >>> 15, 1 | a); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
    return ((t ^ t >>> 14) >>> 0) / 4294967296; };
  function makeNoise(seed) {
    const hash = (x, y) => { let h = (x*374761393 + y*668265263 + seed*1442695041) | 0;
      h = Math.imul(h ^ (h >>> 13), 1274126177); return ((h ^ (h >>> 16)) >>> 0) / 4294967296; };
    const sm = t => t*t*(3-2*t);
    return (x, y) => {
      const xi = Math.floor(x), yi = Math.floor(y), u = sm(x-xi), w = sm(y-yi);
      const a = hash(xi,yi), b = hash(xi+1,yi), c = hash(xi,yi+1), d = hash(xi+1,yi+1);
      return (a + (b-a)*u + (c-a)*w + (a-b-c+d)*u*w) * 2 - 1;
    };
  }
  const smooth = (a, b, x) => { const t = Math.min(1, Math.max(0, (x-a)/(b-a))); return t*t*(3-2*t); };

  const azel = (az, el) => {
    const T = root.THREE, a = az * D2R, e = el * D2R, c = Math.cos(e);
    return new T.Vector3(Math.sin(a) * c, Math.sin(e), -Math.cos(a) * c);
  };
  const toScene = (e, n, y = 0) => new root.THREE.Vector3(e, y, -n);

  function roundedRect(w, h, r) {
    const T = root.THREE, s = new T.Shape(), x = -w/2, y = -h/2;
    s.moveTo(x+r, y); s.lineTo(x+w-r, y); s.quadraticCurveTo(x+w, y, x+w, y+r);
    s.lineTo(x+w, y+h-r); s.quadraticCurveTo(x+w, y+h, x+w-r, y+h);
    s.lineTo(x+r, y+h); s.quadraticCurveTo(x, y+h, x, y+h-r);
    s.lineTo(x, y+r); s.quadraticCurveTo(x, y, x+r, y);
    return s;
  }

  // -- asset loaders: each resolves or rejects; callers render with whatever
  //    has arrived and swap the rest in. Nothing blocks first paint.
  function loadTexture(url, srgb, repeat, aniso) {
    const T = root.THREE;
    return new Promise((res, rej) => new T.TextureLoader().load(url, t => {
      t.wrapS = t.wrapT = T.RepeatWrapping; t.repeat.set(repeat, repeat);
      t.anisotropy = aniso || 1; t.encoding = srgb ? T.sRGBEncoding : T.LinearEncoding;
      t.needsUpdate = true; res(t);
    }, undefined, rej));
  }
  function withTimeout(p, ms, what) {
    return Promise.race([p, new Promise((_, rej) => setTimeout(() => rej(new Error(what + ' timed out')), ms))]);
  }
  // HDRI -> PMREM environment. The sun direction and the horizon fog colour are
  // read off the HDR pixels, so the key light and fog match the sky that lights
  // the scene (console/web/EARTH_BACKDROP.md).
  function loadHDR(url, renderer) {
    const T = root.THREE;
    return new Promise((res, rej) => new T.RGBELoader().setDataType(T.FloatType).load(url, tex => {
      tex.mapping = T.EquirectangularReflectionMapping;
      const pm = new T.PMREMGenerator(renderer); pm.compileEquirectangularShader();
      const env = pm.fromEquirectangular(tex).texture; pm.dispose();
      const {data, width:W, height:H} = tex.image, nc = data.length / (W*H);
      const lum = i => 0.2126*data[i] + 0.7152*data[i+1] + 0.0722*data[i+2];
      let bx = 0, by = 0, bl = -1;
      for (let y = 0; y < H; y += 2) for (let x = 0; x < W; x += 2) {
        const l = lum((y*W + x)*nc); if (l > bl) { bl = l; bx = x; by = y; } }
      const el = 90 - (by + 0.5)/H*180, phi = ((bx + 0.5)/W - 0.5)*2*Math.PI, ce = Math.cos(el*D2R);
      const sunDir = new T.Vector3(Math.cos(phi)*ce, Math.sin(el*D2R), Math.sin(phi)*ce).normalize();
      let r = 0, g = 0, b = 0, n = 0;
      for (let y = 0; y < H; y++) { const e = 90 - (y + 0.5)/H*180; if (e < 0.5 || e > 4) continue;
        for (let x = 0; x < W; x += 2) { const i = (y*W + x)*nc; if (lum(i) > 4) continue;
          r += data[i]; g += data[i+1]; b += data[i+2]; n++; } }
      const fog = new T.Color(); if (n) fog.setRGB(r/n, g/n, b/n);
      const sunAz = ((Math.atan2(sunDir.x, -sunDir.z)/D2R) + 360) % 360;
      tex.dispose();
      res({env, sunDir, sunAz, sunEl: el, fog});
    }, undefined, rej));
  }

  // Rendered equirectangular backdrop (spec.backdrop, CONTRACT.md): the VISIBLE
  // sky. The HDR still lights the scene through PMREM; the backdrop is a JPEG
  // rendered from the theatre's Blender scene (far field only: sky, horizon and
  // anything beyond 120 m), drawn on a sphere just inside the gradient sky,
  // multiplied by spec.backdrop.dim so the console's additive sprites keep their
  // contrast, and rotated so its sun disc (sun_u, measured in the image) sits at
  // aimAz -- the azimuth the module was composed for, normally the HDR's
  // measured sun. The key light then takes the backdrop's sun elevation.
  // Sphere mapping, verified headless with a striped test texture: with
  // SphereGeometry scaled (-1, 1, 1) and FrontSide, texture u = 0 faces compass
  // 90 and u increases clockwise seen from above, so a pixel at u sits at
  // compass 90 + 360 u - rotation.y (degrees).
  function loadBackdrop(bd, aimAz) {
    const T = root.THREE;
    return new Promise((res, rej) => new T.TextureLoader().load(bd.url, tex => {
      tex.encoding = T.sRGBEncoding; tex.needsUpdate = true;
      const geo = new T.SphereGeometry(870, 64, 32); geo.scale(-1, 1, 1);
      const d = bd.dim == null ? 1 : bd.dim;
      const mesh = new T.Mesh(geo, new T.MeshBasicMaterial({map: tex, color: new T.Color(d, d, d), side: T.FrontSide,
                                                            fog: false, toneMapped: false, depthWrite: false, depthTest: false}));
      mesh.rotation.y = (90 + 360 * bd.sun_u - aimAz) * D2R;
      mesh.frustumCulled = false;
      // A pano is at infinity: the sphere follows whichever camera renders it (the
      // chase camera roams the whole theatre) and is drawn first, under everything,
      // so ground beyond its radius still covers it.
      mesh.renderOrder = -1000;
      mesh.onBeforeRender = (renderer, scene, camera) => {
        mesh.position.setFromMatrixPosition(camera.matrixWorld); mesh.updateMatrixWorld();
      };
      res({mesh, sunAz: aimAz, sunEl: bd.sun_el});
    }, undefined, rej));
  }

  // ---- Blender relief (spec.relief, CONTRACT.md): the theatre's height field from the
  //      UGV sim asset pack (the same generator that made the pano), int16 in
  //      /vendor/asset-relief-<mission>.js, oriented to the DISPLAYED pano: the pano
  //      image is mirrored relative to Blender world (verified: image u = 0.5 - lon/2pi
  //      of the camera frame for all four scenes), so Blender +X sits at compass
  //      360 (0.25 - sun_u) + sun.az and Blender +Y ninety degrees clockwise of it.
  //      The field fades to 0 toward its own edge; the module adds its detail noise
  //      and applies the corridor/prop flattening (invariant 1).
  const _relief = {};
  function reliefGrid(id) {
    if (_relief[id] !== undefined) return _relief[id];
    const src = (root.ARBITRAS_RELIEF || {})[id]; if (!src) return (_relief[id] = null);
    const bin = atob(src.b64), n = src.n, data = new Float32Array(n * n);
    for (let i = 0; i < n * n; i++) { let v = bin.charCodeAt(2*i) | (bin.charCodeAt(2*i + 1) << 8); if (v & 0x8000) v -= 0x10000; data[i] = (v + src.offset) * src.scale + src.lo; }
    return (_relief[id] = {n, size: src.size_m, data});
  }
  function reliefFor(spec, cx, cz) {
    const cfg = spec.relief || {}, g = cfg.id ? reliefGrid(cfg.id) : null, scale = cfg.scale == null ? 1 : cfg.scale;
    const blend = cfg.blend_m == null ? 45 : cfg.blend_m;
    if (!g) return {available: false, blend, at: () => 0};
    const sunU = spec.backdrop ? spec.backdrop.sun_u : 0.25, aim = spec.sun ? spec.sun.az : 0;
    const azX = (360 * (0.25 - sunU) + aim) * D2R, azY = azX + Math.PI / 2;
    const exx = Math.sin(azX), exz = -Math.cos(azX), eyx = Math.sin(azY), eyz = -Math.cos(azY);
    const n = g.n, half = g.size / 2, d = g.data;
    const at = (x, z) => {
      const rx = x - cx, rz = z - cz, xb = rx*exx + rz*exz, yb = rx*eyx + rz*eyz;
      const cheb = Math.max(Math.abs(xb), Math.abs(yb)); if (cheb >= half) return 0;
      const fade = 1 - smooth(half - 90, half, cheb);
      const fx = Math.min(Math.max((xb + half) / g.size * n - 0.5, 0), n - 1.001), fy = Math.min(Math.max((yb + half) / g.size * n - 0.5, 0), n - 1.001);
      const x0 = Math.floor(fx), y0 = Math.floor(fy), tx = fx - x0, ty = fy - y0;
      const h = (d[y0*n + x0]*(1 - tx) + d[y0*n + x0 + 1]*tx)*(1 - ty) + (d[(y0 + 1)*n + x0]*(1 - tx) + d[(y0 + 1)*n + x0 + 1]*tx)*ty;
      return h * scale * fade;
    };
    return {available: true, blend, at};
  }

  // ---- anti-tiling for a tiled ground map: the map sampled again ~7x larger and
  //      blended in, times a low-frequency value-noise albedo variation, so a 2k
  //      texture repeated 60-225 times over the theatre stops reading as a grid.
  //      Chains any onBeforeCompile the material already has.
  function antiTile(mat, opts = {}) {
    const macro = opts.macro == null ? 0.137 : opts.macro, mix = opts.mix == null ? 0.45 : opts.mix;
    const nScale = opts.noiseScale == null ? 0.021 : opts.noiseScale, nAmp = opts.noiseAmp == null ? 0.28 : opts.noiseAmp;
    const prev = mat.onBeforeCompile, prevKey = mat.customProgramCacheKey;
    const f = v => Number(v).toFixed(4);
    mat.onBeforeCompile = (sh, renderer) => {
      if (prev) prev(sh, renderer);
      sh.fragmentShader = sh.fragmentShader
        .replace('void main() {', `float arbHash(vec2 p){ return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
float arbNoise(vec2 p){ vec2 i = floor(p), f = fract(p); f = f*f*(3.0-2.0*f);
  return mix(mix(arbHash(i), arbHash(i+vec2(1.0,0.0)), f.x), mix(arbHash(i+vec2(0.0,1.0)), arbHash(i+vec2(1.0,1.0)), f.x), f.y); }
void main() {`)
        .replace('#include <map_fragment>', `#ifdef USE_MAP
  vec4 sampledDiffuseColor = texture2D( map, vUv );
  { vec4 arbMacro = texture2D( map, vUv * ${f(macro)} + vec2(0.31, 0.77) );
    float arbNz = arbNoise(vUv * ${f(nScale)}) * 2.0 - 1.0;
    sampledDiffuseColor.rgb = mix(sampledDiffuseColor.rgb, arbMacro.rgb, ${f(mix)}) * (1.0 + ${f(nAmp)} * arbNz); }
  diffuseColor *= sampledDiffuseColor;
#endif`);
    };
    mat.customProgramCacheKey = () => (prevKey ? prevKey.call(mat) : '') + '|antitile';
    return mat;
  }

  // ---- prototype scatter (spec.props): the pack's low-poly prototypes from
  //      /vendor/asset-props-<mission>.glb (Draco), one InstancedMesh per prototype
  //      variant, seeded positions over the theatre, culled from the corridor, every
  //      prop's rim and the ground station (invariants 1 and 3: set dressing only).
  //      Tree prototypes are one mesh, so trunk and canopy are told apart by height
  //      and radius and tinted per vertex. Async: adds to `scene` when the glb lands,
  //      pushes every mesh into `own` for the module's dispose, reports {props}.
  let _draco = null;
  function dracoLoader() {
    const T = root.THREE;
    if (!_draco && typeof T.DRACOLoader === 'function') { _draco = new T.DRACOLoader(); _draco.setDecoderPath('/vendor/three/draco/'); }
    return _draco;
  }
  function loadPrototypes(url) {
    const T = root.THREE;
    return new Promise((res, rej) => {
      if (typeof T.GLTFLoader !== 'function') return rej(new Error('no GLTFLoader'));
      const gl = new T.GLTFLoader(); const d = dracoLoader(); if (d) gl.setDRACOLoader(d);
      gl.load(url, g => {
        g.scene.updateMatrixWorld(true);
        const protos = {};
        g.scene.traverse(o => { if (o.isMesh) { const geo = o.geometry.clone(); geo.applyMatrix4(o.matrixWorld); geo.computeBoundingBox(); protos[o.name] = geo; } });
        res(protos);
      }, undefined, rej);
    });
  }
  function tintVertices(geo, spec) {
    // spec: {low:[r,g,b], high:[r,g,b], split:[t0,t1], radial:[r0,r1], jitter}
    const T = root.THREE, pos = geo.attributes.position, bb = geo.boundingBox, H = Math.max(1e-3, bb.max.y - bb.min.y);
    let rmax = 1e-3; const v = new T.Vector3();
    for (let i = 0; i < pos.count; i++) { v.fromBufferAttribute(pos, i); rmax = Math.max(rmax, Math.hypot(v.x, v.z)); }
    const col = new Float32Array(pos.count * 3), lo = spec.low, hi = spec.high, jit = spec.jitter == null ? 0.12 : spec.jitter;
    const s0 = spec.split ? spec.split[0] : 0.3, s1 = spec.split ? spec.split[1] : 0.45, r0 = spec.radial ? spec.radial[0] : 0.22, r1 = spec.radial ? spec.radial[1] : 0.4;
    for (let i = 0; i < pos.count; i++) {
      v.fromBufferAttribute(pos, i);
      const t = (v.y - bb.min.y) / H, r = Math.hypot(v.x, v.z) / rmax;
      const k = spec.high ? Math.max(smooth(s0, s1, t), smooth(r0, r1, r)) : 0;
      const j = 1 + jit * (Math.sin(v.x*12.9898 + v.y*78.233 + v.z*37.719) * 43758.5453 % 1);
      for (let c = 0; c < 3; c++) col[3*i + c] = (lo[c] + (spec.high ? (hi[c] - lo[c]) * k : 0)) * j;
    }
    geo.setAttribute('color', new T.BufferAttribute(col, 3));
  }
  function scatterProps(ctx, heightAt, cfg, own, helpers) {
    const T = root.THREE, scene = ctx.scene, m = ctx.mission, hw = m.corridor_half_width_m;
    const lite = !!ctx.lite, report = ctx.report || (() => {});
    if (!cfg || !cfg.url) return Promise.resolve(null);
    return loadPrototypes(cfg.url).then(protos => {
      const rnd = mulberry32(cfg.seed == null ? 1 : cfg.seed);
      const ext = helpers.extent(cfg.margin == null ? 420 : cfg.margin);
      const m4 = new T.Matrix4(), q = new T.Quaternion(), eu = new T.Euler(), pv = new T.Vector3(), sv = new T.Vector3(), tint = new T.Color();
      let total = 0, tris = 0, draws = 0;
      for (const g of cfg.groups || []) {
        const names = Object.keys(protos).filter(k => k.startsWith(g.proto + '_'));
        if (!names.length) continue;
        const n = Math.max(1, Math.round((g.n || 0) * (lite ? (cfg.lite_factor == null ? 0.35 : cfg.lite_factor) : 1)));
        // one InstancedMesh per variant; instances are dealt out round-robin
        const meshes = names.map(nm => {
          const geo = protos[nm];
          if (g.tint && !geo.attributes.color) tintVertices(geo, g.tint);
          const mat = new T.MeshStandardMaterial({vertexColors: !!g.tint, color: g.tint ? 0xffffff : new T.Color(g.color || '#888888'),
                                                 roughness: g.roughness == null ? 0.92 : g.roughness, metalness: 0, flatShading: !!g.flat, envMapIntensity: 0.5});
          const im = new T.InstancedMesh(geo, mat, Math.ceil(n / names.length) + 1);
          im.count = 0; im.frustumCulled = false; im.castShadow = g.castShadow !== false; im.receiveShadow = true;
          if (g.inset) im.layers.enable(INSET_LAYER);
          return im;
        });
        const keepC = hw + (g.keepOut && g.keepOut.corridor != null ? g.keepOut.corridor : 12);
        const keepP = g.keepOut && g.keepOut.prop != null ? g.keepOut.prop : 12;
        const gsR = 20 + (g.keepOut && g.keepOut.gs != null ? g.keepOut.gs : 12);
        const gs = m.ground_station;
        let placed = 0, tries = 0, k = 0;
        while (placed < n && tries++ < n * 40) {
          let e, nn;
          if (g.region === 'near') {                       // a band beside the route: pick a route point, offset laterally
            const L = helpers.routeLength(), s = rnd() * L, p = helpers.routePoint(s), p2 = helpers.routePoint(Math.min(s + 2, L));
            let te = p2.e - p.e, tn = p2.n - p.n; const tl = Math.hypot(te, tn) || 1; te /= tl; tn /= tl;
            const side = rnd() < 0.5 ? -1 : 1, off = keepC + rnd() * Math.max(1, (g.near_m || 120) - keepC);
            e = p.e - side * off * tn; nn = p.n + side * off * te;
          } else { e = ext.minE + rnd() * (ext.maxE - ext.minE); nn = ext.minN + rnd() * (ext.maxN - ext.minN); }
          if (Math.abs(helpers.lateralOffset(e, nn)) < keepC) continue;
          if (helpers.propClearance(e, nn) < keepP) continue;
          if (Math.hypot(e - gs.e, nn - gs.n) < gsR) continue;
          if (g.accept && !g.accept(e, nn)) continue;
          if (g.density && rnd() > g.density(e, nn)) continue;
          const x = e, z = -nn, s = (g.scale ? g.scale[0] + rnd() * (g.scale[1] - g.scale[0]) : 1);
          eu.set(0, rnd() * Math.PI * 2, 0); q.setFromEuler(eu);
          sv.set(s * (1 + (rnd() - 0.5) * (g.aniso || 0)), s, s * (1 + (rnd() - 0.5) * (g.aniso || 0)));
          pv.set(x, heightAt(x, z) - (g.sink || 0) * s, z);
          const im = meshes[k % meshes.length]; k++;
          m4.compose(pv, q, sv); im.setMatrixAt(im.count, m4);
          const v = 1 + (rnd() - 0.5) * (g.vary == null ? 0.25 : g.vary);
          im.setColorAt(im.count, tint.setRGB(v, v * (1 + (rnd() - 0.5) * 0.08), v)); im.count++;
          placed++;
        }
        for (const im of meshes) {
          if (!im.count) { im.geometry.dispose(); im.material.dispose(); continue; }
          im.instanceMatrix.needsUpdate = true; if (im.instanceColor) im.instanceColor.needsUpdate = true;
          scene.add(im); own.push(im); draws++;
          tris += im.count * (im.geometry.index ? im.geometry.index.count : im.geometry.attributes.position.count) / 3;
        }
        total += placed;
      }
      report({props: 'loaded', props_detail: {instances: total, draws, triangles: Math.round(tris)}});
      return {instances: total, draws, triangles: Math.round(tris)};
    }).catch(e => { report({props: 'fallback: ' + (e && e.message || e)}); return null; });
  }

  // The vendored Earth set every environment falls back to.
  const FALLBACK = {
    hdr: '/vendor/asset-earth-sky.hdr',
    ground: {diff:'/vendor/asset-earth-ground-diff-2k.jpg', nor:'/vendor/asset-earth-ground-nor_gl.jpg', rough:'/vendor/asset-earth-ground-rough.jpg'},
    rock:   {diff:'/vendor/asset-ground-diff.jpg', nor:'/vendor/asset-ground-nor_gl.jpg', rough:'/vendor/asset-ground-rough.jpg'},
  };

  // Per-mission helper set: the route-relative functions need the /mission object.
  function forMission(m) {
    const R = root.Route;
    return {
      D2R, INSET_LAYER, FALLBACK,
      makeNoise, mulberry32, smooth, azel, toScene, roundedRect,
      loadTexture, loadHDR, loadBackdrop, withTimeout, antiTile,
      lateralOffset: (e, n) => R.lateralOffset(m, e, n),
      routePoint: s => R.routePoint(m, s),
      routeLength: () => R.routeLength(m),
      // Axis-aligned box (ENU) around the route, props and ground station, plus a margin.
      extent: (margin = 500) => {
        const es = m.route.map(w => w.e).concat(m.props.map(p => p.e), [m.ground_station.e]);
        const ns = m.route.map(w => w.n).concat(m.props.map(p => p.n), [m.ground_station.n]);
        return {minE: Math.min(...es) - margin, maxE: Math.max(...es) + margin,
                minN: Math.min(...ns) - margin, maxN: Math.max(...ns) + margin};
      },
      // Distance to the nearest prop centre minus its radius (negative = inside).
      propClearance: (e, n) => {
        let best = Infinity;
        for (const p of m.props) best = Math.min(best, Math.hypot(e - p.e, n - p.n) - (p.radius_m || 0));
        best = Math.min(best, Math.hypot(e - m.ground_station.e, n - m.ground_station.n));
        return best;
      },
      // Blender relief oriented to the displayed pano, centred on the route box (spec.relief).
      relief(spec) {
        const es = m.route.map(w => w.e), ns = m.route.map(w => w.n);
        return reliefFor(spec, (Math.min(...es) + Math.max(...es)) / 2, -(Math.min(...ns) + Math.max(...ns)) / 2);
      },
      // Prototype scatter (spec.props); see scatterProps above.
      scatterProps(ctx, heightAt, cfg, own) { return scatterProps(ctx, heightAt, cfg, own, this); },
    };
  }

  root.ARBITRAS_HELPERS = {D2R, INSET_LAYER, FALLBACK, makeNoise, mulberry32, smooth, azel, toScene,
                          roundedRect, loadTexture, loadHDR, loadBackdrop, withTimeout, antiTile, reliefGrid, forMission};
})(typeof self !== 'undefined' ? self : this);
