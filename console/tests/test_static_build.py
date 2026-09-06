"""Tests for the static site build (deploy/build_static.py).

The static site's entire justification is that its files are what the live
server would have sent -- not a recording, not an approximation. That is a claim
about two code paths agreeing, and it is exactly the kind of claim that rots
silently: someone adds a field to decide(), or a third event type to _events,
and the deployed site keeps serving yesterday's shape while every other test
stays green.

So the load-bearing test here runs the real server and diffs its response body
against the built artifact, byte for byte. The rest guard the join points the
build reaches into: the shim injection point, and the endpoints the two pages
actually call.
"""
import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen

import pytest

from console import missions as registry
from console.server import Handler
from deploy.build_static import build, inject, render_sse, resolve_stream

ROOT = Path(__file__).resolve().parent.parent.parent

# Every mission stream present; without them there is nothing to compare.
STREAMS_BUILT = all((ROOT / m.stream).exists() for m in registry.REGISTRY.values())
needs_streams = pytest.mark.skipif(
    not STREAMS_BUILT, reason="mission streams absent: run python -m backend.missions")


class _Args:
    """The subset of the CLI namespace Handler reads."""
    source = "out/demo.jsonl"
    rate = 2000.0            # the server sleeps 1/rate per epoch; go fast
    tail = False
    stale_after = 2.0
    port = 0


@pytest.fixture(scope="module")
def live_server():
    Handler.args = _Args()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


# ------------------------------------------------------- the identity claim

@needs_streams
@pytest.mark.parametrize("name", ["logistics", "casevac"])
@pytest.mark.parametrize("layer_on", [True, False])
def test_rendered_stream_is_byte_identical_to_the_live_server(live_server, name, layer_on):
    mm = registry.get(name)
    built = render_sse(resolve_stream(mm), registry.as_dict(mm), layer_on)
    layer = "on" if layer_on else "off"
    with urlopen(f"{live_server}/events?mission={name}&layer={layer}&rate=2000") as r:
        live = r.read()
    assert built == live, (
        f"{name}.{layer}: the static artifact and console/server.py have diverged. "
        f"built {len(built)} B, live {len(live)} B"
    )


@needs_streams
def test_render_is_deterministic():
    """The build's own gate. If this fails, the site cannot claim identity at all."""
    mm = registry.get("casevac")
    path, md = resolve_stream(mm), registry.as_dict(mm)
    assert render_sse(path, md, True) == render_sse(path, md, True)


@needs_streams
def test_layer_off_carries_no_dialogue_frame():
    """The guide is trust-layer output; layer=off must not leak it (server builds
    no Guide at all when the layer is off)."""
    mm = registry.get("casevac")
    assert b"event: dialogue" not in render_sse(resolve_stream(mm), registry.as_dict(mm), False)


@needs_streams
def test_every_stream_ends_with_the_terminating_event():
    """index.html closes the EventSource on `end`; without it the console hangs
    on 'live' forever."""
    mm = registry.get("combat")
    assert render_sse(resolve_stream(mm), registry.as_dict(mm), True).endswith(
        b"event: end\ndata: {}\n\n")


# ------------------------------------------------------------ shim injection

def test_shim_goes_in_before_the_first_script():
    html = "<!doctype html>\n<title>x</title>\n<script>var a=1</script>\n"
    out = inject(html, "/s.js")
    assert out.index("/s.js") < out.index("var a=1")


def test_inject_refuses_a_page_with_no_script_rather_than_silently_passing():
    with pytest.raises(SystemExit):
        inject("<!doctype html><p>nothing</p>", "/s.js")


# ------------------------------------------------------------- build outputs

@needs_streams
def test_build_writes_every_endpoint_the_pages_call(tmp_path):
    """The eight call sites in home.html and index.html, plus the shim and both
    pages. A missing file here is a blank panel on the deployed site."""
    site = tmp_path / "site"
    build(site, verify_identity=False)

    for rel in ("index.html", "console/index.html", "arbitras-static.js",
                "api/missions.json", "api/numbers.json", "api/terrain.json",
                "api/mission/_default.json", "api/mission/logistics.json",
                "api/events/demo.on.sse", "api/events/logistics.off.sse"):
        assert (site / rel).is_file(), f"missing {rel}"

    # Assets the console loads by absolute path must survive the copy.
    assert (site / "route.js").is_file()
    assert (site / "vendor" / "asset-ugv.draco.glb").is_file()
    assert (site / "vendor" / "three" / "draco" / "draco_decoder.wasm").is_file()
    assert not list(site.rglob("*.md")), "docs should not deploy"


@needs_streams
def test_manifest_lists_every_mission_the_selector_offers(tmp_path):
    """The shim falls back to demo for a mission it has no file for. If the
    manifest and the registry disagree, a tile silently replays demo."""
    site = tmp_path / "site"
    build(site, verify_identity=False)
    manifest = json.loads(
        (site / "arbitras-static.js").read_text().split("=", 1)[1].split(";\n", 1)[0])
    for m in registry.summary():
        assert manifest["streams"].get(m["name"]), f"{m['name']} missing from the manifest"
    assert manifest["fallback"] == "demo"


@needs_streams
def test_numbers_records_what_it_was_built_from(tmp_path):
    """A baked headline figure can go stale where a live one cannot; the stamp is
    what makes that visible (CLAUDE.md: every value carries its source)."""
    site = tmp_path / "site"
    build(site, verify_identity=False)
    n = json.loads((site / "api" / "numbers.json").read_text())
    assert n["rows"], "no headline rows"
    assert n["built_at"].endswith("Z")
    assert "carryoff.jsonl" in n["built_from"]
