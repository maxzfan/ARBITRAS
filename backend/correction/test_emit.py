"""D4 acceptance tests (TRACK_D.md) on the hand-written fixture.

fixtures/epoch_obs.csv: 8 GPS + 4 Galileo, exact pseudoranges = geometric
range from RX_ECEF + per-constellation clock {G: 3000 m, E: 2000 m}, zero
noise (fixtures/make_epoch_obs.py) — so exact-recovery asserts to 1e-6 m.
The solve context is built by hand in the exact shape
GeometryEngine.solve_context() emits; H comes from Track C's build_H
unchanged. d_rho is the fixture's own arithmetic (no Sagnac in the fixture,
so d_rho_from_epoch's nav machinery is not exercised here — backend.demo
runs it on the real day).
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from backend.correction.emit import CorrectionEmitter, correction_block
from backend.correction.gate import GRANT_EPOCHS, Gate
from backend.detection import by_sv_scores, record, score
from backend.detection.emit import ecef_to_lla
from backend.detection.features import FEATURE_NAMES, PER_SV_FEATURES
from backend.geometry.hmatrix import build_H

FIXTURE_OBS = Path(__file__).resolve().parents[2] / "fixtures" / "epoch_obs.csv"
FIXTURE_EPOCH = Path(__file__).resolve().parents[2] / "fixtures" / "epoch.json"
RX_ECEF = np.array([1112161.8802, -4842854.4026, 3985497.383])

BLOCK_KEYS = {"corrected_position", "protection_level_m", "alert_limit_m",
              "weights", "trusted_count", "checks", "correction_ok",
              "speed_scale"}
CHECK_KEYS = {"pl_under_al", "redundancy", "residual_test", "continuity",
              "cross_constellation"}


@pytest.fixture(scope="module")
def fx():
    """(sat_pos dict, pr dict) from the committed fixture."""
    df = pd.read_csv(FIXTURE_OBS)
    sat = {r.sv: np.array([r.sat_x, r.sat_y, r.sat_z])
           for r in df.itertuples()}
    pr = {r.sv: float(r.pr_m) for r in df.itertuples()}
    return sat, pr


def _context(sat, excluded=()):
    """A solve context in GeometryEngine.solve_context()'s exact shape."""
    trusted = {sv: p for sv, p in sat.items() if sv not in set(excluded)}
    h, svs, consts = build_H(trusted, RX_ECEF)
    return {"t": datetime(2026, 8, 20, 0, 14, 30), "H_trusted": h,
            "sv_order": svs, "const_order": consts,
            "trusted_sat_pos": trusted, "frozen_basis": dict(sat),
            "frozen": False, "rx_ecef": RX_ECEF}


def _d_rho(sat, pr):
    """Fixture d_rho at x_lin: pr − |s − x| (zero noise, no Sagnac)."""
    return {sv: pr[sv] - float(np.linalg.norm(sat[sv] - RX_ECEF))
            for sv in sat}


# ------------------------------------------------------------------ test 1
def test_exact_recovery_on_clean_fixture(fx):
    """All weights 1, zero noise: corrected position == rx_ecef to ~1e-6 m;
    fitted-threshold checks evaluate, unwired inputs stay null."""
    sat, pr = fx
    block = correction_block(_context(sat), _d_rho(sat, pr), [], Gate())

    truth = ecef_to_lla(*RX_ECEF)
    got = block["corrected_position"]
    assert got is not None
    assert got["lat"] == pytest.approx(truth["lat"], abs=1e-9)   # ~1e-4 m
    assert got["lon"] == pytest.approx(truth["lon"], abs=1e-9)
    assert got["alt"] == pytest.approx(truth["alt"], abs=1e-5)

    assert set(block["weights"].values()) == {1.0}
    assert block["trusted_count"] == len(sat)
    # tau_m is fit (backend.correction.validate, clean-day p99.9 = 5.187 m):
    # PL = tau x max slope = 5.67 m on this geometry, inside the 15 m AL
    assert block["protection_level_m"] == pytest.approx(5.67, abs=0.01)
    assert block["checks"]["pl_under_al"] is True
    assert block["checks"]["redundancy"] is True
    assert block["checks"]["residual_test"] is True    # zero-noise residuals
    assert block["checks"]["continuity"] is None       # drift bound not given
    assert block["checks"]["cross_constellation"] is None
    # one clean epoch is not a grant (10-epoch hysteresis)
    assert block["correction_ok"] is False


# ------------------------------------------------------------------ test 2
def test_weights_invariant_matches_excluded_sv(fx):
    """TRACK_D.md: {sv : w == 0} must equal excluded_sv exactly."""
    sat, pr = fx
    excluded = ["G04", "G18"]
    block = correction_block(_context(sat), _d_rho(sat, pr), excluded, Gate())

    assert {sv for sv, w in block["weights"].items() if w == 0.0} \
        == set(excluded)
    assert {sv for sv, w in block["weights"].items() if w == 1.0} \
        == set(sat) - set(excluded)
    assert block["trusted_count"] == len(sat) - len(excluded)
    # exclusion does not move the zero-noise fix off the antenna
    truth = ecef_to_lla(*RX_ECEF)
    assert block["corrected_position"]["lat"] == pytest.approx(truth["lat"],
                                                               abs=1e-9)


def test_engine_side_exclusion_is_equivalent(fx):
    """Demo path: the engine already removed excluded rows from H_trusted.
    Same excluded_sv list, same fix, same weights block (D1 test 4's row-
    deletion == w-zero equality, seen from the emitter)."""
    sat, pr = fx
    excluded = ["G04", "G18"]
    via_weights = correction_block(_context(sat), _d_rho(sat, pr), excluded,
                                   Gate())
    via_rows = correction_block(_context(sat, excluded), _d_rho(sat, pr),
                                excluded, Gate())
    assert via_rows["weights"] == via_weights["weights"]
    assert via_rows["trusted_count"] == via_weights["trusted_count"]
    for ax in ("lat", "lon", "alt"):
        assert via_rows["corrected_position"][ax] == pytest.approx(
            via_weights["corrected_position"][ax], abs=1e-9)


# ------------------------------------------------------------------ test 3
def test_rank_deficient_path_emits_null_false_block(fx):
    """Excluding every Galileo SV kills the E clock state (GOTCHA 2a):
    corrected_position null, protection_level_m null, correction_ok false,
    redundancy False — never garbage, never a silent pass."""
    sat, pr = fx
    excluded = sorted(sv for sv in sat if sv.startswith("E"))
    block = correction_block(_context(sat), _d_rho(sat, pr), excluded, Gate())

    assert block["corrected_position"] is None
    assert block["protection_level_m"] is None
    assert block["correction_ok"] is False
    assert block["checks"]["redundancy"] is False
    assert {sv for sv, w in block["weights"].items() if w == 0.0} \
        == set(excluded)


def test_rank_deficiency_revokes_a_standing_grant(fx):
    """Gate wiring: 10 clean epochs grant, one rank-deficient epoch revokes
    (TRACK_D.md: one epoch can revoke, no single epoch can grant)."""
    sat, pr = fx
    gate = Gate()
    ctx, d_rho = _context(sat), _d_rho(sat, pr)
    blocks = [correction_block(ctx, d_rho, [], gate)
              for _ in range(GRANT_EPOCHS)]
    assert [b["correction_ok"] for b in blocks] \
        == [False] * (GRANT_EPOCHS - 1) + [True]

    bad = correction_block(ctx, d_rho,
                           sorted(sv for sv in sat if sv.startswith("E")),
                           gate)
    assert bad["correction_ok"] is False


# ------------------------------------------------------------------ test 4
def test_missing_inputs_degrade_to_fail_closed_block(fx):
    """No context / no observations: the block still emits, all checks None,
    correction_ok false, and the gate counted the epoch as a revoke."""
    sat, pr = fx
    for args in ((None, _d_rho(sat, pr)), (_context(sat), {})):
        block = correction_block(args[0], args[1], ["G07"], Gate())
        assert block["corrected_position"] is None
        assert block["correction_ok"] is False
        assert set(block["checks"]) == CHECK_KEYS
        assert all(v is None for v in block["checks"].values())
        assert block["weights"] == {"G07": 0.0}


def test_uncorrectable_constellation_drops_rows_and_column(fx):
    """No d_rho for any Galileo SV (nav-coverage hole, not distrust): the E
    rows and the E clock column leave the solve; the G-only fix still lands
    on the antenna and the E SVs carry no weight entry."""
    sat, pr = fx
    d_rho = {sv: v for sv, v in _d_rho(sat, pr).items()
             if not sv.startswith("E")}
    block = correction_block(_context(sat), d_rho, [], Gate())

    truth = ecef_to_lla(*RX_ECEF)
    assert block["corrected_position"]["lat"] == pytest.approx(truth["lat"],
                                                               abs=1e-9)
    assert set(block["weights"]) == {sv for sv in sat if sv.startswith("G")}
    assert block["trusted_count"] == 8


# ------------------------------------------------------------------ test 5
def test_block_shape_matches_the_contract_fixture(fx):
    """fixtures/epoch.json geometry.correction and features.by_sv carry
    exactly the shape the emitter produces (TRACK_D.md contract extension)."""
    fixture = json.loads(FIXTURE_EPOCH.read_text())
    fx_block = fixture["geometry"]["correction"]
    assert set(fx_block) == BLOCK_KEYS
    assert set(fx_block["checks"]) == CHECK_KEYS
    assert "by_sv" in fixture["features"]

    sat, pr = fx
    block = correction_block(_context(sat), _d_rho(sat, pr), ["G04"], Gate())
    assert set(block) == set(fx_block)
    assert set(block["checks"]) == set(fx_block["checks"])
    assert set(block["corrected_position"]) == set(fx_block["corrected_position"])
    # fixture invariant holds too: zero-weight set == excluded_sv
    assert {sv for sv, w in fx_block["weights"].items() if w == 0.0} \
        == set(fixture["geometry"]["excluded_sv"])


# ------------------------------------------------------------------ test 6
def test_by_sv_rides_inside_features():
    """TRACK_D.md contract extension 1: per-SV max of the three per-SV
    features, [0,1], nested at features.by_sv; cross_constellation absent."""
    per_sv = pd.DataFrame(
        {"cn0_anomaly": [2.0, 0.10], "pseudorange_residual": [0.5, np.nan],
         "code_carrier_divergence": [0.2, 0.05]}, index=["G07", "E11"])
    z_sat = {n: 1.0 for n in PER_SV_FEATURES}
    b = by_sv_scores(per_sv, z_sat)
    assert b == {"G07": 1.0, "E11": 0.10}          # max, clipped to [0,1]

    feats = {n: 0.3 for n in FEATURE_NAMES}
    r = record(datetime(2026, 8, 20, 0, 14, 30), feats,
               score(feats, None), n_sv=12, by_sv=b)
    assert r["features"]["by_sv"] == {"G07": 1.0, "E11": 0.1}
    # the four epoch features are untouched beside it
    for n in FEATURE_NAMES:
        assert r["features"][n] == 0.3


# ------------------------------------------------------------------ test 7
def test_emitter_seam_fails_closed_without_engine_or_nav(fx):
    """CorrectionEmitter with no context (fresh engine) or no nav tables
    emits the fail-closed block; reset() restarts the gate history."""
    emitter = CorrectionEmitter(nav=None, context_fn=lambda: None)
    block = emitter(ep=None, excluded_sv=["G07"])
    assert block["correction_ok"] is False
    assert block["corrected_position"] is None
    assert block["weights"] == {"G07": 0.0}
    emitter.reset()
    assert emitter.gate._run == 0


# --- Track E (tracks/TRACK_E.md): check 6 enters the gate before hysteresis --

def test_extra_checks_are_merged_before_the_gate_steps():
    """A False extra check revokes; None leaves the gate untouched; the key is
    emitted either way; without the hook the key is absent."""
    from backend.correction.emit import _emit
    checks = {"pl_under_al": True, "redundancy": True, "residual_test": True,
              "continuity": None, "cross_constellation": None}
    seen = []

    def extra(lla):
        seen.append(lla)
        return {"terrain_consistent": False}

    g = Gate()
    for _ in range(GRANT_EPOCHS + 2):
        blk = _emit(g, checks, 3.0, {"lat": 1.0, "lon": 2.0, "alt": 3.0}, {"G01": 1.0},
                    15.0, extra_checks=extra)
    assert blk["checks"]["terrain_consistent"] is False and blk["correction_ok"] is False
    assert seen[-1] == {"lat": 1.0, "lon": 2.0, "alt": 3.0}

    g = Gate()
    for _ in range(GRANT_EPOCHS + 2):
        blk = _emit(g, checks, 3.0, {"lat": 1.0, "lon": 2.0, "alt": 3.0}, {"G01": 1.0},
                    15.0, extra_checks=lambda lla: {"terrain_consistent": None})
    assert blk["checks"]["terrain_consistent"] is None and blk["correction_ok"] is True

    blk = _emit(Gate(), checks, 3.0, None, {}, 15.0)
    assert "terrain_consistent" not in blk["checks"]


def test_correction_emitter_threads_extra_checks_through_fail_closed_paths():
    """With no context the block fails closed AND the hook still sees None."""
    seen = []
    em = CorrectionEmitter(nav=None, context_fn=lambda: None,
                           extra_checks=lambda lla: seen.append(lla) or {"terrain_consistent": None})
    blk = em(None, [])
    assert blk["correction_ok"] is False and seen == [None]
    assert blk["checks"]["terrain_consistent"] is None
