"""Measured noise floor of a clean observation stream.

CLAUDE.md: thresholds derive from observed data, never a guessed number. The
injector's magnitudes (§7) and the detector's normalisation both need a scale
to be expressed against, and that scale is measured here, on the clean day,
before anything is injected.

Everything is measured on the **first difference** along time, per satellite.
Differencing removes any smoothly varying term — the elevation-driven C/N0
trend, the geometric range rate, the ionospheric ramp in code-minus-carrier —
and leaves the epoch-to-epoch noise. For white noise
`std(diff) = sqrt(2) * std(sample)`, so the per-epoch sigma is `std(diff)/sqrt(2)`.

    floor = measure(epochs)
    floor.cn0_sigma      # dB-Hz, epoch to epoch
    floor.cmc_sigma      # metres, epoch to epoch, band 1
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

SQRT2 = np.sqrt(2.0)


@dataclass
class NoiseFloor:
    cn0_sigma: float                     # dB-Hz, pooled over satellites
    cmc_sigma: float                     # metres, band 1
    cn0_sigma_by_sv: pd.Series = field(repr=False, default=None)
    cmc_sigma_by_sv: pd.Series = field(repr=False, default=None)
    cn0_level_by_sv: pd.Series = field(repr=False, default=None)
    n_epochs: int = 0

    def __str__(self) -> str:
        return (f"noise floor over {self.n_epochs} epochs: "
                f"C/N0 sigma {self.cn0_sigma:.3f} dB-Hz, "
                f"code-minus-carrier sigma {self.cmc_sigma:.3f} m")


def panel(epochs, column: str) -> pd.DataFrame:
    """(time x sv) panel of one column across a list of epochs."""
    return pd.DataFrame({ep.time: ep.df[column] for ep in epochs}).T.sort_index()


def cmc_panel(epochs, band: int = 1) -> pd.DataFrame:
    """Code-minus-carrier, metres. Near-constant per satellite per pass."""
    code = panel(epochs, f"code_{band}")
    phase = panel(epochs, f"phase_m_{band}")
    return code - phase


def _sigma_from_diff(p: pd.DataFrame) -> pd.Series:
    """Per-satellite epoch-to-epoch sigma, robust to cycle slips and gaps.

    Uses the median absolute deviation of the first difference rather than its
    standard deviation: a cycle slip or a re-acquisition puts a single huge
    value in the difference series and would dominate an ordinary std.
    """
    d = p.diff()
    mad = (d - d.median()).abs().median()
    return 1.4826 * mad / SQRT2          # MAD -> Gaussian sigma, then de-difference


def measure(epochs, min_epochs: int = 20) -> NoiseFloor:
    """Measure the noise floor of a clean epoch stream."""
    cn0 = panel(epochs, "cn0_1")
    cmc = cmc_panel(epochs, band=1)

    enough = cn0.notna().sum() >= min_epochs
    cn0, cmc = cn0.loc[:, enough], cmc.loc[:, enough[cmc.columns]]

    cn0_sv = _sigma_from_diff(cn0).dropna()
    cmc_sv = _sigma_from_diff(cmc).dropna()

    return NoiseFloor(
        cn0_sigma=float(cn0_sv.median()),
        cmc_sigma=float(cmc_sv.median()),
        cn0_sigma_by_sv=cn0_sv,
        cmc_sigma_by_sv=cmc_sv,
        cn0_level_by_sv=cn0.median().dropna(),
        n_epochs=len(epochs),
    )
