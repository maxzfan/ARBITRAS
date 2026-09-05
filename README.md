# HOLDFAST

Spoof detection is solved. What happens in the ninety seconds after detection
is not.

HOLDFAST sits between a GNSS receiver and an autonomy stack and continuously
scores positional trust, then degrades the vehicle's authority in stages —
full autonomy, coast on inertial, finish the leg, hold position — rather than
making one binary trust decision.

**Signal quality is not provenance.**

Built at DNHacks, 5-6 September 2026.

## Run it
    bash bootstrap.sh          # python 3.12 venv + libraries + data (~10 min)
    source .venv/bin/activate

## Read first
- `CLAUDE.md` — working agreement and hard constraints
- `docs/design.md` — the authoritative technical document
- `tracks/TRACK_{A,B,C}.md` — per-person work packets

Full README with results, thresholds, prior art and limitations lands Sunday
(design.md §15).
