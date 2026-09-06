/* LOGISTICS environment -- northern prairie at noon (tracks/TRACK_F.md §2).
 *
 * The reference implementation of console/web/env/CONTRACT.md: this is the
 * console's original terrain, rocks and ground set, moved behind the contract
 * so the other three theatres can be built the same way. Everything here is
 * set dressing; the console draws every measured thing on top of it.
 *
 * Assets: the vendored Earth set (Poly Haven, CC0): kloofendal_48d_partly_cloudy
 * sky for lighting, grass_path_2 ground, rocky_terrain rocks. No new downloads.
 */
(function () {
  const SPEC = {
    hdr: '/vendor/asset-earth-sky.hdr',
    exposure: 1.5,
    // Visible sky: the "grassland" theatre rendered in Blender/Cycles (UGV sim asset pack), an
    // equirectangular far field (sky, horizon, anything beyond 120 m; near props are ours). The HDR
    // above still lights the scene. sun_u/sun_el: the clipped sun disc measured in the image
    // (u 0.6725, el 11.0; the pack's manifest agrees on el). dim 0.58: the pano's 10-25 deg band
    // (linear luminance 0.468) brought to the previous sky.mid target (0.274) so the console's
    // additive sprites keep their contrast. Golden hour replaces noon: the key light drops to el 11.
    backdrop: { url: '/vendor/asset-backdrop-logistics.jpg', sun_u: 0.6725, sun_el: 11.0, dim: 0.58 },
    // Relief: the grassland theatre's Blender height field (asset-relief-logistics.js, +-5.5 m),
    // oriented to the displayed pano, blended in over 45 m from the corridor edge (invariant 1 holds:
    // flat within hw + 3, every prop's rim + 7 and 20 m of the ground station).
    relief: { id: 'logistics', scale: 1.0, blend_m: 45 },
    // Set dressing from the pack's prototypes (asset-props-logistics.glb): acacias, bushes, rocks; instanced,
    // seeded, culled from the corridor. Vertex tints are linear RGB (trunk -> canopy by height and radius).
    props: { url: '/vendor/asset-props-logistics.glb', seed: 5, margin: 420, groups: [
      { proto: 'Tree', n: 90, scale: [0.85, 1.35], sink: 0.05, inset: true, keepOut: { corridor: 26, prop: 16 },
        tint: { low: [0.06, 0.045, 0.028], high: [0.03, 0.06, 0.018], split: [0.30, 0.45], radial: [0.20, 0.40] } },
      { proto: 'Bush', n: 220, scale: [0.6, 1.4], sink: 0.10, castShadow: false, keepOut: { corridor: 10, prop: 10 },
        tint: { low: [0.03, 0.04, 0.014], high: [0.055, 0.075, 0.025], split: [0.2, 0.6], radial: [0.9, 1.0] } },
      { proto: 'Rock', n: 120, scale: [0.6, 3.2], sink: 0.25, inset: true, color: '#5C574D', keepOut: { corridor: 8, prop: 12 } },
    ] },
    fog: { color: '#7A7B78', density: 0.0022 },           // ACES^-1 at exposure 1.5 of the backdrop's horizon as seen (#A3A4A0), so the ground fades into it
    sky: { horizon: '#A3A4A0', mid: '#83919E', zenith: '#596A7F' },   // first paint and fallback: the backdrop's 0.5-4 / 10-25 / 55-90 deg bands at dim 0.58
    sun: { az: 124.4, el: 47.3 },                          // az: measured from the HDR, the aim for the backdrop's sun; el comes from backdrop.sun_el at load
    palette: { ground: '#6B6A5E', rock: '#5C574D', accent: '#E8A21C' },
    attribution: 'Ground: Poly Haven, CC0 · sky: rendered scene (Blender), lit by a Poly Haven HDR',
    hero_camera: { az: 215, el: 13, dist: 82, dolly: 0.6 },   // low oblique over the convoy's left shoulder
  };

  function build(ctx) {
    const T = ctx.THREE, H = ctx.helpers, m = ctx.mission, scene = ctx.scene;
    const hw = m.corridor_half_width_m, gs = m.ground_station;
    const noise = H.makeNoise(7), rnd = H.mulberry32(11);
    const aniso = ctx.renderer.capabilities.getMaxAnisotropy();
    const own = [];                                         // everything we must dispose

    // -- terrain: gentle prairie undulation, FLAT along the corridor, under the
    //    ground station and inside every prop's rim (contract invariant 1). ------
    const R = H.relief(SPEC);                               // Blender relief (spec.relief); at() is 0 without the asset
    const heightAt = (x, z) => {
      const base = 2.4*noise(x/70 + 3.1, z/70 + 1.7) + 0.7*noise(x/22, z/22) + 0.18*noise(x/6, z/6);
      const lat = Math.abs(H.lateralOffset(x, -z));
      const clear = H.propClearance(x, -z), gsd = Math.hypot(x - gs.e, -z - gs.n);
      return base * H.smooth(hw + 3, hw + 16, lat) * H.smooth(7, 20, clear)
           + R.at(x, z) * H.smooth(hw + 3, hw + 3 + R.blend, lat) * H.smooth(7, 7 + R.blend*0.6, clear) * H.smooth(20, 44, gsd);
    };
    const ext = H.extent(500);
    const TW = ext.maxE - ext.minE, TD = ext.maxN - ext.minN, TCx = (ext.minE + ext.maxE)/2, TCz = -(ext.minN + ext.maxN)/2;
    const groundMat = H.antiTile(new T.MeshStandardMaterial({color: new T.Color(SPEC.palette.ground), roughness:1, metalness:0, envMapIntensity:1.0}));
    {
      const geo = new T.PlaneGeometry(TW, TD, Math.min(Math.round(TW/10), 200), Math.min(Math.round(TD/10), 200));
      const pa = geo.attributes.position;
      for (let i = 0; i < pa.count; i++) pa.setZ(i, heightAt(pa.getX(i) + TCx, -pa.getY(i) + TCz));
      geo.computeVertexNormals();
      const ground = new T.Mesh(geo, groundMat);
      ground.rotation.x = -Math.PI/2; ground.position.set(TCx, 0, TCz); ground.receiveShadow = true;
      ground.layers.enable(H.INSET_LAYER); scene.add(ground); own.push(ground);
    }

    // -- rocks: instanced, seeded, never in the corridor or on a prop ------------
    const rockMat = new T.MeshStandardMaterial({color: new T.Color(SPEC.palette.rock), roughness:0.92, metalness:0.02, envMapIntensity:0.55});
    {
      const rockGeo = new T.DodecahedronGeometry(1, 1);
      const pa = rockGeo.attributes.position, rv = new T.Vector3();
      for (let i = 0; i < pa.count; i++) { rv.fromBufferAttribute(pa, i);
        rv.multiplyScalar(1 + 0.30*noise(rv.x*1.7 + 5.2, rv.y*1.7 + rv.z*0.9)); pa.setXYZ(i, rv.x, rv.y*0.8, rv.z); }
      rockGeo.computeVertexNormals();
      const rocks = new T.InstancedMesh(rockGeo, rockMat, 340);
      const m4 = new T.Matrix4(), q = new T.Quaternion(), e = new T.Euler(), pv = new T.Vector3(), sv = new T.Vector3();
      let n = 0, tries = 0;
      const put = (x, z, s) => {
        e.set(rnd()*Math.PI, rnd()*Math.PI, rnd()*Math.PI); q.setFromEuler(e);
        sv.set(s*(0.8+rnd()*0.5), s*(0.55+rnd()*0.5), s*(0.8+rnd()*0.5));
        m4.compose(pv.set(x, heightAt(x, z) - 0.22*s, z), q, sv); rocks.setMatrixAt(n++, m4);
      };
      while (n < 320 && tries++ < 12000) {
        const x = TCx + (rnd()-0.5)*(TW-300), z = TCz + (rnd()-0.5)*(TD-300);
        if (Math.abs(H.lateralOffset(x, -z)) < hw + 8) continue;
        if (H.propClearance(x, -z) < 12) continue;
        if (rnd() > 0.35 + 0.65*(noise(x/40, z/40)+1)/2) continue;
        put(x, z, 0.35 + rnd()*rnd()*1.9);
      }
      for (let i = 0; i < 20; i++) { const a = rnd()*Math.PI*2, d = 160 + rnd()*200;   // skyline outcrops
        put(TCx + Math.sin(a)*d, TCz + Math.cos(a)*d, 5 + rnd()*9); }
      rocks.count = n; rocks.castShadow = true; rocks.receiveShadow = true; rocks.layers.enable(H.INSET_LAYER);
      rocks.frustumCulled = false;   // r147 culls InstancedMesh on the single-instance bounds at the origin
      scene.add(rocks); own.push(rocks);
    }

    // -- dry grass tufts: one InstancedMesh of three crossed blades, vertex-tinted,
    //    seeded, outside the corridor; swayed in tick(). Not on the inset layer. ----
    let grass = null; const GRASS_N = 2600;
    {
      const g = new T.BufferGeometry();
      const verts = [], cols = [];
      const blade = (rot) => {                              // one blade: a thin triangle 0.45 m tall
        const c = Math.cos(rot), s = Math.sin(rot), w = 0.05, h = 0.45;
        verts.push(-w*c, 0, -w*s,  w*c, 0, w*s,  0, h, 0);
        cols.push(0.36,0.33,0.20, 0.36,0.33,0.20, 0.62,0.58,0.36);
      };
      blade(0); blade(Math.PI/3); blade(2*Math.PI/3);
      g.setAttribute('position', new T.Float32BufferAttribute(verts, 3));
      g.setAttribute('color', new T.Float32BufferAttribute(cols, 3));
      g.computeVertexNormals();
      const mat = new T.MeshStandardMaterial({vertexColors:true, roughness:1, metalness:0, side:T.DoubleSide});
      grass = new T.InstancedMesh(g, mat, GRASS_N);
      const m4 = new T.Matrix4(), q = new T.Quaternion(), e = new T.Euler(), pv = new T.Vector3(), sv = new T.Vector3();
      let n = 0, tries = 0;
      while (n < GRASS_N && tries++ < GRASS_N*6) {
        const x = TCx + (rnd()-0.5)*(TW-400), z = TCz + (rnd()-0.5)*(TD-400);
        if (Math.abs(H.lateralOffset(x, -z)) < hw + 1.5) continue;
        if (H.propClearance(x, -z) < 4) continue;
        if (rnd() > 0.45 + 0.55*(noise(x/25 + 9, z/25)+1)/2) continue;
        e.set(0, rnd()*Math.PI*2, 0); q.setFromEuler(e); const s = 0.7 + rnd()*0.8;
        sv.set(s, s*(0.8 + rnd()*0.6), s);
        m4.compose(pv.set(x, heightAt(x, z), z), q, sv); grass.setMatrixAt(n++, m4);
      }
      grass.count = n; grass.castShadow = false; grass.receiveShadow = true; grass.frustumCulled = false;
      scene.add(grass); own.push(grass);
    }

    // -- set dressing at the FOB: a few stacked blocks and a mast, outside the ring --
    for (const p of m.props) {
      if (p.kind !== 'fob') continue;
      const dark = new T.MeshStandardMaterial({color:0x5A5548, roughness:0.9});
      const r = (p.radius_m || 30) + 6;
      for (let i = 0; i < 7; i++) {
        // an arc on the SOUTH side of the pad (bearings ~125-210 deg): the route leaves north
        const a = 2.2 + i*0.22, bx = p.e + Math.sin(a)*r, bz = -(p.n + Math.cos(a)*r);
        const b = new T.Mesh(new T.BoxGeometry(2.2, 1.4 + (i%2)*0.9, 2.2), dark);
        b.position.set(bx, heightAt(bx, bz) + b.geometry.parameters.height/2, bz);
        b.castShadow = b.receiveShadow = true; b.layers.enable(H.INSET_LAYER); scene.add(b); own.push(b);
      }
    }

    // -- textures: swap in when they arrive; report either way ----------------------
    ctx.report({ground:'loading', vegetation:'loaded'});
    Promise.all([
      H.loadTexture(H.FALLBACK.ground.diff, true, 225, aniso), H.loadTexture(H.FALLBACK.ground.nor, false, 225, aniso),
      H.loadTexture(H.FALLBACK.ground.rough, false, 225, aniso),
    ]).then(([map, nor, rough]) => {
      const rep = Math.round(Math.max(TW, TD) / 3.5);                      // one tile ~3.5 m
      for (const t of [map, nor, rough]) { t.repeat.set(rep, rep); t.needsUpdate = true; own.push(t); }
      Object.assign(groundMat, {map, normalMap:nor, roughnessMap:rough, color:new T.Color(0xFFFFFF), envMapIntensity:1.0});
      groundMat.normalScale.set(0.8, 0.8); groundMat.needsUpdate = true;
      ctx.report({ground:'loaded'});
    }).catch(() => ctx.report({ground:'fallback', note:'ground textures failed; flat colour'}));
    Promise.all([
      H.loadTexture(H.FALLBACK.rock.diff, true, 1.6, aniso), H.loadTexture(H.FALLBACK.rock.nor, false, 1.6, aniso),
      H.loadTexture(H.FALLBACK.rock.rough, false, 1.6, aniso),
    ]).then(([rm, rn, rr]) => {
      Object.assign(rockMat, {map:rm, normalMap:rn, roughnessMap:rr, color:new T.Color(0xB8AC9C), envMapIntensity:1.0});
      rockMat.needsUpdate = true; own.push(rm, rn, rr);
    }).catch(() => {});

    let sway = 0;
    H.scatterProps(ctx, heightAt, SPEC.props, own);       // async set dressing; meshes join `own`

    return {
      heightAt,
      bounds: {minX: ext.minE, maxX: ext.maxE, minZ: -ext.maxN, maxZ: -ext.minN},
      sunAz: SPEC.sun.az, sunEl: SPEC.sun.el,
      tick(dt) { if (!grass) return; sway += dt; grass.rotation.z = Math.sin(sway*0.9)*0.012; },
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
  window.ARBITRAS_ENV.logistics = { id: 'logistics', spec: SPEC, build };
})();
