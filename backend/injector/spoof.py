"""Spoofing injector — measurement domain only.

The attack physics are specified by hand in design.md §7 and reproduced in the
table below. Nothing here is a magnitude this file invented; every number is
either quoted from §7 or expressed as a multiple of a sigma measured on the
clean day (backend/rinex/noise.py).

    §7 quantity                         value            used as
    power advantage, crude              10-20 dB         SIMPLISTIC.power_db
    power advantage, sophisticated      1-3 dB           CARRY_OFF.power_db
    onset -> tracking loops locked      ~10 s            capture_s
    walk-off rate                       ~1 m/s range     walk_off_mps

Two modelling decisions, both derived rather than assumed:

**The C/N0 change is a step, not a spike.** §7 stage 2 says the power metric
"drops back as tracking loops lock". §6a.1 says the C/N0 *feature* "goes quiet
~10 s after capture once elevated power becomes the new baseline". The fade is
a property of the detector — its rolling-mean baseline absorbs the new level —
not of the signal. So the injector applies a persistent step of `power_db` and
lets the detector produce the spike-then-fade on its own. This is why no decay
constant appears here: there is no honest way to pick one, and none is needed.

**Sampling.** Capture takes ~10 s and the file is sampled at 30 s. Stages 1
and 2 of the §7 signature therefore occupy at most one epoch of file time.
That is a real limit of the measurement domain, not a defect of the injector,
and it is the concrete reason the C/N0 feature cannot carry detection alone:
its whole window is one sample. Features 2 and 3 do the work after that.

Everything the injector does is recorded in a per-epoch truth log, so the
measurement layer (§10) can ask what the attacker actually achieved rather
than inferring it from the detector's output.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from ..rinex.loader import Epoch
from ..rinex.noise import NoiseFloor

CLEAN, CAPTURE, LOCKED, WALK = "CLEAN", "CAPTURE", "LOCKED", "WALK"


@dataclass
class Spoof:
    """One attack. See SIMPLISTIC / CARRY_OFF / MEACONING for the §7 scenarios."""

    name: str
    power_db: float                  # §7 power advantage, dB
    walk_off_mps: float              # §7 equivalent range drift, m/s
    onset: datetime                  # first epoch of the attack
    svs: object = "all"              # "all", a constellation letter, or a list
    # Target selection, resolved ONCE at the capture epoch and held fixed for
    # the run -- a real spoofer commits to a channel set; it does not re-plan
    # per epoch. None falls back to `svs` (live per-epoch selection, kept for
    # simplistic's "all SVs at once" and meaconing's "whatever the repeater
    # hears", where live selection is the physically right reading).
    # Accepts an explicit SV list, "all_gps", or a rule callable(epoch)->list.
    target: object = None
    capture_s: float = 10.0          # §7 stage 2: ~10 s to loop lock
    liftoff_delay_s: float = 0.0     # §7 stage 3 begins after lock
    duration_s: float | None = None  # None = runs to the end of the replay
    common_bias_m: float = 0.0       # meaconing: fixed rebroadcast path delay

    # Code/carrier divergence during walk-off, ruled by hand 2026-09-05:
    # the spoofer commands a code delay and applies carrier Doppler from its
    # own range-rate model; a mismatch between those two models diverges
    # LINEARLY. Divergence at time t after lift-off is carrier_rate_error * t.
    # 0.0 is a supported case -- a fully carrier-coherent spoofer: code and
    # carrier stay coherent through the entire walk-off and features 2 and 3
    # see nothing (including no lift-off CMC transient; a coherent spoofer
    # produces no code/carrier distortion to spike).
    carrier_rate_error: float = 0.0  # m/s of code-minus-carrier divergence

    # Magnitudes with no figure in §7, expressed against the measured floor.
    capture_jitter_sigma: float = 3.0   # x clean C/N0 sigma, during capture only
    liftoff_transient_sigma: float = 6.0  # x clean cmc sigma, at lift-off

    seed: int = 20260820

    def resolve_target(self, epoch) -> list:
        """Evaluate the target selection at the capture epoch. inject() calls
        this exactly once per run and freezes the result."""
        if callable(self.target):
            return list(self.target(epoch))
        if self.target == "all_gps":
            return all_gps(epoch)
        return list(self.target)

    def select(self, df: pd.DataFrame) -> np.ndarray:
        """Boolean mask over a per-epoch frame: which satellites are spoofed."""
        if self.svs == "all":
            return np.ones(len(df), dtype=bool)
        if isinstance(self.svs, str) and len(self.svs) == 1:
            return (df["system"] == self.svs).to_numpy()
        return df.index.isin(list(self.svs))

    def stage(self, t: datetime) -> tuple[str, float]:
        """(stage, seconds since onset) for an epoch time."""
        dt = (t - self.onset).total_seconds()
        # Half-open [onset, onset+duration): dt == duration_s is the first
        # CLEAN epoch after the attack, not its last attack epoch.
        if dt < 0 or (self.duration_s is not None and dt >= self.duration_s):
            return CLEAN, dt
        if dt < self.capture_s:
            return CAPTURE, dt
        if dt < self.capture_s + self.liftoff_delay_s:
            return LOCKED, dt
        return WALK, dt

    def range_offset_m(self, t: datetime) -> float:
        """Range error the spoofer has walked the receiver out to, in metres."""
        st, dt = self.stage(t)
        if st == CLEAN:
            return 0.0
        walked = max(0.0, dt - self.capture_s - self.liftoff_delay_s)
        return self.common_bias_m + self.walk_off_mps * walked


# --- target selection rules ---------------------------------------------------

def all_gps(epoch) -> list:
    """Every GPS satellite tracked at the capture epoch."""
    return [sv for sv in epoch.df.index if sv.startswith("G")]


def top_n_by_elevation(n: int, sta_ecef=None):
    """The n tracked GPS satellites highest in the sky at the capture epoch.

    Elevation is computed from broadcast ephemeris at the capture epoch and the
    resulting set is held fixed for the run (inject() freezes it). The high
    half of the sky is what a real single-transmitter spoofer takes first:
    strongest signals, longest passes, most leverage on the solution.
    """
    def rule(epoch) -> list:
        from ..detection.emit import USN8_ECEF
        from ..rinex import ephemeris
        gps = [sv for sv in epoch.df.index if sv.startswith("G")]
        el = ephemeris.elevations_at(epoch.time, gps,
                                     sta_ecef or USN8_ECEF)
        return list(el.sort_values(ascending=False).index[:n])
    rule.__name__ = f"top_{n}_by_elevation"
    return rule


# --- the three §7 scenarios -------------------------------------------------
# Power figures are the midpoints of the §7 ranges; the ranges themselves are
# what the §10 sweep varies. Nothing outside a §7 range is a default here.

def SIMPLISTIC(onset: datetime, **kw) -> Spoof:
    """§7 row 1 — crude. Abrupt offset, all SVs at once. Pipeline validation.

    §7 fixes the power advantage (10-20 dB) and the behaviour (abrupt, all SVs)
    but not the size of the offset, because this scenario exists to prove the
    pipeline carries an attack end to end, not to be realistic. 250 m is chosen
    to be unmistakable at a glance and is not derived from anything.
    """
    return replace(Spoof(name="simplistic", power_db=15.0, walk_off_mps=0.0,
                         onset=onset, svs="all", liftoff_delay_s=0.0,
                         common_bias_m=250.0,
                         # Carries the pre-ruling assumption forward: 2 sigma of
                         # measured clean CMC noise per 30 s epoch = 0.014 m/s.
                         # Stated, not derived. Pipeline validation only.
                         carrier_rate_error=0.014), **kw)


def CARRY_OFF(onset: datetime, carrier_rate_error: float,
              target_svs="all_gps", **kw) -> Spoof:
    """§7 row 2 — sophisticated. Capture, then gradual walk-off on an SV subset.

    The primary demo (TRACK_A.md §2). 2 dB is the midpoint of the §7 1-3 dB
    range; against the measured C/N0 floor that is a few sigma, which is the
    whole design point of a low-power spoofer.

    `carrier_rate_error` has NO default: the demo pin is picked by hand from
    the printed arithmetic (ruling of 2026-09-05) and has not been given yet.
    Tests pass an explicit test value; the demo config carries none until the
    number arrives.

    `target_svs` names the captured subset: an explicit SV list, "all_gps"
    (the demo default), or `top_n_by_elevation(n)`. Whatever the rule, it is
    evaluated once at the capture epoch and held fixed. The walk-off rate is
    specified at ~1 m/s (§7) and is not a target parameter.
    """
    return replace(Spoof(name="carry_off", power_db=2.0, walk_off_mps=1.0,
                         onset=onset, svs="G", target=target_svs,
                         carrier_rate_error=carrier_rate_error,
                         liftoff_delay_s=0.0), **kw)


def MEACONING(onset: datetime, **kw) -> Spoof:
    """§7 row 3 — rebroadcast. Common bias across one constellation.

    A meaconer re-radiates the authentic signal, so there is no walk-off and no
    code/carrier mismatch: the relationship between code and carrier survives
    the rebroadcast intact. What it adds is a single path delay common to every
    satellite it repeats, and the elevated power of the repeater. That is why
    this scenario is the one that exercises cross-constellation disagreement
    (§6a.4) and nothing else — it is invisible to code-minus-carrier by
    construction, and its correct response is distrusting one whole
    constellation (CLAUDE.md, geometry note).

    §7 says "rebroadcast" and gives no figure for either the repeater's power
    advantage or its path delay. 300 m is one microsecond of extra path, the
    round order for a repeater with a real antenna separation; 8 dB is stated,
    not derived. Both are swept in §10.
    """
    return replace(Spoof(name="meaconing", power_db=8.0, walk_off_mps=0.0,
                         onset=onset, svs="G", common_bias_m=300.0,
                         capture_jitter_sigma=1.0,
                         carrier_rate_error=0.0,
                         liftoff_transient_sigma=0.0), **kw)


SCENARIOS = {"simplistic": SIMPLISTIC, "carry_off": CARRY_OFF,
             "meaconing": MEACONING}
