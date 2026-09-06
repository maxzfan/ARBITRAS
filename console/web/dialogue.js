/* ARBITRAS guide -- the dialogue box (console/web/DIALOGUE.md §4).
 *
 * Replaces the bottom explanation bar (`.expbar`) with a click-advanced
 * dialogue box: one speaker (ARBITRAS), one line at a time, a typewriter
 * reveal, a chevron when the line is finished, and the replay HELD until the
 * operator clicks through. A Pokemon interaction model on an instrument skin:
 * dark, keyline-ruled, uppercase micro-labels, IBM Plex, nothing rounded and
 * no colour that is not already a console custom property.
 *
 * Classic script, like the overlay modules (console/web/overlays/CONTRACT.md):
 *
 *   window.ARBITRAS_DIALOGUE = { Box };
 *   html`<${Box} lines=${lines} gateReason=${r} verified=${v}
 *                state=${state} onDone=${fn} />`
 *
 * Globals it needs: `React` (index.html vendors it before this file). It does
 * NOT need htm -- htm.js is an ES module with no global, so everything here is
 * React.createElement; the caller may still mount it with htm, as index.html
 * does. It injects its own <style> on load and adds no CSS to index.html.
 *
 * Offline by construction (DIALOGUE.md "What does not bend" 5): the speaker
 * sigil is inline SVG, the fonts are the console's already-vendored families,
 * and nothing here fetches.
 *
 * Input, deliberately narrow (DIALOGUE.md §4): the click handler is on the BOX
 * element only -- the stage pane above owns orbit drag and overlays/takeover.js
 * owns pane touch/drag plus `T` and `W/A/S/D`; index.html owns `R`. The only
 * key taken here is Space.
 */
(function () {
  'use strict';
  if (typeof React === 'undefined') {           // nothing to register against
    console.error('[dialogue] React global missing; ARBITRAS_DIALOGUE not registered');
    return;
  }
  const h = React.createElement;
  const {useState, useEffect, useRef, useLayoutEffect, useCallback} = React;

  /* Reveal rate. 52 chars/s is ~4.2 s for a 220-character line (DIALOGUE.md's
     cap), fast enough to sit through on a rewatch and slow enough to read as a
     reveal. Any click completes the line instantly, so this is a floor on
     patience, never a wait. */
  const CPS = 52;

  /* The band is EXACTLY the height of the `.expbar` it replaces, computed the
     same way and for the same reason (index.html: "the stage below must start
     at the same y in every configuration" -- a WebGL canvas resize clears the
     drawing buffer and paints one black frame). Nothing inside can grow it:
     the text area is line-clamped, the chevron and the unverified note are
     absolutely positioned, and the reveal writes over a full-width, fully
     laid-out line (the unrevealed tail is transparent, not absent), so the
     wrap never moves while typing.
       plain   = 4 lines x 13px x 1.62 + 30px padding + 1px border = 115.24px
       + aside = 8 lines x 15px        + 30px padding + 1px border = 151px
     The aside form matches `.expbar.env`, so the env view keeps its scene key
     at its current height too. */
  const BAND_H = 'calc(4 * 13px * 1.62 + 30px + 1px)';
  const BAND_H_ASIDE = 'calc(8 * 15px + 30px + 1px)';

  /* tone only colours the box (DIALOGUE.md §1). Every value is an existing
     console colour: the four state properties, the accent, the truth blue and
     the corrected green already used for the corrected track in index.html. */
  const TONE = {
    calm:  'var(--truth, #7FA8CC)',
    alert: 'var(--degraded, #E8A21C)',
    bad:   'var(--surrendered, #D62119)',
    good:  '#6FCF97',
    act:   'var(--accent, #E4551F)',
  };
  const STATE_COLOR = {
    NOMINAL: 'var(--nominal, #D8E2EC)', DEGRADED: 'var(--degraded, #E8A21C)',
    RESTRICTED: 'var(--restricted, #E4551F)', SURRENDERED: 'var(--surrendered, #D62119)',
  };
  /* Operator language, not builder language (DIALOGUE.md "does not bend" 3). */
  const REASON = {
    intro: 'briefing', beat: 'mission beat', state: 'trust state changed',
    credential: 'credential changed', end: 'end of run',
  };

  const CSS = [
    '.adlg-band{flex:0 0 auto;display:flex;gap:24px;align-items:stretch;overflow:hidden;',
    '  padding:12px 24px;border-top:1px solid var(--keyline,#2B333D);background:#0A0C0E;',
    "  font-family:var(--sans,'IBM Plex Sans','Inter',system-ui,sans-serif);color:var(--fg,#EEF2F6)}",
    '.adlg-box{position:relative;flex:1 1 auto;min-width:0;height:100%;display:grid;',
    '  grid-template-columns:214px minmax(0,1fr);overflow:hidden;',
    '  border:1px solid var(--keyline,#2B333D);background:var(--panel,#0E1114);',
    '  cursor:pointer;-webkit-user-select:none;user-select:none}',
    '.adlg-box:hover{border-color:#3A434F}',
    ".adlg-box::before{content:'';position:absolute;left:0;top:0;bottom:0;width:2px;",
    '  background:var(--adlg-tone);opacity:.9}',
    '.adlg-box.adlg-held::before{opacity:.35}',
    '.adlg-who{display:flex;flex-direction:column;gap:9px;min-width:0;',
    '  padding:11px 14px 10px 17px;border-right:1px solid var(--line,#1C222A)}',
    '.adlg-name{display:flex;align-items:center;gap:8px;white-space:nowrap;',
    '  font-weight:600;font-size:11px;letter-spacing:.16em;text-transform:uppercase;',
    '  color:var(--adlg-state)}',
    '.adlg-sigil{flex:0 0 auto;display:block}',
    '.adlg-reason{font-weight:600;font-size:10px;letter-spacing:.14em;text-transform:uppercase;',
    '  color:var(--adlg-tone);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}',
    '.adlg-held .adlg-reason{color:var(--faint,#4E5A67)}',
    '.adlg-foot{margin-top:auto;display:flex;align-items:center;gap:8px;white-space:nowrap;',
    "  font-family:var(--mono,'IBM Plex Mono',ui-monospace,Menlo,monospace);",
    '  font-size:10px;letter-spacing:.04em;color:var(--faint,#4E5A67);',
    '  font-variant-numeric:tabular-nums}',
    '.adlg-foot .adlg-count{color:var(--dim,#8794A2)}',
    '/* The advance prompt. It flickers on the same 1.05s step as the chevron and',
    '   starts at the same moment (both mount on `done`), so the two read as one',
    '   signal rather than two blinking things. Tone-coloured and uppercase: the',
    '   whole interaction depends on the operator knowing the replay is waiting',
    '   for them, and a faint 10px line did not say so. */',
    '.adlg-go{font-weight:600;letter-spacing:.14em;text-transform:uppercase;',
    '  color:var(--adlg-tone);animation:adlg-blink 1.05s steps(1,end) infinite}',
    '.adlg-sp{color:var(--faint,#4E5A67)}',
    '.adlg-dot{width:5px;height:5px;flex:0 0 auto;background:var(--dim,#8794A2)}',
    '.adlg-held .adlg-dot{animation:adlg-pulse 1.7s ease-in-out infinite}',
    '@keyframes adlg-pulse{0%,100%{opacity:.22}50%{opacity:1}}',
    '/* Centred in the cell, so one short line does not float at the top of the',
    '   band, and clamped at three lines so a long one cannot grow it. The 132ch',
    '   measure is the `.expbar .d` measure: it also fixes the wrap for every',
    '   viewport above ~1150px, so the line count is width-independent. */',
    '.adlg-text{align-self:center;padding:0 42px 0 17px;min-width:0;max-width:132ch;',
    '  overflow:hidden;font-size:14.5px;line-height:1.5;color:var(--fg,#EEF2F6);',
    '  display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:3}',
    '.adlg-held .adlg-text{color:var(--faint,#4E5A67)}',
    '/* The unverified note is absolute: hold the columns clear of it. */',
    '.adlg-unver .adlg-text{color:var(--dim,#8794A2);-webkit-line-clamp:2;',
    '  margin-bottom:20px}',
    '/* The unrevealed tail is laid out and transparent, so the line wraps once',
    '   and the reveal never re-flows the paragraph under itself. */',
    '.adlg-veil{color:transparent}',
    "/* A claim's text (DIALOGUE.md §1 Claims): mono, dotted, and its contract",
    '   path or named source in the tooltip -- the strip does the same. */',
    ".adlg-cl{font-family:var(--mono,'IBM Plex Mono',ui-monospace,Menlo,monospace);",
    '  font-size:13px;border-bottom:1px dotted var(--faint,#4E5A67);cursor:help}',
    '.adlg-veil.adlg-cl{border-bottom-color:transparent}',
    '.adlg-unver .adlg-cl{border-bottom-style:none}',
    '/* Zero-width caret: drawn outside its own box so it cannot shift the wrap. */',
    '.adlg-caret{position:relative;display:inline-block;width:0;height:1em;vertical-align:-.14em}',
    ".adlg-caret::after{content:'';position:absolute;left:0;top:1px;bottom:1px;width:2px;",
    '  background:var(--adlg-tone)}',
    '.adlg-chev{position:absolute;right:15px;bottom:11px;display:block;color:var(--adlg-tone);',
    '  animation:adlg-blink 1.05s steps(1,end) infinite}',
    '.adlg-unver .adlg-chev{bottom:27px}',
    '@keyframes adlg-blink{0%,55%{opacity:1}56%,100%{opacity:.15}}',
    '/* The note belongs to the line, so it spans the text column only and',
    '   leaves the speaker column (214px + its 1px rule) alone. */',
    '.adlg-note{position:absolute;left:215px;right:0;bottom:0;padding:3px 17px;',
    '  background:var(--surrendered,#D62119);color:#fff;',
    '  font-weight:600;font-size:9.5px;letter-spacing:.14em;text-transform:uppercase;',
    '  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}',
    '.adlg-aside{flex:0 0 auto;min-width:0;overflow:hidden}',
    '@media (prefers-reduced-motion: reduce){',
    '  .adlg-chev,.adlg-go,.adlg-held .adlg-dot{animation:none}',
    '}',
  ].join('\n');

  (function injectStyle() {
    const id = 'arbitras-dialogue-css';
    if (document.getElementById(id)) return;
    const el = document.createElement('style');
    el.id = id;
    el.textContent = CSS;
    document.head.appendChild(el);
  })();

  const reducedMotion = () => {
    try { return window.matchMedia('(prefers-reduced-motion: reduce)').matches; }
    catch (e) { return false; }
  };

  /* The speaker sigil: four lines of sight converging on one fix. Inline SVG,
     currentColor, 1px strokes -- an instrument mark, not a portrait. */
  function Sigil(props) {
    const s = (props && props.size) || 17;
    return h('svg', {className: 'adlg-sigil', width: s, height: s, viewBox: '0 0 20 20',
                     fill: 'none', stroke: 'currentColor', 'aria-hidden': 'true'},
      h('circle', {key: 'r', cx: 10, cy: 10, r: 8.5, strokeWidth: 1, opacity: .45}),
      h('path', {key: 'l', d: 'M10 10 L4.4 4.4 M10 10 L15.6 4.4 M10 10 L2.6 11.6 M10 10 L17.4 11.6',
                 strokeWidth: 1, opacity: .75}),
      h('circle', {key: 'c', cx: 10, cy: 10, r: 2, strokeWidth: 1.4}));
  }

  function Chevron() {
    return h('svg', {className: 'adlg-chev', width: 13, height: 8, viewBox: '0 0 13 8',
                     fill: 'none', stroke: 'currentColor', strokeWidth: 1.7,
                     strokeLinecap: 'square', 'aria-hidden': 'true'},
      h('path', {d: 'M1 1.5 L6.5 6.5 L12 1.5'}));
  }

  /* --- claims -----------------------------------------------------------
     Split a line into [{text, claim}] so every numeral a claim covers is
     rendered as instrument data and carries its path or its named source in
     the tooltip (design.md §14: no number on screen that cannot be sourced).
     A claim whose text does not occur verbatim is skipped -- the backend
     verifier, not this box, decides whether a line may be shown at all. */
  function segments(line) {
    const text = (line && line.text) || '';
    const claims = (line && line.claims) || [];
    const spans = [];
    for (let k = 0; k < claims.length; k++) {
      const c = claims[k];
      if (!c || !c.text) continue;
      let from = 0, at = -1;
      for (;;) {
        at = text.indexOf(c.text, from);
        if (at === -1) break;
        const clash = spans.some(s => at < s.end && at + c.text.length > s.start);
        if (!clash) break;
        from = at + 1;
      }
      if (at === -1) continue;
      spans.push({start: at, end: at + c.text.length, claim: c});
    }
    spans.sort((a, b) => a.start - b.start);
    const out = [];
    let cur = 0;
    for (const s of spans) {
      if (s.start > cur) out.push({text: text.slice(cur, s.start), claim: null});
      out.push({text: text.slice(s.start, s.end), claim: s.claim});
      cur = s.end;
    }
    if (cur < text.length) out.push({text: text.slice(cur), claim: null});
    return out.length ? out : [{text: '', claim: null}];
  }

  function claimTitle(c) {
    if (!c) return undefined;
    if (c.path) return c.text + ' = ' + (c.value != null ? c.value : '?') + '  ·  ' + c.path;
    if (c.source) return c.text + '  ·  source: ' + c.source;
    return c.text;
  }

  /* Render the line with the first `n` characters revealed. Both halves of a
     split segment keep the segment's own classes, so the metrics -- and so the
     wrap -- are identical before and after the reveal reaches them. */
  function revealed(segs, n, showCaret) {
    const kids = [];
    let seen = 0, caretPlaced = false;
    segs.forEach((s, i) => {
      const cls = s.claim ? 'adlg-cl' : null;
      const cut = Math.max(0, Math.min(s.text.length, n - seen));
      if (cut > 0) {
        kids.push(h('span', {key: 'r' + i, className: cls, title: claimTitle(s.claim)},
                    s.text.slice(0, cut)));
      }
      if (!caretPlaced && cut < s.text.length) {
        if (showCaret) kids.push(h('span', {key: 'caret', className: 'adlg-caret'}));
        caretPlaced = true;
      }
      if (cut < s.text.length) {
        kids.push(h('span', {key: 'v' + i, className: cls ? 'adlg-veil ' + cls : 'adlg-veil',
                             'aria-hidden': 'true'}, s.text.slice(cut)));
      }
      seen += s.text.length;
    });
    return kids;
  }

  const signature = (lines, gateReason) =>
    (lines || []).map(l => (l && l.text) || '').join('') + '|' + (gateReason || '');

  /* --- the box ----------------------------------------------------------
     props (DIALOGUE.md §4):
       lines       the current gate's lines, or [] when free-running
       gateReason  intro | beat | state | credential | end | null
       verified    false dims the box and shows the withheld note
       state       trust state, for the accent colour
       onDone()    called ONCE when the operator clicks past the last line
       aside       OPTIONAL React node in a right-hand column (the env view's
                   scene key). Its presence switches the band to the taller
                   `.expbar.env` height; both heights are fixed constants. */
  function Box(props) {
    const lines = Array.isArray(props.lines) ? props.lines : [];
    const gateReason = props.gateReason || null;
    const verified = props.verified !== false;
    const onDone = typeof props.onDone === 'function' ? props.onDone : null;
    const sig = signature(lines, gateReason);

    const [i, setI] = useState(0);
    const [n, setN] = useState(0);
    const [spent, setSpent] = useState(false);        // onDone already fired for this gate
    const sigRef = useRef('');
    const heldRef = useRef(null);                     // last line delivered, for the dim hold
    const nRef = useRef(0); nRef.current = n;
    const instant = reducedMotion();

    /* A new gate can arrive mid-reveal. Restart on the new lines and let the
       single onDone belong to the gate now on screen -- the caller's gate flag
       is one boolean, so firing the old gate's onDone here would close the new
       gate the instant it opened. Empty lines clear the signature, so an
       identical gate arriving after a stretch of free-running still restarts. */
    useLayoutEffect(() => {
      if (!lines.length) { sigRef.current = ''; return; }
      if (sig === sigRef.current) return;
      sigRef.current = sig;
      setI(0);
      setN(instant ? ((lines[0] && lines[0].text) || '').length : 0);
      setSpent(false);
    }, [sig, lines.length, instant]);

    const live = lines.length > 0 && !spent;          // a gate is open and unspent
    const cur = live ? lines[Math.min(i, lines.length - 1)] : null;
    if (cur) heldRef.current = cur;
    const shown = cur || heldRef.current;
    const len = ((shown && shown.text) || '').length;
    const done = !live || n >= len;                   // the current line is fully revealed

    /* Typewriter. rAF-paced, so the rate is wall-clock and not frame count. */
    useEffect(() => {
      if (!live || instant || nRef.current >= len) return;
      let raf = 0, last = 0, acc = 0;
      const step = t => {
        if (!last) last = t;
        acc += (t - last) / 1000 * CPS;
        last = t;
        const add = Math.floor(acc);
        if (add > 0) { acc -= add; setN(v => Math.min(len, v + add)); }
        raf = nRef.current < len ? requestAnimationFrame(step) : 0;
      };
      raf = requestAnimationFrame(step);
      return () => { if (raf) cancelAnimationFrame(raf); };
    }, [sig, i, live, len, instant]);

    /* One interaction: complete the reveal, else advance, else finish. */
    const advance = useCallback(() => {
      if (!live) return;
      if (nRef.current < len) { setN(len); return; }
      if (i < lines.length - 1) {
        const next = (lines[i + 1] && lines[i + 1].text) || '';
        setI(i + 1);
        setN(instant ? next.length : 0);
        return;
      }
      setSpent(true);
      if (onDone) onDone();
    }, [live, len, i, lines, onDone, instant]);

    /* Space is the alias and the ONLY key taken (DIALOGUE.md §4): `T` and
       `W/A/S/D` are the takeover overlay's, `R` is index.html's reload. Bound
       once through a ref, not re-bound on every frame of the reveal. */
    const advRef = useRef(advance);
    advRef.current = advance;
    useEffect(() => {
      const onKey = ev => {
        if (ev.code !== 'Space' && ev.key !== ' ') return;
        if (ev.repeat || ev.metaKey || ev.ctrlKey || ev.altKey) return;
        const t = ev.target;
        if (t && (t.isContentEditable ||
                  /^(INPUT|TEXTAREA|SELECT|BUTTON)$/.test(t.tagName || ''))) return;
        ev.preventDefault();
        advRef.current();
      };
      window.addEventListener('keydown', onKey);
      return () => window.removeEventListener('keydown', onKey);
    }, []);

    const tone = TONE[(shown && shown.tone) || 'calm'] || TONE.calm;
    const stateColor = STATE_COLOR[props.state] || 'var(--fg, #EEF2F6)';
    const reasonLabel = live ? (REASON[gateReason] || 'guide') : 'held';
    const cls = 'adlg-box' + (live ? '' : ' adlg-held') + (verified ? '' : ' adlg-unver');

    return h('div', {className: 'adlg-band',
                     style: {height: props.aside ? BAND_H_ASIDE : BAND_H}},
      h('div', {key: 'box', className: cls,
                style: {'--adlg-tone': tone, '--adlg-state': stateColor},
                onClick: advance,
                'aria-label': (shown && shown.text) || 'no guidance yet'},
        h('div', {key: 'who', className: 'adlg-who'},
          h('div', {key: 'n', className: 'adlg-name'}, h(Sigil, null), 'Arbitras'),
          h('div', {key: 'r', className: 'adlg-reason'}, reasonLabel),
          h('div', {key: 'f', className: 'adlg-foot'},
            live
              ? [lines.length > 1
                   ? h('span', {key: 'c', className: 'adlg-count'}, (i + 1) + ' / ' + lines.length)
                   : null,
                 done
                   ? h('span', {key: 'k'},
                       h('span', {className: 'adlg-go'}, 'click to continue'),
                       h('span', {className: 'adlg-sp'}, '\u2003or space'))
                   : h('span', {key: 'k'}, 'click \u00b7 space to skip')]
              : [h('i', {key: 'd', className: 'adlg-dot'}),
                 h('span', {key: 'r'}, 'replay running')])),
        h('div', {key: 'text', className: 'adlg-text'},
          shown ? revealed(segments(shown), live ? n : len, live && !done) : ''),
        live && done ? h(Chevron, {key: 'chev'}) : null,
        verified ? null : h('div', {key: 'note', className: 'adlg-note'},
          'Unverified · a claim in this line did not check against this epoch')),
      props.aside ? h('div', {key: 'aside', className: 'adlg-aside'}, props.aside) : null);
  }

  window.ARBITRAS_DIALOGUE = {Box: Box};
})();
