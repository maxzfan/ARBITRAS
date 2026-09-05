"""Weighted least-squares corrector solve — TRACK_D.md D1, "The math".

Pure numpy, no I/O. Consumes Track C's H unchanged (backend/geometry/hmatrix
build_H: rows sorted by sv id, row_i = [u_x, u_y, u_z, one-hot clock column],
u = unit LOS receiver -> satellite, clock columns per constellation in sorted
order, empty-constellation columns already dropped — TRACK_C.md GOTCHA 2a).

## Sign convention

d_rho_i = observed pseudorange - predicted range at x_lin. The range model is
rho_i(x) = |s_i - x|, so d(rho)/dx = -u_i and a receiver displaced by Delta
from x_lin gives d_rho_i ~= -u_i . Delta + b_{sys(i)}. build_H carries +u, so
this module negates the three LOS columns internally (the same linearisation
Track C's wls_fix writes as H[:, :3] = -d/rho) and solves in the state
[Delta; b]. Therefore **dx[0:3] IS the receiver displacement** and

    corrected_position = x_lin + dx[0:3]        (TRACK_D.md, verbatim)

with dx[3:] the per-constellation receiver clock biases in metres, ordered as
H's clock columns. Callers pass Track C's H as-is. S and G are expressed in
the same [Delta; b] state; P and r are convention-invariant (P = I - H S is
unchanged by the column sign flip), so D2's slope ||S[0:3, i]|| / sqrt(P_ii)
reads straight off this solution.

## Math (TRACK_D.md, exactly)

    W = diag(w),  G = H^T W H,  S = G^-1 H^T W,
    dx = S @ d_rho,  P = I - H S,  r = P @ d_rho

## Rank handling (TRACK_C.md GOTCHA 2a)

The weighted system must support the full (3+k) state. Raise
RankDeficientError — never return garbage — when any column of sqrt(W) H is
~zero (every SV of some constellation has w == 0: its clock state is
unobservable), when rank(sqrt(W) H) < 3+k, or when n_eff = sum(w) <= 3+k
(no redundancy; the residual projection means nothing).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

_COL_TOL = 1e-9          # column of sqrt(W)H below this norm is unobservable


class RankDeficientError(ValueError):
    """The weighted system cannot support the (3+k) state (GOTCHA 2a)."""


@dataclass(frozen=True)
class WlsSolution:
    """One weighted solve. State is [Delta; clock biases], metres.

    dx    : (3+k,)  dx[0:3] = receiver displacement from x_lin (ECEF metres);
            dx[3:] = per-constellation clock biases (metres), H column order.
    S     : (3+k, n) solution matrix, dx = S @ d_rho. Column i is zero when
            w_i == 0 — an excluded SV cannot move the fix.
    P     : (n, n)  residual projection I - H S; P @ H == 0.
    r     : (n,)    post-fit residuals P @ d_rho, metres.
    G     : (3+k, 3+k) weighted information matrix H^T W H ([Delta; b] state).
    n_eff : float   sum of w — effective observation count.
    dof   : float   n_eff - (3+k) — redundancy of the residual test.
    """
    dx: np.ndarray
    S: np.ndarray
    P: np.ndarray
    r: np.ndarray
    G: np.ndarray
    n_eff: float
    dof: float


def weighted_solve(H: np.ndarray, w: np.ndarray,
                   d_rho: np.ndarray) -> WlsSolution:
    """TRACK_D.md weighted solve on Track C's H. See module docstring.

    H (n x (3+k)) from build_H, w (n,) trust weights in [0, 1], d_rho (n,)
    observed pseudorange minus predicted range at x_lin, satellite clock
    already removed, same row order as H.
    """
    H = np.asarray(H, dtype=float)
    w = np.asarray(w, dtype=float)
    d_rho = np.asarray(d_rho, dtype=float)
    n, m = H.shape
    if w.shape != (n,) or d_rho.shape != (n,):
        raise ValueError(f"shape mismatch: H {H.shape}, w {w.shape}, "
                         f"d_rho {d_rho.shape}")
    if np.any(w < 0.0):
        raise ValueError("negative weights")

    hd = H.copy()
    hd[:, :3] = -hd[:, :3]              # [Delta; b] state — module docstring

    n_eff = float(w.sum())
    a = np.sqrt(w)[:, None] * hd        # sqrt(W) H
    col_norms = np.linalg.norm(a, axis=0)
    if np.any(col_norms < _COL_TOL):
        dead = np.flatnonzero(col_norms < _COL_TOL)
        raise RankDeficientError(
            f"unobservable state column(s) {dead.tolist()}: every supporting "
            f"SV has w == 0 (drop the constellation, do not carry the column)")
    if n_eff <= m:
        raise RankDeficientError(f"n_eff {n_eff:.3f} <= 3+k = {m}: "
                                 f"no redundancy")
    if np.linalg.matrix_rank(a) < m:
        raise RankDeficientError(f"rank(sqrt(W) H) < {m}")

    g = hd.T @ (w[:, None] * hd)                    # H^T W H
    s = np.linalg.solve(g, hd.T * w)                # G^-1 H^T W
    dx = s @ d_rho
    p = np.eye(n) - hd @ s                          # I - H S
    r = p @ d_rho
    return WlsSolution(dx=dx, S=s, P=p, r=r, G=g,
                       n_eff=n_eff, dof=n_eff - m)
