"""Console server: tails or replays the §5 contract stream, arbitrates, serves SSE.

    python -m console.server                      # replay the fixture stream
    python -m console.server --source out/run.jsonl
    python -m console.server --source out/live.jsonl --tail     # follow Track A live
    python -m console.server --rate 8             # slower, for video beats 2 and 3

Then open http://localhost:8420

Hard reset (design.md §11a, "under five seconds"): each /events connection gets
a fresh Arbitras and replays from epoch 0, so a browser reload IS the reset.
Press r in the console. Nothing to restart server-side between takes.

Transport is one JSON object per line, per design.md §5. This process never
opens a RINEX file and never sees an observable.
"""
import argparse
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from console.arbitras.explain import explain, verify
from console.arbitras.machine import Arbitras
from console.arbitras.states import THRESHOLD_PROVENANCE, THRESHOLDS, TrustState
from console import mission
from console import missions as mission_registry

WEB = Path(__file__).parent / "web"
VENDOR_MIME = {
    ".js": "application/javascript", ".woff2": "font/woff2",
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".hdr": "application/octet-stream",          # RGBELoader reads an arraybuffer
    ".glb": "model/gltf-binary", ".gltf": "model/gltf+json",
    ".wasm": "application/wasm",
}
DEFAULT_SOURCE = Path("out/demo.jsonl")   # real USN8 data (backend/demo.py); fixture retired


def read_epochs(path: Path):
    """Whole-file read. Replay mode: rate is ours to control, reset is instant."""
    out = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                out.append(None)   # malformed lines are evidence, not skipped (§5)
    return out


def follow(path: Path, stale_after: float = 2.0, poll: float = 0.05):
    """Tail a growing file, yielding an epoch per line.

    Yields None ONLY after `stale_after` seconds of genuine silence, and at most
    once per such window -- not once per poll.

    This distinction is the whole bug it fixes. The arbitras treats every None as
    a missing epoch and steps authority down after STALE_GRACE_TICKS of them
    (design.md §5, "silence is not consent"). If the tail yields None on every
    poll timeout, a perfectly healthy producer that emits slower than the poll
    interval gets driven to SURRENDERED on clean data. Staleness is a property
    of wall-clock time against the expected epoch cadence, not of how often we
    happen to check.

    Set --stale-after to roughly 3x Track A's actual emit interval at the 18:30
    integration checkpoint.

    The wall clock lives here and not in Arbitras on purpose: the arbitras stays
    pure and deterministic so Track C's Dirichlet sweep replays it identically
    every draw (design.md §10).
    """
    with open(path) as fh:
        fh.seek(0, os.SEEK_END)
        last_seen = time.monotonic()
        while True:
            line = fh.readline()
            if line:
                stripped = line.strip()
                if not stripped:
                    continue
                last_seen = time.monotonic()
                try:
                    yield json.loads(stripped)
                except json.JSONDecodeError:
                    yield None      # malformed is evidence, not a skip (§5)
                continue
            if time.monotonic() - last_seen >= stale_after:
                last_seen = time.monotonic()   # rearm: one None per window
                yield None
                continue
            time.sleep(poll)


_TERRAIN_CACHE: dict = {}
_NUMBERS_CACHE: dict = {}


def numbers(clean_path=Path("out/clean.jsonl"), attack_path=Path("out/carryoff.jsonl")) -> dict:
    """design.md §10 headline numbers from the streams on disk, cached per mtime.

    max adversarial displacement = |position - _truth| at the epoch before the
    first arbitrated transition out of NOMINAL after onset; the analytic bound
    is that same epoch's geometry.displacement_bound_m; time-to-alert is epochs
    from onset to that transition; FSR is epochs below NOMINAL over the clean
    replay. Nulls, with a note, when a stream is missing."""
    key = tuple((str(q), q.stat().st_mtime if q.exists() else None) for q in (clean_path, attack_path))
    if _NUMBERS_CACHE.get("key") == key:
        return _NUMBERS_CACHE["v"]
    from console.replay import arbitrate, false_surrender_rate, time_to_alert
    rows = []
    def row(label, value, unit, source): rows.append({"label": label, "value": value, "unit": unit, "source": source})
    if attack_path.exists():
        eps = read_epochs(attack_path)
        onset = next((i for i, e in enumerate(eps) if isinstance(e, dict)
                      and (e.get("_attack") or {}).get("stage", "CLEAN") != "CLEAN"), None)
        if onset is not None:
            dec = arbitrate(eps)
            tta = time_to_alert(dec, onset)
            k = onset + tta - 1 if tta else None
            disp = bound = None
            if k is not None and isinstance(eps[k], dict):
                disp = (eps[k].get("_solution") or {}).get("displacement_m")
                bound = (eps[k].get("geometry") or {}).get("displacement_bound_m")
            src = f"{attack_path}, epoch {k} (before the first transition out of NOMINAL)"
            row("MAX ADVERSARIAL DISPLACEMENT", disp, "M", src)
            row("ANALYTIC BOUND · SAME EPOCH", bound, "M", src)
            row("TIME TO ALERT", tta, "EPOCHS", f"{attack_path}, onset epoch {onset}, 30 s epochs")
        else:
            row("MAX ADVERSARIAL DISPLACEMENT", None, "M", f"{attack_path}: no attack epochs")
    else:
        row("MAX ADVERSARIAL DISPLACEMENT", None, "M", f"{attack_path} missing: run python -m backend.demo")
    if clean_path.exists():
        m = false_surrender_rate(arbitrate(read_epochs(clean_path)))
        row("FALSE SURRENDER RATE", None if m.get("fsr") is None else round(m["fsr"], 4), "",
            f"{clean_path}, {m.get('epochs')} clean epochs, {m.get('downgrade_events')} events")
    else:
        row("FALSE SURRENDER RATE", None, "", f"{clean_path} missing")
    row("STATION", "USN8 · 2026-08-20 · 30 S · 5 CONSTELLATIONS", "", "design.md §4")
    row("THRESHOLDS", THRESHOLD_PROVENANCE.split(":")[0], "", "console/arbitras/states.py")
    v = {"rows": rows, "note": "computed from the streams on disk at server start; every value carries its source"}
    _NUMBERS_CACHE.update(key=key, v=v)
    return v


def terrain_map(path: Path = Path("data/terrain_usn8.npz")) -> dict | None:
    """Class grid of the signed pre-map as plain JSON (row index grows north,
    column index east, -1 = unlabelled). Cached after the first read."""
    if "t" in _TERRAIN_CACHE:
        return _TERRAIN_CACHE["t"]
    if not path.exists():
        return None
    import numpy as np
    z = np.load(path, allow_pickle=False)
    hdr = json.loads(str(z["header"]))
    grid = z["grid"].astype(int)
    t = {"rows": int(grid.shape[0]), "cols": int(grid.shape[1]),
         "cell_m": hdr.get("cell_m"), "origin_enu": hdr.get("origin_enu"),
         "classes": hdr.get("classes"), "map_id": hdr.get("map_id"),
         "signed": path.with_suffix(".npz.sig").exists(),
         "grid": grid.ravel().tolist()}
    _TERRAIN_CACHE["t"] = t
    return t


def decide(arb: Arbitras, epoch, layer_on: bool) -> dict:
    """One arbitration + explanation + verification, ready for the wire."""
    d = arb.step(epoch)
    payload = d.to_dict()

    if layer_on:
        ex = explain(d)
        ok, failures = verify(ex, epoch if isinstance(epoch, dict) else {})
        if not ok:
            # design.md §14: a number we cannot source does not get displayed.
            ex = {"headline": ex["headline"],
                  "detail": "(numeric detail withheld: unverified claim)",
                  "claims": []}
        payload["explanation"] = ex
        payload["explanation_verified"] = ok
        payload["explanation_failures"] = failures
    else:
        # Video beat 2: the trust layer is OFF. An unprotected vehicle reports a
        # confident, plausible position and nothing alarms.
        #
        # Everything the trust layer PRODUCES must come off the wire, not just be
        # hidden: confidence, the four features, the geometry block and the
        # explanation are all its output. Leaving them visible while the badge
        # reads OFF is a contradiction a judge will spot immediately -- and the
        # empty panel is the argument anyway. This is what an operator sees today.
        payload["state"] = TrustState.NOMINAL.name
        payload["previous_state"] = TrustState.NOMINAL.name
        payload["changed"] = False
        payload["explanation"] = None
        payload["confidence"] = None
        payload["features"] = {}
        # Satellite POSITIONS are not trust-layer output -- an unprotected receiver
        # still sees the sky -- but the trusted FLAGS are. Keep the positions,
        # force every flag true, drop everything else in the block. The dome then
        # shows the whole constellation with nothing dark during beat 2.
        sky = (epoch.get("geometry") or {}).get("sky") if isinstance(epoch, dict) else None
        payload["geometry"] = {"sky": [dict(sv, trusted=True) for sv in sky]} if sky else {}
        payload["geometry_divergence"] = None
        payload["implied_state"] = None
        payload["reason"] = "layer_off"
        # Track E: the terrain verdict and the DEGRADED advisory built on it
        # are the layer's output too (tracks/TRACK_E.md); off means off.
        payload["terrain"] = {}
        payload["advisory"] = None
        payload["pursuing"] = None

    payload["layer_on"] = layer_on
    payload["thresholds"] = {s.name: v for s, v in THRESHOLDS.items()}
    payload["threshold_provenance"] = THRESHOLD_PROVENANCE
    if isinstance(epoch, dict):
        payload["position"] = epoch.get("position")
        # §5 provenance flag ("solution" | "surveyed" | "wls_differential"):
        # copied through so the console can say which solver stood behind the
        # dot it draws. Absent from older streams; omitted rather than faked.
        if "position_source" in epoch:
            payload["position_source"] = epoch["position_source"]
        payload["satellites_tracked"] = epoch.get("satellites_tracked")
        payload["synthetic"] = bool(epoch.get("_synthetic"))
        # Replay ground truth. Out of the §5 contract on purpose -- a real
        # vehicle has no truth channel, a replay does. Needed for video beat 2
        # (believed and true track separating). See NOTE in tracks/TRACK_B.md.
        payload["_truth"] = epoch.get("_truth")
    return payload


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    args = None

    def log_message(self, *a):
        pass    # silent: a clean terminal is part of the capture (design.md §11b)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/":
            # Track F: the routing page is the front door; the console lives at
            # /console (and /index.html) and is what the page's third section hosts.
            home = WEB / "home.html"
            return self._file(home if home.exists() else WEB / "index.html", "text/html; charset=utf-8")
        if u.path in ("/console", "/index.html"):
            return self._file(WEB / "index.html", "text/html; charset=utf-8")
        if u.path == "/route.js":
            # Our own kinematics module (mirrors console/mission.py); not vendor.
            return self._file(WEB / "route.js", "application/javascript")
        if u.path == "/mission":
            # Track F (tracks/TRACK_F.md §3): ?name= selects a registry mission;
            # without it the legacy console/mission.py frame is served unchanged.
            q = parse_qs(u.query)
            name = q.get("name", [None])[0]
            if name is None:
                return self._json(mission.as_dict())
            try:
                return self._json(mission_registry.as_dict(mission_registry.get(name)))
            except KeyError as e:
                return self.send_error(404, str(e))
        if u.path == "/missions":
            return self._json(mission_registry.summary())
        if u.path == "/numbers":
            # Hero spec block (TRACK_F.md §1): headline results computed from
            # the streams on disk, each with its source, never typed by hand.
            return self._json(numbers())
        if u.path == "/terrain":
            # The signed pre-map (Track E) as a class grid, for the CASEVAC
            # environment. Mission context, not an observable: loaded with numpy
            # straight from the .npz; nothing from backend/ is imported.
            t = terrain_map()
            return self._json(t) if t else self.send_error(404, "no terrain map in data/")
        if u.path == "/home":                       # kept as an alias
            return self._file(WEB / "home.html", "text/html; charset=utf-8")
        if u.path.startswith("/overlays/"):
            name = Path(u.path[10:]).name
            if not name or Path(name).suffix.lower() not in (".js", ".md"):
                return self.send_error(404)
            return self._file(WEB / "overlays" / name,
                              "application/javascript" if name.endswith(".js") else "text/plain; charset=utf-8")
        if u.path.startswith("/env/"):
            name = Path(u.path[5:]).name
            ext = Path(name).suffix.lower()
            if not name or ext not in (".js", ".html", ".md", ".txt", ".json"):
                return self.send_error(404)
            return self._file(WEB / "env" / name,
                              {".js": "application/javascript", ".html": "text/html; charset=utf-8",
                               ".json": "application/json"}.get(ext, "text/plain; charset=utf-8"))
        if u.path == "/events":
            return self._events(parse_qs(u.query))
        if u.path.startswith("/papers/"):
            # Research section (home.html): the team's own papers, served from
            # console/web/papers. Basename only, PDFs only.
            name = Path(u.path[8:]).name
            if not name or Path(name).suffix.lower() != ".pdf":
                return self.send_error(404)
            return self._file(WEB / "papers" / name, "application/pdf")
        if u.path.startswith("/vendor/"):
            # Basename only, plus at most one whitelisted subdirectory, so a
            # request can never walk out of vendor/.
            parts = [x for x in u.path.split("/")[2:] if x]
            if ".." in parts or not parts or len(parts) > 3:
                return self.send_error(404)
            sub = parts[:-1]
            if sub and (sub[0] != "three" or len(sub) > 2 or ".." in sub):
                return self.send_error(404)
            name = Path(parts[-1]).name
            ext = Path(name).suffix.lower()
            # A wrong MIME fails silently in the browser: fonts are refused,
            # textures may or may not be sniffed. Be explicit.
            ctype = VENDOR_MIME.get(ext, "application/octet-stream")
            return self._file(WEB.joinpath("vendor", *sub, name), ctype)
        self.send_error(404)

    def _json(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        return self.wfile.write(body)

    def _file(self, path: Path, ctype):
        if not path.exists():
            return self.send_error(404, f"missing {path}")
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _events(self, q):
        a = self.args
        rate = float(q.get("rate", [a.rate])[0])
        layer_on = q.get("layer", ["on"])[0] != "off"
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        # An SSE body has no Content-Length and we are not chunk-encoding, so
        # under HTTP/1.1 the ONLY legal framing is delimit-by-close. Advertising
        # keep-alive here leaves the body length undefined: curl tolerates it and
        # reads to EOF, browsers do not and render nothing. The client closes the
        # EventSource on our `end` event so this does not become a reconnect loop.
        self.send_header("Connection", "close")
        self.close_connection = True
        self.end_headers()

        arb = Arbitras()      # fresh per connection: reload == hard reset
        delay = 1.0 / max(rate, 0.1)
        # Track F: ?mission=<name> replays that mission's stream (or its
        # fallback until generated); --source still wins in tail mode.
        src_path = Path(a.source)
        mname = q.get("mission", [None])[0]
        if mname and not a.tail:
            try:
                mm = mission_registry.get(mname)
            except KeyError:
                mm = None
            if mm is not None:
                for cand in (mm.stream, mm.fallback_stream):
                    if cand and Path(cand).exists():
                        src_path = Path(cand)
                        break
        try:
            tailing = bool(a.tail)
            source = (follow(src_path, a.stale_after) if tailing
                      else iter(read_epochs(src_path)))
            for epoch in source:
                payload = decide(arb, epoch, layer_on)
                self.wfile.write(f"data: {json.dumps(payload)}\n\n".encode())
                self.wfile.flush()
                if not tailing:
                    time.sleep(delay)   # tail mode is paced by the producer
            self.wfile.write(b"event: end\ndata: {}\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--source", default=str(DEFAULT_SOURCE))
    p.add_argument("--rate", type=float, default=15.0, help="epochs/sec (design.md §5: 10-20)")
    p.add_argument("--tail", action="store_true", help="follow a growing file")
    p.add_argument("--stale-after", type=float, default=2.0, dest="stale_after",
                   help="seconds of silence before an epoch counts as missing "
                        "(tail mode only; set to ~3x the producer's interval)")
    p.add_argument("--port", type=int, default=8420)
    a = p.parse_args()

    if not Path(a.source).exists():
        raise SystemExit(
            f"no stream at {a.source}\n"
            f"  generate the real streams:  python -m backend.demo\n"
            f"  or point --source at Track A's output"
        )

    Handler.args = a
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    print(f"console  http://localhost:{a.port}")
    print(f"source   {a.source}{'  (tailing)' if a.tail else ''}")
    print(f"rate     {a.rate} epochs/sec        thresholds: {THRESHOLD_PROVENANCE}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
