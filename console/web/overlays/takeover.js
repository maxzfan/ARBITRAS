/* Operator takeover overlay -- LOGISTICS and COMBAT (tracks/TRACK_F.md, revision §1).
 *
 * SURRENDERED means control is with the operator (design.md §8). This overlay
 * lets a REAL PERSON drive the TRUE vehicle in the presentation frame while
 * autonomy is withdrawn, and hands back when the arbiter climbs to DEGRADED.
 * ARBITER never commands motion: every metre driven here is a human's input
 * (keyboard / gamepad / touch) or, when nobody is at the controls, a scripted
 * stand-in that follows the route at half speed and is labelled as such.
 *
 * What the operator drives by is what the vehicle reports: the believed ghost
 * (flagged) and, when the gate passes, the corrected fix with its protection
 * level -- both drawn by the console from the stream. Nothing here is data.
 *
 * Controls: T take / hand back control · W/S or ↑/↓ throttle · A/D or ←/→ steer ·
 * gamepad left stick · touch: drag on the pane. Hand-back is allowed only when
 * the state is DEGRADED or NOMINAL (the arbiter's call, not the operator's).
 */
(function () {
  const ORDER = {SURRENDERED: 0, RESTRICTED: 1, DEGRADED: 2, NOMINAL: 3};

  function mount(ctx) {
    const T = ctx.THREE, H = ctx.helpers, m = ctx.mission, C = ctx.colors;
    const L = H.routeLength();
    const st = {
      mode: 'autonomy',            // autonomy | operator | scripted
      forced: false,               // SURRENDERED forces control to the operator
      wantManual: false,           // T toggles the operator's own intent
      e: null, n: null, headingAz: 0, speed: 0,     // presentation-frame kinematics (m, m, deg, m/s of frame time)
      s: 0,                        // scripted operator's arc length
      lastInput: -Infinity, epochsDriven: 0, epochsScripted: 0, maxLateral: 0,
      state: 'NOMINAL', ok: false, pl: null, epoch: 0, epochInterval: 0.07,
      keys: new Set(), touch: null,
    };
    // -- input ------------------------------------------------------------
    const onKey = (ev, down) => {
      const k = ev.key.toLowerCase();
      if (down && k === 't') { st.wantManual = !st.wantManual; st.lastInput = performance.now(); return; }
      if (['w','a','s','d','arrowup','arrowdown','arrowleft','arrowright'].includes(k)) {
        if (down) st.keys.add(k); else st.keys.delete(k);
        st.lastInput = performance.now(); ev.preventDefault();
      }
    };
    const kd = ev => onKey(ev, true), ku = ev => onKey(ev, false);
    window.addEventListener('keydown', kd); window.addEventListener('keyup', ku);
    const pane = ctx.labels && ctx.labels.parentElement;
    const onTouch = ev => { const t = ev.touches && ev.touches[0]; if (!t) { st.touch = null; return; }
      const r = pane.getBoundingClientRect(); st.touch = {x: (t.clientX - r.left) / r.width * 2 - 1, y: (t.clientY - r.top) / r.height * 2 - 1};
      st.lastInput = performance.now(); ev.preventDefault(); };
    const onTouchEnd = () => { st.touch = null; };
    if (pane) { pane.addEventListener('touchstart', onTouch, {passive:false}); pane.addEventListener('touchmove', onTouch, {passive:false}); pane.addEventListener('touchend', onTouchEnd); }
    const readInput = () => {
      let throttle = 0, steer = 0, any = false;
      if (st.keys.has('w') || st.keys.has('arrowup')) { throttle += 1; any = true; }
      if (st.keys.has('s') || st.keys.has('arrowdown')) { throttle -= 1; any = true; }
      if (st.keys.has('a') || st.keys.has('arrowleft')) { steer -= 1; any = true; }
      if (st.keys.has('d') || st.keys.has('arrowright')) { steer += 1; any = true; }
      const gps = navigator.getGamepads ? navigator.getGamepads() : [];
      for (const g of gps) { if (!g) continue; const x = g.axes[0] || 0, y = g.axes[1] || 0;
        if (Math.abs(x) > 0.15 || Math.abs(y) > 0.15) { steer += x; throttle += -y; any = true; st.lastInput = performance.now(); } }
      if (st.touch) { steer += st.touch.x; throttle += -st.touch.y; any = true; }
      return {throttle: Math.max(-1, Math.min(1, throttle)), steer: Math.max(-1, Math.min(1, steer)), any};
    };
    // -- scene objects: a hold/control marker on the terrain at the true position --
    const ring = new T.Mesh(new T.RingGeometry(3.2, 3.8, 48), new T.MeshBasicMaterial({color: new T.Color(C.amber), transparent:true, opacity:0.85, side:T.DoubleSide, depthWrite:false, toneMapped:false}));
    ring.rotation.x = -Math.PI/2; ring.visible = false; ctx.scene.add(ring);
    const label = document.createElement('div'); label.className = 'skl env al lb'; label.style.opacity = '0'; ctx.labels.appendChild(label);
    const status = () => ({mode: st.mode, forced: st.forced, epochsDriven: st.epochsDriven, epochsScripted: st.epochsScripted, maxLateral: +st.maxLateral.toFixed(1), e: st.e, n: st.n});
    const nearestS = (e, n) => {                          // arc length of the nearest route point (coarse then fine)
      let best = 0, bd = Infinity;
      for (let s = 0; s <= L; s += 5) { const P = H.routePoint(s); const d = Math.hypot(P.e - e, P.n - n); if (d < bd) { bd = d; best = s; } }
      return best;
    };
    const active = () => st.mode !== 'autonomy';

    // -- per epoch: decide who is in control ----------------------------------
    function epoch(payload, frame) {
      st.state = payload.state; st.epoch = payload.epoch_index;
      const c = ((payload.geometry || {}).correction) || {};
      st.ok = !!c.correction_ok; st.pl = c.protection_level_m;
      const rank = ORDER[st.state] ?? 3;
      st.forced = rank === 0;
      const canHandBack = rank >= 2;
      const wasActive = active();
      if (st.forced || st.wantManual) {
        if (!wasActive) {                                  // take control from wherever the vehicle is
          st.e = frame.truePos.x; st.n = -frame.truePos.z; st.headingAz = frame.headingAz; st.speed = 0;
          st.s = nearestS(st.e, st.n);
        }
        const recent = performance.now() - st.lastInput < 3000;
        st.mode = recent ? 'operator' : 'scripted';
      } else if (wasActive && canHandBack) {
        st.mode = 'autonomy';                              // hand back: autonomy resumes from the nearest route point
        ctx.setProgress(nearestS(st.e, st.n));
      } else if (wasActive && !canHandBack) {
        st.mode = (performance.now() - st.lastInput < 3000) ? 'operator' : 'scripted';
      }
      if (st.mode === 'operator') st.epochsDriven++; else if (st.mode === 'scripted') st.epochsScripted++;
      if (active() && st.e != null) st.maxLateral = Math.max(st.maxLateral, Math.abs(H.lateralOffset(st.e, st.n)));
      st.epochInterval = frame.epochInterval || st.epochInterval;
    }

    // -- per frame: move the TRUE vehicle when in control -----------------------
    function drive(dt, consoleSt) {
      if (!active() || st.e == null) { ring.visible = false; label.style.opacity = '0'; return null; }
      const iv = (consoleSt.layer && consoleSt.layer.interval ? consoleSt.layer.interval : 70) / 1000;   // s of wall time per epoch
      const frameSpeed = m.speed_m_per_epoch / Math.max(iv, 0.02);                                       // m of frame per s of wall time at gain 1
      const inp = readInput();
      if (st.mode === 'operator' || inp.any) {
        st.mode = 'operator';
        const target = inp.throttle * frameSpeed;
        st.speed += (target - st.speed) * Math.min(1, dt * 2.5);
        st.headingAz = (st.headingAz + inp.steer * 70 * dt * Math.min(1, Math.abs(st.speed) / (0.3 * frameSpeed) + 0.25) + 360) % 360;
        const a = st.headingAz * Math.PI / 180;
        st.e += Math.sin(a) * st.speed * dt; st.n += Math.cos(a) * st.speed * dt;
      } else {                                              // scripted stand-in: half speed along the route, labelled
        st.s = Math.min(L, st.s + 0.5 * frameSpeed * dt);
        const P = H.routePoint(st.s); st.e = P.e; st.n = P.n; st.headingAz = (90 - P.heading * 180 / Math.PI + 360) % 360; st.speed = 0.5 * frameSpeed;
      }
      ring.position.set(st.e, ctx.heightAt(st.e, -st.n) + 0.12, -st.n); ring.visible = true;
      ring.material.color.set(st.mode === 'operator' ? C.amber : C.dim);
      const [x, y, vis] = ctx.project(new T.Vector3(st.e, ctx.heightAt(st.e, -st.n) + 3.2, -st.n));
      label.textContent = (st.mode === 'operator' ? 'OPERATOR DRIVING' : 'OPERATOR (SCRIPTED STAND-IN)') + ' · ' + (st.ok ? `CORRECTED FIX PL ${st.pl != null ? st.pl.toFixed(1) : '—'} M` : 'NO TRUSTED FIX · DRIVE BY VIEW');
      label.style.left = x + 'px'; label.style.top = y + 'px'; label.style.opacity = vis ? '1' : '0';
      return {e: st.e, n: st.n, headingAz: st.headingAz};
    }

    function hud() {
      const ctl = st.mode === 'operator' ? 'OPERATOR' : st.mode === 'scripted' ? 'SCRIPTED STAND-IN' : 'AUTONOMY';
      return {title: 'Operator takeover', lines: [
        {k: 'control', v: ctl + (st.forced ? ' · forced' : st.wantManual ? ' · requested' : ''), path: 'state (SURRENDERED forces the handoff)'},
        {k: 'drive by', v: st.ok ? `corrected fix · PL ${st.pl != null ? st.pl.toFixed(1) : '—'} m` : 'view · fix unverified', path: 'geometry.correction.correction_ok / protection_level_m'},
        {k: 'hand back', v: (ORDER[st.state] ?? 3) >= 2 ? 'allowed (T)' : 'not yet', path: 'state ≥ DEGRADED'},
        {k: 'epochs driven', v: `${st.epochsDriven} · scripted ${st.epochsScripted}`, path: 'presentation frame'},
        {k: 'max off-track', v: `${st.maxLateral.toFixed(1)} m`, path: 'presentation frame (lateral offset of the true vehicle)'},
      ]};
    }

    return {
      epoch, drive, hud, active, status,
      tick() {},
      dispose() {
        window.removeEventListener('keydown', kd); window.removeEventListener('keyup', ku);
        if (pane) { pane.removeEventListener('touchstart', onTouch); pane.removeEventListener('touchmove', onTouch); pane.removeEventListener('touchend', onTouchEnd); }
        ctx.scene.remove(ring); ring.geometry.dispose(); ring.material.dispose(); label.remove();
      },
    };
  }

  window.ARBITER_OVERLAY = window.ARBITER_OVERLAY || {};
  window.ARBITER_OVERLAY.takeover = { id: 'takeover', mount };
})();
