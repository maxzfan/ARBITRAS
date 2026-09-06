"""Render the ARBITRAS console to a static site.

    python -m deploy.build_static                     # writes site/
    python -m deploy.build_static --out /tmp/site
    python -m deploy.build_static --probe https://arbitras.example   # MIME check

Why this exists: `console/server.py` computes four JSON endpoints and pushes an
SSE stream that constructs a fresh `Arbitras` per connection. None of that needs
a server. `Arbitras` is pure and deterministic in (stream, layer) -- the property
Track C's Dirichlet sweep already relies on -- so every response the server would
ever give can be rendered once, at build time, and served as files.

This module contains NO arbitration logic. It imports `console.server` and calls
the same `decide()`, `numbers()` and `terrain_map()` the server calls, which is
what makes the output identical rather than merely similar. Anything the server
learns to emit, this learns to emit with it.

Design: docs/superpowers/specs/2026-09-06-public-static-deploy-design.md
"""
from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "console" / "web"
SHIM_SRC = Path(__file__).resolve().parent / "static_shim.js"
SHIM_NAME = "arbitras-static.js"

# The server's own default, so a viewer who passes no ?rate= sees what the
# server would have paced (console/server.py: `--rate`, design.md §5 says 10-20).
DEFAULT_RATE = 15.0

# Not deployed: docs, editor droppings, and the Blender export scripts, none of
# which a browser asks for.
SKIP_SUFFIX = {".md"}
SKIP_NAME = {".DS_Store"}
SKIP_DIR = {"tools", "__pycache__"}


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------

def render_sse(stream_path: Path, mission_dict: dict | None, layer_on: bool) -> bytes:
    """The exact response body `_events` would write for this mission and layer.

    Mirrors console/server.py `_events` step for step: one fresh Arbitras, one
    fresh Guide when the layer is on, a `data:` frame per epoch, the guide's
    closing `event: dialogue` frame, then `event: end`. Storing the body verbatim
    (rather than a bespoke JSON array of payloads) is what makes the identity
    check in `verify()` meaningful, and it means a new event type on the wire
    reaches the static site without anyone editing the shim.
    """
    from console.arbitras.machine import Arbitras
    from console.server import decide, read_epochs
    try:
        from console.arbitras.dialogue import Guide
    except ImportError:                     # dialogue is optional, as in the server
        Guide = None

    arb = Arbitras()
    guide = Guide(mission_dict) if (Guide is not None and layer_on) else None

    frames: list[str] = []
    for epoch in read_epochs(stream_path):
        frames.append(f"data: {json.dumps(decide(arb, epoch, layer_on, guide))}\n\n")
    if guide is not None:
        tail = guide.finish()
        if isinstance(tail, dict) and tail.get("lines"):
            frames.append(f"event: dialogue\ndata: {json.dumps(tail)}\n\n")
    frames.append("event: end\ndata: {}\n\n")
    return "".join(frames).encode()


def resolve_stream(mm) -> Path | None:
    """Stream for a mission, falling back exactly as `_events` falls back."""
    for cand in (mm.stream, mm.fallback_stream):
        if cand and (ROOT / cand).exists():
            return ROOT / cand
    return None


def stamped_numbers() -> dict:
    """`/numbers` plus the provenance of the files it was computed from.

    The convention is that every value carries its source (CLAUDE.md). On a live
    server the numbers are recomputed per request and cached per mtime, so they
    cannot go stale unnoticed. Baked into a file they can, and a stale headline
    figure passing as current is exactly the failure the convention exists to
    prevent -- so the file says when it was built and from what.
    """
    from console.server import numbers
    n = dict(numbers())
    srcs = {}
    for p in (ROOT / "out/clean.jsonl", ROOT / "out/carryoff.jsonl"):
        srcs[p.name] = (
            {"mtime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(p.stat().st_mtime)),
             "bytes": p.stat().st_size} if p.exists() else None
        )
    n["built_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    n["built_from"] = srcs
    return n


# --------------------------------------------------------------------------
# page copying
# --------------------------------------------------------------------------

def inject(html: str, src: str) -> str:
    """Put the shim before the page's first <script>, so it is installed before
    anything can call fetch() or construct an EventSource."""
    tag = f'<script src="{src}"></script>\n'
    i = html.find("<script")
    if i < 0:
        raise SystemExit("no <script> tag to inject before")
    return html[:i] + tag + html[i:]


def copy_web(site: Path) -> int:
    """Everything under console/web/ that a browser asks for, verbatim.

    Directories are walked rather than enumerated on purpose: console/web/DIALOGUE.md
    adds /dialogue.js alongside /route.js, and the in-flight dialogue work should
    reach the static site without anyone remembering to edit this file.
    """
    count = 0
    for src in sorted(WEB.rglob("*")):
        if not src.is_file():
            continue
        rel = src.relative_to(WEB)
        if (src.suffix in SKIP_SUFFIX or src.name in SKIP_NAME
                or set(rel.parts[:-1]) & SKIP_DIR):
            continue
        if rel.name in ("index.html", "home.html") and len(rel.parts) == 1:
            continue                        # the two pages are placed by build()
        dst = site / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        count += 1
    return count


# --------------------------------------------------------------------------
# build
# --------------------------------------------------------------------------

def build(site: Path, verify_identity: bool = True) -> dict:
    from console import mission as legacy_mission
    from console import missions as registry
    from console.server import DEFAULT_SOURCE, terrain_map

    if site.exists():
        shutil.rmtree(site)
    (site / "api" / "mission").mkdir(parents=True)
    (site / "api" / "events").mkdir(parents=True)

    def write_json(rel: str, obj) -> int:
        p = site / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        b = json.dumps(obj).encode()
        p.write_bytes(b)
        return len(b)

    report: dict = {"json": {}, "events": {}, "files": 0}

    # --- the four JSON endpoints -----------------------------------------
    report["json"]["missions.json"] = write_json("api/missions.json", registry.summary())
    report["json"]["numbers.json"] = write_json("api/numbers.json", stamped_numbers())
    t = terrain_map()
    if t is not None:
        report["json"]["terrain.json"] = write_json("api/terrain.json", t)
    else:
        print("  WARN: no data/terrain_usn8.npz -- CASEVAC ground falls back")
    report["json"]["mission/_default.json"] = write_json(
        "api/mission/_default.json", legacy_mission.as_dict())
    for m in registry.summary():
        name = m["name"]
        report["json"][f"mission/{name}.json"] = write_json(
            f"api/mission/{name}.json", registry.as_dict(registry.get(name)))

    # --- the streams ------------------------------------------------------
    # Keyed by what the client asks for (?mission=), not by the file it lands on,
    # so the shim never has to know about fallbacks.
    targets: list[tuple[str, Path, dict | None]] = []
    demo = ROOT / DEFAULT_SOURCE
    if not demo.exists():
        raise SystemExit(f"no default stream at {demo}\n  run: python -m backend.demo")
    targets.append(("demo", demo, None))
    for m in registry.summary():
        mm = registry.get(m["name"])
        path = resolve_stream(mm)
        if path is None:
            print(f"  WARN: no stream for {m['name']} -- skipped")
            continue
        targets.append((m["name"], path, registry.as_dict(mm)))

    streams: dict[str, bool] = {}
    for name, path, mdict in targets:
        streams[name] = True
        for layer_on in (True, False):
            body = render_sse(path, mdict, layer_on)
            rel = f"api/events/{name}.{'on' if layer_on else 'off'}.sse"
            (site / rel).write_bytes(body)
            report["events"][rel] = len(body)
            if verify_identity:
                again = render_sse(path, mdict, layer_on)
                if again != body:
                    raise SystemExit(
                        f"NOT DETERMINISTIC: {rel} differs between two renders of the "
                        f"same input. The static site's identity claim does not hold; "
                        f"fix the nondeterminism before deploying."
                    )
        print(f"  {name:10s} {path.name:22s} "
              f"on {report['events'][f'api/events/{name}.on.sse']/1e6:5.2f} MB  "
              f"off {report['events'][f'api/events/{name}.off.sse']/1e6:5.2f} MB")

    # --- edge headers -----------------------------------------------------
    # `.sse` is not a known extension, so the edge would type these
    # application/octet-stream, and Cloudflare decides whether to compress from
    # the content type. Uncompressed, logistics.on is 2.4 MB on the wire instead
    # of ~250 KB.
    #
    # text/event-stream is what these bodies ARE, and it was the first thing
    # tried -- but the edge deliberately never compresses that type, because SSE
    # is meant to stream and compressing it would buffer it. Measured on the
    # deployed site: correct type, no Content-Encoding, full 2.4 MB.
    #
    # Nothing here streams: the shim fetches each file whole and calls .text().
    # So text/plain is both the honest description of the delivery and the one
    # that gets compressed.
    (site / "_headers").write_text(
        "/api/events/*\n"
        "  Content-Type: text/plain; charset=utf-8\n"
        "  Cache-Control: public, max-age=300\n"
        "\n"
        "/vendor/*\n"
        "  Cache-Control: public, max-age=31536000, immutable\n"
    )

    # --- shim + pages -----------------------------------------------------
    manifest = {"api": "/api", "rate": DEFAULT_RATE, "streams": streams, "fallback": "demo"}
    shim = (f"window.__ARBITRAS_STATIC__ = {json.dumps(manifest)};\n"
            + SHIM_SRC.read_text())
    (site / SHIM_NAME).write_text(shim)

    home = (WEB / "home.html").read_text()
    console = (WEB / "index.html").read_text()
    (site / "index.html").write_text(inject(home, f"/{SHIM_NAME}"))
    (site / "console").mkdir(parents=True, exist_ok=True)
    (site / "console" / "index.html").write_text(inject(console, f"/{SHIM_NAME}"))

    report["files"] = copy_web(site) + 3 + len(report["json"]) + len(report["events"])
    return report


# --------------------------------------------------------------------------
# post-deploy probe
# --------------------------------------------------------------------------

# A wrong Content-Type on either of these fails SILENTLY in the browser: the
# scene renders with no sky, or with no vehicle, and nothing is logged.
PROBES = [
    ("/vendor/asset-env-combat-sky.hdr", ("application/octet-stream", "image/vnd.radiance")),
    ("/vendor/three/draco/draco_decoder.wasm", ("application/wasm",)),
    ("/vendor/asset-ugv.draco.glb", ("model/gltf-binary", "application/octet-stream")),
    # Typed by site/_headers. octet-stream means the header file did not apply;
    # text/event-stream means it applied but the edge will refuse to compress.
    # Either way every stream ships ~10x its compressed size.
    ("/api/events/logistics.on.sse", ("text/plain",)),
    ("/api/numbers.json", ("application/json",)),
    ("/console", ("text/html",)),
]


def probe(base: str) -> int:
    """HEAD one file per asset class against a DEPLOYED site.

    Targets the real edge, not a local `python -m http.server`: two of the
    expectations below (the event-stream type, and therefore compression) come
    from site/_headers, which only Cloudflare Pages applies.
    """
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError, URLError

    base = base.rstrip("/")
    bad = 0
    for path, want in PROBES:
        # Cloudflare answers urllib's default User-Agent with 403, which reads as
        # a broken deploy when the site is fine. Ask as a browser would.
        req = Request(base + path, method="HEAD", headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
            "Accept-Encoding": "gzip",
        })
        try:
            with urlopen(req, timeout=20) as r:
                ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip()
                enc = r.headers.get("Content-Encoding") or "none"
                ok = want is None or ctype in want
                print(f"  {'ok  ' if ok else 'BAD '} {path:45s} {r.status} {ctype:28s} enc={enc}")
                if not ok:
                    print(f"       expected one of {want}")
                    bad += 1
        except (HTTPError, URLError) as e:
            print(f"  BAD  {path:45s} {e}")
            bad += 1
    return bad


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default="site", help="output directory (default: site)")
    ap.add_argument("--probe", metavar="URL", help="HEAD-check a deployed site instead of building")
    ap.add_argument("--no-verify", action="store_true",
                    help="skip the double-render identity check (faster; not for a deploy)")
    a = ap.parse_args()

    if a.probe:
        raise SystemExit(1 if probe(a.probe) else 0)

    site = Path(a.out)
    if not site.is_absolute():
        site = ROOT / site
    print(f"building {site}")
    r = build(site, verify_identity=not a.no_verify)
    total = sum(f.stat().st_size for f in site.rglob("*") if f.is_file())
    n = sum(1 for f in site.rglob("*") if f.is_file())
    print(f"\n  {n} files, {total/1e6:.1f} MB")
    print(f"  identity check: {'skipped' if a.no_verify else 'passed'}")
    print(f"\nnext:  npx wrangler pages deploy {site} --project-name arbitras")


if __name__ == "__main__":
    main()
