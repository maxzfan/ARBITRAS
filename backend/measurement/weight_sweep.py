"""Dirichlet weight-sensitivity sweep (design.md §10, the arXiv 2607.05415
answer): is the verdict an artefact of the chosen feature weights, or does
it survive any reasonable weighting?

Definitions are §10's, fixed:

  - FSR (False Surrender Rate, continuity risk): fraction of CLEAN-replay
    epochs whose confidence falls below the NOMINAL threshold. Per project
    convention it is reported as a *distribution over sampled weight
    vectors* (array + min/median/max), never a point.
  - detection fraction: fraction of attack-window epochs below NOMINAL —
    the integrity-side companion, per draw.
  - weight_sensitive_fraction: fraction of all epochs (clean + attack)
    whose verdict (below/above NOMINAL) differs across draws — the epochs
    where the weighting actually decides the outcome.

Harness, not policy: `compose_fn(features, geometry, w) -> confidence` is
supplied by the caller. The stub in test_measurement.py pins semantics;
`compose_from_track_a` adapts Track A's real `backend.detection.score`.
Only the feature weights (Dirichlet, dimension = number of features) are
swept; the feature/geometry blend beta stays at Track A's default.

Epoch format: (features: dict[str, float], geometry) — geometry is passed
through to compose_fn untouched (a §5 geometry block for the real score,
a bare information ratio for the stub). Clean and attack epochs arrive as
separate lists; no truth column is needed.

Run over real stream JSONL (§5 records, one per line):

  python -m backend.measurement.weight_sweep out/clean.jsonl \
      out/carryoff.jsonl --nominal <T> [--n-draws N]

The attack file carries no truth column, so its attack window is every
epoch at or after the known injection onset, 2026-08-20T12:30:00Z;
pre-onset epochs of the attack file are dropped (they are neither the
clean replay nor the attack window).
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Callable

import numpy as np

# Feature names come from Track A's detection module when it exports them;
# the local tuple is the agreed fallback so this harness never blocks on A.
try:
    from backend.detection import FEATURE_NAMES as FEATURES
except ImportError:  # pragma: no cover - Track A always present in-repo
    FEATURES = ("cn0_anomaly", "pseudorange_residual",
                "code_carrier_divergence", "cross_constellation")

# Known injection onset for the attack replay (injector config, DOY 232).
ATTACK_ONSET = datetime(2026, 8, 20, 12, 30, 0, tzinfo=timezone.utc)


def dirichlet_sweep(compose_fn: Callable, clean_epochs: list[tuple],
                    attack_epochs: list[tuple], n_draws: int = 1000, *,
                    nominal: float, seed: int = 20260905) -> dict:
    """Sweep Dirichlet feature weightings over clean and attack epochs.

    `nominal` is the NOMINAL confidence floor and has no default: thresholds
    derive from observed data, never from a guess (CLAUDE.md conventions).

    Returns per-draw FSR and detection-fraction distributions plus the
    weight-sensitive epoch fraction. Deterministic under `seed`.
    """
    rng = np.random.default_rng(seed)
    draws = rng.dirichlet(np.ones(len(FEATURES)), size=n_draws)

    n_clean, n_attack = len(clean_epochs), len(attack_epochs)
    epochs = list(clean_epochs) + list(attack_epochs)
    # below[draw, epoch] — confidence < nominal under that weighting
    below = np.zeros((n_draws, len(epochs)), dtype=bool)
    for d in range(n_draws):
        w = tuple(map(float, draws[d]))
        for i, (features, geometry) in enumerate(epochs):
            below[d, i] = compose_fn(features, geometry, w) < nominal

    # §10 FSR: clean epochs below NOMINAL — a distribution over draws.
    fsr = (below[:, :n_clean].mean(axis=1) if n_clean
           else np.zeros(n_draws))
    detection = (below[:, n_clean:].mean(axis=1) if n_attack
                 else np.full(n_draws, np.nan))
    sensitive = below.any(axis=0) & ~below.all(axis=0)

    def _stats(a):
        if np.isnan(a).all():
            return float("nan"), float("nan"), float("nan")
        return float(a.min()), float(np.median(a)), float(a.max())

    fsr_min, fsr_med, fsr_max = _stats(fsr)
    det_min, det_med, det_max = _stats(detection)
    return {
        "fsr": fsr,
        "fsr_min": fsr_min, "fsr_median": fsr_med, "fsr_max": fsr_max,
        "detection": detection,
        "detection_min": det_min, "detection_median": det_med,
        "detection_max": det_max,
        "weight_sensitive_fraction": float(sensitive.mean())
        if len(epochs) else 0.0,
        "n_draws": n_draws,
        "n_clean": n_clean,
        "n_attack": n_attack,
        "nominal": float(nominal),
    }


@lru_cache(maxsize=64)
def _weights_for(w: tuple):
    """One Weights object per draw, not per epoch (draws repeat per epoch)."""
    from backend.detection import Weights
    return Weights(feature=dict(zip(FEATURES, w)),
                   note="Dirichlet draw (§10 sweep), beta at default")


def compose_from_track_a(features: dict, geometry, w: tuple) -> float:
    """Adapter: run Track A's real composite for one epoch and weighting.

    `geometry` is the record's §5 geometry block (score reads
    `information_ratio` from it); a bare float is wrapped for convenience.
    beta stays at the Weights dataclass default — only the feature
    weighting is swept here.
    """
    from backend.detection import score
    if geometry is not None and not isinstance(geometry, dict):
        geometry = {"information_ratio": float(geometry)}
    return score(features, geometry, _weights_for(tuple(w)))["confidence"]


def report(result: dict) -> str:
    """Human-readable summary. Distributions, never a point (CLAUDE.md)."""
    lines = [
        f"{result['n_draws']} weightings x {result['n_clean']} clean + "
        f"{result['n_attack']} attack epochs (nominal {result['nominal']:.3f})",
        f"FSR distribution min/median/max: {result['fsr_min']:.3f} / "
        f"{result['fsr_median']:.3f} / {result['fsr_max']:.3f}",
    ]
    if result["n_attack"]:
        lines.append(
            f"detection fraction distribution min/median/max: "
            f"{result['detection_min']:.3f} / {result['detection_median']:.3f}"
            f" / {result['detection_max']:.3f}")
    else:
        lines.append("detection fraction distribution: n/a (no attack epochs)")
    lines.append(f"weight-sensitive epochs: "
                 f"{result['weight_sensitive_fraction']:.1%}")

    wsf = result["weight_sensitive_fraction"]
    if wsf == 0.0:
        verdict = ("verdict: weight-robust — every epoch gets the same call "
                   "under all sampled weightings")
    else:
        verdict = (f"verdict: weighting decides {wsf:.1%} of epochs "
                   f"(FSR spread {result['fsr_min']:.3f}-"
                   f"{result['fsr_max']:.3f})")
    lines.append(verdict)
    return "\n".join(lines)


def load_epochs(path) -> list[tuple[datetime, dict, dict]]:
    """Read §5 JSONL stream records -> (timestamp, features, geometry)."""
    epochs = []
    with Path(path).open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            t = datetime.fromisoformat(
                rec["timestamp"].replace("Z", "+00:00"))
            epochs.append((t, rec["features"], rec.get("geometry")))
    return epochs


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(
        description="design.md §10 Dirichlet weight sweep over stream JSONL")
    ap.add_argument("clean", help="clean-replay stream JSONL")
    ap.add_argument("attack", help="attack-replay stream JSONL")
    ap.add_argument("--nominal", type=float, required=True,
                    help="NOMINAL confidence floor (from the threshold "
                         "session; never a guess)")
    ap.add_argument("--n-draws", type=int, default=1000)
    args = ap.parse_args(argv)

    clean = [(f, g) for _, f, g in load_epochs(args.clean)]
    attack_all = load_epochs(args.attack)
    attack = [(f, g) for t, f, g in attack_all if t >= ATTACK_ONSET]
    dropped = len(attack_all) - len(attack)
    if dropped:
        print(f"note: dropped {dropped} pre-onset epochs from the attack "
              f"file (onset {ATTACK_ONSET.isoformat()})")

    result = dirichlet_sweep(compose_from_track_a, clean, attack,
                             n_draws=args.n_draws, nominal=args.nominal)
    print(report(result))


if __name__ == "__main__":
    main()
