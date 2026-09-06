"""Composite confidence — the tuned half, and how it blends with the derived half.

design.md §6: confidence has two halves, computed separately and combined last.
Track A owns the first. The second (§6b, the Fisher-information geometry score)
is Track C's and arrives through the interface, never recomputed here.

    anomaly    = sum_i w_i * feature_i                 tuned, weights sum to 1
    deficit    = 1 - geometry.information_ratio        derived, no weights
    confidence = 1 - (beta * anomaly + (1 - beta) * deficit)

**Both defaults here are placeholders and are marked as such at runtime.**
Equal feature weights and beta = 0.5 are not tuned numbers — they are the
absence of a tuned number, which is the honest state until the threshold
session (design.md §10, TRACK_A.md 21:00-22:30) sets them against the observed
clean and injected distributions. `Weights.tuned` is False until someone does
that, and the emitted record carries the flag, so nothing downstream can quote
a figure that came from a guess.

The blend `beta` is itself a threshold-class decision (design.md §16, open
items) and one of the quantities the §10 Dirichlet sweep varies. Reporting how
much of the score is weight-sensitive is the answer to arXiv 2607.05415, so
`weight_sensitive_fraction` is reported alongside.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .features import FEATURE_NAMES


@dataclass
class Weights:
    """Feature weights and the feature/geometry blend."""
    feature: dict = field(default_factory=lambda:
                          {n: 1.0 / len(FEATURE_NAMES) for n in FEATURE_NAMES})
    beta: float = 0.5
    tuned: bool = False
    note: str = "untuned placeholder: equal weights, beta 0.5"

    def __post_init__(self):
        total = sum(self.feature.values())
        if not np.isclose(total, 1.0):
            raise ValueError(f"feature weights must sum to 1, got {total:.6f}")
        if not 0.0 <= self.beta <= 1.0:
            raise ValueError(f"beta must be in [0,1], got {self.beta}")

    @classmethod
    def equal(cls, names) -> "Weights":
        """Untuned equal weights over an explicit feature set (Track E adds
        OPTIONAL_FEATURE_NAMES when its sensor is present)."""
        names = tuple(names)
        return cls(feature={n: 1.0 / len(names) for n in names}, tuned=False,
                   note=f"untuned placeholder: equal weights over {len(names)}, beta 0.5")

    @classmethod
    def dirichlet(cls, rng, alpha: float = 1.0, beta=None,
                  names=FEATURE_NAMES) -> "Weights":
        """One draw for the §10 sweep: feature weights from a Dirichlet, and a
        blend drawn uniformly unless one is pinned."""
        names = tuple(names)
        w = rng.dirichlet([alpha] * len(names))
        b = float(rng.uniform()) if beta is None else float(beta)
        return cls(feature=dict(zip(names, map(float, w))), beta=b,
                   tuned=False, note="Dirichlet draw (§10 sweep)")

    @property
    def weight_sensitive_fraction(self) -> float:
        """Share of the composite that a re-weighting can move. The geometry
        half has no weights, so this is beta."""
        return self.beta


def _finite(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and np.isfinite(v)


def scored_features(features: dict, w: Weights) -> list[str]:
    """Names in the weight vector that carry a finite value this epoch.
    Absent or NaN features are not scored (TRACK_E.md E3 seam)."""
    return [n for n in w.feature if _finite(features.get(n))]


def anomaly(features: dict, w: Weights) -> float:
    """Weighted mean of the tuned half over the features actually scored,
    weights renormalised to the scored subset. [0,1], higher = more anomalous."""
    scored = scored_features(features, w)
    total = sum(w.feature[n] for n in scored)
    if total <= 0.0:
        return 0.0
    return float(sum(w.feature[n] * features[n] for n in scored) / total)


def score(features: dict, geometry: dict | None, w: Weights | None = None) -> dict:
    """Composite confidence plus the parts it was made of.

    `geometry` is Track C's §5 block. When it is absent the blend collapses to
    the feature half alone — beta is forced to 1 and `geometry_available` is
    False, so a run without the nav file is visibly a different measurement
    rather than a quietly worse one (§6b, degraded fallback).
    """
    w = w or Weights()
    a = anomaly(features, w)
    have_geom = bool(geometry) and geometry.get("information_ratio") is not None

    if have_geom:
        deficit = 1.0 - float(geometry["information_ratio"])
        beta = w.beta
    else:
        deficit, beta = 0.0, 1.0

    conf = 1.0 - (beta * a + (1.0 - beta) * deficit)
    return {
        "confidence": float(np.clip(conf, 0.0, 1.0)),
        "feature_score": a,
        "geometry_deficit": deficit,
        "geometry_available": have_geom,
        "beta": beta,
        "weights_tuned": w.tuned,
        "weights": dict(w.feature),
        "weight_sensitive_fraction": beta,
        "features_scored": scored_features(features, w),
    }
