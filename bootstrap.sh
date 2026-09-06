#!/usr/bin/env bash
# Run once per machine, from the repo root:  bash bootstrap.sh
# Idempotent. Takes ~10 min on a cold machine, mostly unattended.
set -euo pipefail

echo "== 1/3  Python 3.12 (gnss-lib-py caps at <3.13; 3.13/3.14 will NOT work) =="
PY=$(command -v python3.12 || echo /opt/homebrew/bin/python3.12)
if [ ! -x "$PY" ]; then
  echo "installing python@3.12 via homebrew..."
  brew install python@3.12
  PY=/opt/homebrew/bin/python3.12
fi
"$PY" --version

echo "== 2/3  venv + libraries =="
[ -d .venv ] || "$PY" -m venv .venv
source .venv/bin/activate
pip install -q -U pip wheel setuptools
pip install -q "gnss-lib-py==1.0.4" "georinex==1.16.1" "cryptography"
python -c "import georinex, gnss_lib_py, sys; print('python', sys.version.split()[0]); print('georinex ok'); print('gnss_lib_py ok')"

echo "== 3/3  data (BKG mirror — no login needed; CDDIS requires Earthdata auth) =="
mkdir -p data && cd data
OBS=https://igs.bkg.bund.de/root_ftp/IGS/obs/2026/232
BRDC=https://igs.bkg.bund.de/root_ftp/IGS/BRDC/2026/232
for F in USN800USA_R_20262320000_01D_30S_MO.crx.gz \
         STFU00USA_S_20262320000_01D_30S_MO.crx.gz \
         GODE00USA_R_20262320000_01D_30S_MO.crx.gz \
         ALGO00CAN_R_20262320000_01D_30S_MO.crx.gz; do
  [ -s "$F" ] || curl -sS --max-time 300 -L -O "$OBS/$F"
  echo "  $F  $(du -h "$F" | cut -f1)"
done
N=BRDC00IGS_R_20262320000_01D_MN.rnx.gz
[ -s "$N" ] || curl -sS --max-time 300 -L -O "$BRDC/$N"
echo "  $N  $(du -h "$N" | cut -f1)"
cd ..

echo
echo "DONE. Every new terminal, including any Claude Code spawns:"
echo "    cd $(pwd) && source .venv/bin/activate"
echo "NOTE: there is no conda on this setup. design.md §13 says 'conda activate dnhacks' — that is stale."

echo "== Track F environment assets (console/web/env/ASSETS.txt; Poly Haven CC0, no login; gitignored) =="
while IFS=$'\t' read -r url name; do
  [ -z "$url" ] && continue
  case "$url" in \#*) continue;; esac
  dest="console/web/vendor/$name"
  if [ ! -s "$dest" ]; then
    echo "  fetch $name"; curl -sSL -o "$dest" "$url" || echo "  WARN: $name failed; the console falls back to the vendored Earth set"
  fi
done < console/web/env/ASSETS.txt
