"""The three detection features of design.md §6a, computed causally.

Each returns a scalar in [0,1] per epoch, higher = more anomalous, alongside
the per-satellite detail the explanation layer needs.

**Causal by construction.** This is a replay of a real day and the console
tails it live, so every baseline here is built from strictly past epochs. No
statistic may see an epoch at or after the one it is scoring. Anything else
would make the false-surrender rate and the time-to-alert meaningless.

    cn0_anomaly              per-SV C/N0 against its own trailing mean
    pseudorange_residual     code-minus-carrier LEVEL against a slow baseline
    code_carrier_divergence  code-minus-carrier RATE, epoch to epoch

## What feature 2 is, honestly

§6a.2 calls for "deviation from a smoothed per-satellite baseline" for the
pseudorange residual. A *true* pseudorange residual is the leftover after
fitting one receiver position and clock to all satellites at once — and that
needs line-of-sight vectors, which need the nav file, which is Track C's
geometry (§6b). Track A does not take that dependency: §6b already warns that
one file failure costs two components.

Without geometry, every nav-free per-satellite check reduces to the same
observable. Pseudorange change over an epoch is geometric range change plus
receiver clock; carrier phase measures the same range change to millimetres;
so code minus carrier is what is left, and there is nothing else to look at.
So features 2 and 3 are **two statistics of one observable — its level and its
rate — not two independent observables.** They are genuinely complementary in
time, which is what §6a claims for them:

- the **rate** fires on the lift-off transient and on any epoch where the
  spoofer's carrier stops matching its code, then settles;
- the **level** integrates that rate, so it keeps climbing for as long as the
  walk-off continues and is still elevated long after the C/N0 signal is gone.

Say this out loud rather than presenting three independent observables. The
upgrade is available and cheap: once Track C's H matrix exists, feature 2 is
replaced by the real position-solution residual and the two become independent.

## Why C/N0 fades, and on whose clock

§6a.1 says the C/N0 anomaly "goes quiet ~10 s after capture". In this pipeline
it fades over `cn0_window` epochs, because that is how long the trailing mean
takes to absorb the step — the receiver's own loop dynamics are not observable
at 30 s sampling. The fade is real and the reason for it is the detector's
memory, not the tracking loop's. Quote the window, not the 10 s.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# C/N0 is reported to 0.25 dB (docs/measured.md). The differenced-MAD estimator
# cannot resolve noise below that, so a per-satellite sigma is never trusted
# smaller than the quantisation-limited value 1.4826 * 0.25 / sqrt(2).
CN0_SIGMA_FLOOR = 1.4826 * 0.25 / np.sqrt(2)

# The full §6a feature set. The first three are per-satellite statistics
# computed by FeatureExtractor below; cross_constellation is an epoch-level
# feature (backend/detection/cross.py) merged in by the replay pipeline.
FEATURE_NAMES = ("cn0_anomaly", "pseudorange_residual",
                 "code_carrier_divergence", "cross_constellation")
PER_SV_FEATURES = ("cn0_anomaly", "pseudorange_residual",
                   "code_carrier_divergence")

# Minimum tracked satellites for a constellation to be scored on its own.
MIN_CONSTELLATION_SV = 5


def aggregate(scores: pd.Series) -> float:
    """Per-satellite [0,1] scores -> one number for the epoch.

    The worst constellation's mean, over constellations tracking at least
    MIN_CONSTELLATION_SV satellites; the whole-sky mean if none qualifies.

    Not the mean over the whole sky, and the reason is the attack model. §7's
    two realistic scenarios both target a subset — carry-off takes an SV subset,
    meaconing takes one constellation — so a sky-wide mean divides the signal by
    the fraction of the sky under attack. On this station 12 of 44 tracked
    satellites are GPS, so a total capture of GPS could never score above 0.27.
    Taking the worst constellation removes that dilution, and taking its *mean*
    rather than its worst satellite keeps one multipath outlier from firing it.

    Measured on the clean day against a §7 carry-off, this is worth d' 2.8 -> 5.4
    on the pseudorange residual. The floor of MIN_CONSTELLATION_SV exists because
    the max of several noisy means is biased upward, and SBAS tracks three.
    """
    if scores.empty:
        return 0.0
    by_sys = scores.groupby([sv[0] for sv in scores.index])
    big = by_sys.mean()[by_sys.size() >= MIN_CONSTELLATION_SV]
    return float(big.max() if len(big) else scores.mean())


@dataclass
class FeatureConfig:
    """Window lengths, in epochs. At 30 s sampling: 20 epochs = 10 minutes."""
    cn0_window: int = 20
    cmc_window: int = 40
    min_history: int = 5
    # A jump this many sigma in one epoch is a cycle slip or a re-acquisition,
    # not an attack; the satellite's baseline is reset rather than scored.
    # Calibrated against the clean day -- see Calibration.fit.
    slip_sigma: float = 100.0


@dataclass
class Calibration:
    """Per-feature saturation scale and per-satellite sigmas, fitted on clean data.

    A per-satellite z-score is turned into a [0,1] score by dividing by the
    scale at which it saturates. That scale is measured, not chosen: each
    satellite's own high quantile of |z| over the clean day, pooled across
    satellites by median.

    Pooled by median, and per-satellite first, for the same reason the noise
    floor is (docs/measured.md): the clean per-SV |z| distribution has a heavy
    tail — p99.9 of 33.9 sigma on C/N0 against a median of 1.1 — driven by real
    excursions on a handful of low-elevation, multipath-prone satellites. Taking
    a quantile of the whole satellite-epoch pool lets those few satellites set
    the scale for all 109, and a genuine 7.6-sigma spoofer then scores 0.2.
    """
    z_sat: dict                       # feature name -> saturating |z|
    cn0_sigma: pd.Series              # per-SV, dB-Hz, floored
    cmc_sigma: pd.Series              # per-SV, metres
    quantile: float = 0.99
    n_epochs: int = 0

    def __str__(self) -> str:
        z = "  ".join(f"{k} {v:.1f}" for k, v in self.z_sat.items())
        return (f"calibration on {self.n_epochs} clean epochs, "
                f"saturating |z| at the median per-SV "
                f"p{self.quantile * 100:g}: {z}")


def _sigma_series(by_sv: pd.Series, floor: float) -> pd.Series:
    return by_sv.clip(lower=floor)


class FeatureExtractor:
    """Streaming, causal. Feed epochs in order; get one feature set per epoch."""

    def __init__(self, cal: Calibration, cfg: FeatureConfig | None = None):
        self.cal = cal
        self.cfg = cfg or FeatureConfig()
        self.reset()

    def reset(self) -> None:
        c = self.cfg
        self._cn0 = defaultdict(lambda: deque(maxlen=c.cn0_window))
        self._cmc = defaultdict(lambda: deque(maxlen=c.cmc_window))
        self._last_cmc = {}
        self._last_seen = {}
        self._n = 0

    # -- per-satellite z-scores, all against strictly past epochs --------------

    def _sv_z(self, sv: str, cn0: float, cmc: float):
        cfg, cal = self.cfg, self.cal
        s_cn0 = float(cal.cn0_sigma.get(sv, cal.cn0_sigma.median()))
        s_cmc = float(cal.cmc_sigma.get(sv, cal.cmc_sigma.median()))

        z = {}
        hist = self._cn0[sv]
        if len(hist) >= cfg.min_history and np.isfinite(cn0):
            z["cn0_anomaly"] = abs(cn0 - float(np.mean(hist))) / s_cn0

        if np.isfinite(cmc):
            prev = self._last_cmc.get(sv)
            rate = None
            if prev is not None:
                # sigma of a difference of two noisy samples is sqrt(2) * sigma
                rate = (cmc - prev) / (s_cmc * np.sqrt(2))
                if abs(rate) > cfg.slip_sigma:
                    # cycle slip / re-acquisition: drop the history, score nothing
                    self._cmc[sv].clear()
                    self._last_cmc[sv] = cmc
                    return {}
                z["code_carrier_divergence"] = abs(rate)

            base = self._cmc[sv]
            if len(base) >= cfg.min_history:
                z["pseudorange_residual"] = abs(cmc - float(np.mean(base))) / s_cmc

        return z

    def _absorb(self, sv: str, cn0: float, cmc: float) -> None:
        if np.isfinite(cn0):
            self._cn0[sv].append(cn0)
        if np.isfinite(cmc):
            self._cmc[sv].append(cmc)
            self._last_cmc[sv] = cmc

    # -- one epoch ------------------------------------------------------------

    def step(self, epoch, band: int = 1) -> dict:
        """Score one epoch, then absorb it into the baselines. Returns

            {"features": {name: [0,1]}, "per_sv": DataFrame of |z| by SV,
             "n_scored": int}
        """
        df = epoch.df
        cn0 = df[f"cn0_{band}"]
        cmc = df[f"code_{band}"] - df[f"phase_m_{band}"]

        # Any satellite absent last epoch has a broken carrier-phase arc.
        for sv in list(self._last_cmc):
            if sv not in df.index:
                self._cmc[sv].clear()
                self._last_cmc.pop(sv, None)

        rows = {sv: self._sv_z(sv, cn0.get(sv, np.nan), cmc.get(sv, np.nan))
                for sv in df.index}
        for sv in df.index:
            self._absorb(sv, cn0.get(sv, np.nan), cmc.get(sv, np.nan))
        self._n += 1

        per_sv = pd.DataFrame.from_dict(rows, orient="index").reindex(
            columns=list(PER_SV_FEATURES))
        per_sv.index.name = "sv"

        feats = {}
        for name in PER_SV_FEATURES:
            col = per_sv[name].dropna()
            if col.empty:
                feats[name] = 0.0
                continue
            feats[name] = aggregate((col / self.cal.z_sat[name]).clip(0, 1))

        return {"features": feats, "per_sv": per_sv,
                "n_scored": int(per_sv.notna().any(axis=1).sum())}

    def run(self, epochs, band: int = 1) -> list:
        self.reset()
        return [self.step(ep, band=band) for ep in epochs]


def fit(clean_epochs, floor, cfg: FeatureConfig | None = None,
        quantile: float = 0.99) -> Calibration:
    """Fit the saturation scales on a clean replay.

    Two passes: the first collects per-satellite z-scores with the saturation
    scale set to 1 (so the raw |z| comes through), the second is what callers
    use. Nothing here sees injected data.
    """
    cfg = cfg or FeatureConfig()
    cal = Calibration(
        z_sat={n: 1.0 for n in PER_SV_FEATURES},
        cn0_sigma=_sigma_series(floor.cn0_sigma_by_sv, CN0_SIGMA_FLOOR),
        cmc_sigma=_sigma_series(floor.cmc_sigma_by_sv, 1e-3),
        quantile=quantile,
    )
    out = FeatureExtractor(cal, cfg).run(clean_epochs)
    z = pd.concat([o["per_sv"] for o in out])
    per_sv = z.groupby(level=0).quantile(quantile)     # each satellite's own tail
    cal.z_sat = {n: float(per_sv[n].median()) for n in PER_SV_FEATURES}
    cal.n_epochs = len(clean_epochs)
    return cal


def frame(results, times) -> pd.DataFrame:
    """Per-epoch feature values as a dataframe indexed by time."""
    return pd.DataFrame([r["features"] for r in results],
                        index=pd.Index(times, name="time"))
