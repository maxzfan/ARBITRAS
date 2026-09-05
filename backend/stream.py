"""Live contract stream — what Track B's console actually tails.

Replays a precomputed §5 JSONL file (from `python -m backend.replay`) into a
destination file at demo rate, appending one record at a time with a flush per
line, so `tail -f` / the console's follower sees epochs arrive live.

    python -m backend.replay                             # precompute batch files
    python -m backend.stream --source out/clean.jsonl    # stream to out/live.jsonl

Design points, all from §5:

- **Rate.** File time is 30 s/epoch; the demo replays at 10-20 epochs/sec.
  Default 15. `--rate` changes it live-side only — record timestamps are file
  time and are never rewritten.
- **Hard reset under five seconds (§11a).** A fresh invocation truncates the
  destination and starts over; there is no state anywhere else. Ctrl-C, rerun,
  and the console watches the file restart.
- **Silence is not consent.** When the source is exhausted the streamer just
  stops appending and exits; it does not write any end-of-stream marker. A
  console that freezes on a quiet file instead of stepping down on timeout is
  wrong by §5, and a marker would hide that bug. `--stall N` deliberately goes
  silent for N seconds mid-stream (once, halfway) so B can test the timeout
  path without killing the process.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path


def stream(source: Path, dest: Path, rate: float = 15.0,
           start: int = 0, count: int | None = None,
           stall_s: float = 0.0, echo: bool = False) -> int:
    lines = Path(source).read_text().splitlines()
    lines = lines[start:start + count if count else None]
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    stall_at = len(lines) // 2 if stall_s > 0 else -1
    n = 0
    with dest.open("w") as fh:                    # truncate: the hard reset
        for i, line in enumerate(lines):
            if i == stall_at:
                time.sleep(stall_s)               # console must step down here
            fh.write(line + "\n")
            fh.flush()
            n += 1
            if echo:
                sys.stdout.write(line[:100] + "\n")
            time.sleep(1.0 / rate)
    return n


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", default="out/clean.jsonl")
    ap.add_argument("--dest", default="out/live.jsonl")
    ap.add_argument("--rate", type=float, default=15.0,
                    help="epochs per second (§5 demo rate 10-20)")
    ap.add_argument("--start", type=int, default=0,
                    help="first epoch index of the slice to stream")
    ap.add_argument("--count", type=int, default=None,
                    help="number of epochs to stream (default: to the end)")
    ap.add_argument("--stall", type=float, default=0.0,
                    help="go silent this many seconds at the halfway point, "
                         "to exercise the console's stale-epoch timeout")
    ap.add_argument("--echo", action="store_true")
    args = ap.parse_args(argv)
    n = stream(args.source, args.dest, rate=args.rate, start=args.start,
               count=args.count, stall_s=args.stall, echo=args.echo)
    print(f"streamed {n} epochs -> {args.dest} at {args.rate:g}/s, then went "
          f"silent (no end marker: silence is the console's problem, per §5)")


if __name__ == "__main__":
    main()
