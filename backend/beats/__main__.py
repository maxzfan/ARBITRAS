"""Run every protected beat from one command.

    python -m backend.beats --nominal N --degraded D --restricted R

Beat 1 needs no thresholds and always runs. Beats 2 and 7 arbitrate states and
are BLOCKED until thresholds are supplied; this reports which beats ran and
which are blocked rather than substituting a value to make the set complete.
"""
from __future__ import annotations

import argparse

from . import beat1, beat2, beat7, config as cfg


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=cfg.OUT)
    cfg.add_threshold_args(ap)
    args = ap.parse_args(argv)

    thr_args = [a for name in cfg.THRESHOLD_NAMES
                for a in (f"--{name}", str(getattr(args, name)))
                if getattr(args, name) is not None]

    beat1.main(["--out", args.out])
    print()

    try:
        cfg.require_thresholds(args.nominal, args.degraded, args.restricted)
    except cfg.MissingThreshold as exc:
        print(f"BEATS 2 and 7 — BLOCKED: {exc}")
        raise SystemExit(2)

    for mod in (beat2, beat7):
        mod.main(["--out", args.out] + thr_args)
        print()


if __name__ == "__main__":
    main()
