/* ARBITRAS guide -- the dialogue box (console/web/DIALOGUE.md, Revision 2).
 *
 * Replaces the bottom explanation bar (`.expbar`) with an AUTO-ADVANCING
 * dialogue box: one speaker (ARBITRAS), one line at a time, a typewriter
 * reveal, and a dwell timer that moves to the next line on its own. The replay
 * is NEVER held: under overlays/takeover.js a human (`T`) or the scripted
 * stand-in (`S`) is driving, and freezing the vehicle under a driver -- or
 * asking someone at the controls to click a text box -- is wrong. Clicking is a
 * FAST-FORWARD, never a requirement.
 *
 * Revision 2 in one paragraph: no `gate`, no `gate_reason`, no `onDone`. Lines
 * arrive anchored to a CHECKPOINT (`{index, total, label, kind}`) and the box
 * shows `CHECKPOINT n / N` plus the label, so the operator can see how much
 * story is left and where the vehicle is. `checkpoint` may be null -- an
 * INTERJECTION, a trust-state or credential change nobody scripted, which
 * cannot be in a schedule fixed at construction; those lines show no fraction
 * and no label, because inflating `total` would revise the denominator the
 * operator is already reading. Every line carries `at` (UTC) and
 * `epoch`, and the box prints them: a line reading "confidence 0.91" used to
 * sit on screen while the strip moved to 0.92 with nothing saying the number
 * was from an earlier epoch. Now the box says so, and keeps saying so while the
 * line is held after its dwell.
 *
 * Classic script, like the overlay modules (console/web/overlays/CONTRACT.md):
 *
 *   window.ARBITRAS_DIALOGUE = { Box };
 *   html`<${Box} lines=${lines} checkpoint=${cp} verified=${v}
 *                state=${state} rate=${epochsPerSecond} aside=${node} />`
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
 * owns pane touch/drag plus `T` (take), `S` (simulate) and `W/A/D`; index.html
 * owns `R`. The only key taken here is Space.
 */
(function () {
  'use strict';
  if (typeof React === 'undefined') {           // nothing to register against
    console.error('[dialogue] React global missing; ARBITRAS_DIALOGUE not registered');
    return;
  }
  const h = React.createElement;
  const {useState, useEffect, useRef, useLayoutEffect, useCallback} = React;

  /* --- pace -------------------------------------------------------------
     MIN_DWELL is a readability FLOOR, not the pace (DIALOGUE.md "Dwell"). The
     pace is the fair share of the wall-clock gap to the next checkpoint, which
     at 5 epochs/s and the measured checkpoint spacing lands around 4 s a line;
     the floor only binds where checkpoints crowd (an attack onset and the trust
     state change it causes are a handful of epochs apart).

     3.0 s, because:
       - the reveal of a mid-length line (~150 chars) takes ~2.6 s at CPS, and a
         line has to stand still whole for a beat after it finishes writing;
       - at 5 epochs/s it is 15 epochs, so the `at`/`epoch` stamp beside the line
         still describes roughly what the strip is showing;
       - it is comfortably under the ~4 s the contract's own budget check
         assumes, so the floor never becomes the pace and the budget still
         closes (RECON: 19 lines in 510 epochs = 102 s of replay).
     CATCHUP_DWELL is the harder floor used only when a new checkpoint has
     arrived with lines still pending: the remainder is shown FASTER, never
     dropped (DIALOGUE.md: "they are shown faster, never dropped").
     MAX_DWELL caps a line's turn on screen at 60 epochs; past that its numbers
     are history, and the box dims to the held treatment -- still readable,
     still stamped with the epoch it was true for. */
  const MIN_DWELL = 3000;
  const CATCHUP_DWELL = 1500;
  const MAX_DWELL = 12000;

  /* Reveal rate, and the promise that the reveal never eats the dwell: the
     writing takes the SHORTER of len/CPS and REVEAL_FRAC of the dwell, so every
     line is whole on screen for at least the remaining fraction of its turn.
     58 chars/s is ~3.8 s for a 220-character line (DIALOGUE.md's cap) if the
     dwell is long enough to allow it. */
  const CPS = 58;
  const REVEAL_FRAC = 0.55;

  /* The band is EXACTLY the height of the `.expbar` it replaces, computed the
     same way and for the same reason (index.html: "the stage below must start
     at the same y in every configuration" -- a WebGL canvas resize clears the
     drawing buffer and paints one black frame). Nothing inside can grow it:
     the band is a fixed height with `overflow:hidden`, the text area is
     line-clamped, the stamp and the unverified note are absolutely positioned,
     and the reveal writes over a full-width, fully laid-out line (the
     unrevealed tail is transparent, not absent), so the wrap never moves while
     typing.
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
  /* Operator language, not builder language (DIALOGUE.md "does not bend" 3).
     `kind` is waypoint | event | intro | end (DIALOGUE.md revision 2). */
  const KIND = {
    waypoint: 'waypoint', event: 'event', intro: 'briefing', end: 'closing',
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
    '/* The speaker column is the ATTRIBUTION column: who is talking, which',
    '   checkpoint of how many, where the vehicle is, and -- at the foot -- that',
    '   the line is on a timer. Fixed line-heights and `overflow:hidden`, so no',
    '   label length can push the band. */',
    '.adlg-who{display:flex;flex-direction:column;gap:4px;min-width:0;overflow:hidden;',
    '  padding:9px 14px 8px 17px;border-right:1px solid var(--line,#1C222A)}',
    '/* No row may shrink: the four rows are 56px, the gaps 12px and the padding',
    '   17px, which is 85px inside the 88.24px the shorter band leaves -- 3px of',
    '   slack. Without this, flexbox absorbs any overspill by squeezing a text',
    '   row instead of failing loudly, and a font metric that moved would quietly',
    '   clip the checkpoint counter. */',
    '.adlg-who>*{flex:0 0 auto}',
    '.adlg-name{display:flex;align-items:center;gap:8px;white-space:nowrap;line-height:15px;',
    '  font-weight:600;font-size:11px;letter-spacing:.16em;text-transform:uppercase;',
    '  color:var(--adlg-state)}',
    '.adlg-sigil{flex:0 0 auto;display:block}',
    '/* CHECKPOINT n / N -- how much story is left (DIALOGUE.md revision 2). */',
    '.adlg-cp{font-weight:600;font-size:10px;line-height:13px;letter-spacing:.14em;',
    '  text-transform:uppercase;color:var(--adlg-tone);white-space:nowrap;',
    '  overflow:hidden;text-overflow:ellipsis;font-variant-numeric:tabular-nums}',
    '.adlg-held .adlg-cp,.adlg-held .adlg-where{color:var(--faint,#4E5A67)}',
    '/* An interjection (checkpoint null): no fraction, no label. It says so',
    '   rather than leaving the row blank, and it is NOT tone-coloured -- the',
    '   fraction is the thing the operator tracks, and a look-alike in its place',
    "   would be read as one. */",
    '.adlg-cp.adlg-off{color:var(--faint,#4E5A67);font-weight:500;letter-spacing:.11em;',
    '  cursor:help}',
    '/* label (OP-1, PL AMBER, ...) and the kind, plus the line counter. */',
    '.adlg-where{display:flex;align-items:baseline;gap:8px;line-height:13px;',
    '  font-size:10px;letter-spacing:.1em;text-transform:uppercase;color:var(--dim,#8794A2)}',
    '.adlg-lbl{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;',
    '  font-weight:600}',
    '.adlg-kind{color:var(--faint,#4E5A67);font-weight:500;letter-spacing:.08em}',
    '.adlg-of{margin-left:auto;flex:0 0 auto;color:var(--faint,#4E5A67);',
    "  font-family:var(--mono,'IBM Plex Mono',ui-monospace,Menlo,monospace);",
    '  font-size:10px;letter-spacing:.02em;text-transform:none;font-variant-numeric:tabular-nums}',
    '.adlg-foot{margin-top:auto;display:flex;align-items:center;gap:8px;white-space:nowrap;',
    '  line-height:13px;font-weight:500;font-size:9.5px;letter-spacing:.11em;',
    '  text-transform:uppercase;color:var(--faint,#4E5A67)}',
    '/* The progress affordance, in place of the old blinking CLICK TO CONTINUE:',
    '   nothing waits for a click any more, so the box has to say the opposite --',
    '   this line is TIMED, and a click only skips ahead. A 56x2 rule that fills',
    '   over the dwell reads as motion at a glance without becoming the loudest',
    '   thing on an instrument panel (the old prompt blinked, in tone colour, at',
    '   1.05 s -- that was the loudest thing, and it was also a lie). The fill is',
    '   a CSS animation, so it is smooth without a React render per frame, and a',
    '   negative animation-delay resumes it in place when the dwell is recomputed',
    '   mid-line by an arriving checkpoint. */',
    '.adlg-track{position:relative;flex:0 0 auto;width:56px;height:2px;overflow:hidden;',
    '  background:var(--keyline,#2B333D)}',
    '.adlg-fill{position:absolute;left:0;top:0;bottom:0;width:100%;background:var(--adlg-tone);',
    '  transform:scaleX(0);transform-origin:left center;opacity:.85;',
    '  animation-name:adlg-fill;animation-timing-function:linear;animation-fill-mode:forwards}',
    '@keyframes adlg-fill{from{transform:scaleX(0)}to{transform:scaleX(1)}}',
    '.adlg-dot{width:5px;height:5px;flex:0 0 auto;background:var(--dim,#8794A2)}',
    '.adlg-held .adlg-dot{animation:adlg-pulse 1.7s ease-in-out infinite}',
    '@keyframes adlg-pulse{0%,100%{opacity:.22}50%{opacity:1}}',
    '/* Centred in the cell, so one short line does not float at the top of the',
    '   band, and clamped at three lines so a long one cannot grow it. The 132ch',
    '   measure is the `.expbar .d` measure. The right padding is the stamp',
    '   reserve: the attribution below is absolutely positioned in it and the',
    '   text never runs under it at any width. */',
    '.adlg-text{align-self:center;padding:0 168px 0 17px;min-width:0;max-width:132ch;',
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
    '/* When this line was true. Quiet (mono, 10px, faint) but always present,',
    '   and it stays with the line while it is held after its dwell -- that hold',
    '   is exactly when the strip has moved on and the number has not. */',
    '.adlg-stamp{position:absolute;right:16px;bottom:9px;max-width:150px;overflow:hidden;',
    "  font-family:var(--mono,'IBM Plex Mono',ui-monospace,Menlo,monospace);",
    '  font-size:10px;line-height:13px;letter-spacing:.02em;color:var(--faint,#4E5A67);',
    '  white-space:nowrap;text-align:right;font-variant-numeric:tabular-nums;cursor:help}',
    '.adlg-unver .adlg-stamp{bottom:26px}',
    '/* The note belongs to the line, so it spans the text column only and',
    '   leaves the speaker column (214px + its 1px rule) alone. */',
    '.adlg-note{position:absolute;left:215px;right:0;bottom:0;padding:3px 17px;',
    '  background:var(--surrendered,#D62119);color:#fff;',
    '  font-weight:600;font-size:9.5px;letter-spacing:.14em;text-transform:uppercase;',
    '  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}',
    '.adlg-aside{flex:0 0 auto;min-width:0;overflow:hidden}',
    '/* Reduced motion: the reveal is instant (handled in JS), the held dot stops',
    '   pulsing, and the dwell fill steps instead of sliding -- it still says the',
    '   line is timed, in six discrete moves rather than continuous travel. */',
    '@media (prefers-reduced-motion: reduce){',
    '  .adlg-held .adlg-dot{animation:none}',
    '  .adlg-fill{animation-timing-function:steps(6,end)}',
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
  const nowMs = () => (window.performance && performance.now ? performance.now() : Date.now());

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

  /* --- attribution ------------------------------------------------------
     `at` is a UTC clock string ("12:30:00"); an ISO stamp is tolerated because
     the epoch objects carry one and a caller may pass it straight through. */
  function fmtAt(at) {
    if (typeof at !== 'string' || !at) return '';
    const s = at.indexOf('T') === 10 ? at.slice(11, 19) : at;
    return s.replace(/Z$/, '');
  }
  function stampOf(line) {
    if (!line) return '';
    const t = fmtAt(line.at);
    const e = (line.epoch === 0 || line.epoch) ? 'epoch ' + line.epoch : '';
    if (t && e) return t + ' UTC · ' + e;
    return t ? t + ' UTC' : e;
  }
  function stampTitle(line) {
    if (!line || !stampOf(line)) return undefined;
    return 'this line was spoken at ' + (fmtAt(line.at) || '—') + ' UTC' +
           ((line.epoch === 0 || line.epoch) ? ', and its claims were verified against epoch ' +
             line.epoch : '') +
           ' — the strip has moved on since';
  }

  /* One signature per delivery. The checkpoint index and the first line's epoch
     are in it, so two checkpoints that happen to speak the same words are two
     deliveries, and re-passing the SAME delivery (the caller holding the last
     non-empty `dialogue` across epochs) is not. */
  function signature(lines, cp) {
    const l = lines || [];
    const first = l[0] || {};
    return (cp ? cp.index + '/' + cp.total + '@' + (cp.label || '') : '-') + '|' +
           (first.epoch != null ? first.epoch : '-') + '|' + l.length + '|' +
           l.map(x => (x && x.text) || '').join('');
  }

  const EMPTY = [];

  /* --- the box ----------------------------------------------------------
     props (DIALOGUE.md revision 2):
       lines       the lines a checkpoint just delivered, `[]` on every other
                   epoch. Deliveries are QUEUED, never dropped; re-passing the
                   same delivery is a no-op.
       checkpoint  {index, total, label, kind} -- shown as `CHECKPOINT n / N`
                   and the label that names where the vehicle is. `null` is an
                   INTERJECTION: the lines are shown exactly as any other, with
                   no fraction and no label (DIALOGUE.md revision 2).
       rate        OPTIONAL. Epochs per second of the stream (server default 5).
                   With it the box measures checkpoint spacing in the epoch
                   domain rather than the wall clock.
       verified    false dims the box and shows the withheld note
       state       trust state, for the accent colour
       msToNext    OPTIONAL. Wall-clock ms until the next checkpoint is expected.
                   The dwell is the fair share of it. Absent, the box uses the
                   gap it MEASURED between the last two deliveries, and before
                   there are two, the MIN_DWELL floor.
       aside       OPTIONAL React node in a right-hand column (the env view's
                   scene key). Its presence switches the band to the taller
                   `.expbar.env` height; both heights are fixed constants.
     There is no `onDone` and nothing waits for this component. */
  function Box(props) {
    const lines = Array.isArray(props.lines) ? props.lines : EMPTY;
    const verified = props.verified !== false;
    const instant = reducedMotion();
    const sig = lines.length ? signature(lines, props.checkpoint) : '';

    const [queue, setQueue] = useState([]);           // [0] is the line on screen
    const [n, setN] = useState(0);                    // characters revealed
    const [timing, setTiming] = useState({key: '', dwell: MIN_DWELL, off: 0});
    const nRef = useRef(0); nRef.current = n;
    const sigRef = useRef('');
    const seqRef = useRef(0);                         // delivery counter
    const arrivalRef = useRef({at: 0, gap: 0, seq: 0, epoch: null});
    const lineRef = useRef({key: '', at: 0});         // current line's start
    const heldRef = useRef(null);                     // last item shown, for the dim hold

    /* A delivery lands: queue its lines BEHIND anything still pending (nothing
       is ever dropped) and re-budget. `gap` is the estimate of how long the
       whole queue has before the guide speaks again. Nothing on the wire says
       when the next checkpoint is, so the gap that just ELAPSED is the
       predictor -- checkpoints are spaced by the script, and the last spacing
       is the best cheap guess at the next. Three ways to measure it, best
       first:
         1. `msToNext`, if the caller knows the schedule and hands it over;
         2. the EPOCH gap between this delivery and the last, divided by `rate`
            (epochs/s, the stream's own pace). Epoch domain, so a stalled tab or
            a buffered burst cannot skew it -- the console applies one epoch per
            stream interval, and lines carry the epoch they were true for;
         3. the wall-clock gap between the two arrivals, when neither is known. */
    useLayoutEffect(() => {
      if (!sig || sig === sigRef.current) return;
      sigRef.current = sig;
      const now = nowMs();
      const prev = arrivalRef.current;
      const hint = Number(props.msToNext);
      const rate = Number(props.rate);
      const ep = lines[0] && lines[0].epoch != null ? Number(lines[0].epoch) : null;
      let gap = hint > 0 ? hint : 0;
      if (!gap && rate > 0 && ep != null && prev.epoch != null && ep > prev.epoch) {
        gap = (ep - prev.epoch) / rate * 1000;
      }
      if (!gap && prev.at) gap = now - prev.at;
      const seq = ++seqRef.current;
      arrivalRef.current = {at: now, gap: gap, seq: seq, epoch: ep};
      const cp = props.checkpoint || null;
      const add = lines.map((l, k) => ({
        line: l, cp: cp, seq: seq, idx: k + 1, of: lines.length,
        key: seq + ':' + k,
      }));
      setQueue(q => q.concat(add));
    }, [sig]);                                        // eslint-disable-line

    const head = queue.length ? queue[0] : null;
    if (head) heldRef.current = head;
    const shown = head || heldRef.current;
    const line = shown ? shown.line : null;
    const len = ((line && line.text) || '').length;
    const live = !!head;

    /* Advance. Dropping the head is the whole mechanism -- the effect below
       re-budgets the rest of the queue against the time that is left, so
       skipping a line gives its unspent time back to the lines behind it.
       `key` is the line the caller MEANT to drop: the dwell timer passes the
       line it was scheduled for, so a click and an expiring timer landing in
       the same tick cannot drop two lines. A click passes null: it always means
       whatever is on screen now. */
    const drop = useCallback(key => {
      setQueue(q => (q.length && (key == null || q[0].key === key) ? q.slice(1) : q));
    }, []);
    const advance = useCallback(() => drop(null), [drop]);
    const dropRef = useRef(drop);
    dropRef.current = drop;
    const advRef = useRef(advance);
    advRef.current = advance;

    /* The dwell. Recomputed when the head changes AND when the queue grows
       under it (a checkpoint arriving with lines still pending): the budget is
       then shared over old and new together, so the remainder is shown faster,
       floored at CATCHUP_DWELL rather than MIN_DWELL. The timer is set to the
       time REMAINING on the current line, not a fresh dwell, so a re-budget
       mid-line cannot extend it. */
    useLayoutEffect(() => {
      if (!head) return;
      const now = nowMs();
      if (lineRef.current.key !== head.key) {
        lineRef.current = {key: head.key, at: now};
        setN(instant ? len : 0);
      }
      const a = arrivalRef.current;
      const left = Math.max(0, (a.gap || 0) - (now - a.at));
      const share = left / queue.length;
      const behind = head.seq !== a.seq;              // head is from an older checkpoint
      const dwell = Math.min(MAX_DWELL, Math.max(behind ? CATCHUP_DWELL : MIN_DWELL, share));
      const off = Math.min(dwell, now - lineRef.current.at);
      setTiming({key: head.key, dwell: dwell, off: off});
      const t = setTimeout(() => dropRef.current(head.key), Math.max(60, dwell - off));
      return () => clearTimeout(t);
    }, [head && head.key, queue.length, len, instant]);

    /* Typewriter. rAF-paced, so the rate is wall-clock and not frame count, and
       fast enough that the line is whole for at least (1 - REVEAL_FRAC) of its
       dwell however short the dwell is. */
    useEffect(() => {
      if (!live || instant || nRef.current >= len) return;
      const budget = Math.max(0.25, REVEAL_FRAC * (timing.dwell - timing.off) / 1000);
      const cps = Math.max(CPS, len / budget);
      let raf = 0, last = 0, acc = 0;
      const step = t => {
        if (!last) last = t;
        acc += (t - last) / 1000 * cps;
        last = t;
        const add = Math.floor(acc);
        if (add > 0) { acc -= add; setN(v => Math.min(len, v + add)); }
        raf = nRef.current < len ? requestAnimationFrame(step) : 0;
      };
      raf = requestAnimationFrame(step);
      return () => { if (raf) cancelAnimationFrame(raf); };
    }, [head && head.key, live, len, instant, timing.dwell, timing.off]);

    /* Space is the alias and the ONLY key taken (DIALOGUE.md §4): `T` (take),
       `S` (simulate) and `W/A/D` are the takeover overlay's, `R` is
       index.html's reload. Bound once through a ref, not re-bound on every
       frame of the reveal. Repeats and modified presses are ignored, and a
       press inside a form control is left alone. */
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

    const done = !live || n >= len;
    /* The checkpoint shown is the one the LINE arrived with, never the caller's
       current one: an interjection (cp null) must not borrow the fraction of a
       scheduled checkpoint that happens to be on the wire at the same time.
       props.checkpoint is used only before anything has ever been spoken. */
    const cp = shown ? shown.cp : (props.checkpoint || null);
    const tone = TONE[(line && line.tone) || 'calm'] || TONE.calm;
    const stateColor = STATE_COLOR[props.state] || 'var(--fg, #EEF2F6)';
    const cls = 'adlg-box' + (live ? '' : ' adlg-held') + (verified ? '' : ' adlg-unver');
    const stamp = stampOf(line);

    return h('div', {className: 'adlg-band',
                     style: {height: props.aside ? BAND_H_ASIDE : BAND_H}},
      h('div', {key: 'box', className: cls,
                style: {'--adlg-tone': tone, '--adlg-state': stateColor},
                onClick: advance,
                title: live ? 'click or press Space to skip ahead' : undefined,
                'aria-label': (line && line.text) || 'no guidance yet'},
        h('div', {key: 'who', className: 'adlg-who'},
          h('div', {key: 'n', className: 'adlg-name'},
            h(Sigil, null), (line && line.speaker) || 'Arbitras'),
          h('div', {key: 'c', className: cp && cp.total ? 'adlg-cp' : 'adlg-cp adlg-off',
                     title: cp && cp.total ? undefined
                       : 'spoken because something changed, not because we reached a point — ' +
                         'an interjection is not in the checkpoint count'},
            cp && cp.total ? 'Checkpoint ' + cp.index + ' / ' + cp.total
                           : (shown ? 'Unscheduled' : 'Guide')),
          h('div', {key: 'w', className: 'adlg-where'},
            h('span', {key: 'l', className: 'adlg-lbl'},
              cp && cp.label ? cp.label : (shown ? '' : '—')),
            cp && KIND[cp.kind]
              ? h('span', {key: 'k', className: 'adlg-kind'}, KIND[cp.kind]) : null,
            live && shown.of > 1
              ? h('span', {key: 'o', className: 'adlg-of'}, shown.idx + '/' + shown.of) : null),
          h('div', {key: 'f', className: 'adlg-foot',
                    title: live ? 'this line advances on its own; click or press Space to skip ahead'
                                : 'the replay is running; the guide speaks again at the next checkpoint'},
            live
              ? [h('span', {key: 't', className: 'adlg-track', 'aria-hidden': 'true'},
                    h('i', {key: timing.key + '/' + Math.round(timing.dwell),
                            className: 'adlg-fill',
                            style: {animationDuration: Math.round(timing.dwell) + 'ms',
                                    animationDelay: '-' + Math.round(timing.off) + 'ms'}})),
                 h('span', {key: 'g'}, 'auto · click to skip')]
              : [h('i', {key: 'd', className: 'adlg-dot'}),
                 h('span', {key: 'r'}, 'replay running')])),
        h('div', {key: 'text', className: 'adlg-text'},
          line ? revealed(segments(line), live ? n : len, live && !done) : ''),
        stamp ? h('div', {key: 'stamp', className: 'adlg-stamp', title: stampTitle(line)},
                  stamp) : null,
        verified ? null : h('div', {key: 'note', className: 'adlg-note'},
          'Unverified · a claim in this line did not check against this epoch')),
      props.aside ? h('div', {key: 'aside', className: 'adlg-aside'}, props.aside) : null);
  }

  window.ARBITRAS_DIALOGUE = {Box: Box};
})();
