// node console/tests/route_parity.mjs  -- JS kinematics must match Python to < 1e-6 m.
import { execFileSync } from "node:child_process";
import { createRequire } from "node:module";
import assert from "node:assert/strict";
const require = createRequire(import.meta.url);
const Route = require("../web/route.js");
// Repo root is two levels up from this file; prefer the project venv so `console` imports.
import { fileURLToPath } from "node:url"; import { dirname, join } from "node:path"; import { existsSync } from "node:fs";
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const PY = existsSync(join(ROOT, ".venv/bin/python")) ? join(ROOT, ".venv/bin/python") : "python3";
const py = (code) => JSON.parse(execFileSync(PY, ["-c", code], { cwd: ROOT, encoding: "utf8" }));
const m = py("import json; from console import mission; print(json.dumps(mission.as_dict()))");
const S = [0, 37.5, 100, 143.2, 250, 400.5, 619, 700, 819.463, 900, -5];
const ref = py(`import json; from console import mission
S=${JSON.stringify(S)}
print(json.dumps({"L": mission.route_length(), "pts": [mission.route_point(s) for s in S],
 "lat": [mission.lateral_offset(e,n) for e,n in [(50,40),(50,-20),(400,60),(-30,0),(850,150)]]}))`);
assert.ok(Math.abs(Route.routeLength(m) - ref.L) < 1e-6, "length");
S.forEach((s, i) => { const p = Route.routePoint(m, s), [e, n, h] = ref.pts[i];
  assert.ok(Math.abs(p.e - e) < 1e-6 && Math.abs(p.n - n) < 1e-6, `routePoint(${s})`);
  assert.ok(Math.abs(p.heading - h) < 1e-9, `heading(${s})`); });
[[50,40],[50,-20],[400,60],[-30,0],[850,150]].forEach(([e,n],i) =>
  assert.ok(Math.abs(Route.lateralOffset(m,e,n) - ref.lat[i]) < 1e-6, `lateralOffset(${e},${n})`));
for (const w of m.route) { const ll = Route.enuToLatLon(m, w.e, w.n), back = Route.latLonToEnu(m, ll.lat, ll.lon);
  assert.ok(Math.hypot(back.e - w.e, back.n - w.n) < 1e-3, "enu<->latlon round trip < 1 mm"); }
console.log(`route.js parity OK: ${S.length} route points, 5 lateral offsets, ${m.route.length} round-trips`);
