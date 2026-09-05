"""D5 tests — the pure fit/count helpers of backend/correction/validate.py.

Synthetic arrays only: the heavy data-driven run (`python -m
backend.correction.validate`) stays out of the suite, the same split as
backend/measurement (pure check_epochs tested, replay runs by hand). The
gate-replay test drives the same Gate + evaluate_checks path the contract
uses, on hand-built EpochSol objects — no RINEX, no nav tables.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta

import numpy as np
import pytest

from backend.correction.gate import GRANT_EPOCHS
from backend.correction.validate import (EpochSol, attack_displacement,
                                         availability, distribution,
                                         exceedances, first_index,
                                         fit_threshold, gate_replay,
                                         residual_stat)


# ------------------------------------------------------------ fit_threshold

def test_fit_threshold_is_the_stated_percentile():
    vals = list(range(1, 1001))                      # 1..1000
    assert fit_threshold(vals, q=99.9) == pytest.approx(999.001, abs=1e-6)
    assert fit_threshold(vals, q=50.0) == pytest.approx(500.5)


def test_fit_threshold_ignores_none_and_nonfinite():
    vals = [1.0, None, math.nan, math.inf, 2.0, 3.0]
    assert fit_threshold(vals, q=50.0) == 2.0


def test_fit_threshold_refuses_empty_pool():
    """CLAUDE.md: never a guessed number — an empty pool raises, it does not
    invent a threshold."""
    with pytest.raises(ValueError):
        fit_threshold([])
    with pytest.raises(ValueError):
        fit_threshold([None, math.nan])


def test_distribution_keys_and_values():
    d = distribution([1.0, 2.0, 3.0, 4.0])
    assert d["n"] == 4
    assert d["p50"] == pytest.approx(2.5)
    assert d["max"] == 4.0
    assert distribution([]) == {"n": 0}


# -------------------------------------------------------------- exceedances

def test_exceedances_counts_only_ok_epochs_over_pl():
    err = [1.0, 20.0, 20.0, 5.0]
    pl = [10.0, 10.0, 10.0, 10.0]
    ok = [True, True, False, True]
    # epoch 1: ok and 20 > 10 -> hit. epoch 2: same error but not ok -> no
    # claim. epoch 3: under PL -> pass.
    assert exceedances(err, pl, ok) == [1]


def test_exceedances_skips_missing_and_nonfinite_pl():
    err = [20.0, None, 20.0]
    pl = [None, 10.0, math.inf]
    ok = [True, True, True]
    assert exceedances(err, pl, ok) == []


def test_exceedance_boundary_is_strict():
    """err == PL is NOT a violation: PL claims 'at most PL'."""
    assert exceedances([10.0], [10.0], [True]) == []


# --------------------------------------------------- first_index / availability

def test_first_index():
    assert first_index([False, False, True, True]) == 2
    assert first_index([True, False, True], start=1) == 2
    assert first_index([False, False]) is None
    assert first_index([], start=0) is None


def test_availability():
    assert availability([True, False, True, True]) == 0.75
    assert availability([]) == 0.0


# ------------------------------------------------------------ residual_stat

def _sol(t, r, w, k=2, slopes=(2.0,), err3d=1.0, err_h=0.5):
    r = np.asarray(r, dtype=float)
    w = np.asarray(w, dtype=float)
    return EpochSol(t=t, k=k, svs=[f"G{i:02d}" for i in range(len(r))],
                    w=w, r=r, slopes=np.asarray(slopes, dtype=float),
                    n_eff=float(w.sum()), dof=float(w.sum()) - (3 + k),
                    corrected_ecef=np.zeros(3), err3d_m=err3d, err_h_m=err_h,
                    excluded=[])


def test_residual_stat_matches_hand_arithmetic():
    # 8 SVs, w all 1, k=2: dof = 8 - 5 = 3; r'Wr = 8 * 0.25 = 2.0
    s = _sol(datetime(2026, 8, 20), [0.5] * 8, [1.0] * 8)
    assert residual_stat(s) == pytest.approx(2.0 / 3.0)


def test_residual_stat_none_when_no_redundancy():
    s = _sol(datetime(2026, 8, 20), [0.0] * 5, [1.0] * 5)   # dof == 0
    assert residual_stat(s) is None


# ------------------------------------------------------- attack_displacement

def test_attack_displacement_differential_and_gaps():
    """Identical corrected fixes -> 0 (the pre-onset case: common errors
    cancel in the differential); a pure displacement comes through in 3D;
    epochs either run could not solve carry no claim (None)."""
    from backend.detection.emit import USN8_ECEF
    t0 = datetime(2026, 8, 20)
    base = np.asarray(USN8_ECEF, dtype=float)

    def at(ecef, i):
        s = _sol(t0 + timedelta(seconds=30 * i), [0.1] * 8, [1.0] * 8)
        s.corrected_ecef = np.asarray(ecef, dtype=float)
        return s

    ref = [at(base, 0), at(base, 1), at(base, 2), None]
    inj = [at(base, 0), at(base + [3.0, 0.0, 4.0], 1), None, at(base, 3)]
    d3, dh = attack_displacement(inj, ref)
    assert d3[0] == pytest.approx(0.0, abs=1e-9)
    assert d3[1] == pytest.approx(5.0)               # 3-4-5 in ECEF
    assert dh[1] is not None and dh[1] <= d3[1]      # horizontal <= 3D
    assert d3[2] is None and d3[3] is None


# -------------------------------------------------------------- gate_replay

def _run(sols, tau=3.0, chi2=5.0, drift=10.0):
    return gate_replay(sols, tau_m=tau, chi2_cutoff=chi2, drift_bound_m=drift)


def test_gate_replay_grants_after_hysteresis_and_pl_is_tau_times_slope():
    t0 = datetime(2026, 8, 20)
    sols = [_sol(t0 + timedelta(seconds=30 * i), [0.1] * 8, [1.0] * 8)
            for i in range(GRANT_EPOCHS + 2)]
    out = _run(sols)
    # PL = tau * max slope = 3.0 * 2.0 on every epoch
    assert all(p == pytest.approx(6.0) for p in out["pl"])
    # 10-epoch grant, exactly as the contract gate (D3 acceptance)
    assert out["ok"][:GRANT_EPOCHS - 1] == [False] * (GRANT_EPOCHS - 1)
    assert out["ok"][GRANT_EPOCHS - 1:] == [True] * 3


def test_gate_replay_none_epoch_revokes_and_restarts_run():
    t0 = datetime(2026, 8, 20)
    good = [_sol(t0 + timedelta(seconds=30 * i), [0.1] * 8, [1.0] * 8)
            for i in range(GRANT_EPOCHS)]
    out = _run(good + [None] + good)
    assert out["ok"][GRANT_EPOCHS - 1] is True       # granted
    assert out["ok"][GRANT_EPOCHS] is False          # fail-closed epoch
    assert out["pl"][GRANT_EPOCHS] is None
    # run restarted: grant lands GRANT_EPOCHS after the gap, not before
    assert out["ok"][GRANT_EPOCHS + 1:2 * GRANT_EPOCHS] \
        == [False] * (GRANT_EPOCHS - 1)
    assert out["ok"][2 * GRANT_EPOCHS] is True


def test_gate_replay_residual_failure_flagged_and_revokes():
    t0 = datetime(2026, 8, 20)
    good = [_sol(t0 + timedelta(seconds=30 * i), [0.1] * 8, [1.0] * 8)
            for i in range(GRANT_EPOCHS)]
    # r'Wr/dof = 8*100/3 >> chi2 cutoff 5.0
    bad = _sol(t0 + timedelta(seconds=30 * GRANT_EPOCHS), [10.0] * 8,
               [1.0] * 8)
    out = _run(good + [bad])
    assert out["residual_fail"][-1] is True
    assert out["ok"][-1] is False                    # one epoch revokes


def test_gate_replay_continuity_failure_revokes():
    """The majority-spoof defence (gate check 4): a walked-off but
    self-consistent corrected fix fails against the DR drift bound."""
    t0 = datetime(2026, 8, 20)
    good = [_sol(t0 + timedelta(seconds=30 * i), [0.1] * 8, [1.0] * 8)
            for i in range(GRANT_EPOCHS)]
    walked = _sol(t0 + timedelta(seconds=30 * GRANT_EPOCHS), [0.1] * 8,
                  [1.0] * 8, err3d=50.0)             # > drift bound 10 m
    out = _run(good + [walked])
    assert out["checks"][-1]["continuity"] is False
    assert out["checks"][-1]["residual_test"] is True
    assert out["ok"][-1] is False


def test_gate_replay_unmonitorable_slope_gives_infinite_pl_and_no_grant():
    t0 = datetime(2026, 8, 20)
    sols = [_sol(t0 + timedelta(seconds=30 * i), [0.1] * 8, [1.0] * 8,
                 slopes=(2.0, math.inf))
            for i in range(GRANT_EPOCHS + 2)]
    out = _run(sols)
    assert all(math.isinf(p) for p in out["pl"])
    assert not any(out["ok"])                        # pl_under_al False
