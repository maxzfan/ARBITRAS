"""Harness tests with a stub compose function — the real
compose_confidence arrives from Track A at the 21:00 session; these
tests pin the harness semantics so the overnight run is plumbing only.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np

from backend.measurement.displacement import check_epochs, report as d_report
from backend.measurement.weight_sweep import FEATURES, dirichlet_sweep, report


def stub_compose(features, geometry_ratio, w4, blend):
    """Placeholder with the agreed signature: features are anomaly scores
    in [0,1] (high = suspicious); confidence falls as they rise."""
    feat = 1.0 - float(np.dot(w4, [features[f] for f in FEATURES]))
    return blend * feat + (1.0 - blend) * geometry_ratio


def _epoch(anom: float, geom: float, spoofed: bool):
    return ({f: anom for f in FEATURES}, geom, spoofed)


def test_sweep_unambiguous_epochs_are_weight_insensitive():
    # Clean epochs (low anomaly, full geometry) and blatant spoofed epochs
    # (high anomaly, degraded geometry) get the same verdict under every
    # weighting: FSR pinned at 1.0, weight-sensitive fraction 0.
    epochs = [_epoch(0.05, 1.0, False)] * 5 + [_epoch(0.95, 0.3, True)] * 5
    r = dirichlet_sweep(stub_compose, epochs, n_draws=200)
    assert r["fsr_min"] == r["fsr_max"] == 1.0
    assert r["weight_sensitive_fraction"] == 0.0


def test_sweep_borderline_epoch_is_weight_sensitive():
    # An epoch where one feature says spoofed and the rest say clean, with
    # mid geometry, flips verdict with the weighting — exactly what the
    # weight-sensitive fraction is defined to catch.
    borderline = ({"cn0_anomaly": 0.9, "pseudorange_residual": 0.1,
                   "code_carrier_divergence": 0.1,
                   "cross_constellation": 0.1}, 0.55, True)
    epochs = [_epoch(0.05, 1.0, False)] * 4 + [borderline]
    r = dirichlet_sweep(stub_compose, epochs, n_draws=500)
    assert 0.0 < r["weight_sensitive_fraction"] <= 0.2  # only the 1 epoch
    assert r["fsr_min"] < r["fsr_max"]                  # verdict varies
    assert "weight-sensitive" in report(r)


def test_sweep_is_deterministic_under_seed():
    epochs = [_epoch(0.5, 0.7, True)] * 3
    a = dirichlet_sweep(stub_compose, epochs, n_draws=50)
    b = dirichlet_sweep(stub_compose, epochs, n_draws=50)
    assert np.array_equal(a["fsr"], b["fsr"])


def test_displacement_check_pass_and_fail():
    t0 = datetime(2026, 8, 20, 12, 0, 0)
    ok = [(t0 + timedelta(seconds=30 * k), 5.0 + k, 40.0) for k in range(4)]
    r = check_epochs(ok)
    assert r["ok"] and r["n_pass"] == 4
    assert d_report(r) == "empirical <= bound 4/4 — PASS"

    bad = ok + [(t0 + timedelta(seconds=150), 55.0, 40.0)]
    r2 = check_epochs(bad)
    assert not r2["ok"] and len(r2["violations"]) == 1
    assert "FAIL" in d_report(r2)


def test_displacement_check_none_bounds_excluded_not_passed():
    # None bound = no residual test exists; must not count as a pass,
    # and an all-None run must not claim PASS.
    t0 = datetime(2026, 8, 20, 12, 0, 0)
    mixed = [(t0, 5.0, 40.0), (t0 + timedelta(seconds=30), 500.0, None)]
    r = check_epochs(mixed)
    assert r["ok"] and r["n_bounded"] == 1 and "1 epochs unbounded" in d_report(r)
    r_all_none = check_epochs([(t0, 5.0, None)])
    assert not r_all_none["ok"]
