"""E3 acceptance: a sensorless record is unchanged apart from the additive
score_detail.features_scored; terrain on adds exactly the specified keys."""
from datetime import datetime

from backend.detection import FEATURE_NAMES, Weights, record, score


def test_sensorless_record_has_no_terrain_keys():
    feats = {n: 0.2 for n in FEATURE_NAMES}
    geom = {"information_ratio": 0.9, "displacement_bound_m": 12.0}
    r = record(datetime(2026, 8, 20), feats, score(feats, geom, Weights()), n_sv=9, geometry=geom)
    assert "terrain" not in r
    assert "bound_source" not in r["geometry"] and "residual_bound_m" not in r["geometry"]
    assert set(r["features"]) == set(FEATURE_NAMES)
    assert r["score_detail"]["features_scored"] == list(FEATURE_NAMES)
