// node console/tests/static_shim_check.mjs
//
// The static site's shim (deploy/static_shim.js) stands in for the server on
// eight call sites, and it is the one piece of the deployment that no Python
// test can reach. It runs in a browser, so it is checked here in Node against a
// stubbed window rather than left to be discovered by a judge.
//
// What matters: the five JSON rewrites, pass-through for everything else, and
// an EventSource that reproduces the server's frame ORDER (epochs, then the
// guide's dialogue frame, then end) -- because index.html closes the connection
// on `end` and queues the dialogue behind the epoch backlog.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import vm from "node:vm";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const SHIM = readFileSync(join(ROOT, "deploy/static_shim.js"), "utf8");

const MANIFEST = {
  api: "/api", rate: 15,
  streams: { demo: true, logistics: true, casevac: true },
  fallback: "demo",
};

// One SSE body shaped exactly like console/server.py `_events` writes it.
const BODY =
  'data: {"epoch":0}\n\n' +
  'data: {"epoch":1}\n\n' +
  'data: {"epoch":2}\n\n' +
  'event: dialogue\ndata: {"lines":["closing"]}\n\n' +
  "event: end\ndata: {}\n\n";

/** Load the shim into a fresh sandbox; returns the sandbox plus a fetch log. */
function load() {
  const asked = [];
  const origin = "https://arbitras.test";
  const win = {
    __ARBITRAS_STATIC__: MANIFEST,
    fetch(url) {
      asked.push(String(url));
      return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve(BODY) });
    },
    EventSource: function Native() {},
  };
  const ctx = { window: win, location: { origin }, URL, URLSearchParams, setTimeout,
                clearTimeout, console };
  ctx.globalThis = ctx;
  vm.createContext(ctx);
  vm.runInContext(SHIM, ctx);
  return { win, asked, origin };
}

// ------------------------------------------------------------ fetch rewrites

{
  const { win, asked } = load();
  const cases = [
    ["/missions", "/api/missions.json"],
    ["/numbers", "/api/numbers.json"],
    ["/terrain", "/api/terrain.json"],
    ["/mission", "/api/mission/_default.json"],
    ["/mission?name=casevac", "/api/mission/casevac.json"],
  ];
  for (const [from, to] of cases) {
    asked.length = 0;
    win.fetch(from);
    assert.equal(asked[0], to, `${from} should map to ${to}`);
  }

  // Anything that is not one of the five endpoints must reach the network
  // untouched: the console loads vendor assets, env modules and overlays by
  // absolute path through the same global.
  for (const p of ["/vendor/asset-ugv.draco.glb", "/env/casevac.js",
                   "/overlays/takeover.js", "/route.js", "/api/numbers.json"]) {
    asked.length = 0;
    win.fetch(p);
    assert.equal(asked[0], p, `${p} must pass through unmodified`);
  }
  console.log("ok  fetch rewrites five endpoints and passes the rest through");
}

// --------------------------------------------------------------- EventSource

/** Drive one EventSource to completion and record what it emitted. */
function replay(query) {
  const { win, asked } = load();
  return new Promise((resolve, reject) => {
    const seen = { messages: [], dialogue: 0, end: 0, opened: 0 };
    const es = new win.EventSource("/events" + query);
    es.onopen = () => seen.opened++;
    es.onmessage = (e) => seen.messages.push(JSON.parse(e.data).epoch);
    es.addEventListener("dialogue", (e) => {
      seen.dialogue++;
      assert.deepEqual(JSON.parse(e.data).lines, ["closing"]);
    });
    es.addEventListener("end", () => { seen.end++; es.close(); resolve({ seen, asked, es }); });
    es.onerror = () => reject(new Error("unexpected error event"));
    setTimeout(() => reject(new Error("timed out: `end` never fired")), 4000);
  });
}

{
  // rate is honoured, so a high one keeps the test quick without special-casing.
  const { seen, asked, es } = await replay("?mission=logistics&rate=5000");
  assert.equal(asked[0], "/api/events/logistics.on.sse");
  assert.equal(seen.opened, 1, "onopen fires once");
  assert.deepEqual(seen.messages, [0, 1, 2], "every epoch, in order, none skipped");
  assert.equal(seen.dialogue, 1, "the guide's closing frame arrives on its own event");
  assert.equal(seen.end, 1, "`end` fires once");
  assert.equal(es.readyState, 2, "close() leaves it CLOSED, as index.html expects");
  console.log("ok  replays epochs, then dialogue, then end");
}

{
  const { asked } = await replay("?mission=casevac&layer=off&rate=5000");
  assert.equal(asked[0], "/api/events/casevac.off.sse", "layer=off selects the off body");
  console.log("ok  layer=off selects the layer-off body");
}

{
  // console/server.py: an unknown ?mission= leaves mission_registry.get() raising
  // and the source falls back to --source, i.e. the demo stream. Same here.
  const { asked } = await replay("?mission=nosuchmission&rate=5000");
  assert.equal(asked[0], "/api/events/demo.on.sse");
  console.log("ok  unknown mission falls back to demo, as the server does");
}

{
  const { asked } = await replay("?rate=5000");
  assert.equal(asked[0], "/api/events/demo.on.sse");
  console.log("ok  no mission falls back to demo");
}

console.log("\nstatic_shim_check: all assertions passed");
