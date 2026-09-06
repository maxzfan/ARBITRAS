"""Simulated terrain sensor (TRACK_E.md E2).

There is no sensor. This stands in with a confusion matrix M[c_true, c_read]:
at each epoch it draws a hard reading s ~ M[c_true, :] and emits the posterior
a calibrated sensor would report, Bayes with a flat prior over the drawn
column: p_s(c) = M[c, s] / sum_c' M[c', s]. Seeded, so reset() replays the
identical draw sequence — the clean and injected replays then differ in the
terrain channel ONLY through the believed position.

Every describe() carries source "simulated". The diagonal of M is a swept
parameter (TRACK_E.md "Measurement plan"); this module has no default for it.
"""
from __future__ import annotations

import numpy as np


def confusion_from_diag(k: int, diag: float) -> np.ndarray:
    """Symmetric confusion: `diag` on the diagonal, the rest spread evenly."""
    if not 0.0 < diag <= 1.0:
        raise ValueError(f"diag must be in (0, 1], got {diag}")
    if k < 2:
        raise ValueError("need at least two classes")
    off = (1.0 - diag) / (k - 1)
    m = np.full((k, k), off)
    np.fill_diagonal(m, diag)
    return m


class ConfusionSensor:
    def __init__(self, M, classes: list[str], seed: int = 20260820,
                 footprint_m: float = 0.0, sensor_id: str = "sim-confusion-v0"):
        m = np.asarray(M, dtype=float)
        if m.ndim != 2 or m.shape[0] != m.shape[1] or m.shape[0] != len(classes):
            raise ValueError(f"M must be {len(classes)}x{len(classes)}, got {m.shape}")
        if (m < 0).any() or not np.allclose(m.sum(axis=1), 1.0):
            raise ValueError("M must be row-stochastic")
        self.M = m
        self.classes = list(classes)
        self.seed = seed
        self.footprint_m = float(footprint_m)
        self.sensor_id = sensor_id
        self.last_reading: int | None = None
        self.reset()

    def reset(self) -> None:
        self._rng = np.random.default_rng(self.seed)
        self.last_reading = None

    def read(self, true_class: int) -> np.ndarray:
        s = int(self._rng.choice(len(self.classes), p=self.M[int(true_class)]))
        self.last_reading = s
        col = self.M[:, s]
        return col / col.sum()

    def describe(self) -> dict:
        return {"id": self.sensor_id, "source": "simulated",
                "footprint_m": self.footprint_m, "k": len(self.classes),
                "confusion_diag": float(np.mean(np.diag(self.M)))}
