"""Weighted-RAIM protection level: per-satellite fault slopes and PL.

TRACK_D.md "The math" / agent D2. The slope form is the analytic
displacement bound of design.md §6b evaluated on the corrected solution;
design.md §10 pairs it with the empirical sweep as a consistency check.

    slope_i = ||S[0:3, i]|| / sqrt(P_ii)    [m displacement per m of
                                             undetected residual]
    PL      = tau_m * max_i slope_i          (single fault)
    PL_k    = tau_m * sum of k largest       (k-fault, k from §7 scenario)

S = G^-1 H^T W is the weighted estimator ((3+k) x n) and P = I - H S the
residual projection (n x n), both from the weighted solve (D1). Pure
numpy, no I/O. tau_m is Track A's per-satellite residual threshold; the
caller owns its provenance.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# P_ii at or below this is "no self-residual": the other satellites cannot
# see a bias on this one at all. P_ii = 1 - w_i h_i^T G^-1 h_i is 1 minus
# a leverage of the W^(1/2)-scaled problem, so it lies in [0, 1] exactly
# for weights in [0, 1]; it only approaches 0 when a satellite alone
# determines some state direction. 1e-10 sits ~3 orders of magnitude above
# the rounding noise of the (3+k)-state solve (O(1) entries, float64,
# ~1e-13) and far below any physically monitorable leverage, so it
# separates "unmonitorable" from "poorly monitored" without tuning.
EPS_PII = 1e-10


def slopes(S: np.ndarray, P: np.ndarray) -> np.ndarray:
    """Per-satellite slope_i = ||S[0:3, i]|| / sqrt(P_ii) (TRACK_D D2).

    Edges are derived, not patched:
    - w_i = 0 needs no special case: S[:, i] = w_i * G^-1 h_i = 0 and
      P_ii = 1, so the formula itself yields 0 — excluded satellites
      cannot displace the fix.
    - P_ii <= EPS_PII with a nonzero position column: unmonitorable
      (nobody checks it, but it moves the position). Slope is an honest
      +inf for the gate to see — never NaN, never a silent large float.
    - P_ii <= EPS_PII with a ~zero position column: its bias is absorbed
      entirely by clock states; zero position displacement, slope 0.
    """
    S = np.asarray(S, dtype=float)
    P = np.asarray(P, dtype=float)
    pos = np.linalg.norm(S[0:3, :], axis=0)          # metres per metre
    pii = np.diagonal(P).astype(float)

    out = np.zeros_like(pos)
    monitored = pii > EPS_PII
    out[monitored] = pos[monitored] / np.sqrt(pii[monitored])
    out[~monitored] = np.where(pos[~monitored] > EPS_PII, np.inf, 0.0)
    return out


@dataclass(frozen=True)
class ProtectionLevel:
    """Slope-form protection level (TRACK_D.md "The math")."""

    slopes: np.ndarray  # per-satellite, row order of S/P columns
    pl_m: float         # tau_m * max slope (single fault)
    pl_k_m: float       # tau_m * sum of k_faults largest slopes
    k_faults: int
    tau_m: float


def protection_level(S: np.ndarray, P: np.ndarray, tau_m: float,
                     k_faults: int = 1) -> ProtectionLevel:
    """PL = tau_m * max slope; PL_k = tau_m * (k largest slopes summed).

    tau_m must be finite and > 0 (0 * inf would manufacture a NaN, which
    the slope contract forbids). k_faults in [1, n].
    """
    tau_m = float(tau_m)
    if not np.isfinite(tau_m) or tau_m <= 0.0:
        raise ValueError(f"tau_m must be finite and > 0, got {tau_m}")
    sl = slopes(S, P)
    if not 1 <= k_faults <= sl.size:
        raise ValueError(f"k_faults must be in [1, {sl.size}], got {k_faults}")
    ordered = np.sort(sl)[::-1]
    return ProtectionLevel(
        slopes=sl,
        pl_m=float(tau_m * ordered[0]),
        pl_k_m=float(tau_m * ordered[:k_faults].sum()),
        k_faults=int(k_faults),
        tau_m=tau_m,
    )
