"""Cross-constellation disagreement — feature 4 of design.md §6a.

Per epoch, positions are solved for each constellation alone and all-in-view
(backend/rinex/solve.py). Two kinds of channel are scored:

- **Position disagreement**: the pairwise distances between the
  per-constellation solutions, one channel per pair (a max over pairs is not a
  continuous physical quantity and cannot be baselined). Fires when an
  attacker moves one constellation's solution and not the others'.
- **Inter-system clock offsets**: dt_G - dt_E, dt_G - dt_C, dt_E - dt_C from
  the all-in-view solution. Not decoration — this is the channel that sees the
  §7 meaconing scenario AND the uniform carry-off: a range offset applied
  equally to every satellite of one constellation is indistinguishable from
  that constellation's clock and never moves a position at all. A
  position-only comparison is blind to both. Measured: the injected 300 m
  meaconing bias lands on the G-E clock channel at its full 300 m and on the
  position channel not at all.

## Scoring, and the three failure modes it went through

The channels are not stationary. Three designs were measured before this one:

1. **Global clean-day centre** — solutions drift metres over hours as geometry
   turns; clean idled at 0.655.
2. **Trailing baseline, gated update** (absorb an epoch only if |z| is under
   the gate) — sustained attacks never fade, but when a satellite rises or
   sets the solution legitimately jumps, the gate refuses the new level, and
   the CLEAN day latched at 1.0 for 10% of its epochs.
3. **Gate + relearn on SV-set change** — clean healthy, but an 11-hour attack
   gets adopted as the new baseline at the first natural rise/set inside it;
   full-day meaconing detection collapsed to d' 0.09.

The resolution is that a rise/set step is **measurable at the instant it
happens**, so the baseline can be *shifted* by the measured step instead of
either refusing it or relearning around it. When constellation S's tracked set
changes between epochs k-1 and k, both epochs are re-solved with S restricted
to the COMMON subset:

    step = [ch(k-1, common) - ch(k-1, old set)]   departures, measured at k-1
         + [ch(k, new set)  - ch(k, common)]      arrivals,   measured at k

and the channel's running offset absorbs the step. Both terms difference two
solves of the SAME instant's measurements, so a bias the attacker has already
placed sits in both solves and cancels; a bias STEP the attacker applies at
epoch k is in neither term and lands in the scored residual. The gate never
opens: sustained attacks stay detected indefinitely, through every rise and
set.

One more measured failure mode shaped the gate itself. Gating on the LEVEL
(|z| under a limit) latched on clean data anyway: the inter-system clock
channels drift ~12 m over the day at centimetres per epoch, the drift
accumulates past any level gate, and the frozen window never recovers — 588 of
2,880 clean epochs pinned. Drift and attack differ not in level but in the
PER-EPOCH INCREMENT: centimetres for drift, 30-300 m for the §7 attacks. So
absorption is gated on the increment against the last accepted value (clean
p99.9 of the compensated per-epoch step, measured per channel), while the
score stays a level z against the trailing median. Slow drift is followed;
any step or walk beyond the clean increment tail is refused forever. When the common subset is too small to solve (rare), that channel's
history is cleared and relearned, and that is the stated corner: a static bias
whose onset lands exactly inside such a relearn window is absorbed — features
1-3 own the C/N0 step it still makes.

Residual risk, stated: an attacker moving slowly enough to stay under the gate
(~gate x sigma, metres per epoch) poisons the baseline. At the §7 walk-off of
1 m/s the per-epoch motion is 30 m, an order of magnitude over the gate.

Known blind spot, stated rather than discovered: an attack consistent across
ALL constellations (the simplistic scenario's common bias) shifts every clock
together and no channel moves. That is what features 1-3 are for.
"""
from __future__ import annotations

import hashlib
import pickle
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from ..rinex.solve import solve, solve_per_constellation

CACHE = Path(".cache/rinex")
PAIRS = (("G", "E"), ("G", "C"), ("E", "C"))
MIN_SUBSET = 4                    # a constellation solution needs 3+1 unknowns


def channels(sols: dict) -> dict:
    """Raw channel values (metres) from one epoch's solutions."""
    out = {}
    clk = sols.get("all", {}).get("clock_m", {})
    for a, b in PAIRS:
        if a in sols and b in sols:
            out[f"pos_{a}{b}_m"] = float(
                np.linalg.norm(sols[a]["pos"] - sols[b]["pos"]))
        if a in clk and b in clk:
            out[f"clk_{a}{b}_m"] = float(clk[a] - clk[b])
    return out


def sv_sets(sols: dict) -> dict:
    """Which satellites stood behind each constellation's solution."""
    return {sysc: frozenset(sols[sysc]["svs_used"])
            for sysc in "GEC" if sysc in sols}


def _touched(name: str) -> set:
    """The constellations a channel depends on ("pos_GE_m" -> {G, E})."""
    return set(name[4:6])


class ChannelExtractor:
    """Stateful, causal: emits rise/set-compensated channel values.

    extract(epoch, sols) -> (values, resets): `values` are the channels in a
    continuous frame (raw minus the accumulated geometry offset); `resets`
    names channels whose continuity could not be measured and whose baseline
    must be relearned.
    """

    def __init__(self, nav):
        self.nav = nav
        self.reset()

    def reset(self) -> None:
        self._off = defaultdict(float)
        self._sets = {}
        self._prev = None            # (epoch, sols, raw)

    def _restricted(self, epoch, sols, restrict: dict) -> dict:
        """Channels of one epoch with some constellations cut to a subset."""
        keep = []
        for sv in epoch.df.index:
            sysc = sv[0]
            if sysc not in "GEC":
                continue
            if sysc in restrict and sv not in restrict[sysc]:
                continue
            keep.append(sv)
        sub = type(epoch)(time=epoch.time, df=epoch.df.loc[keep])
        return channels(solve_per_constellation(sub, self.nav))

    def extract(self, epoch, sols: dict | None = None):
        sols = sols or solve_per_constellation(epoch, self.nav)
        raw = channels(sols)
        sets = sv_sets(sols)
        changed = {s for s in sets
                   if s in self._sets and sets[s] != self._sets[s]}
        resets = []
        if changed and self._prev is not None:
            common = {s: self._sets[s] & sets[s] for s in changed}
            solvable = all(len(v) >= MIN_SUBSET for v in common.values())
            prev_epoch, prev_sols, prev_raw = self._prev
            if solvable:
                dep = self._restricted(prev_epoch, prev_sols, common)
                arr = self._restricted(epoch, sols, common)
            for name in raw:
                if not (_touched(name) & changed):
                    continue
                if (solvable and name in dep and name in arr
                        and name in prev_raw):
                    self._off[name] += ((dep[name] - prev_raw[name])
                                        + (raw[name] - arr[name]))
                else:
                    resets.append(name)
                    self._off[name] = 0.0
        self._sets = sets
        self._prev = (epoch, sols, raw)
        return {n: v - self._off[n] for n, v in raw.items()}, resets


@dataclass
class CrossCal:
    """Per-channel scale (= the absorption gate), a sigma floor, and the
    feature-level saturation.

    `scale` is each channel's clean-day p99 of trailing |z| on COMPENSATED
    values. The feature is the max over channels, and the max saturates far
    more often than any one channel, so `feature_sat` is calibrated on the
    max-statistic itself at the clean p99.9. The sigma floor is the clean
    epoch-to-epoch channel noise (differenced MAD / sqrt2), so a locally quiet
    window can never zero the trailing MAD. `inc_gate` is the absorption rule:
    the clean p99.9 of the compensated per-epoch step, per channel."""
    scale: dict
    sigma_floor: dict
    inc_gate: dict = None
    feature_sat: float = 1.0
    window: int = 40
    min_history: int = 10
    n_epochs: int = 0

    def __str__(self):
        ch = "  ".join(f"{k} scale {v:.1f}" for k, v in self.scale.items())
        return (f"cross-constellation calibration on {self.n_epochs} epochs "
                f"(trailing {self.window}, gated, compensated, feature sat "
                f"{self.feature_sat:.2f}): {ch}")


class CrossConstellation:
    """Streaming scorer. Feed epochs in order; reset() between replays."""

    def __init__(self, cal: CrossCal, nav):
        self.cal = cal
        self.extractor = ChannelExtractor(nav)
        self.reset()

    def reset(self) -> None:
        self._hist = {}
        self._last = {}
        self.extractor.reset()

    def _z(self, name: str, v: float) -> float | None:
        hist = self._hist.setdefault(name, deque(maxlen=self.cal.window))
        if len(hist) < self.cal.min_history:
            hist.append(v)
            self._last[name] = v
            return None
        arr = np.fromiter(hist, dtype=float)
        med = float(np.median(arr))
        mad = 1.4826 * float(np.median(np.abs(arr - med)))
        sig = max(mad, self.cal.sigma_floor.get(name, 0.0), 1e-6)
        z = abs(v - med) / sig
        # Absorption is gated on the per-epoch INCREMENT, not the level: slow
        # drift is followed, a step or walk beyond the clean tail is refused
        # forever (see module docstring).
        gate = (self.cal.inc_gate or {}).get(name, np.inf)
        if abs(v - self._last.get(name, v)) < gate:
            hist.append(v)
            self._last[name] = v
        return z

    def score(self, epoch, sols: dict | None = None) -> dict:
        """{"value": [0,1], "channels": {name: z}, "raw": {...}} for one epoch."""
        values, resets = self.extractor.extract(epoch, sols)
        for name in resets:
            self._hist.pop(name, None)
        zs = {}
        for name, v in values.items():
            z = self._z(name, v)
            if z is not None and name in self.cal.scale:
                zs[name] = float(z)
        if not zs:
            return {"value": 0.0, "channels": {}, "raw": values}
        m = max(z / self.cal.scale[n] for n, z in zs.items())
        return {"value": float(min(m / self.cal.feature_sat, 1.0)),
                "channels": zs, "raw": values}


def _compensated_frame(clean_epochs, nav, use_cache: bool = True):
    """Compensated channel values + reset lists over a clean replay. The
    solves are the slow part, so this is what gets cached."""
    key = None
    if use_cache:
        # The span is NOT a safe key. A masked and an unmasked clean day cover
        # the same span with the same epoch count, as do a clean and an
        # injected replay -- so the second caller would silently receive the
        # first's calibration. Fingerprint the observables. (Same defect was
        # fixed in rinex.solve.residual_panel; it was missed here, and it cost
        # a set of cross-constellation d' figures that had to be re-measured.)
        fp = hashlib.sha1()
        for ep in clean_epochs[:: max(1, len(clean_epochs) // 50)]:
            fp.update(",".join(map(str, ep.df.index)).encode())
            fp.update(np.ascontiguousarray(
                ep.df["code_1"].to_numpy(dtype=float)).tobytes())
        raw = (f"xc6|{clean_epochs[0].time}|{clean_epochs[-1].time}"
               f"|{len(clean_epochs)}|{fp.hexdigest()[:16]}")
        key = CACHE / ("xc_" + hashlib.sha1(raw.encode()).hexdigest()[:16] + ".pkl")
        if key.exists():
            with key.open("rb") as fh:
                return pickle.load(fh)
    ex = ChannelExtractor(nav)
    rows, resets = [], []
    for ep in clean_epochs:
        v, r = ex.extract(ep)
        rows.append(v)
        resets.append(r)
    out = (pd.DataFrame(rows), resets)
    if key:
        CACHE.mkdir(parents=True, exist_ok=True)
        with key.open("wb") as fh:
            pickle.dump(out, fh, protocol=pickle.HIGHEST_PROTOCOL)
    return out


def fit_cross(clean_epochs, nav, window: int = 40, min_history: int = 10,
              quantile: float = 0.99, feature_quantile: float = 0.999,
              use_cache: bool = True) -> CrossCal:
    """Two passes over compensated clean channels: ungated trailing z gives
    each channel's scale (= gate) at the clean p99; the feature saturation is
    the clean p99.9 of the max-over-channels statistic."""
    df, resets = _compensated_frame(clean_epochs, nav, use_cache=use_cache)

    floor, inc_gate = {}, {}
    for name in df.columns:
        d = df[name].dropna().diff().dropna()
        floor[name] = float(1.4826 * (d - d.median()).abs().median() / np.sqrt(2))
        inc_gate[name] = float(d.abs().quantile(0.999))

    probe = CrossCal(scale={n: np.inf for n in df.columns}, sigma_floor=floor,
                     inc_gate=inc_gate, window=window, min_history=min_history)
    scorer = CrossConstellation.__new__(CrossConstellation)
    scorer.cal = probe
    scorer._hist = {}
    scorer._last = {}
    ztab = []
    for (_, row), rs in zip(df.iterrows(), resets):
        for name in rs:
            scorer._hist.pop(name, None)
        zrow = {}
        for name in df.columns:
            v = row[name]
            if pd.notna(v):
                z = scorer._z(name, float(v))
                if z is not None:
                    zrow[name] = z
        ztab.append(zrow)
    zdf = pd.DataFrame(ztab)
    scale = {n: float(zdf[n].quantile(quantile))
             for n in zdf.columns if zdf[n].count() >= 50}
    m = (zdf[list(scale)] / pd.Series(scale)).max(axis=1).dropna()
    return CrossCal(scale=scale, sigma_floor={n: floor[n] for n in scale},
                    inc_gate={n: inc_gate[n] for n in scale},
                    feature_sat=float(m.quantile(feature_quantile)),
                    window=window, min_history=min_history,
                    n_epochs=len(clean_epochs))
