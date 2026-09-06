/* ARBITRAS static-site shim.
 *
 * Build artifact only: deploy/build_static.py prepends a manifest and copies
 * this to site/arbitras-static.js, injecting one <script> tag into COPIES of
 * console/web/{home,index}.html. The sources are never modified, and this file
 * is never present when console/server.py serves the pages locally.
 *
 * It exists because the console talks to eight server endpoints. All eight are
 * pre-rendered into /api/ at build time, so the only thing missing on a static
 * host is the redirection -- and, for /events, a client-side replayer that
 * paces the stored SSE body the way console/server.py `_events` paced it.
 */
(function () {
  'use strict';

  var M = window.__ARBITRAS_STATIC__ || {};
  var API = M.api || '/api';
  var DEFAULT_RATE = M.rate || 15;
  var STREAMS = M.streams || {};          // mission name -> true
  var FALLBACK = M.fallback || 'demo';

  /* ---- fetch: the five JSON endpoints ---------------------------------- */

  function staticFor(raw) {
    var u;
    try { u = new URL(raw, location.origin); } catch (e) { return null; }
    if (u.origin !== location.origin) return null;
    switch (u.pathname) {
      case '/missions': return API + '/missions.json';
      case '/numbers':  return API + '/numbers.json';
      case '/terrain':  return API + '/terrain.json';
      case '/mission':  return API + '/mission/' +
        encodeURIComponent(u.searchParams.get('name') || '_default') + '.json';
      default: return null;
    }
  }

  var nativeFetch = window.fetch.bind(window);
  window.fetch = function (input, init) {
    var raw = (typeof input === 'string') ? input
            : (input && typeof input.url === 'string') ? input.url : null;
    if (raw) {
      var mapped = staticFor(raw);
      if (mapped) return nativeFetch(mapped, init);
    }
    return nativeFetch(input, init);
  };

  /* ---- EventSource: replay a stored SSE body --------------------------- */

  /* Frames are exactly what the server wrote: `data: {...}` per epoch, then an
   * optional `event: dialogue` frame, then `event: end`. Parsing the stored
   * body rather than a bespoke JSON array is what keeps the build's
   * byte-identity check meaningful and lets new event types ride along
   * without touching this file. */
  function parseFrames(body) {
    var out = [];
    var chunks = body.split('\n\n');
    for (var i = 0; i < chunks.length; i++) {
      var chunk = chunks[i];
      if (!chunk) continue;
      var lines = chunk.split('\n'), type = 'message', data = [];
      for (var j = 0; j < lines.length; j++) {
        var line = lines[j];
        if (line.indexOf('event:') === 0) type = line.slice(6).trim();
        else if (line.indexOf('data:') === 0) data.push(line.slice(5).replace(/^ /, ''));
      }
      if (data.length) out.push({type: type, data: data.join('\n')});
    }
    return out;
  }

  function StaticEventSource(url) {
    var self = this;
    var u;
    try { u = new URL(url, location.origin); } catch (e) { u = null; }
    var qs = u ? u.searchParams : new URLSearchParams();

    /* Mirrors `_events`: ?mission= selects the registry stream; absent OR
     * unknown falls back to the default source, which is what the server does
     * when mission_registry.get() raises. */
    var mission = qs.get('mission');
    if (!mission || !STREAMS[mission]) mission = FALLBACK;
    var layer = qs.get('layer') === 'off' ? 'off' : 'on';
    var rate = parseFloat(qs.get('rate'));
    if (!(rate > 0)) rate = DEFAULT_RATE;

    this.url = url;
    this.readyState = 0;                  // CONNECTING
    this.withCredentials = false;
    this.onopen = null;
    this.onmessage = null;
    this.onerror = null;
    this._listeners = {};
    this._timer = null;
    this._delay = 1000 / Math.max(rate, 0.1);

    nativeFetch(API + '/events/' + mission + '.' + layer + '.sse')
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.text();
      })
      .then(function (body) {
        if (self.readyState === 2) return;
        self.readyState = 1;              // OPEN
        self._emit({type: 'open'});
        self._play(parseFrames(body), 0);
      })
      .catch(function () {
        self.readyState = 2;              // CLOSED
        self._emit({type: 'error'});
      });
  }

  StaticEventSource.prototype._emit = function (ev) {
    ev.target = ev.currentTarget = this;
    var handler = this['on' + ev.type];
    if (typeof handler === 'function') handler.call(this, ev);
    var ls = this._listeners[ev.type];
    if (ls) for (var i = 0; i < ls.length; i++) ls[i].call(this, ev);
  };

  /* One frame per tick. Only `message` frames cost a delay, because the server
   * slept once per epoch and wrote the dialogue and end frames immediately
   * after the last one. */
  StaticEventSource.prototype._play = function (frames, i) {
    var self = this;
    if (this.readyState === 2) return;
    if (i >= frames.length) return;
    var f = frames[i];
    this._emit({type: f.type, data: f.data, lastEventId: '', origin: location.origin});
    if (this.readyState === 2) return;
    var next = i + 1;
    if (next >= frames.length) return;
    var wait = f.type === 'message' ? this._delay : 0;
    this._timer = setTimeout(function () { self._play(frames, next); }, wait);
  };

  StaticEventSource.prototype.addEventListener = function (type, fn) {
    (this._listeners[type] = this._listeners[type] || []).push(fn);
  };

  StaticEventSource.prototype.removeEventListener = function (type, fn) {
    var ls = this._listeners[type];
    if (!ls) return;
    var k = ls.indexOf(fn);
    if (k >= 0) ls.splice(k, 1);
  };

  StaticEventSource.prototype.close = function () {
    this.readyState = 2;                  // CLOSED
    if (this._timer) { clearTimeout(this._timer); this._timer = null; }
  };

  StaticEventSource.CONNECTING = StaticEventSource.prototype.CONNECTING = 0;
  StaticEventSource.OPEN       = StaticEventSource.prototype.OPEN       = 1;
  StaticEventSource.CLOSED     = StaticEventSource.prototype.CLOSED     = 2;

  window.EventSource = StaticEventSource;
})();
