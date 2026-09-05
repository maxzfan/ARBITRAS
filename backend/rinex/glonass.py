"""Recover GLONASS FDMA channel numbers from the observations themselves.

GLONASS is the one constellation whose carrier frequency differs per satellite:
G1 = 1602.0 MHz + k * 562.5 kHz, k in -7..6. The channel number k lives in the
nav file, not the observation file, so bands.py has to assume a nominal k = 0 —
and that assumption is measurably wrong.

The symptom: code-minus-carrier is formed as `C - lambda * L`. Getting lambda
wrong by a fraction eps turns the satellite's own range rate into a spurious
CMC rate, `eps * range_rate`. GLONASS range rate reaches ~800 m/s, so over a
30 s epoch a 0.23% wavelength error (k = +-7 assumed as 0) injects tens of
metres of fake divergence. Measured on the clean USN8 day that is exactly what
appears: median epoch-to-epoch CMC sigma of 14.0 m for R against 0.18 m for G.

The fix needs no nav file. k is a small integer and the wrong k is loud, so
sweep all fourteen and keep whichever minimises the CMC difference noise. The
result is checkable against the nav file's frequency numbers, and the margin
between best and runner-up says how confident the fit is.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import bands

CHANNELS = range(-7, 7)


def _cmc_noise(code: pd.Series, phase: pd.Series, lam: float) -> float:
    """Robust epoch-to-epoch scatter of code-minus-carrier for one wavelength."""
    d = (code - phase * lam).diff()
    d = d[np.isfinite(d)]
    if len(d) < 10:
        return np.inf
    return float(1.4826 * (d - d.median()).abs().median())


def fit_channels(epochs, min_epochs: int = 60) -> dict:
    """{sv: (k, noise_m, margin)} for every GLONASS satellite in the stream.

    `margin` is the ratio of the runner-up channel's residual noise to the
    winner's. A clean fit is a large margin; anything near 1 means the sweep
    could not tell two channels apart and the result should not be trusted.
    """
    code = pd.DataFrame({ep.time: ep.df["code_1"] for ep in epochs}).T.sort_index()
    phase = pd.DataFrame({ep.time: ep.df["phase_1"] for ep in epochs}).T.sort_index()
    rsv = [s for s in code.columns if s.startswith("R")]

    fits = {}
    for sv in rsv:
        c, p = code[sv], phase[sv]
        if c.notna().sum() < min_epochs:
            continue
        noise = np.array([_cmc_noise(c, p, bands.wavelengths("R", k)[0])
                          for k in CHANNELS])
        order = np.argsort(noise)
        best, second = order[0], order[1]
        if not np.isfinite(noise[best]):
            continue
        margin = float(noise[second] / noise[best]) if noise[best] > 0 else np.inf
        fits[sv] = (int(list(CHANNELS)[best]), float(noise[best]), margin)
    return fits


def apply_channels(epochs, fits: dict) -> None:
    """Rewrite lam_1/lam_2 and phase_m_* in place for the fitted GLONASS SVs."""
    lam = {sv: bands.wavelengths("R", k) for sv, (k, _, _) in fits.items()}
    for ep in epochs:
        idx = [s for s in ep.df.index if s in lam]
        if not idx:
            continue
        ep.df.loc[idx, "lam_1"] = [lam[s][0] for s in idx]
        ep.df.loc[idx, "lam_2"] = [lam[s][1] for s in idx]
        for b in (1, 2):
            ep.df.loc[idx, f"phase_m_{b}"] = (ep.df.loc[idx, f"phase_{b}"]
                                              * ep.df.loc[idx, f"lam_{b}"])
