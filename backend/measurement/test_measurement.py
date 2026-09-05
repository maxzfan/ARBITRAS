"""Harness tests with a stub compose function pinning the §10 sweep
semantics: FSR is the fraction of CLEAN epochs below NOMINAL, reported as
a distribution over Dirichlet weight draws — never a point. The real
composite arrives through `compose_from_track_a` (Track A's score with
beta at its default); the stub keeps the same shape so these tests never
block on A.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np

from backend.measurement.displacement import check_epochs, report as d_report
from backend.measurement.weight_sweep import (FEATURES, compose_from_track_a,
                                              dirichlet_sweep, report)

NOMINAL = 0.5  # stub-scale floor; the real one comes from the threshold session


def stub_compose(features, geometry_ratio, w, beta):
    """Same idea as Track A's composite: anomaly-weighted feature half
    blended with the fixed geometry half; §10 sweeps beta too."""
    feat = 1.0 - float(np.dot(w, [features[f] for f in FEATURES]))
    return beta * feat + (1.0 - beta) * geometry_ratio


def _epoch(anom: float, geom: float):
    return ({f: anom for f in FEATURES}, geom)


def test_sweep_clean_unambiguous_epochs_fsr_all_zero():
    # Clean epochs (low anomaly, full geometry) stay above NOMINAL under
    # every weighting: the FSR distribution is identically zero and no
    # epoch is weight-sensitive.
    clean = [_epoch(0.05, 1.0)] * 5
    r = dirichlet_sweep(stub_compose, clean, [], n_draws=200, nominal=NOMINAL)
    assert np.all(r["fsr"] == 0.0)
    assert r["fsr_min"] == r["fsr_median"] == r["fsr_max"] == 0.0
    assert r["weight_sensitive_fraction"] == 0.0
    assert r["n_clean"] == 5 and r["n_attack"] == 0
    assert "n/a" in report(r)  # no attack epochs -> no detection figure


def test_sweep_blatant_attack_detected_under_every_draw():
    # Blatant attack epochs (high anomaly, degraded geometry) fall below
    # NOMINAL under every weighting: detection fraction pinned at 1.0
    # across the whole distribution, and clean FSR stays zero.
    clean = [_epoch(0.05, 1.0)] * 5
    attack = [_epoch(0.95, 0.3)] * 5
    r = dirichlet_sweep(stub_compose, clean, attack, n_draws=200,
                        nominal=NOMINAL)
    assert np.all(r["detection"] == 1.0)
    assert r["detection_min"] == r["detection_max"] == 1.0
    assert np.all(r["fsr"] == 0.0)
    assert r["weight_sensitive_fraction"] == 0.0


def test_sweep_borderline_clean_epoch_moves_fsr_across_draws():
    # A clean epoch where one feature reads high and the rest low, with mid
    # geometry, crosses NOMINAL depending on the weighting: FSR varies
    # across draws (a genuine distribution) and only that 1-of-5 epoch is
    # weight-sensitive.
    borderline = ({"cn0_anomaly": 0.9, "pseudorange_residual": 0.1,
                   "code_carrier_divergence": 0.1,
                   "cross_constellation": 0.1}, 0.55)
    clean = [_epoch(0.05, 1.0)] * 4 + [borderline]
    r = dirichlet_sweep(stub_compose, clean, [], n_draws=500, nominal=NOMINAL)
    assert r["fsr_min"] < r["fsr_max"]          # FSR varies with weighting
    assert 0.0 < r["weight_sensitive_fraction"] <= 0.2  # only the 1 epoch


def test_sweep_is_deterministic_under_seed():
    clean = [_epoch(0.45, 0.55)] * 3
    attack = [_epoch(0.6, 0.4)] * 2
    a = dirichlet_sweep(stub_compose, clean, attack, n_draws=50,
                        nominal=NOMINAL)
    b = dirichlet_sweep(stub_compose, clean, attack, n_draws=50,
                        nominal=NOMINAL)
    assert np.array_equal(a["fsr"], b["fsr"])
    assert np.array_equal(a["detection"], b["detection"])


def test_report_gives_distributions_not_a_point():
    clean = [_epoch(0.05, 1.0)] * 4
    attack = [_epoch(0.95, 0.3)] * 2
    r = dirichlet_sweep(stub_compose, clean, attack, n_draws=100,
                        nominal=NOMINAL)
    text = report(r)
    assert "FSR distribution min/median/max" in text
    assert "detection fraction distribution" in text
    assert "weight-sensitive" in text
    assert "verdict" in text


def test_compose_from_track_a_matches_real_score():
    # The adapter must reproduce Track A's score() with the drawn feature
    # weights and beta left at its dataclass default.
    from backend.detection import FEATURE_NAMES, Weights, score
    features = {"cn0_anomaly": 0.3, "pseudorange_residual": 0.1,
                "code_carrier_divergence": 0.2, "cross_constellation": 0.05}
    geometry = {"information_ratio": 0.8}
    w = (0.4, 0.3, 0.2, 0.1)
    expected = score(features, geometry,
                     Weights(feature=dict(zip(FEATURE_NAMES, w))))
    assert compose_from_track_a(features, geometry, w) == \
        expected["confidence"]
    assert Weights().beta == expected["beta"]  # beta stayed at default
    # A bare ratio is wrapped into a geometry block, not treated as absent.
    assert compose_from_track_a(features, 0.8, w) == expected["confidence"]


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
