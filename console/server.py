"""Console server: tails or replays the §5 contract stream, arbitrates, serves SSE.

    python -m console.server                      # replay the fixture stream
    python -m console.server --source out/run.jsonl
    python -m console.server --source out/live.jsonl --tail     # follow Track A live
    python -m console.server --rate 8             # slower, for video beats 2 and 3

Then open http://localhost:8420

Hard reset (design.md §11a, "under five seconds"): each /events connection gets
a fresh Arbiter and replays from epoch 0, so a browser reload IS the reset.
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

from console.arbiter.explain import explain, verify
from console.arbiter.machine import Arbiter
from console.arbiter.states import THRESHOLD_PROVENANCE, THRESHOLDS, TrustState
from console import mission

WEB = Path(__file__).parent / "web"
VENDOR_MIME = {
    ".js": "application/javascript", ".woff2": "font/woff2",
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".hdr": "application/octet-stream",          # RGBELoader reads an arraybuffer
    ".glb": "model/gltf-binary", ".gltf": "model/gltf+json",
    ".wasm": "application/wasm",
}
DEFAULT_SOURCE = Path("out/fixture_stream.jsonl")


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

    This distinction is the whole bug it fixes. The arbiter treats every None as
    a missing epoch and steps authority down after STALE_GRACE_TICKS of them
    (design.md §5, "silence is not consent"). If the tail yields None on every
    poll timeout, a perfectly healthy producer that emits slower than the poll
    interval gets driven to SURRENDERED on clean data. Staleness is a property
    of wall-clock time against the expected epoch cadence, not of how often we
    happen to check.

    Set --stale-after to roughly 3x Track A's actual emit interval at the 18:30
    integration checkpoint.

    The wall clock lives here and not in Arbiter on purpose: the arbiter stays
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


def decide(arb: Arbiter, epoch, layer_on: bool) -> dict:
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

    payload["layer_on"] = layer_on
    payload["thresholds"] = {s.name: v for s, v in THRESHOLDS.items()}
    payload["threshold_provenance"] = THRESHOLD_PROVENANCE
    if isinstance(epoch, dict):
        payload["position"] = epoch.get("position")
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
        if u.path in ("/", "/index.html"):
            return self._file(WEB / "index.html", "text/html; charset=utf-8")
        if u.path == "/mission":
            body = json.dumps(mission.as_dict()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return self.wfile.write(body)
        if u.path == "/events":
            return self._events(parse_qs(u.query))
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

        arb = Arbiter()      # fresh per connection: reload == hard reset
        delay = 1.0 / max(rate, 0.1)
        try:
            tailing = bool(a.tail)
            source = (follow(Path(a.source), a.stale_after) if tailing
                      else iter(read_epochs(Path(a.source))))
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
            f"  generate the development fixture:  python -m console.fixture_stream\n"
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
