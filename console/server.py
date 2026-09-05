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

WEB = Path(__file__).parent / "web"
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


def follow(path: Path):
    """Tail a growing file. Yields None on timeout so silence reaches the arbiter."""
    with open(path) as fh:
        fh.seek(0, os.SEEK_END)
        while True:
            line = fh.readline()
            if not line:
                yield None
                time.sleep(0.2)
                continue
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                yield None


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
        # Video beat 2: the trust layer is OFF. The vehicle reports a confident,
        # plausible position and nothing alarms. No arbitration is shown.
        payload["state"] = TrustState.NOMINAL.name
        payload["previous_state"] = TrustState.NOMINAL.name
        payload["changed"] = False
        payload["explanation"] = None

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
        if u.path == "/events":
            return self._events(parse_qs(u.query))
        if u.path.startswith("/vendor/"):
            name = Path(u.path).name          # no traversal: basename only
            ctype = ("application/javascript" if name.endswith(".js")
                     else "application/octet-stream")
            return self._file(WEB / "vendor" / name, ctype)
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
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        arb = Arbiter()      # fresh per connection: reload == hard reset
        delay = 1.0 / max(rate, 0.1)
        try:
            source = follow(Path(a.source)) if a.tail else iter(read_epochs(Path(a.source)))
            for epoch in source:
                payload = decide(arb, epoch, layer_on)
                self.wfile.write(f"data: {json.dumps(payload)}\n\n".encode())
                self.wfile.flush()
                time.sleep(delay)
            self.wfile.write(b"event: end\ndata: {}\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--source", default=str(DEFAULT_SOURCE))
    p.add_argument("--rate", type=float, default=15.0, help="epochs/sec (design.md §5: 10-20)")
    p.add_argument("--tail", action="store_true", help="follow a growing file")
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
