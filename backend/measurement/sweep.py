"""Dirichlet weight-sensitivity sweep (design.md, the arXiv 2607.05415
answer): is the detection verdict an artefact of the chosen feature
weights, or does it survive any reasonable weighting?

Harness, not policy: `compose_fn` is Track A's pure function
compose_confidence(features, geometry_ratio, w4, blend) -> confidence.
The geometry term is FIXED per epoch (the weight-independent half of the
score); only the four feature weights (Dirichlet(1,1,1,1)) and the
feature/geometry blend (Uniform(0,1)) are swept.

Reported:
  - fsr: per-draw flag success rate — fraction of spoofed epochs with
    confidence below `threshold` (the NOMINAL floor). Its distribution
    over draws is the headline: tight and high = weighting-robust.
  - weight_sensitive_fraction: fraction of epochs whose verdict
    (flagged vs not) differs across draws — the epochs where weighting
    actually decides the outcome.

Epoch format (agreed with Track A at 21:00, plumbed overnight):
  (features: dict[str, float], geometry_ratio: float, spoofed: bool)

Run (once injected-run epochs exist):
  python -m backend.measurement.sweep <epochs.json>
"""
from __future__ import annotations

from typing import Callable

import numpy as np

FEATURES = ("cn0_anomaly", "pseudorange_residual",
            "code_carrier_divergence", "cross_constellation")


def dirichlet_sweep(compose_fn: Callable, epochs: list[tuple],
                    n_draws: int = 1000, threshold: float = 0.5,
                    seed: int = 20260905) -> dict:
    """Sweep weightings; return fsr per draw + weight-sensitive fraction."""
    rng = np.random.default_rng(seed)
    w4s = rng.dirichlet(np.ones(4), size=n_draws)
    blends = rng.uniform(0.0, 1.0, size=n_draws)

    spoofed_idx = [i for i, (_, _, s) in enumerate(epochs) if s]
    # flags[draw, epoch] — verdict per weighting per epoch
    flags = np.zeros((n_draws, len(epochs)), dtype=bool)
    for d in range(n_draws):
        for i, (features, geom_ratio, _) in enumerate(epochs):
            c = compose_fn(features, geom_ratio, w4s[d], blends[d])
            flags[d, i] = c < threshold

    fsr = (flags[:, spoofed_idx].mean(axis=1) if spoofed_idx
           else np.zeros(n_draws))
    sensitive = flags.any(axis=0) & ~flags.all(axis=0)
    return {
        "fsr": fsr,
        "fsr_min": float(fsr.min()),
        "fsr_median": float(np.median(fsr)),
        "fsr_max": float(fsr.max()),
        "weight_sensitive_fraction": float(sensitive.mean()),
        "n_draws": n_draws,
        "n_epochs": len(epochs),
        "n_spoofed": len(spoofed_idx),
    }


def report(result: dict) -> str:
    return (f"{result['n_draws']} weightings x {result['n_epochs']} epochs "
            f"({result['n_spoofed']} spoofed)\n"
            f"FSR min/median/max: {result['fsr_min']:.3f} / "
            f"{result['fsr_median']:.3f} / {result['fsr_max']:.3f}\n"
            f"weight-sensitive epochs: "
            f"{result['weight_sensitive_fraction']:.1%}")
