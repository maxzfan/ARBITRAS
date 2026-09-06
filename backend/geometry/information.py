"""Fisher-information geometry score: normalised det ratio, analytic
displacement bound, next-best-observation ranking.

Whiteboard derivations (the two-minute rule):

Information ratio — H'H is the Fisher information of the position solution
(DOP, as actually defined). det scales with the (3+k)th power of geometry,
so the raw ratio collapses to 0.00 on screen after two exclusions. We report
the per-state geometric mean det(H'H)^(1/(3+k)) — the D-optimality /
GDOP-volume form — for each matrix at its own dimensionality, then the
ratio. Legible across the exclusion range, no free parameter.

Displacement bound — an undetected attack must keep the trusted residuals
inside the chi-square acceptance region ||H x||^2 <= T sigma^2 with
T = chi2.ppf(1-alpha, n-d). The largest position displacement satisfying
that lies along the max-eigenvalue direction of the position marginal
covariance inv(H'H)[:3,:3], giving sigma * sqrt(T * lambda_max).
sigma_UERE is the measured clean-day residual RMS; alpha is the detector's
own residual-test significance. No guessed numbers. Note the two honest,
opposing effects as SVs are excluded: lambda_max grows monotonically
(information decreases — a theorem), while T shrinks with residual DOF
(a smaller acceptance region constrains the attacker more). Geometry
dominates; the bound widens as trust erodes.
    Successor note (TRACK_D.md, deferred): the weighted-RAIM slope form
PL = tau * max_i(||S[0:3,i]|| / sqrt(P_ii)) computed on the corrected
solution is the planned replacement for this eigenvalue form — one
definition of the bound, reconciled when backend/correction/ lands.
Until then this is the single source.

Next-best observation — adding a row is the rank-one update
det(G + hh') = det(G) (1 + h' G^-1 h), so ranking candidates is one
quadratic form each. Borrowed from active view selection (CONVERGE,
Chen/Dai/Adang/Gao/Schwager, Stanford). A lone SV from a constellation
whose clock column was dropped adds zero net information (it is spent
estimating its own clock bias) — the block-determinant form shows this,
so dropped constellations are ranked by readmitting their whole visible
set at the per-state scale.
"""
from __future__ import annotations

import numpy as np
from scipy.stats import chi2

from backend.geometry.hmatrix import build_H


def norm_info(h: np.ndarray) -> float:
    """Per-state geometric-mean information: det(H'H)^(1/dim).

    0.0 if underdetermined (rows < cols) or det <= 0.
    """
    n, d = h.shape
    if n < d:
        return 0.0
    det = np.linalg.det(h.T @ h)
    if det <= 0.0:
        return 0.0
    return float(det ** (1.0 / d))


def information_ratio(h_trusted: np.ndarray, h_full: np.ndarray) -> float:
    """Normalised information ratio, clipped to [0, 1]."""
    full = norm_info(h_full)
    if full <= 0.0:
        return 0.0
    return float(np.clip(norm_info(h_trusted) / full, 0.0, 1.0))


def rank_one_gain(g_inv: np.ndarray, h_row: np.ndarray) -> float:
    """det(G + hh') / det(G) = 1 + h' G^-1 h  (the CONVERGE identity)."""
    return float(1.0 + h_row @ g_inv @ h_row)


def displacement_bound_m(h_trusted: np.ndarray, sigma_uere_m: float,
                         alpha: float = 1e-3) -> float | None:
    """Analytic bound on undetected position displacement, metres.

    None when the trusted geometry is not overdetermined (n <= 3+k) —
    no residual test exists, so no bound. The console treats None as
    "no bound available", which is itself alarming, correctly.
    """
    n, d = h_trusted.shape
    if n <= d:
        return None
    g = h_trusted.T @ h_trusted
    if np.linalg.det(g) <= 0.0:
        return None
    p_pos = np.linalg.inv(g)[:3, :3]
    t = chi2.ppf(1.0 - alpha, df=n - d)
    return float(sigma_uere_m * np.sqrt(t * np.linalg.eigvalsh(p_pos)[-1]))


def next_best_observation(sat_pos_visible: dict[str, np.ndarray],
                          trusted_sv: list[str],
                          rx_ecef: np.ndarray) -> str | None:
    """Constellation whose readmission recovers the most information.

    Candidates are visible-but-untrusted SVs, grouped by constellation.
    Gain is the per-state normalised information of H(trusted + group)
    over H(trusted) — computed with whole groups so that re-adding a
    dropped constellation's clock column is handled exactly.
    """
    trusted = {sv: sat_pos_visible[sv] for sv in trusted_sv
               if sv in sat_pos_visible}
    candidates = sorted(set(sat_pos_visible) - set(trusted))
    if not candidates or not trusted:
        return None

    h_t, _, _ = build_H(trusted, rx_ecef)
    base = norm_info(h_t)
    if base <= 0.0:
        return None

    gains: dict[str, float] = {}
    for const in sorted({sv[0] for sv in candidates}):
        group = {sv: sat_pos_visible[sv] for sv in candidates
                 if sv[0] == const}
        h_aug, _, _ = build_H({**trusted, **group}, rx_ecef)
        gains[const] = norm_info(h_aug) / base

    best = max(gains, key=gains.get)
    return best if gains[best] > 1.0 else None
