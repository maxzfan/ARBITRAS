/* RECON overlay -- the stationary deduction (console/web/overlays/CONTRACT.md).
 *
 * Console half of backend/missions_recon.py. Draws ONLY what the block
 * `geometry.correction.stationary` adds:
 *   - the ANCHOR: a hollow ring on the terrain at the dead-reckoned position of
 *     the last trusted fix (colour = corrected), with a label;
 *   - the DEDUCED OFFSET: a dashed line from the anchor to the believed ghost;
 *   - while stationary and the bearing is claimed: an arrow from the anchor
 *     toward the emitter, labelled with the bearing and the path delay.
 * Every number on screen carries its contract path. The only arithmetic here
 * is the frame conversion the contract allows (ENU of a lat/lon): the anchor
 * is placed exactly as the console places any stream position,
 * TRUE + (ENU(anchor) - ENU(_truth)). Nothing here moves the vehicle.
 */
(function () {
  const BLOCK = 'geometry.correction.stationary';
  const ARROW_M = 40;                  // display length of the bearing arrow; a bearing has no range
  const D2R = Math.PI / 180;

  function mount(ctx) {
    const T = ctx.THREE, scene = ctx.scene, C = ctx.colors;
    // -- scene objects ------------------------------------------------------
    const ring = new T.Mesh(new T.RingGeometry(2.4, 3.1, 48),
      new T.MeshBasicMaterial({color: C.corrected, side: T.DoubleSide, transparent: true, opacity: 0.95,
                               toneMapped: false, depthWrite: false}));
    ring.rotation.x = -Math.PI / 2; ring.visible = false; ring.renderOrder = 5; scene.add(ring);
    const ring2 = new T.Mesh(new T.RingGeometry(0.9, 1.2, 32), ring.material.clone());
    ring2.rotation.x = -Math.PI / 2; ring2.visible = false; ring2.renderOrder = 5; scene.add(ring2);

    const lineGeo = new T.BufferGeometry().setFromPoints([new T.Vector3(), new T.Vector3(0, 0, 1)]);
    const line = new T.Line(lineGeo, new T.LineDashedMaterial({color: C.amber, dashSize: 3, gapSize: 2,
      transparent: true, opacity: 0.95, toneMapped: false, depthTest: false}));
    line.computeLineDistances();       // creates the lineDistance attribute once; tick() rewrites it in place
    line.visible = false; line.frustumCulled = false; line.renderOrder = 6; scene.add(line);

    const redMat = new T.MeshBasicMaterial({color: C.red, toneMapped: false, depthTest: false, transparent: true, opacity: 0.95});
    const arrow = new T.Mesh(new T.CylinderGeometry(0.35, 0.35, 1, 8), redMat);   // unit shaft, scaled to ARROW_M in tick()
    arrow.visible = false; arrow.frustumCulled = false; arrow.renderOrder = 6; scene.add(arrow);
    const head = new T.Mesh(new T.ConeGeometry(1.6, 4.2, 12), redMat);
    head.visible = false; head.renderOrder = 6; scene.add(head);

    // -- labels (HTML, projected each frame) -------------------------------
    const mk = (color) => { const d = document.createElement('div'); d.className = 'skl env';
      d.style.color = color; d.style.opacity = 0; d.style.whiteSpace = 'nowrap'; ctx.labels.appendChild(d); return d; };
    const lblAnchor = mk(C.corrected), lblOffset = mk(C.amber), lblArrow = mk(C.red);

    // -- per-epoch targets and the lerp state (no allocation in tick) -------
    const st = {
      block: null, epoch: null, state: null, anchorEpoch: null,
      prevA: new T.Vector3(), tgtA: new T.Vector3(), curA: new T.Vector3(),
      prevG: new T.Vector3(), tgtG: new T.Vector3(), curG: new T.Vector3(),
      dir: new T.Vector3(1, 0, 0), t0: 0, interval: 70, lastEpochAt: 0,
      showRing: false, showLine: false, showArrow: false, offBearing: false, drawnOnce: false,
    };
    const tmp = new T.Vector3(), tmpB = new T.Vector3(), mid = new T.Vector3();
    const linePos = lineGeo.attributes.position, lineDist = lineGeo.attributes.lineDistance;
    const UP = new T.Vector3(0, 1, 0);

    const put = (el, v) => { const p = ctx.project(v); const vis = p[2];
      el.style.left = (vis ? p[0] : 0) + 'px'; el.style.top = (vis ? p[1] : 0) + 'px'; el.style.opacity = vis ? 1 : 0; };
    const hide = (el) => { el.style.opacity = 0; };
    const fmt = (v, d) => (v == null ? '—' : (+v).toFixed(d));

    function epoch(payload, frame) {
      const now = performance.now();
      if (st.lastEpochAt) st.interval = Math.min(2000, Math.max(20, now - st.lastEpochAt));
      st.lastEpochAt = now; st.t0 = now;
      st.epoch = payload.epoch_index; st.state = payload.state;
      const corr = ((payload.geometry || {}).correction) || {};
      const b = corr.stationary || null;
      st.block = b;
      st.prevA.copy(st.drawnOnce ? st.curA : frame.truePos);
      st.prevG.copy(st.drawnOnce ? st.curG : frame.ghostPos);
      st.showRing = st.showLine = st.showArrow = false;
      if (!b || !b.anchor || !payload._truth) { st.drawnOnce = false; return; }
      // anchor in the scene: TRUE + (ENU(anchor) - ENU(_truth)) -- the console's own placement rule
      const a = ctx.latLonToEnu(b.anchor.lat, b.anchor.lon), t = ctx.latLonToEnu(payload._truth.lat, payload._truth.lon);
      st.tgtA.set(frame.truePos.x + (a.e - t.e), 0, frame.truePos.z - (a.n - t.n));
      st.tgtA.y = ctx.heightAt(st.tgtA.x, st.tgtA.z) + 0.12;
      st.tgtG.copy(frame.ghostPos); st.tgtG.y = ctx.heightAt(st.tgtG.x, st.tgtG.z) + 0.9;
      st.anchorEpoch = b.anchor_epoch;
      st.showRing = true;
      st.showLine = !!(b.anchored && b.deduced_offset);
      st.showArrow = !!(b.emitter_bearing_deg != null);
      if (st.showArrow) { const az = b.emitter_bearing_deg * D2R; st.dir.set(Math.sin(az), 0, -Math.cos(az)); }
      lblAnchor.textContent = (b.anchored ? 'ANCHOR · HELD FROM EPOCH ' + b.anchor_epoch : 'ANCHOR · LIVE FIX')
        + ' · ' + BLOCK + '.anchor';
      // a bearing is only meaningful once the offset is outside the corrected fix's own PL
      const pl = corr.protection_level_m, off = b.deduced_offset;
      st.offBearing = !!(off && off.bearing_deg != null && (pl == null || off.mag_m > pl));
      lblOffset.textContent = st.showLine
        ? 'DEDUCED OFFSET · ' + fmt(off.mag_m, 0) + ' M' + (st.offBearing ? ' · ' + fmt(off.bearing_deg, 0) + '°' : '')
          + ' · ' + BLOCK + '.deduced_offset.mag_m' : '';
      lblArrow.textContent = st.showArrow
        ? 'EMITTER BEARING ' + fmt(b.emitter_bearing_deg, 0) + '° · PATH DELAY ' + fmt(b.path_delay_m, 0) + ' M · '
          + BLOCK + '.emitter_bearing_deg / .path_delay_m' : '';
      if (!st.drawnOnce) { st.prevA.copy(st.tgtA); st.prevG.copy(st.tgtG); }
      st.drawnOnce = true;
      if (ctx.report) ctx.report(b.anchored
        ? 'anchor held · offset ' + fmt(b.deduced_offset && b.deduced_offset.mag_m, 0) + ' m · agreement ' + fmt(b.agreement_m, 1) + ' m'
        : 'anchor live');
    }

    function tick(dt, cam) {
      if (!st.drawnOnce || !st.showRing) {
        ring.visible = ring2.visible = line.visible = arrow.visible = head.visible = false;
        hide(lblAnchor); hide(lblOffset); hide(lblArrow); return;
      }
      const t = Math.min(1, (performance.now() - st.t0) / st.interval);
      st.curA.lerpVectors(st.prevA, st.tgtA, t);
      st.curG.lerpVectors(st.prevG, st.tgtG, t);
      ring.visible = ring2.visible = true;
      ring.position.copy(st.curA); ring2.position.copy(st.curA);
      tmp.copy(st.curA); tmp.y += 1.6; put(lblAnchor, tmp);

      line.visible = st.showLine;
      if (st.showLine) {
        linePos.setXYZ(0, st.curA.x, st.curA.y + 0.9, st.curA.z);
        linePos.setXYZ(1, st.curG.x, st.curG.y, st.curG.z);
        linePos.needsUpdate = true;
        lineDist.setX(0, 0); lineDist.setX(1, st.curA.distanceTo(st.curG)); lineDist.needsUpdate = true;
        mid.lerpVectors(st.curA, st.curG, 0.5); mid.y += 2.2; put(lblOffset, mid);
      } else hide(lblOffset);

      arrow.visible = head.visible = st.showArrow;
      if (st.showArrow) {
        tmpB.copy(st.dir).multiplyScalar(ARROW_M).add(st.curA); tmpB.y = st.curA.y + 1.2;
        arrow.position.lerpVectors(st.curA, tmpB, 0.5); arrow.position.y = st.curA.y + 1.2;
        arrow.scale.set(1, ARROW_M, 1); arrow.quaternion.setFromUnitVectors(UP, st.dir);
        head.position.copy(tmpB); head.quaternion.copy(arrow.quaternion);
        tmp.copy(tmpB); tmp.y += 6.0; put(lblArrow, tmp);      // above the offset label, which sits further out along the same bearing
      } else hide(lblArrow);
    }

    function hud() {
      const b = st.block;
      if (!b) return {title: 'STATIONARY DEDUCTION', lines: [{k: 'block', v: 'not in stream', path: BLOCK}]};
      const off = b.deduced_offset, fr = b.frame || {};
      const bearing = b.emitter_bearing_deg != null
        ? fmt(b.emitter_bearing_deg, 0) + '° · ' + fmt(b.path_delay_m, 0) + ' m path'
        : (fr.stationary ? (b.anchored ? 'in PL · none' : 'trusted') : 'moving · none');
      // the tag minus its state and dropped list (the authority and DROPPED cells show those) so it fits the cell
      const tag = (b.report_tag || '—').replace(/^[A-Z]+ · /, '').replace(/ · [GEC+]+ dropped/, '').replace('on ', '')
        .replace('clock in holdover', 'HOLDOVER');
      return {title: 'STATIONARY DEDUCTION', lines: [
        {k: 'anchor', v: b.anchored ? 'HELD · ep ' + b.anchor_epoch + (fr.stationary ? ' · still' : ' · odom') : 'LIVE', path: BLOCK + '.anchored'},
        {k: 'deduced offset', v: off ? fmt(off.mag_m, 0) + ' m' + (st.offBearing ? ' · ' + fmt(off.bearing_deg, 0) + '°' : '') : '—', path: BLOCK + '.deduced_offset.mag_m'},
        {k: 'agree · Galileo', v: fmt(b.agreement_m, 2) + ' m', path: BLOCK + '.agreement_m'},
        {k: 'emitter', v: bearing, path: BLOCK + '.emitter_bearing_deg / .path_delay_m'},
        {k: 'tag', v: tag, path: BLOCK + '.report_tag'},
      ]};
    }

    function status() {
      const b = st.block || {};
      return {epoch: st.epoch, state: st.state, anchored: !!b.anchored, anchor_epoch: b.anchor_epoch,
        offset_m: b.deduced_offset ? b.deduced_offset.mag_m : null, agreement_m: b.agreement_m,
        emitter_bearing_deg: b.emitter_bearing_deg, path_delay_m: b.path_delay_m,
        stationary: b.frame ? b.frame.stationary : null, reason: b.frame ? b.frame.reason : null,
        drawn: {ring: ring.visible, line: line.visible, arrow: arrow.visible},
        anchor_px: ring.visible ? ctx.project(ring.position).slice(0, 2).map(x => Math.round(x)) : null,
        labels: [lblAnchor, lblOffset, lblArrow].map(l => l.style.opacity === '1' || l.style.opacity === 1 ? l.textContent : null)};
    }

    function dispose() {
      for (const o of [ring, ring2, line, arrow, head]) { scene.remove(o); o.geometry.dispose(); }
      for (const m of [ring.material, ring2.material, line.material, redMat]) m.dispose();
      for (const l of [lblAnchor, lblOffset, lblArrow]) l.remove();
    }

    return {epoch, tick, hud, status, dispose};
  }

  window.ARBITRAS_OVERLAY = window.ARBITRAS_OVERLAY || {};
  window.ARBITRAS_OVERLAY.recon = {id: 'recon', mount};
})();
