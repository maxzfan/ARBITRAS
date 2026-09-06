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
    # Ruling of 2026-09-05: what the walk rate MEANS depends on the domain.
    #   walk_mode "clock"    -> m/s of range offset applied uniformly (the
    #                           clock-domain attack: it corrupts time, and the
    #                           solver absorbs it into a constellation clock)
    #   walk_mode "position" -> m/s of POSITION displacement along `bearing_deg`;
    #                           per-SV range rates are its projections and are
    #                           each <= this figure.
    walk_off_mps: float              # see walk_mode for the domain
    onset: datetime                  # first epoch of the attack
    walk_mode: str = "clock"
    bearing_deg: float | None = None  # position mode: degrees CW from ENU north
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

    # Track F (tracks/TRACK_F_COMBAT.md, TRACK_F_RECON.md): an ABRUPT
    # position-domain offset applied from the first attack epoch, on top of
    # any walk: displacement = step + walk_off_mps * walked_s. 0.0 leaves every
    # existing scenario and truth log byte-identical. Position mode only.
    step_displacement_m: float = 0.0

    # Magnitudes with no figure in §7, expressed against the measured floor.
    # Approved by hand 2026-09-05 at these values, one epoch each.
    capture_jitter_sigma: float = 3.0   # x clean C/N0 sigma, during capture only
    liftoff_transient_sigma: float = 6.0  # x clean cmc sigma, at lift-off
    # Master switch for both, ruled 2026-09-05: transients ON in the demo
    # config, OFF in every sweep and measurement run. A one-epoch injected
    # spike is a legitimate part of the §7 signature and a contaminant in a
    # measurement -- if a reported number moves when it is switched off, the
    # detector was partly detecting our own artefact. Every reported figure
    # records which setting produced it.
    transients: bool = True

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

    def walked_s(self, t: datetime) -> float:
        """Seconds of walk-off elapsed at epoch time t."""
        st, dt = self.stage(t)
        if st == CLEAN:
            return 0.0
        return max(0.0, dt - self.capture_s - self.liftoff_delay_s)

    def range_offset_m(self, t: datetime) -> float:
        """Uniform range offset in metres (clock-domain scenarios).

        In position mode this is the common bias only: the walk is not a
        uniform range offset there, it is a displacement whose per-satellite
        projections inject() computes epoch by epoch."""
        st, _ = self.stage(t)
        if st == CLEAN:
            return 0.0
        if self.walk_mode == "position":
            return self.common_bias_m
        return self.common_bias_m + self.walk_off_mps * self.walked_s(t)

    def displacement_m(self, t: datetime) -> float:
        """Commanded horizontal displacement magnitude, metres (position mode):
        the abrupt step (from the first attack epoch) plus the walk."""
        if self.walk_mode != "position":
            return 0.0
        st, _ = self.stage(t)
        if st == CLEAN:
            return 0.0
        return self.step_displacement_m + self.walk_off_mps * self.walked_s(t)


# --- displacement geometry ----------------------------------------------------

# The eight swept bearings, 45 degrees apart from local ENU north. Fixed set,
# and deliberately not derived from the detector or from H: the injector must
# not be a function of the thing it is attacking.
SWEEP_BEARINGS_DEG = tuple(range(0, 360, 45))
EAST_BEARING_DEG = 90.0          # demo pin: cross-corridor east

# Demo pin for the code/carrier divergence rate, ruled by hand 2026-09-05 from
# the printed arithmetic at k = 2 sigma, t = 30 s: 2 * 0.204 / 30 = 0.0136 m/s.
# Chosen for demo legibility, not as a physical claim; the reported
# displacement bound is measured at carrier_rate_error = 0.
DEMO_CARRIER_RATE_ERROR = 0.0136  # m/s


def enu_basis(sta_ecef) -> np.ndarray:
    """Rows (east, north, up) as ECEF unit vectors at a station."""
    from ..detection.emit import ecef_to_lla
    lla = ecef_to_lla(*np.asarray(sta_ecef, dtype=float))
    lat, lon = np.radians(lla["lat"]), np.radians(lla["lon"])
    sl, cl, sp, cp = np.sin(lon), np.cos(lon), np.sin(lat), np.cos(lat)
    return np.array([[-sl, cl, 0.0],
                     [-sp * cl, -sp * sl, cp],
                     [cp * cl, cp * sl, sp]])


def bearing_unit_ecef(bearing_deg: float, sta_ecef) -> np.ndarray:
    """Unit ECEF vector for a horizontal bearing (degrees CW from ENU north).

    HORIZONTAL ONLY, by ruling: the up component is identically zero. VDOP is
    the weak axis, so an unconstrained sweep would find "up" and inflate the
    headline displacement with a direction no road-bound vehicle can be walked
    along."""
    e, n, _ = enu_basis(sta_ecef)
    b = np.radians(bearing_deg)
    return np.sin(b) * e + np.cos(b) * n


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
                         walk_mode="clock", common_bias_m=250.0,
                         # Carries the pre-ruling assumption forward: 2 sigma of
                         # measured clean CMC noise per 30 s epoch = 0.014 m/s.
                         # Stated, not derived. Pipeline validation only.
                         carrier_rate_error=0.014), **kw)


def CLOCK_CARRY_OFF(onset: datetime, carrier_rate_error: float,
                    target_svs="all_gps", **kw) -> Spoof:
    """§7 row 2, clock domain — a range offset applied uniformly to the
    captured set.

    Kept as its own named scenario by ruling, because it is a genuinely
    different attack from the position walk and its result is worth reporting:
    a uniform offset across one constellation is indistinguishable from that
    constellation's clock, so the least-squares absorbs all of it and the
    believed position never moves (measured: 0.0 m under a 300 m bias). It
    corrupts TIME, not position. That makes it the scenario the inter-system
    clock channels of feature 4 exist to catch, and a reminder that "the
    position looks fine" is not the same as "nothing is wrong".

    `walk_off_mps` here is m/s of uniform range drift, the §7 figure read in
    the range domain.
    """
    return replace(Spoof(name="clock_carry_off", power_db=2.0,
                         walk_mode="clock", walk_off_mps=1.0,
                         onset=onset, svs="G", target=target_svs,
                         carrier_rate_error=carrier_rate_error,
                         liftoff_delay_s=0.0), **kw)


def CARRY_OFF(onset: datetime, carrier_rate_error: float,
              bearing_deg: float = EAST_BEARING_DEG,
              target_svs="all_gps", **kw) -> Spoof:
    """§7 row 2, position domain — the primary demo (TRACK_A.md §2).

    Capture, then walk the receiver's believed POSITION along a fixed
    horizontal bearing. Per-satellite offset is `-e_sv . dp` for commanded
    displacement `dp`, which is the linearised range change a receiver
    genuinely displaced by dp would see; injecting it is what makes the
    solution move rather than the clock.

    Ruled parameters (2026-09-05):
      - **1 m/s is the POSITION displacement rate** along the bearing, not a
        range drift. Each satellite's range rate is `e_sv . v_hat` times that,
        so every per-SV rate is <= 1 m/s and the ones near the horizon
        perpendicular to the walk barely move at all.
      - **Horizontal only.** dp has no vertical component, by construction.
      - **Bearing is swept, never derived.** Eight bearings 45 degrees apart
        (SWEEP_BEARINGS_DEG); the demo pin is cross-corridor east, 90 degrees.
        The injector is not permitted to choose its direction from the
        detector's response or from H.

    `carrier_rate_error` still has no default -- that pin is separate and
    remains pending.
    """
    return replace(Spoof(name="carry_off", power_db=2.0,
                         walk_mode="position", walk_off_mps=1.0,
                         bearing_deg=bearing_deg,
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
                         walk_mode="clock",
                         onset=onset, svs="G", common_bias_m=300.0,
                         capture_jitter_sigma=1.0,
                         carrier_rate_error=0.0,
                         liftoff_transient_sigma=0.0), **kw)


def SIMPLISTIC_POSITION(onset: datetime, bearing_deg: float = EAST_BEARING_DEG,
                        target_svs="all_gps", step_m: float = 250.0, **kw) -> Spoof:
    """§7 row 1 read in the POSITION domain (tracks/TRACK_F_COMBAT.md).

    A crude single-transmitter spoofer captures the whole GPS set at +15 dB
    (§7 crude midpoint, as SIMPLISTIC) and presents ranges consistent with a
    receiver `step_m` metres along `bearing_deg`, abruptly, from the first
    attack epoch: per-SV offsets `-e_sv . dp`, computed by inject(). 250 m is
    chosen to be unmistakable at a glance and is not derived from anything
    (stated, not derived), exactly as SIMPLISTIC's common bias. The
    code/carrier mismatch rate is carried from SIMPLISTIC (stated). Measured
    on the real day (TRACK_F_COMBAT.md): the G+E joint fix lands 98.5 m out,
    Galileo anchoring the rest; a step on ALL 45 tracked SVs would land the
    full 250 m and is invisible to three features (README limitation).
    """
    return replace(Spoof(name="simplistic_position", power_db=15.0, walk_off_mps=0.0,
                         onset=onset, walk_mode="position", bearing_deg=bearing_deg,
                         svs="G", target=target_svs, step_displacement_m=step_m,
                         carrier_rate_error=0.014, liftoff_delay_s=0.0), **kw)


def REPEATER_OFFSET(onset: datetime, bearing_deg: float = EAST_BEARING_DEG,
                    standoff_m: float = 300.0, **kw) -> Spoof:
    """Meaconing from a repeater at a standoff (tracks/TRACK_F_RECON.md).

    A repeater re-radiates every GPS signal from its OWN antenna, so the
    receiver solves toward the repeater's position: a position-domain step
    of `standoff_m` along `bearing_deg` on all GPS, plus the common path delay
    of the same length (1 us per 300 m) and the repeater's elevated power.
    No walk, no code/carrier mismatch (the rebroadcast keeps them coherent).
    300 m and 8 dB are MEACONING's stated figures; both are sweep parameters.
    Measured (TRACK_F_RECON.md): the G+E fix lands 118-139 m out at 300 m;
    the constellation-level rule then drops exactly GPS.
    """
    return replace(Spoof(name="repeater_offset", power_db=8.0, walk_off_mps=0.0,
                         onset=onset, walk_mode="position", bearing_deg=bearing_deg,
                         svs="G", target="all_gps", step_displacement_m=standoff_m,
                         common_bias_m=standoff_m, capture_jitter_sigma=1.0,
                         carrier_rate_error=0.0, liftoff_transient_sigma=0.0), **kw)


SCENARIOS = {"simplistic": SIMPLISTIC, "carry_off": CARRY_OFF,
             "clock_carry_off": CLOCK_CARRY_OFF, "meaconing": MEACONING,
             "simplistic_position": SIMPLISTIC_POSITION,
             "repeater_offset": REPEATER_OFFSET}

# Scenarios whose walk-off rate is a position rate rather than a range rate.
POSITION_DOMAIN = ("carry_off", "simplistic_position", "repeater_offset")
