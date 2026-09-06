"""Demo configuration. Thresholds are REQUIRED and have no defaults.

The demo settings are fixed by ruling (2026-09-05):

    transients            ON   (demo only; every measurement run forces OFF)
    carrier_rate_error    DEMO_CARRIER_RATE_ERROR = 0.0136 m/s, the pin
    combine mode          weighted_sum
    elevation mask        5 deg, the ARAIM convention

**Thresholds are deliberately absent.** Beats 2 and 7 need arbitrated states,
states need confidence thresholds, and no threshold exists for the current
detector: the values in `console/arbiter/states.py` were measured against the
distribution produced by the previous feature 2, before the 5 degree mask and
before the cross-constellation calibration fix, and are stale by construction.

So this module refuses to guess. `require_thresholds()` raises with the name of
the first missing value. Supply them explicitly:

    python -m backend.beats.beat2 --nominal 0.70 --degraded 0.60 \\
                                  --restricted 0.55

or in the environment (HOLDFAST_NOMINAL, HOLDFAST_DEGRADED,
HOLDFAST_RESTRICTED). Whatever is supplied is stamped into every emitted
decision through the provenance string, so a number invented at the command
line cannot reach a rendered beat unlabelled.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime

from ..injector import DEMO_CARRIER_RATE_ERROR
from ..rinex.solve import EL_MASK_DEG

OBS = "data/USN800USA_R_20262320000_01D_30S_MO.crx.gz"
ONSET = datetime(2026, 8, 20, 12, 30)
WINDOW = (datetime(2026, 8, 20, 12, 0), datetime(2026, 8, 20, 13, 0))
OUT = "out/beats"

# Ruled demo settings, not tunable from the command line.
TRANSIENTS = True
CARRIER_RATE_ERROR = DEMO_CARRIER_RATE_ERROR
COMBINE_MODE = "weighted_sum"
BEARING_DEG = 90.0                    # cross-corridor east, the demo pin

THRESHOLD_NAMES = ("nominal", "degraded", "restricted")
ENV = {n: f"HOLDFAST_{n.upper()}" for n in THRESHOLD_NAMES}


class MissingThreshold(RuntimeError):
    """Raised when a beat needs a threshold nobody has set."""


@dataclass
class Thresholds:
    nominal: float
    degraded: float
    restricted: float
    source: str = "unset"

    def __post_init__(self):
        if not (self.nominal > self.degraded > self.restricted):
            raise ValueError(
                f"thresholds must be strictly decreasing, got nominal="
                f"{self.nominal} degraded={self.degraded} "
                f"restricted={self.restricted}")

    @property
    def provenance(self) -> str:
        return (f"NOMINAL {self.nominal:.4g} / DEGRADED {self.degraded:.4g} / "
                f"RESTRICTED {self.restricted:.4g}  [{self.source}]")


def require_thresholds(nominal=None, degraded=None, restricted=None,
                       source: str | None = None) -> Thresholds:
    """Resolve thresholds from arguments, then the environment. No defaults.

    Raises MissingThreshold naming the first value that is not set anywhere.
    """
    given = {"nominal": nominal, "degraded": degraded, "restricted": restricted}
    resolved, origins = {}, []
    for name in THRESHOLD_NAMES:
        if given[name] is not None:
            resolved[name] = float(given[name])
            origins.append("cli")
            continue
        env = os.environ.get(ENV[name])
        if env is not None:
            resolved[name] = float(env)
            origins.append("env")
            continue
        raise MissingThreshold(
            f"missing threshold: {name!r}.\n"
            f"  No default exists. The values in console/arbiter/states.py "
            f"were measured against the superseded detector (pre-mask, "
            f"pre-post-fit-residual) and are not valid for this pipeline.\n"
            f"  Supply it with --{name} or {ENV[name]}=<value>, or set the "
            f"threshold in the 21:00 session.")
    src = source or ("+".join(sorted(set(origins))) + " (operator-supplied, "
                     "NOT measured for this detector)")
    return Thresholds(source=src, **resolved)


def apply_thresholds(t: Thresholds) -> None:
    """Push resolved thresholds into the console's single swap point.

    `console/arbiter/states.py` owns the confidence-to-state mechanism and
    reads module-level constants; this supplies the values and overwrites the
    provenance string so the stale measured one cannot be displayed alongside
    numbers it did not produce.
    """
    from console.arbiter import states

    states.THRESHOLDS[states.TrustState.NOMINAL] = t.nominal
    states.THRESHOLDS[states.TrustState.DEGRADED] = t.degraded
    states.THRESHOLDS[states.TrustState.RESTRICTED] = t.restricted
    states.THRESHOLD_PROVENANCE = t.provenance


def resolve_or_exit(args):
    """require_thresholds, but exiting cleanly with the message.

    An operator who forgot a threshold should get one legible line, not a
    traceback -- the point of failing loudly is that the reason is readable.
    """
    import sys
    try:
        return require_thresholds(args.nominal, args.degraded, args.restricted)
    except MissingThreshold as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        raise SystemExit(2)


def add_threshold_args(ap) -> None:
    for name in THRESHOLD_NAMES:
        ap.add_argument(f"--{name}", type=float, default=None,
                        help=f"{name.upper()} confidence threshold. No "
                             f"default; see backend.beats.config.")
