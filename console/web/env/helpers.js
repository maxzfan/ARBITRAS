/* Environment helpers -- shared by the console's EnvScene, the hero, the
 * selector tiles and the env/preview.html harness (console/web/env/CONTRACT.md).
 *
 * Extracted from index.html so that every environment module builds against
 * ONE implementation of noise, texture/HDR loading and the scene frame.
 * Classic script: defines window.ARBITER_HELPERS. Requires window.THREE and
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
      loadTexture, loadHDR, withTimeout,
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
    };
  }

  root.ARBITER_HELPERS = {D2R, INSET_LAYER, FALLBACK, makeNoise, mulberry32, smooth, azel, toScene,
                          roundedRect, loadTexture, loadHDR, withTimeout, forMission};
})(typeof self !== 'undefined' ? self : this);
