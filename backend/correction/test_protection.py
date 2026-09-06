"""Tests for the weighted-RAIM protection level (TRACK_D.md D2).

S and P are built locally from fixtures/epoch_obs.csv via
backend.geometry.hmatrix.build_H and explicit numpy — deliberately NOT
via backend.correction.solve (D1, written in parallel; protection.py
takes plain arrays and must be testable without it).
"""
from __future__ import annotations

import csv
import math
from pathlib import Path

import numpy as np
import pytest

from backend.correction.protection import (EPS_PII, ProtectionLevel,
                                           protection_level, slopes)
from backend.geometry.hmatrix import build_H

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "epoch_obs.csv"
RX_ECEF = np.array([1112161.8802, -4842854.4026, 3985497.383])


def load_fixture_H() -> tuple[np.ndarray, list[str]]:
    """H over the fixture epoch (12 SVs, GPS+GAL -> n x 5, TRACK_C GOTCHA 2a)."""
    sat_pos = {}
    with open(FIXTURE) as f:
        for row in csv.DictReader(f):
            sat_pos[row["sv"]] = np.array([float(row["sat_x"]),
                                           float(row["sat_y"]),
                                           float(row["sat_z"])])
    H, sv_ids, _consts = build_H(sat_pos, RX_ECEF)
    return H, sv_ids


def solve_SP(H: np.ndarray, w: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Weighted LS matrices per TRACK_D.md "The math". Local on purpose."""
    W = np.diag(w)
    G = H.T @ W @ H
    S = np.linalg.inv(G) @ H.T @ W
    P = np.eye(H.shape[0]) - H @ S
    return S, P


def enu_unit(az_deg: float, el_deg: float) -> np.ndarray:
    """Unit LOS in a local ENU frame (synthetic geometries only)."""
    az, el = math.radians(az_deg), math.radians(el_deg)
    return np.array([math.cos(el) * math.sin(az),
                     math.cos(el) * math.cos(az),
                     math.sin(el)])


def synthetic_H(azel: list[tuple[float, float]]) -> np.ndarray:
    """Single-constellation H: [unit LOS | 1] rows, ENU frame."""
    return np.column_stack([np.array([enu_unit(a, e) for a, e in azel]),
                            np.ones(len(azel))])


# 1. Hand-computed match ----------------------------------------------------

def test_hand_computed_slope_matches_fixture():
    """slope_i for one fixture SV, arithmetic spelled out independently.

    Independent route: S[:, i] = w_i * G^-1 h_i and P_ii = 1 - w_i h_i^T
    G^-1 h_i (TRACK_D "The math"), computed via np.linalg.solve rather
    than the S/P matrix products fed to slopes().
    """
    H, sv_ids = load_fixture_H()
    n = H.shape[0]
    S, P = solve_SP(H, np.ones(n))
    got = slopes(S, P)

    i = sv_ids.index("G04")
    G = H.T @ H                      # W = I: all weights one
    h_i = H[i, :]
    g_i = np.linalg.solve(G, h_i)    # G^-1 h_i, the i-th S column
    # explicit norm of the three position components
    norm_pos = math.sqrt(g_i[0] * g_i[0] + g_i[1] * g_i[1] + g_i[2] * g_i[2])
    # explicit residual-projection diagonal
    p_ii = 1.0 - (h_i[0] * g_i[0] + h_i[1] * g_i[1] + h_i[2] * g_i[2]
                  + h_i[3] * g_i[3] + h_i[4] * g_i[4])
    slope_hand = norm_pos / math.sqrt(p_ii)

    assert got[i] == pytest.approx(slope_hand, rel=1e-12)
    # regression anchor: measured on the committed fixture
    assert 0.46 < got[i] < 0.48


# 2. Near-collinear geometry ------------------------------------------------

def test_near_collinear_pair_carries_largest_slopes():
    """A near-collinear pair straddling the rest's plane owns the max slope.

    Four satellites sit exactly in the az 45/225 vertical plane; the pair
    at (44.75, 10) and (45.25, 10) is 0.5 deg apart and is the ONLY
    observer of the cross-plane direction. A bias on one pair member is
    absorbed by a huge position excursion along that direction, which the
    in-plane satellites cannot see: the others cannot check the pair, its
    P_ii is small (~0.08 measured vs ~0.5-0.7 for the rest), and its
    slope is two orders of magnitude above everyone else's.
    """
    azel = [(45.0, 80.0), (45.0, 35.0), (225.0, 70.0), (225.0, 25.0),
            (44.75, 10.0), (45.25, 10.0)]
    H = synthetic_H(azel)
    S, P = solve_SP(H, np.ones(6))
    sl = slopes(S, P)
    pair = {4, 5}

    assert int(np.argmax(sl)) in pair
    assert set(np.argsort(sl)[-2:]) == pair
    # the pair dwarfs the well-checked satellites (measured ~421 vs ~3)
    assert min(sl[4], sl[5]) > 10.0 * max(sl[:4])
    # and the mechanism is small P_ii, not a large S column alone
    assert P[4, 4] < 0.1 and P[5, 5] < 0.1


# 3. Monotonicity under down-weighting --------------------------------------

def test_downweighted_satellite_slope_monotone_to_zero():
    """Excluding a satellite shrinks the PL of ITS fault hypothesis to 0.

    TRACK_D acceptance ("satellites with w near 0 have near-zero slope:
    excluding them shrinks PL") holds per fault hypothesis: as w_i steps
    1.0 -> 0.0, slope_i = w_i ||G^-1 h_i|| / sqrt(P_ii) is monotone
    non-increasing and lands at exactly 0 — an excluded satellite cannot
    displace the fix. The GLOBAL max-slope PL is provably NOT monotone
    in any single weight: down-weighting removes Fisher information, so
    G^-1 grows and every OTHER slope inflates (measured on this fixture:
    sweeping G18 to w=0 moves PL 1.09 -> 1.50 m/m as E24/G31 slopes
    grow). Asserting global monotonicity would enshrine a false claim;
    test_global_pl_not_monotone_documented pins the counterexample.
    """
    H, sv_ids = load_fixture_H()
    n = H.shape[0]
    steps = [1.0, 0.75, 0.5, 0.25, 0.1, 0.0]

    for i in range(n):  # every satellite of the fixture epoch
        own = []
        for wi in steps:
            w = np.ones(n)
            w[i] = wi
            S, P = solve_SP(H, w)
            own.append(slopes(S, P)[i])
        for a, b in zip(own, own[1:]):
            assert b <= a + 1e-12, f"{sv_ids[i]}: slope rose {a} -> {b}"
        assert own[0] > 0.0
        assert own[-1] == 0.0  # exact: S column and P_ii edge, no eps needed


def test_global_pl_not_monotone_documented():
    """Measured counterexample: global PL rises when G18 is excluded.

    Kept as a test so the fact is not silently 'fixed': information loss
    is real and the gate's redundancy check (D3) exists because of it.
    """
    H, sv_ids = load_fixture_H()
    n = H.shape[0]
    i = sv_ids.index("G18")

    w = np.ones(n)
    pl_full = protection_level(*solve_SP(H, w), tau_m=1.0).pl_m
    w[i] = 0.0
    pl_excl = protection_level(*solve_SP(H, w), tau_m=1.0).pl_m
    assert pl_excl > pl_full  # 1.50 > 1.09 measured


# 4. Edge cases -------------------------------------------------------------

def test_excluded_satellite_slope_exactly_zero():
    """w_i = 0: S column is exactly 0 and P_ii exactly 1 -> slope 0.0."""
    H, sv_ids = load_fixture_H()
    n = H.shape[0]
    w = np.ones(n)
    i = sv_ids.index("E11")
    w[i] = 0.0
    S, P = solve_SP(H, w)

    assert np.allclose(S[:, i], 0.0)
    assert P[i, i] == pytest.approx(1.0)
    assert slopes(S, P)[i] == 0.0


def test_unmonitorable_satellite_is_honest_infinity():
    """Sole observer of a direction: P_ii = 0, slope +inf, pl_m +inf.

    Four satellites span E/W/N/S at the horizon; the fifth alone
    observes up. Its residual is fully absorbed by the fit (P_55 = 0
    exactly, below EPS_PII) while its S position column is nonzero, so
    an undetected bias displaces the fix without bound. The gate must
    see np.inf — never NaN, never a large float.
    """
    H = np.array([[1.0, 0.0, 0.0, 1.0],
                  [-1.0, 0.0, 0.0, 1.0],
                  [0.0, 1.0, 0.0, 1.0],
                  [0.0, -1.0, 0.0, 1.0],
                  [0.0, 0.0, 1.0, 1.0]])
    S, P = solve_SP(H, np.ones(5))
    sl = slopes(S, P)

    assert abs(P[4, 4]) <= EPS_PII
    assert np.linalg.norm(S[0:3, 4]) > 0.0
    assert np.isinf(sl[4]) and sl[4] > 0
    assert not np.any(np.isnan(sl))

    pl = protection_level(S, P, tau_m=5.0)
    assert np.isinf(pl.pl_m)
    assert not math.isnan(pl.pl_m)


# 5. pl_k -------------------------------------------------------------------

def test_pl_k_sums_k_largest_slopes():
    """k=1 equals pl_m; k=2 equals tau * (two largest slopes summed)."""
    H, _sv_ids = load_fixture_H()
    S, P = solve_SP(H, np.ones(H.shape[0]))
    sl = np.sort(slopes(S, P))[::-1]
    tau = 2.5

    p1 = protection_level(S, P, tau_m=tau, k_faults=1)
    assert isinstance(p1, ProtectionLevel)
    assert p1.pl_k_m == pytest.approx(p1.pl_m)
    assert p1.pl_m == pytest.approx(tau * sl[0])

    p2 = protection_level(S, P, tau_m=tau, k_faults=2)
    assert p2.pl_m == pytest.approx(p1.pl_m)  # single-fault PL unchanged
    assert p2.pl_k_m == pytest.approx(tau * (sl[0] + sl[1]))
    assert p2.k_faults == 2 and p2.tau_m == tau


def test_protection_level_rejects_bad_inputs():
    """tau_m <= 0 or non-finite would manufacture NaN (0 * inf); k in [1, n]."""
    H, _sv_ids = load_fixture_H()
    S, P = solve_SP(H, np.ones(H.shape[0]))
    with pytest.raises(ValueError):
        protection_level(S, P, tau_m=0.0)
    with pytest.raises(ValueError):
        protection_level(S, P, tau_m=float("inf"))
    with pytest.raises(ValueError):
        protection_level(S, P, tau_m=1.0, k_faults=0)
    with pytest.raises(ValueError):
        protection_level(S, P, tau_m=1.0, k_faults=H.shape[0] + 1)
