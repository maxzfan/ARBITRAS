"""E3 acceptance (TRACK_E.md): M = I gives mismatch exactly 0 at the true
position; across a boundary it is exactly 1 - p_s(c')/max p_s; calibration
sets saturation and floor from the clean run; the min rule names its source;
the gate check is null until calibrated."""
from pathlib import Path

import numpy as np
import pytest

from backend.terrain.channel import (TerrainChannel, apply_bound,
                                     match_likelihood, mismatch)
from backend.terrain.rastermap import RasterMap, m_per_deg
from backend.terrain.sensor import ConfusionSensor, confusion_from_diag

FIXTURE = Path("fixtures/terrain_fixture.json")
LAT0, LON0 = 38.92, -77.067


@pytest.fixture
def rmap():
    return RasterMap.from_fixture(FIXTURE)


def lla_at(rmap, e, n):
    m_lat, m_lon = m_per_deg(LAT0)
    return {"lat": LAT0 + n / m_lat, "lon": LON0 + e / m_lon, "alt": 58.0}


def test_match_likelihood_and_mismatch_by_hand():
    q = np.array([1.0, 0.0, 0.0])
    p = np.array([0.7, 0.2, 0.1])
    L, Lmax = match_likelihood(q, p)
    assert L == pytest.approx(0.7) and Lmax == pytest.approx(0.7)
    assert mismatch(q, p) == pytest.approx(0.0)
    q2 = np.array([0.0, 1.0, 0.0])
    assert mismatch(q2, p) == pytest.approx(1 - 0.2 / 0.7)


def test_identity_sensor_reads_zero_at_the_true_position(rmap):
    ch = TerrainChannel(rmap, ConfusionSensor(np.eye(7), rmap.classes), sigma_uere_m=1.9)
    for _ in range(5):
        out = ch.step(lla_at(rmap, 0.3, -0.5), hdop=0.8)
        assert out["feature"] == 0.0
        b = out["block"]
        assert b["available"] and b["sensed"]["class"] == "grass"
        assert b["map_at_position"]["class"] == "grass"
        assert b["match_likelihood"] == pytest.approx(1.0)
        assert b["sensor"]["source"] == "simulated"


def test_walked_across_a_boundary_reads_one_minus_ratio(rmap):
    m = confusion_from_diag(7, 0.9)
    ch = TerrainChannel(rmap, ConfusionSensor(m, rmap.classes, seed=3), sigma_uere_m=0.1)
    out = ch.step(lla_at(rmap, 70.0, 0.0), hdop=0.5)      # believed inside the building block
    p_s = ch.last_posterior
    c_building = rmap.classes.index("building")
    expected = 1 - p_s[c_building] / p_s.max()
    assert out["feature"] == pytest.approx(expected)
    assert out["block"]["map_at_position"]["class"] == "building"


def test_identity_sensor_walked_reads_exactly_one(rmap):
    ch = TerrainChannel(rmap, ConfusionSensor(np.eye(7), rmap.classes), sigma_uere_m=0.1)
    assert ch.step(lla_at(rmap, 70.0, 0.0), hdop=0.5)["feature"] == 1.0


def test_window_is_a_trailing_mean(rmap):
    ch = TerrainChannel(rmap, ConfusionSensor(np.eye(7), rmap.classes),
                        sigma_uere_m=0.1, window_epochs=4)
    for _ in range(4):
        ch.step(lla_at(rmap, 0.0, 0.0), hdop=0.5)
    assert ch.step(lla_at(rmap, 70.0, 0.0), hdop=0.5)["feature"] == pytest.approx(0.25)
    assert ch.step(lla_at(rmap, 70.0, 0.0), hdop=0.5)["feature"] == pytest.approx(0.5)


def test_off_map_or_unsolved_epoch_is_not_scored(rmap):
    ch = TerrainChannel(rmap, ConfusionSensor(np.eye(7), rmap.classes), sigma_uere_m=1.0)
    out = ch.step(None, None)
    assert out["feature"] is None and out["block"]["match_likelihood"] is None
    assert out["block"]["available"] is True
    far = ch.step(lla_at(rmap, 5000.0, 0.0), hdop=0.5)
    assert far["feature"] is None and far["block"]["map_at_position"] is None


def test_unscored_epoch_makes_no_claim_even_after_a_scored_one(rmap):
    """A believed position that walks off the map (or onto unlabelled ground)
    must not carry the previous epoch's verdict forward — that would let a
    stale 0 read as agreement, or a stale 1 as an alarm."""
    ch = TerrainChannel(rmap, ConfusionSensor(np.eye(7), rmap.classes), sigma_uere_m=0.1,
                        window_epochs=2)
    assert ch.step(lla_at(rmap, 70.0, 0.0), hdop=0.5)["feature"] == 1.0
    assert ch.step(lla_at(rmap, 5000.0, 0.0), hdop=0.5)["feature"] is None
    assert ch.step(None, None)["feature"] is None
    # lookups resume: the trailing window continues from where it left off
    assert ch.step(lla_at(rmap, 0.0, 0.0), hdop=0.5)["feature"] == pytest.approx(0.5)


def test_calibrate_sets_saturation_and_floor_then_resets(rmap):
    m = confusion_from_diag(7, 0.8)
    ch = TerrainChannel(rmap, ConfusionSensor(m, rmap.classes, seed=5), sigma_uere_m=1.9)
    lla = lla_at(rmap, 0.0, 0.0)
    stats = ch.calibrate([(lla["lat"], lla["lon"], 0.8)] * 400)
    assert 0.0 < ch.saturation <= 1.0 and 0.0 < ch.floor <= 1.0
    assert stats["n"] == 400 and stats["saturation"] == ch.saturation
    assert ch.gate_check(lla) == {"terrain_consistent": None}   # no posterior since reset
    ch.step(lla, hdop=0.8)
    assert ch.gate_check(lla)["terrain_consistent"] in (True, False)
    first = ch.sensor.last_reading
    ch.reset()
    ch.step(lla, hdop=0.8)
    assert ch.sensor.last_reading == first                       # same draw sequence


def test_gate_check_is_true_at_truth_and_false_across_boundary(rmap):
    ch = TerrainChannel(rmap, ConfusionSensor(np.eye(7), rmap.classes), sigma_uere_m=0.1,
                        floor=0.5)
    ch.step(lla_at(rmap, 0.0, 0.0), hdop=0.5)
    assert ch.gate_check(lla_at(rmap, 0.0, 0.0)) == {"terrain_consistent": True}
    assert ch.gate_check(lla_at(rmap, 70.0, 0.0)) == {"terrain_consistent": False}
    assert ch.gate_check(None) == {"terrain_consistent": None}


def test_apply_bound_min_rule_names_its_source():
    g = apply_bound({"displacement_bound_m": 14.0}, {"available": True, "consistent_extent_m": 9.5})
    assert g["displacement_bound_m"] == 9.5 and g["bound_source"] == "terrain"
    assert g["residual_bound_m"] == 14.0
    g = apply_bound({"displacement_bound_m": 14.0}, {"available": True, "consistent_extent_m": 210.0})
    assert g["displacement_bound_m"] == 14.0 and g["bound_source"] == "residual"
    g = apply_bound({"displacement_bound_m": None}, {"available": True, "consistent_extent_m": 210.0})
    assert g["displacement_bound_m"] == 210.0 and g["bound_source"] == "terrain"
    g = apply_bound({"displacement_bound_m": 14.0}, {"available": True, "consistent_extent_m": None})
    assert g["displacement_bound_m"] == 14.0 and g["bound_source"] == "residual"
    untouched = apply_bound({"displacement_bound_m": 14.0}, None)
    assert untouched == {"displacement_bound_m": 14.0}


def test_saturation_scales_the_feature(rmap):
    ch = TerrainChannel(rmap, ConfusionSensor(np.eye(7), rmap.classes), sigma_uere_m=0.1,
                        saturation=0.5)
    assert ch.step(lla_at(rmap, 70.0, 0.0), hdop=0.5)["feature"] == 1.0   # clipped
