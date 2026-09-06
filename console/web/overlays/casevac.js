/* CASEVAC overlay -- the console half of the route-constrained terrain fix
 * (console/web/overlays/CONTRACT.md; backend/missions_casevac.py).
 *
 * Draws ONLY what the mechanism adds, every number with its contract path:
 *   - ring + pin at terrain.route_fix.position (the terrain-referenced fix)
 *   - a bar along the route between s_believed_m and s_pinned_m, labelled with
 *     terrain.route_fix.correction_m
 *   - the last pinned transition point on the route with its classes
 *     (SENSOR · SIMULATED: the wheel sensor is a confusion-matrix stand-in)
 *   - a CCP caption: AT CCP · TERRAIN-CONFIRMED when terrain.route_fix.at_ccp
 * It never moves the vehicle. Positions come from the block's lat/lon through
 * the mission's frame conversion (a pure conversion), heights from heightAt.
 */
(function () {
  window.ARBITER_OVERLAY = window.ARBITER_OVERLAY || {};

  const PATH = 'terrain.route_fix';
  const fmt = (v, d = 0) => (v == null || !isFinite(v)) ? '—' : (Math.abs(v) < 0.05 && d === 0 ? '0' : v.toFixed(d));
  const sgn = (v, d = 0) => (v == null || !isFinite(v)) ? '—' : ((v > 0 ? '+' : '') + v.toFixed(d));

  function mount(ctx) {
    const T = ctx.THREE, scene = ctx.scene, H = ctx.helpers, C = ctx.colors;
    const m = ctx.mission;
    const ccpProp = (m.props || []).find(p => p.kind === 'ccp') || null;
    const own = [], labels = [];
    const add = o => { scene.add(o); own.push(o); return o; };
    const mkLabel = (cls) => {
      const d = document.createElement('div'); d.className = 'skl env ' + cls;
      d.style.opacity = 0; ctx.labels.appendChild(d);
      const L = { el: d, p: new T.Vector3(), on: false };
      labels.push(L); return L;
    };
    const ground = (e, n) => ctx.heightAt(e, -n);
    const at = (v, e, n, lift) => v.set(e, ground(e, n) + (lift || 0), -n);

    // --- the fix: flat ring on the terrain + a pin with a head ---------------
    const fixG = add(new T.Group());
    const ringMat = new T.MeshBasicMaterial({ color: C.corrected, transparent: true, opacity: 0.9,
                                             side: T.DoubleSide, depthWrite: false });
    const ring = new T.Mesh(new T.RingGeometry(2.4, 3.2, 48).rotateX(-Math.PI / 2), ringMat);
    const ring2 = new T.Mesh(new T.RingGeometry(0.5, 0.9, 32).rotateX(-Math.PI / 2), ringMat);
    const pin = new T.Mesh(new T.CylinderGeometry(0.1, 0.1, 5.0, 8), new T.MeshBasicMaterial({ color: C.corrected }));
    pin.position.y = 2.5;
    const head = new T.Mesh(new T.SphereGeometry(0.45, 12, 8), new T.MeshBasicMaterial({ color: C.corrected }));
    head.position.y = 5.0;
    fixG.add(ring, ring2, pin, head);
    const lblFix = mkLabel('');

    // --- the correction bar along the route: believed -> pinned ---------------
    const MAXPTS = 512;
    const barGeom = new T.BufferGeometry();
    barGeom.setAttribute('position', new T.BufferAttribute(new Float32Array(MAXPTS * 3), 3));
    barGeom.setDrawRange(0, 0);
    const bar = add(new T.Line(barGeom, new T.LineBasicMaterial({ color: C.corrected, transparent: true, opacity: 0.95 })));
    const capB = add(new T.Mesh(new T.RingGeometry(1.0, 1.6, 32).rotateX(-Math.PI / 2),
                                new T.MeshBasicMaterial({ color: C.believed, transparent: true, opacity: 0.9, side: T.DoubleSide, depthWrite: false })));
    const lblBar = mkLabel('');

    // --- the last pinned transition on the route ------------------------------
    const trG = add(new T.Group());
    const trMat = new T.MeshBasicMaterial({ color: C.amber, transparent: true, opacity: 0.9, side: T.DoubleSide, depthWrite: false });
    const trRing = new T.Mesh(new T.RingGeometry(1.3, 1.9, 32).rotateX(-Math.PI / 2), trMat);
    const trPost = new T.Mesh(new T.CylinderGeometry(0.07, 0.07, 2.4, 6), new T.MeshBasicMaterial({ color: C.amber }));
    trPost.position.y = 1.2;
    // a short bar across the route at the boundary, so the boundary reads as a line on the ground
    const trBar = new T.Mesh(new T.BoxGeometry(0.25, 0.12, 9.0), trMat);
    trBar.position.y = 0.08;
    trG.add(trRing, trPost, trBar);
    const lblTr = mkLabel('');

    // --- CCP caption -----------------------------------------------------------
    const lblCcp = mkLabel('');
    if (ccpProp) at(lblCcp.p, ccpProp.e, ccpProp.n, 3.2);

    const hideAll = () => { fixG.visible = bar.visible = capB.visible = trG.visible = false;
                            for (const L of labels) L.on = false; };
    hideAll();

    let last = null, lastEpoch = null;

    function epoch(payload, frame) {
      const rf = payload && payload.terrain && payload.terrain.route_fix;
      last = rf || null; lastEpoch = payload ? payload.epoch_index : null;
      if (!rf || !rf.available || !rf.position || rf.s_pinned_m == null) { hideAll(); return; }

      // the fix: block lat/lon -> ENU (frame conversion only)
      const P = ctx.latLonToEnu(rf.position.lat, rf.position.lon);
      at(fixG.position, P.e, P.n, 0.06);
      fixG.visible = true;
      lblFix.el.textContent = 'TERRAIN FIX · S ' + fmt(rf.s_pinned_m) + ' M · SENSOR · SIMULATED · ' + PATH + '.position';
      lblFix.el.style.color = C.corrected;
      at(lblFix.p, P.e, P.n, 6.2); lblFix.on = true;

      // the correction bar between s_believed and s_pinned, along the route
      const sB = rf.s_believed_m, sP = rf.s_pinned_m;
      if (sB != null && rf.correction_m != null) {
        const s0 = Math.min(sB, sP), s1 = Math.max(sB, sP), len = s1 - s0;
        const n = Math.max(2, Math.min(MAXPTS, Math.ceil(len / 2) + 1));
        const pos = barGeom.attributes.position.array;
        for (let i = 0; i < n; i++) {
          const s = s0 + (len * i) / (n - 1), q = H.routePoint(s);
          pos[3 * i] = q.e; pos[3 * i + 1] = ground(q.e, q.n) + 0.9; pos[3 * i + 2] = -q.n;
        }
        barGeom.setDrawRange(0, n); barGeom.attributes.position.needsUpdate = true;
        barGeom.computeBoundingSphere();
        bar.visible = len > 0.5;
        const qb = H.routePoint(sB);
        at(capB.position, qb.e, qb.n, 0.05); capB.visible = true;
        const qm = H.routePoint((s0 + s1) / 2);
        lblBar.el.textContent = 'TERRAIN FIX · CORRECTION ' + sgn(rf.correction_m) + ' M · ' + PATH + '.correction_m';
        lblBar.el.style.color = C.corrected;
        at(lblBar.p, qm.e, qm.n, 2.6); lblBar.on = true;
      } else { bar.visible = capB.visible = false; lblBar.on = false; }

      // the last pinned transition
      const tr = rf.transition;
      if (tr && tr.s_m != null) {
        const q = H.routePoint(tr.s_m);
        at(trG.position, q.e, q.n, 0.05); trG.rotation.y = q.heading; trG.visible = true;
        lblTr.el.textContent = (tr.from || '?').toUpperCase() + ' → ' + (tr.to || '?').toUpperCase() + ' · S ' + fmt(tr.s_m) +
          ' M · PINNED E' + tr.epoch + ' · SENSOR · SIMULATED · ' + PATH + '.transition';
        lblTr.el.style.color = C.amber;
        at(lblTr.p, q.e, q.n, 3.4); lblTr.on = true;
      } else { trG.visible = false; lblTr.on = false; }

      // CCP caption on the terrain fix
      if (ccpProp) {
        if (rf.at_ccp) {
          lblCcp.el.textContent = 'AT CCP · TERRAIN-CONFIRMED · ' + PATH + '.at_ccp';
          lblCcp.el.style.color = C.corrected; lblCcp.on = true;
        } else if (rf.ccp_range_m != null) {
          lblCcp.el.textContent = 'CCP · ' + fmt(rf.ccp_range_m) + ' M ON TERRAIN FIX · NOT CONFIRMED · ' + PATH + '.ccp_range_m';
          lblCcp.el.style.color = C.dim; lblCcp.on = true;
        } else lblCcp.on = false;
      }
      if (ctx.report) ctx.report('terrain fix ' + sgn(rf.correction_m) + ' m' + (rf.at_ccp ? ' · AT CCP' : ''));
    }

    function tick(dt, cam) {
      for (const L of labels) {
        if (!L.on) { if (L.el.style.opacity !== '0') L.el.style.opacity = 0; continue; }
        const r = ctx.project(L.p);
        L.el.style.left = (r[2] ? r[0] : 0) + 'px'; L.el.style.top = (r[2] ? r[1] : 0) + 'px';
        L.el.style.opacity = r[2] ? 1 : 0;
      }
    }

    function hud() {
      const rf = last;
      if (!rf) return { title: 'TERRAIN-REFERENCED FIX', lines: [{ k: 'ROUTE FIX', v: 'no block', path: PATH }] };
      const tr = rf.transition;
      return {
        title: 'TERRAIN-REFERENCED FIX',
        lines: [
          { k: 'SENSED · SIM / MAP @ BELIEVED', v: (rf.sensed_class || '—') + ' / ' + (rf.map_class_at_believed || '—'),
            path: PATH + '.sensed_class · ' + PATH + '.map_class_at_believed (terrain.sensed.class / terrain.map_at_position.class are the channel at the antenna)' },
          { k: 'CORRECTION', v: rf.available ? sgn(rf.correction_m, 1) + ' M' : 'NOT PINNED', path: PATH + '.correction_m' },
          { k: 'LAST TRANSITION', v: tr ? (tr.from || '?') + '→' + (tr.to || '?') + ' @ ' + fmt(tr.s_m) + ' M · E' + tr.epoch : '—',
            path: PATH + '.transition' },
          { k: 'AT CCP', v: rf.at_ccp ? 'TERRAIN-CONFIRMED' : (rf.ccp_range_m != null ? 'NO · ' + fmt(rf.ccp_range_m) + ' M' : 'NO'),
            path: PATH + '.at_ccp · ' + PATH + '.ccp_range_m' },
          { k: 'FIX ERROR (REPLAY)', v: rf._error_vs_truth_m != null ? fmt(rf._error_vs_truth_m, 1) + ' M' : '—',
            path: PATH + '._error_vs_truth_m (replay metadata)' },
        ],
      };
    }

    function status() {
      if (!last) return 'casevac: no route_fix';
      return 'casevac route_fix e' + lastEpoch + ' avail=' + last.available + ' corr=' + fmt(last.correction_m, 1) +
        ' s_bel=' + fmt(last.s_believed_m, 1) + ' s_pin=' + fmt(last.s_pinned_m, 1) + ' at_ccp=' + last.at_ccp +
        ' err=' + fmt(last._error_vs_truth_m, 2) + ' labels=' + labels.filter(L => L.on).length;
    }

    function dispose() {
      for (const o of own) { scene.remove(o); o.traverse && o.traverse(c => { if (c.geometry) c.geometry.dispose(); if (c.material) c.material.dispose(); }); }
      for (const L of labels) L.el.remove();
    }

    return { epoch, tick, hud, status, dispose };
  }

  window.ARBITER_OVERLAY.casevac = { id: 'casevac', mount };
})();
