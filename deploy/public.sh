#!/usr/bin/env bash
# Serve the ARBITER mission page publicly from this machine through a
# Cloudflare quick tunnel (no account, random *.trycloudflare.com URL).
#
#     bash deploy/public.sh            # prints the public URL; Ctrl-C stops everything
#     PORT=8490 bash deploy/public.sh  # another local port
#
# What judges get: the routing page at /, the four mission tiles, and the
# console replaying each mission's stream from out/ (one replay per viewer;
# the server is threaded). Nothing is written by viewers; the console only
# serves files and replays. The URL changes every launch and dies with this
# script, the laptop's network, or sleep -- caffeinate holds off sleep while
# the script runs. Logs: out/public/server.log, out/public/tunnel.log.
set -euo pipefail
cd "$(dirname "$0")/.."

PORT="${PORT:-8480}"

# Everything the pages read that git does not carry (README "Roadmap" and
# tracks/TRACK_F.md list why each exists).
NEEDED=(out/logistics.jsonl out/recon.jsonl out/casevac.jsonl out/combat.jsonl
        out/demo.jsonl out/clean.jsonl out/carryoff.jsonl data/terrain_usn8.npz)
missing=0
for f in "${NEEDED[@]}"; do
  [ -s "$f" ] || { echo "missing $f"; missing=1; }
done
if [ "$missing" = 1 ]; then
  echo "generate the streams first:  python -m backend.demo && python -m backend.missions"
  exit 1
fi
ls console/web/vendor/asset-env-*.hdr >/dev/null 2>&1 \
  || echo "WARN: environment assets not fetched (bash bootstrap.sh); missions fall back to the Earth set"
command -v cloudflared >/dev/null || { echo "cloudflared not installed:  brew install cloudflared"; exit 1; }
[ -x .venv/bin/python ] || { echo "no .venv:  bash bootstrap.sh"; exit 1; }
if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "port $PORT is already in use; pick another:  PORT=8490 bash deploy/public.sh"; exit 1
fi

mkdir -p out/public
: > out/public/tunnel.log

.venv/bin/python -m console.server --port "$PORT" > out/public/server.log 2>&1 &
SERVER=$!
cloudflared tunnel --url "http://127.0.0.1:$PORT" --no-autoupdate > out/public/tunnel.log 2>&1 &
TUNNEL=$!
caffeinate -i -w $$ &
trap 'kill $SERVER $TUNNEL 2>/dev/null; echo; echo "stopped"' EXIT INT TERM

URL=""
for _ in $(seq 1 60); do
  URL=$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' out/public/tunnel.log | head -1 || true)
  [ -n "$URL" ] && break
  kill -0 "$SERVER" 2>/dev/null || { echo "server exited:"; cat out/public/server.log; exit 1; }
  sleep 1
done
[ -n "$URL" ] || { echo "no tunnel URL after 60 s:"; tail -20 out/public/tunnel.log; exit 1; }

# The edge takes a few seconds to route a new quick tunnel.
for _ in $(seq 1 30); do
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$URL/missions" || true)
  [ "$code" = 200 ] && break
  sleep 1
done
echo "$URL" > out/public/url.txt
echo
echo "  local    http://localhost:$PORT"
echo "  public   $URL        (/missions -> HTTP $code)"
echo
echo "  send judges the public URL; Ctrl-C here ends it"
wait "$SERVER" "$TUNNEL"
