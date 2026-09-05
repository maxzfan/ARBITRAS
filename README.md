# Verifiable Position for Autonomous Systems

A trust layer between a GNSS receiver and an autonomy stack, so a vehicle can
tell when it is being lied to about where it is and give up authority before
acting on bad data.

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
