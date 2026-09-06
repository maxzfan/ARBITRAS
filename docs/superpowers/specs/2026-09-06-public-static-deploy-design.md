# Public static deployment — design

**Date:** 2026-09-06
**Status:** implemented on `track_f` — `deploy/build_static.py`,
`deploy/static_shim.js`, `console/tests/test_static_build.py`,
`console/tests/static_shim_check.mjs`
**Goal:** a stable URL on a domain we own that judges can open after the
hackathon, with our machines closed.

## 1. Context

`deploy/public.sh` serves the mission page from this laptop through a
Cloudflare *quick* tunnel. The URL is random, changes every launch, and dies
with the script, the network, or sleep. It is the right tool for driving a
live demo at the table and the wrong one for a link a judge opens on Tuesday.

The obstacle to static hosting is that the console is not static. `/events`
constructs a fresh `Arbitras` per connection and pushes arbitrated decisions
over SSE; `/numbers` and `/terrain` are computed server-side.

That obstacle turns out to be cheap to remove. Measured on the current
streams:

| stream | source epochs (gzip) | pre-rendered decisions (gzip) |
|---|---|---|
| logistics | 0.24 MB | 0.25 MB layer-on · 0.11 MB layer-off |
| casevac | 0.18 MB | 0.18 MB layer-on · 0.07 MB layer-off |

Pre-rendering every decision — state, confidence, the four features, the
geometry block, the explanation and its verification — costs the same
gzipped as the raw epochs it was derived from. All five streams at both
layer settings total **~1.9 MB**. `/terrain` is 190 KB raw, **10 KB**
gzipped. `/numbers` is 912 B. `console/server.py` imports nothing outside
the standard library except a lazy `numpy` for `/terrain`.

So the server's entire dynamic surface collapses into about 2 MB of files.

## 2. Approach

Pre-render every endpoint to a static tree at build time and inject a small
shim that points the existing pages at it. Host the result on Cloudflare
Pages behind our own domain.

**Why this is not a mock.** `Arbitras` is pure and deterministic in
`(stream, layer)` — a property Track C already depends on, since its
Dirichlet sweep replays the arbitras identically on every draw. The
pre-rendered SSE bodies are therefore byte-identical to what the live server
would have pushed, and §5 makes the build prove it rather than assert it.

**No new claim is made.** The server was already replaying `out/*.jsonl`.
The site replays the same epochs through the same arbitration. Provenance
fields carry through untouched because the identical payloads are
serialised, `sensor.source: "simulated"` included.

### Alternatives considered

**Container on Fly.io / Render / a VPS.** Runs `console/server.py`
unchanged, so zero console code changes. Rejected: it keeps a process alive
to babysit, costs money, cold-starts, and buys nothing the static tree does
not already give. It remains the fallback if a live `--tail` demo ever needs
to be public.

**Refactor the eight client call sites** to route through a
`window.ARB_BASE` helper instead of shimming. Cleaner code and no global
monkey-patching. Rejected *for now* on timing: `console/web/index.html` is
under concurrent edit by another session, and this is the one file that
session is holding. Worth doing in a calmer week; §4 keeps the shim small
enough to delete when that happens.

**Port `Arbitras` to JavaScript.** Rejected outright. It creates a second
implementation of the arbitras, when the existence of exactly one is what
makes the Track C guarantee meaningful.

## 3. Build output and path map

`deploy/build_static.py` imports `console.server` in-process and calls the
same `decide()`, `numbers()` and `terrain_map()` the server calls. It
contains no arbitration logic of its own.

It is run as `python -m deploy.build_static` rather than as a path, so that
the repo root is on `sys.path` and `import console.server` resolves. That
requires adding an empty `deploy/__init__.py`; the directory currently holds
only `public.sh`, which is invoked as `bash deploy/public.sh` and is
unaffected.

```
site/index.html                      home.html + injected <script>
site/console/index.html              index.html + injected <script>
site/arbitras-static.js              the shim; build artifact only
site/api/missions.json                                    3 KB
site/api/numbers.json                                   912 B
site/api/terrain.json                       190 KB → 10 KB gz
site/api/mission/{logistics,recon,casevac,combat}.json
site/api/mission/_default.json               console/mission.py frame
site/api/events/{demo,logistics,recon,casevac,combat}.{on,off}.sse
site/{vendor,env,overlays}/**        copied verbatim, 45 MB / 86 files
site/route.js  + any other web-root *.js
```

Two details that matter:

**Web-root scripts are globbed, not enumerated.** `console/web/DIALOGUE.md`
§216 adds `/dialogue.js` alongside `/route.js`. Globbing means the in-flight
dialogue work deploys without anyone editing the build script.

**`/console` is a directory index.** Writing `site/console/index.html` is
what lets `/console?mission=combat&layer=off&rate=3` resolve on a static
host. Static hosts ignore the query string for file resolution and preserve
it in `location.search`, which is exactly where the client already reads
`mission`, `layer` and `rate`. Every existing deep link keeps working, the
`home.html` tile iframes (`/console?mission=X&hz=3`) included.

## 4. Shim contract — `site/arbitras-static.js`

Injected as a single `<script>` tag into *copies* of the two pages. The
sources in `console/web/` are never modified, so there is no merge surface
against the session editing `index.html`, and `console/server.py` keeps
working locally including `--tail`.

The shim covers exactly eight call sites:

| page | call sites |
|---|---|
| `index.html` | `/mission?name=` · `/terrain` · `new EventSource('/events' + location.search)` |
| `home.html` | `/numbers` · `/mission?name=` (×2) · `/terrain` · `/missions` |

**`fetch`** — wraps the global and rewrites five prefixes (`/mission`,
`/missions`, `/numbers`, `/terrain`, `/events`) to their `api/`
equivalents. Every other request passes through untouched.

**`EventSource`** — replaced by a class that fetches
`api/events/<mission>.<layer>.sse`, parses the stored frames, and dispatches
them paced at `1/rate` from `?rate=` (default 15, matching `_events`). It
fires `open`, a `message` per epoch, the guide's `dialogue` frame, then
`end`, because `index.html` closes the connection on `end`.

**The artifact is the raw SSE response body, not a JSON array of payloads.**
This changed during implementation. `_events` had gained a second event type
(`event: dialogue`, carrying `Guide.finish()`), and storing the body verbatim
means the identity check in §5 is a plain `cmp` and a third event type will
reach the site without anyone editing the shim.

**Mission and layer resolution mirrors `_events` exactly:** `?mission=`
selects the registry stream, falling back `mm.stream` → `mm.fallback_stream`;
absent, it is `demo`. `layer=off` selects the `.off` file.

**Reload is still the hard reset.** A fresh page load fetches from the
first line, which preserves the design.md §11a "under five seconds"
property without server-side state.

*Note:* `index.html` already buffers arriving epochs and applies one per
interval (the dialogue-gate consumer), so correctness does not strictly
depend on delivery pacing. Pacing is implemented anyway — reproducing the
server's observable behaviour is the entire premise, and it keeps the `conn`
state honest during load.

## 5. Verification gate

"These bytes are what the server would have sent" is a claim, so the build
checks it.

1. **Byte-identity.** Two layers, both implemented. The build re-renders
   every stream a second time and fails on any difference, which catches
   nondeterminism before a deploy. `console/tests/test_static_build.py` then
   runs the real `console.server` in-process and diffs its `/events` response
   against the artifact byte for byte — that is the check that catches the
   build and the server *drifting apart*, which the double-render cannot see.
   Measured on the current streams: all eight mission bodies identical.
2. **MIME probe.** After deploy, `HEAD` one file per asset class against the
   live URL: `.hdr` must arrive as an opaque byte stream for `RGBELoader`,
   and `draco_decoder.wasm` as `application/wasm`. Both fail *silently* in
   the browser when wrong — the scene renders without sky or without the
   vehicle and nothing is logged.
3. **Freshness stamp.** `numbers.json` records the build timestamp and the
   source mtimes of `clean.jsonl` and `carryoff.jsonl`. A stale headline
   figure then shows as stale rather than passing as current, which is what
   the "every value carries its source" convention is for.

## 6. Deploy procedure

**Preconditions** (none of this is in git — `data/`, `out/` and
`console/web/vendor/asset-env-*` are all ignored):

```bash
bash bootstrap.sh                 # venv, libraries, IGS data, env assets
source .venv/bin/activate
python -m backend.demo            # demo.jsonl, clean.jsonl, carryoff.jsonl
python -m backend.missions        # the four mission streams
```

`clean.jsonl` and `carryoff.jsonl` are 21 MB and feed `/numbers` only; they
are read at build time and never deployed.

**Nameservers first.** The domain is registered elsewhere and is not on
Cloudflare, so before anything else: add the site at `dash.cloudflare.com`
(Free plan), take the two nameservers it issues, and set them at the
registrar. Propagation is minutes to hours and runs in the background while
the rest of this happens. This is worth doing rather than leaving DNS at the
registrar, because an apex domain cannot be a CNAME in standard DNS — most
registrars cannot point the bare domain at `arbitras.pages.dev` at all.
Cloudflare flattens CNAMEs at the apex, so the bare domain works.

**Build and deploy:**

```bash
npx wrangler login                                  # once
npx wrangler pages project create arbitras --production-branch main
python -m deploy.build_static                       # writes site/, runs check 1
npx wrangler pages deploy site/ --project-name arbitras
python -m deploy.build_static --probe https://<domain>   # runs check 2
```

The build takes about 1.6 s and produces **103 files, 64.6 MB** — 45 MB of
assets plus ~19.5 MB of SSE bodies, which the edge serves gzipped at roughly
2 MB. Largest single file is the 6.0 MB combat sky HDR. Cloudflare Pages
allows 20,000 files and 25 MiB each, so there is about 4x per-file headroom.

**Substrate: Cloudflare Pages, direct upload via wrangler.** Chosen over
GitHub Pages for three reasons: unlimited bandwidth, which matters because
of §7; 45 MB of assets never enter git; and gzip/brotli is automatic. The
free tier needs no card.

**Custom domain**: Workers & Pages → arbitras → Custom domains. With the
zone on the same account, Cloudflare writes the DNS record itself and issues
TLS automatically. `arbitras.pages.dev` keeps working as a fallback link
while DNS propagates.

`deploy/public.sh` stays exactly as it is. Quick tunnel for driving a live
demo, static site for the durable link; they do not interact.

## 7. Known issue, out of scope

**Landing-page weight.** `home.html` iterates all four missions to render
the selector tiles, so the first visit can pull most of the 45 MB of HDRs
and ground textures. On conference wifi that is the single worst part of a
judge's experience, and it is a console change — lazy tiles, or smaller
preview environments — not a deployment change. Recorded here so the deploy
work is not blamed for it and so it is not silently forgotten. It should get
its own scope.

## 8. Open questions

- Which domain? Resolved: it is registered but not on Cloudflare, so the
  nameserver move in §6 is a prerequisite rather than an option.
- Should the site carry a visible "replay of recorded USN8 observations,
  2026-08-20" banner? The console already says so internally; a judge
  arriving cold at the console URL rather than the landing page may not see
  it.
