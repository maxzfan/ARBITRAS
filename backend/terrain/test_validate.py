from pathlib import Path

import numpy as np

from backend.detection import FEATURE_NAMES, OPTIONAL_FEATURE_NAMES, Weights
from backend.terrain.channel import TerrainChannel
from backend.terrain.rastermap import RasterMap, m_per_deg
from backend.terrain.sensor import ConfusionSensor
from backend.terrain.validate import predicted_tta, rescore

FIXTURE = Path("fixtures/terrain_fixture.json")
LAT0, LON0 = 38.92, -77.067


def rec(e, n, conf=0.9):
    m_lat, m_lon = m_per_deg(LAT0)
    return {"timestamp": "t", "confidence": conf, "credential_status": "VALID",
            "position": {"lat": LAT0 + n / m_lat, "lon": LON0 + e / m_lon, "alt": 0.0},
            "position_source": "wls_differential",
            "features": {k: 0.0 for k in FEATURE_NAMES},
            "geometry": {"information_ratio": 0.9, "displacement_bound_m": 14.0},
            "satellites_tracked": 10,
            "_solution": {"hdop": 0.8, "displacement_m": float(np.hypot(e, n))},
            "_truth": {"lat": LAT0, "lon": LON0}}


def test_rescore_adds_terrain_and_recomputes_confidence():
    rmap = RasterMap.from_fixture(FIXTURE)
    ch = TerrainChannel(rmap, ConfusionSensor(np.eye(7), rmap.classes), sigma_uere_m=1.0)
    w = Weights.equal(FEATURE_NAMES + OPTIONAL_FEATURE_NAMES)
    out = rescore([rec(0, 0), rec(0, 0), rec(70, 0)], ch, w)
    assert out[0]["features"]["terrain_mismatch"] == 0.0
    assert out[2]["features"]["terrain_mismatch"] == 1.0
    assert out[2]["confidence"] < out[0]["confidence"]
    assert out[0]["geometry"]["bound_source"] in ("residual", "terrain")
    assert out[2]["terrain"]["map_at_position"]["class"] == "building"
    assert out[0]["score_detail"]["features_scored"][-1] == "terrain_mismatch"


def test_predicted_tta_from_the_fixture():
    rmap = RasterMap.from_fixture(FIXTURE)
    t = predicted_tta(rmap, walk_mps=1.0, epoch_s=30.0, window=1)
    assert set(t) == {0, 45, 90, 135, 180, 225, 270, 315}
    assert t[90]["r_T_m"] == 35.0 and t[90]["epochs"] == 35.0 / 30.0 + 1
    assert t[0]["r_T_m"] == 85.0
