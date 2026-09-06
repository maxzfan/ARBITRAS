"""Gate + hysteresis tests (TRACK_D.md D3 acceptance).

Modelled on console/tests/test_machine.py's invariant-2 tests: drive the
stateful object epoch by epoch and assert the grant lands exactly on the
GRANT_EPOCHS-th consecutive pass and revokes on the first failure.
"""
import math

import pytest

from backend.correction.gate import (
    ALERT_LIMIT_M,
    CHI2_CUTOFF,
    GRANT_EPOCHS,
    Gate,
    GateDecision,
    continuity,
    cross_constellation,
    evaluate_checks,
    pl_under_al,
    redundancy,
    residual_test,
    speed_scale,
)

PASSING = {
    "pl_under_al": True,
    "redundancy": True,
    "residual_test": True,
    "continuity": True,
    "cross_constellation": None,
}
FAILING = dict(PASSING, pl_under_al=False)
ALL_NONE = {k: None for k in PASSING}


def drive(gate, checks, n, pl_m=5.0):
    d = None
    for _ in range(n):
        d = gate.step(checks, pl_m)
    return d


# ------------------------------------------------------------- hysteresis

def test_ten_epoch_grant():
    """correction_ok is False for the first GRANT_EPOCHS-1 passes, True on
    the GRANT_EPOCHS-th. No single epoch can grant."""
    gate = Gate()
    for i in range(GRANT_EPOCHS - 1):
        d = gate.step(PASSING, 5.0)
        assert d.correction_ok is False, f"granted early at pass {i + 1}"
    d = gate.step(PASSING, 5.0)
    assert d.correction_ok is True, "must grant on the 10th consecutive pass"
    # Stays True while passes continue.
    assert gate.step(PASSING, 5.0).correction_ok is True


def test_one_epoch_revoke_and_counter_restart():
    """One failing epoch revokes immediately and zeroes the run: 9 further
    passes stay False, the 10th grants again."""
    gate = Gate()
    drive(gate, PASSING, GRANT_EPOCHS)
    d = gate.step(FAILING, 20.0)
    assert d.correction_ok is False, "one epoch must revoke"
    for i in range(GRANT_EPOCHS - 1):
        d = gate.step(PASSING, 5.0)
        assert d.correction_ok is False, f"counter not reset: granted at {i + 1}"
    assert gate.step(PASSING, 5.0).correction_ok is True


def test_none_checks_do_not_fail_but_all_none_does_not_pass():
    """A null check is 'not evaluated', never a failure — but an all-None
    dict is not a pass either: no evidence, no grant progress."""
    gate = Gate()
    # Nulls alongside all-True non-nulls: passing epochs, grant on time.
    for _ in range(GRANT_EPOCHS):
        d = gate.step(PASSING, 5.0)  # cross_constellation is None throughout
    assert d.correction_ok is True
    # All-None epochs make no grant progress.
    gate.reset()
    drive(gate, ALL_NONE, GRANT_EPOCHS + 5)
    assert gate.step(ALL_NONE, 5.0).correction_ok is False
    # ...and interrupting a run with all-None resets it (fail closed).
    gate.reset()
    drive(gate, PASSING, GRANT_EPOCHS - 1)
    gate.step(ALL_NONE, 5.0)
    d = drive(gate, PASSING, GRANT_EPOCHS - 1)
    assert d.correction_ok is False, "all-None must not preserve the run"


def test_reset_restarts_replay():
    gate = Gate()
    drive(gate, PASSING, GRANT_EPOCHS)
    gate.reset()
    assert gate.step(PASSING, 5.0).correction_ok is False


# ---------------------------------------------------------- check functions

def test_pl_under_al():
    assert pl_under_al(5.0) is True
    assert pl_under_al(ALERT_LIMIT_M) is False          # strict <
    assert pl_under_al(math.inf) is False               # unbounded slope
    assert pl_under_al(None) is None                    # not evaluated


def test_redundancy():
    # 6 SVs trusted (w > 0.5), n_eff = 6.0 >= 3+2+1 with k=2: passes.
    assert redundancy([1.0] * 6, 2) is True
    # Only 4 SVs above the trust floor: fails the count arm.
    assert redundancy([1.0] * 4 + [0.4] * 4, 2) is False
    # 5 trusted but n_eff too low for k=3 (need >= 7): fails the n_eff arm.
    assert redundancy([0.6] * 5, 3) is False


def test_residual_test():
    # An unfit cutoff (None) -> not evaluated; the module default is no
    # longer that — it was fit on the clean day (backend.correction.validate,
    # p99.9 of r'Wr/dof, USN8 2026-08-20). Guard the fitted value so a
    # placeholder regression is caught here, not on the vehicle.
    assert residual_test([0.1] * 8, [1.0] * 8, 2, chi2_cutoff=None) is None
    assert CHI2_CUTOFF == pytest.approx(7.703)
    # Small residuals, healthy dof, generous cutoff: passes.
    assert residual_test([0.1] * 8, [1.0] * 8, 2, chi2_cutoff=1.0) is True
    # Large residuals: fails.
    assert residual_test([10.0] * 8, [1.0] * 8, 2, chi2_cutoff=1.0) is False
    # dof <= 0 (n_eff == 3+k): fail closed, even with zero residuals.
    assert residual_test([0.0] * 5, [1.0] * 5, 2, chi2_cutoff=1.0) is False


def test_continuity():
    assert continuity(0.3, 0.5) is True
    assert continuity(0.6, 0.5) is False
    assert continuity(None, 0.5) is None


def test_cross_constellation_is_null_seam():
    assert cross_constellation() is None
    assert cross_constellation(1, 2, foo=3) is None


def test_evaluate_checks_keys_and_nulls():
    checks = evaluate_checks(
        pl_m=5.0, w=[1.0] * 8, k=2, r=[0.1] * 8,
        corrected_dr_delta_m=0.1, drift_bound_m=0.5,
    )
    assert set(checks) == {
        "pl_under_al", "redundancy", "residual_test",
        "continuity", "cross_constellation",
    }
    assert checks["pl_under_al"] is True
    assert checks["redundancy"] is True
    # fitted CHI2_CUTOFF is the default: 8 * 0.01 / 3 << 7.703 -> True
    assert checks["residual_test"] is True
    assert checks["continuity"] is True
    assert checks["cross_constellation"] is None
    # Missing inputs -> None, never a silent pass.
    empty = evaluate_checks()
    assert all(v is None for v in empty.values())


# ------------------------------------------------------------- speed scale

def test_speed_scale_endpoints_and_clamp():
    assert speed_scale(0.0) == 1.0
    assert speed_scale(ALERT_LIMIT_M) == 0.0
    assert speed_scale(ALERT_LIMIT_M / 2) == pytest.approx(0.5)
    assert speed_scale(2 * ALERT_LIMIT_M) == 0.0     # clamped below
    assert speed_scale(-5.0) == 1.0                  # clamped above
    assert speed_scale(math.inf) == 0.0
    assert speed_scale(None) == 0.0


def test_step_speed_scale_inf_pl():
    d = Gate().step(FAILING, math.inf)
    assert isinstance(d, GateDecision)
    assert d.speed_scale == 0.0
    assert d.correction_ok is False


# ------------------------------------------------------------ purity rules

def test_determinism_two_gates_identical():
    """Same sequence -> identical decisions. Track C replays the gate inside
    the Dirichlet sweep; any hidden state or clock would break it."""
    seq = ([PASSING] * 7 + [FAILING] + [PASSING] * 15 + [ALL_NONE]
           + [PASSING] * 12)
    pls = [float(i % 14) for i in range(len(seq))]
    a, b = Gate(), Gate()
    for checks, pl in zip(seq, pls):
        assert a.step(checks, pl) == b.step(checks, pl)


def test_no_wall_clock_in_gate_source():
    """TRACK_B.md arbiter purity, applied here: no time inside the gate."""
    import inspect
    import backend.correction.gate as gate_mod
    src = inspect.getsource(gate_mod)
    assert "import time" not in src
    assert "datetime" not in src
    assert "time.time" not in src


# --------------------------------------------------- cross-boundary parity

def test_alert_limit_matches_console_mission():
    """design.md §5 boundary: backend must not import console, so the parity
    is asserted here, in the test, by importing both sides."""
    from backend.correction import gate as backend_gate
    from console import mission
    assert backend_gate.ALERT_LIMIT_M == mission.ALERT_LIMIT_M
