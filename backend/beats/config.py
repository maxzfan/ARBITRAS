"""Demo configuration. Thresholds are REQUIRED and have no defaults.

The demo settings are fixed by ruling (2026-09-05):

    transients            ON   (demo only; every measurement run forces OFF)
    carrier_rate_error    DEMO_CARRIER_RATE_ERROR = 0.0136 m/s, the pin
    combine mode          weighted_sum
    elevation mask        5 deg, the ARAIM convention

**Thresholds were ruled by hand on 2026-09-06** from the distributions printed
by `backend.beats.thresholds`, and are `RULED` below. They are NOT the values in
`console/arbiter/states.py`, which were fitted to the distribution produced by
the previous feature 2, before the 5 degree mask and before the
cross-constellation calibration fix, and are stale by construction.

The three have different provenance and the string says so, because two of them
are measured on this detector and one pair is carried:

- **NOMINAL 0.8247** is the measured clean p1 of THIS detector.
- **DEGRADED 0.50 / RESTRICTED 0.25** are carried from design.md §8. Their
  justification is not a fit: both sit below the measured clean minimum of
  0.7500, so neither can contribute a false surrender on this day. They
  partition the attack distribution, not the clean one.

Explicit values still override, by argument or environment (HOLDFAST_NOMINAL,
HOLDFAST_DEGRADED, HOLDFAST_RESTRICTED), and an override is stamped as
operator-supplied so a number invented at the command line cannot reach a
rendered beat wearing the ruled provenance.
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
    """Raised when a beat needs a threshold nobody has set.

    Retained although `RULED` now exists: an operator can still disable the
    ruled values (`HOLDFAST_NOMINAL=` empty is not the same as unset, but
    `require_thresholds(..., allow_ruled=False)` is), and the failure path is
    what keeps a future detector change from silently inheriting these.
    """


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


# Ruled by hand 2026-09-06 from backend.beats.thresholds. Split provenance:
# NOMINAL is measured on this detector; DEGRADED and RESTRICTED are carried.
RULED_PROVENANCE = (
    "ruled 2026-09-06: NOMINAL 0.8247 = measured clean p1 of THIS detector "
    "(5 deg mask, post-fit-residual feature 2, corrected cross calibration, "
    "weighted_sum, beta 0.5); DEGRADED 0.50 and RESTRICTED 0.25 carried from "
    "design.md §8, justified as sitting below the measured clean minimum "
    "0.7500 and therefore carrying no false-surrender cost"
)
RULED = Thresholds(nominal=0.8247, degraded=0.50, restricted=0.25,
                   source=RULED_PROVENANCE)


def require_thresholds(nominal=None, degraded=None, restricted=None,
                       source: str | None = None,
                       allow_ruled: bool = True) -> Thresholds:
    """Resolve thresholds: arguments, then environment, then the ruled values.

    With `allow_ruled=False` there is no fallback and a missing value raises
    MissingThreshold naming it -- the path that stopped a superseded threshold
    being inherited, kept so a future detector change hits it again.
    """
    given = {"nominal": nominal, "degraded": degraded, "restricted": restricted}
    resolved, origins = {}, []
    for name in THRESHOLD_NAMES:
        if given[name] is not None:
            resolved[name] = float(given[name])
            origins.append("cli")
            continue
        env = os.environ.get(ENV[name])
        if env:
            resolved[name] = float(env)
            origins.append("env")
            continue
        if allow_ruled:
            resolved[name] = getattr(RULED, name)
            origins.append("ruled")
            continue
        raise MissingThreshold(
            f"missing threshold: {name!r}.\n"
            f"  No default is permitted here. The values in "
            f"console/arbiter/states.py were measured against the superseded "
            f"detector (pre-mask, pre-post-fit-residual).\n"
            f"  Supply it with --{name} or {ENV[name]}=<value>.")
    kinds = set(origins)
    if kinds == {"ruled"}:
        src = source or RULED_PROVENANCE
    else:
        src = source or (
            "+".join(sorted(kinds)) + " — OVERRIDDEN, operator-supplied, "
            "not the ruled values")
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
