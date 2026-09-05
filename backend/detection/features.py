"""The three detection features of design.md §6a, computed causally.

Each returns a scalar in [0,1] per epoch, higher = more anomalous, alongside
the per-satellite detail the explanation layer needs.

**Causal by construction.** This is a replay of a real day and the console
tails it live, so every baseline here is built from strictly past epochs. No
statistic may see an epoch at or after the one it is scoring. Anything else
would make the false-surrender rate and the time-to-alert meaningless.

    cn0_anomaly              per-SV C/N0 against its own trailing mean
    pseudorange_residual     POST-FIT residual of the position solution, per
                             satellite, against its own trailing baseline
    code_carrier_divergence  code-minus-carrier RATE, epoch to epoch

## Feature 2 is the post-fit residual, and why that matters

§6a.2 specifies "deviation from a smoothed per-satellite baseline" for the
pseudorange residual. It is now exactly that: the per-satellite post-fit
residual of the all-in-view least-squares solution (`solve()['resid_m']`)
against its own trailing median, normalised by a per-satellite sigma measured
on the clean day.

An earlier version scored the LEVEL of code-minus-carrier here, which made
features 2 and 3 two statistics of one observable. Measured before replacing
it (docs/measured.md): the two were not redundant -- correlation +0.11 on
clean data, because a level and its own first difference are near-orthogonal
for a drifting signal -- but feature 2 earned d' 0.77 against both meaconing
and a coherent walk, and removing it entirely RAISED the composite. The reason
to replace it was never redundancy; it was that **no feature in 1-3 carried
geometry, so none of them could see a coordinated position walk**, leaving
feature 4 as the single channel holding the whole detection.

The post-fit residual carries geometry by construction. A coordinated walk on
one constellation lies in the position columns of H, so a single-constellation
solve absorbs it -- but the all-in-view solution cannot satisfy the spoofed
and authentic subsets at once, and the leftover lands here. Measured on the
build check: the all-in-view residual RMS grows 4 -> 40 m across a 0 -> 140 m
walk while a GPS-only solve stays flat at 4.7-5.4 m.

**The dependency this takes on.** Feature 2 now needs satellite positions, so
it needs the nav file, which §6b warns costs two components on one file
failure -- now three. The fallback is explicit rather than silent: with no
residual supplied, feature 2 is not scored at all (NaN, excluded from the
aggregate) instead of quietly reading zero, and `n_scored` drops. A run
without the nav file is visibly a different measurement.

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
class ExclusionRule:
    """When the detector stops trusting a satellite. Ruled by hand 2026-09-05.

    `k` is the multiple of a satellite's calibrated saturation its worst
    per-SV score must reach. Measured on the clean full day, per epoch (the
    denominator that matters, because it is epochs that lose H rows):

        k = 1.0 -> 78.9% of epochs flag >= 1 SV, 1.78 SV mean
        k = 1.5 -> 34.6%   k = 2.0 -> 12.7%   k = 3.0 -> 2.7%   k = 5.0 -> 0.5%

    The tails are an order of magnitude heavier than Gaussian at every k and
    floor at 0.5%. That floor IS elevation-driven, and overwhelmingly so:
    binned by elevation at both k = 3 and k = 5, **100% of clean-day
    exclusions fall in the 0-15 degree bin**, with a median elevation of 0.7
    degrees. Above 15 degrees the clean exclusion rate is 0.00% in every bin.

    So the ruled rule is `k = 3.0` plus a low-elevation mask. **The mask
    cutoff is UNSET and the rule returns nothing until a cutoff is given** --
    picking it is a threshold-session decision. `FLAT_K5` is the configured
    fallback: k = 5.0 with no mask, usable without a cutoff and without
    elevations.
    """
    k: float
    elevation_mask_deg: float | None = None
    requires_mask: bool = True

    @property
    def armed(self) -> bool:
        return not self.requires_mask or self.elevation_mask_deg is not None

    def __str__(self) -> str:
        if not self.armed:
            return (f"exclusion k={self.k} with low-elevation mask: "
                    f"DISARMED (mask cutoff unset -> no satellites excluded)")
        m = ("no mask" if self.elevation_mask_deg is None
             else f"mask <{self.elevation_mask_deg:g} deg")
        return f"exclusion k={self.k}, {m}"


# The ruled rule: k = 3.0, mask cutoff deliberately unset.
MASKED_K3 = ExclusionRule(k=3.0, elevation_mask_deg=None, requires_mask=True)
# Configured fallback: flat k = 5.0, no mask, no elevations needed.
FLAT_K5 = ExclusionRule(k=5.0, elevation_mask_deg=None, requires_mask=False)


def flagged_sv(per_sv: pd.DataFrame, z_sat: dict,
               rule: ExclusionRule | float | None,
               elevations: pd.Series | None = None) -> list:
    """Satellites the detector has stopped trusting.

    The only honest source for §5's `excluded_sv`: per-satellite scores, not a
    restatement of what the injector did. Returns [] when the rule is None or
    disarmed. A bare float is accepted as a flat rule for convenience.

    A satellite below the mask cutoff is NOT excluded by this rule -- the mask
    says "this satellite's score is not trustworthy evidence of spoofing",
    which is the opposite of "this satellite is spoofed". Whether a
    low-elevation satellite should be dropped from H for its own noise is a
    separate question and Track C's.
    """
    if rule is None or per_sv.empty:
        return []
    if not isinstance(rule, ExclusionRule):
        rule = ExclusionRule(k=float(rule), requires_mask=False)
    if not rule.armed:
        return []
    norm = per_sv / pd.Series(z_sat)
    worst = norm.max(axis=1).dropna()
    hit = worst.index[worst >= rule.k]
    if rule.elevation_mask_deg is not None:
        if elevations is None:
            raise ValueError("this exclusion rule needs elevations")
        el = elevations.reindex(hit)
        hit = el.index[el >= rule.elevation_mask_deg]
    return sorted(hit)


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
    resid_sigma: pd.Series = None     # per-SV post-fit residual, metres
    quantile: float = 0.99
    n_epochs: int = 0

    def __str__(self) -> str:
        z = "  ".join(f"{k} {v:.1f}" for k, v in self.z_sat.items())
        r = (f", resid sigma median "
             f"{self.resid_sigma.median():.2f} m"
             if self.resid_sigma is not None and len(self.resid_sigma)
             else ", resid sigma UNSET (feature 2 not scored)")
        return (f"calibration on {self.n_epochs} clean epochs, "
                f"saturating |z| at the median per-SV "
                f"p{self.quantile * 100:g}: {z}{r}")


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
        self._res = defaultdict(lambda: deque(maxlen=c.cmc_window))
        self._last_cmc = {}
        self._last_seen = {}
        self._n = 0

    # -- per-satellite z-scores, all against strictly past epochs --------------

    def _sv_z(self, sv: str, cn0: float, cmc: float, resid: float = np.nan):
        cfg, cal = self.cfg, self.cal
        s_cn0 = float(cal.cn0_sigma.get(sv, cal.cn0_sigma.median()))
        s_cmc = float(cal.cmc_sigma.get(sv, cal.cmc_sigma.median()))
        s_res = (float(cal.resid_sigma.get(sv, cal.resid_sigma.median()))
                 if cal.resid_sigma is not None and len(cal.resid_sigma)
                 else np.nan)

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

        # Feature 2: post-fit residual of the position solution against its own
        # trailing baseline. Not scored at all when no solution was supplied.
        if np.isfinite(resid) and np.isfinite(s_res):
            hist = self._res[sv]
            if len(hist) >= cfg.min_history:
                z["pseudorange_residual"] = (abs(resid - float(np.median(hist)))
                                             / s_res)
            hist.append(resid)

        return z

    def _absorb(self, sv: str, cn0: float, cmc: float) -> None:
        if np.isfinite(cn0):
            self._cn0[sv].append(cn0)
        if np.isfinite(cmc):
            self._cmc[sv].append(cmc)
            self._last_cmc[sv] = cmc

    # -- one epoch ------------------------------------------------------------

    def step(self, epoch, band: int = 1, resid: dict | None = None) -> dict:
        """Score one epoch, then absorb it into the baselines. Returns

            {"features": {name: [0,1]}, "per_sv": DataFrame of |z| by SV,
             "n_scored": int}

        `resid` is {sv: post-fit residual m} from the all-in-view solution.
        Without it feature 2 is not scored (see the module docstring).
        """
        resid = resid or {}
        df = epoch.df
        cn0 = df[f"cn0_{band}"]
        cmc = df[f"code_{band}"] - df[f"phase_m_{band}"]

        # Any satellite absent last epoch has a broken carrier-phase arc.
        for sv in list(self._last_cmc):
            if sv not in df.index:
                self._cmc[sv].clear()
                self._last_cmc.pop(sv, None)

        rows = {sv: self._sv_z(sv, cn0.get(sv, np.nan), cmc.get(sv, np.nan),
                               resid.get(sv, np.nan))
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

    def run(self, epochs, band: int = 1, resid_panel=None) -> list:
        self.reset()
        out = []
        for ep in epochs:
            r = None
            if resid_panel is not None and ep.time in resid_panel.index:
                r = resid_panel.loc[ep.time].dropna().to_dict()
            out.append(self.step(ep, band=band, resid=r))
        return out


def fit(clean_epochs, floor, cfg: FeatureConfig | None = None,
        quantile: float = 0.99, resid_panel=None) -> Calibration:
    """Fit the saturation scales on a clean replay.

    Two passes: the first collects per-satellite z-scores with the saturation
    scale set to 1 (so the raw |z| comes through), the second is what callers
    use. Nothing here sees injected data.
    """
    cfg = cfg or FeatureConfig()
    # Per-SV post-fit residual sigma, from the differenced MAD of the clean
    # residual series -- the same estimator noise.py uses, for the same reason:
    # differencing removes the slowly varying part and leaves the noise.
    resid_sigma = pd.Series(dtype=float)
    if resid_panel is not None and len(resid_panel):
        d = resid_panel.diff()
        resid_sigma = (1.4826 * (d - d.median()).abs().median()
                       / np.sqrt(2)).dropna().clip(lower=1e-3)
    cal = Calibration(
        z_sat={n: 1.0 for n in PER_SV_FEATURES},
        cn0_sigma=_sigma_series(floor.cn0_sigma_by_sv, CN0_SIGMA_FLOOR),
        cmc_sigma=_sigma_series(floor.cmc_sigma_by_sv, 1e-3),
        resid_sigma=resid_sigma,
        quantile=quantile,
    )
    out = FeatureExtractor(cal, cfg).run(clean_epochs, resid_panel=resid_panel)
    z = pd.concat([o["per_sv"] for o in out])
    per_sv = z.groupby(level=0).quantile(quantile)     # each satellite's own tail
    cal.z_sat = {n: (float(per_sv[n].median())
                     if per_sv[n].notna().any() else 1.0)
                 for n in PER_SV_FEATURES}
    cal.n_epochs = len(clean_epochs)
    return cal


def frame(results, times) -> pd.DataFrame:
    """Per-epoch feature values as a dataframe indexed by time."""
    return pd.DataFrame([r["features"] for r in results],
                        index=pd.Index(times, name="time"))
