"""D1 acceptance tests (TRACK_D.md) on the hand-written fixture.

fixtures/epoch_obs.csv: 8 GPS + 4 Galileo, exact pseudoranges = geometric
range from RX_ECEF + per-constellation clock {G: 3000 m, E: 2000 m}, zero
noise (fixtures/make_epoch_obs.py). H comes from Track C's build_H unchanged.

The unweighted reference (test 1) is an explicit lstsq on Track C's own
linearisation — H[:, :3] = -LOS, per-constellation clock columns, exactly
what geometry/solve.py wls_fix builds each iteration. wls_fix itself operates
on higher-level inputs (broadcast nav, Sagnac, elevation weighting) and
cannot be called on the Sagnac-free fixture, so the convention is compared,
not the wrapper.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from backend.correction.solve import (RankDeficientError, WlsSolution,
                                      weighted_solve)
from backend.geometry.hmatrix import build_H

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "epoch_obs.csv"
RX_ECEF = np.array([1112161.8802, -4842854.4026, 3985497.383])
CLOCK_M = {"G": 3000.0, "E": 2000.0}     # fixtures/make_epoch_obs.py


@pytest.fixture(scope="module")
def fx():
    """(sat_pos dict, pr dict) from the committed fixture."""
    df = pd.read_csv(FIXTURE)
    sat = {r.sv: np.array([r.sat_x, r.sat_y, r.sat_z])
           for r in df.itertuples()}
    pr = {r.sv: float(r.pr_m) for r in df.itertuples()}
    return sat, pr


def _solve_inputs(sat, pr, x_lin):
    """(H from build_H, sv order, const order, d_rho at x_lin)."""
    h, svs, consts = build_H(sat, x_lin)
    d_rho = np.array([pr[sv] - np.linalg.norm(sat[sv] - x_lin)
                      for sv in svs])
    return h, svs, consts, d_rho


# ------------------------------------------------------------------ test 1
def test_unit_weights_match_track_c_unweighted(fx):
    """w = ones: dx and G match Track C's linearisation (lstsq) to 1e-9."""
    sat, pr = fx
    h, svs, consts, d_rho = _solve_inputs(sat, pr, RX_ECEF)
    sol = weighted_solve(h, np.ones(len(svs)), d_rho)

    # Track C convention rebuilt independently: -LOS, one-hot clock columns
    # (geometry/solve.py wls_fix: H[:, :3] = -d/rho).
    href = np.zeros_like(h)
    col = {c: 3 + j for j, c in enumerate(consts)}
    for i, sv in enumerate(svs):
        d = sat[sv] - RX_ECEF
        href[i, :3] = -d / np.linalg.norm(d)
        href[i, col[sv[0]]] = 1.0
    dx_ref, *_ = np.linalg.lstsq(href, d_rho, rcond=None)

    assert np.allclose(sol.dx, dx_ref, rtol=0, atol=1e-9)
    assert np.allclose(sol.G, href.T @ href, rtol=0, atol=1e-9)
    assert sol.n_eff == pytest.approx(len(svs))
    assert sol.dof == pytest.approx(len(svs) - (3 + len(consts)))


# ------------------------------------------------------------------ test 2
def test_exact_recovery_clean_fixture(fx):
    """x_lin = truth: dx[0:3] ~ 0, clocks recover (3000 G, 2000 E) to 1e-6."""
    sat, pr = fx
    h, svs, consts, d_rho = _solve_inputs(sat, pr, RX_ECEF)
    sol = weighted_solve(h, np.ones(len(svs)), d_rho)

    assert np.allclose(sol.dx[:3], 0.0, atol=1e-6)
    clocks = dict(zip(consts, sol.dx[3:]))
    assert clocks["G"] == pytest.approx(CLOCK_M["G"], abs=1e-6)
    assert clocks["E"] == pytest.approx(CLOCK_M["E"], abs=1e-6)


# ------------------------------------------------------------------ test 3
def test_displaced_receiver_sign(fx):
    """5 m east of x_lin: x_lin + dx[0:3] recovers truth to 1e-6 m.

    Proves dx[0:3] is the receiver displacement Delta, not its negation
    (module docstring sign convention). A flipped sign lands 10 m out.
    """
    sat, _ = fx
    up = RX_ECEF / np.linalg.norm(RX_ECEF)
    east = np.cross([0.0, 0.0, 1.0], up)
    east /= np.linalg.norm(east)                 # hmatrix local frame
    x_true = RX_ECEF + 5.0 * east

    pr = {sv: np.linalg.norm(p - x_true) + CLOCK_M[sv[0]]
          for sv, p in sat.items()}
    h, svs, _, d_rho = _solve_inputs(sat, pr, RX_ECEF)  # H built at x_lin
    sol = weighted_solve(h, np.ones(len(svs)), d_rho)

    assert np.linalg.norm(RX_ECEF + sol.dx[:3] - x_true) < 1e-6


# ------------------------------------------------------------------ test 4
def test_binary_exclusion_matches_reduced_solve(fx):
    """w = 0 on two GPS SVs == solving on H with those rows removed."""
    sat, pr = fx
    h, svs, consts, d_rho = _solve_inputs(sat, pr, RX_ECEF)
    drop = {"G04", "G18"}
    w = np.array([0.0 if sv in drop else 1.0 for sv in svs])
    sol = weighted_solve(h, w, d_rho)

    sat_red = {sv: p for sv, p in sat.items() if sv not in drop}
    h_red, svs_red, consts_red, d_red = _solve_inputs(sat_red, pr, RX_ECEF)
    assert consts_red == consts                  # both constellations survive
    sol_red = weighted_solve(h_red, np.ones(len(svs_red)), d_red)

    assert np.allclose(sol.dx, sol_red.dx, rtol=1e-12, atol=1e-9)
    assert np.allclose(sol.G, sol_red.G, rtol=1e-12, atol=1e-9)
    keep = [i for i, sv in enumerate(svs) if sv not in drop]
    assert np.allclose(sol.S[:, keep], sol_red.S, rtol=1e-12, atol=1e-12)
    assert np.allclose(sol.r[keep], sol_red.r, rtol=0, atol=1e-6)
    # excluded SVs cannot move the fix: their S columns are exactly zero
    excl = [i for i, sv in enumerate(svs) if sv in drop]
    assert np.all(sol.S[:, excl] == 0.0)
    assert sol.n_eff == pytest.approx(len(svs) - len(drop))


# ------------------------------------------------------------------ test 5
def test_rank_deficiency_raises(fx):
    """GOTCHA 2a: dead constellation and insufficient n_eff both raise."""
    sat, pr = fx
    h, svs, _, d_rho = _solve_inputs(sat, pr, RX_ECEF)

    # every Galileo SV at w == 0 -> E clock column unobservable
    w = np.array([0.0 if sv.startswith("E") else 1.0 for sv in svs])
    with pytest.raises(RankDeficientError):
        weighted_solve(h, w, d_rho)

    # all columns observable but n_eff = 3.6 <= 3+k = 5 -> no redundancy
    with pytest.raises(RankDeficientError):
        weighted_solve(h, np.full(len(svs), 0.3), d_rho)


# ------------------------------------------------------------------ test 6
def test_residual_projection(fx):
    """Clean r ~ 0; +30 m on one SV shows up as r_i = 30 * P_ii; P H = 0."""
    sat, pr = fx
    h, svs, _, d_rho = _solve_inputs(sat, pr, RX_ECEF)
    w = np.ones(len(svs))

    clean = weighted_solve(h, w, d_rho)
    assert np.allclose(clean.r, 0.0, atol=1e-6)

    i = svs.index("G07")
    d_bad = d_rho.copy()
    d_bad[i] += 30.0
    bad = weighted_solve(h, w, d_bad)
    assert bad.r[i] == pytest.approx(30.0 * bad.P[i, i], abs=1e-6)
    assert np.abs(bad.r).max() > 1.0             # the fault is visible
    assert int(np.argmax(np.abs(bad.r))) == i    # and lands on the right SV

    # P annihilates the model space: P @ H == 0 (both column-sign conventions)
    assert np.allclose(bad.P @ h, 0.0, atol=1e-9)
    # and dx = S @ d_rho by construction
    assert np.allclose(bad.dx, bad.S @ d_bad, atol=1e-9)


# ------------------------------------------------------------------ misc
def test_solution_is_frozen_dataclass(fx):
    sat, pr = fx
    h, svs, _, d_rho = _solve_inputs(sat, pr, RX_ECEF)
    sol = weighted_solve(h, np.ones(len(svs)), d_rho)
    assert isinstance(sol, WlsSolution)
    with pytest.raises(AttributeError):
        sol.dx = None  # type: ignore[misc]


def test_shape_and_weight_validation(fx):
    sat, pr = fx
    h, svs, _, d_rho = _solve_inputs(sat, pr, RX_ECEF)
    with pytest.raises(ValueError):
        weighted_solve(h, np.ones(len(svs) - 1), d_rho)
    w = np.ones(len(svs))
    w[0] = -0.1
    with pytest.raises(ValueError):
        weighted_solve(h, w, d_rho)
