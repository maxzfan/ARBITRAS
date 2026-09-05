"""Detection features, confidence and the §5 contract, against the real day."""
import json
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from backend.detection import (FEATURE_NAMES, FeatureExtractor, Weights, fit,
                               frame, record, score)
from backend.detection.features import CN0_SIGMA_FLOOR, aggregate
from backend.injector import CARRY_OFF, MEACONING, SIMPLISTIC, inject
from backend.rinex import noise
from backend.rinex.loader import load_obs

OBS = "data/USN800USA_R_20262320000_01D_30S_MO.crx.gz"
ONSET = datetime(2026, 8, 20, 12, 30)
# Test value for the divergence rate. NOT the demo pin, which is
# picked by hand from the printed arithmetic and is still pending.
TEST_RATE = 0.02  # m/s


@pytest.fixture(scope="module")
def day():
    return load_obs(OBS, systems="GERCS")


@pytest.fixture(scope="module")
def floor(day):
    return noise.measure(day)


@pytest.fixture(scope="module")
def cal(day, floor):
    return fit(day, floor)


@pytest.fixture(scope="module")
def clean_features(day, cal):
    return frame(FeatureExtractor(cal).run(day), [e.time for e in day])


def attacked(day, cal, floor, spoof):
    inj, truth = inject(day, spoof, floor)
    return frame(FeatureExtractor(cal).run(inj), [e.time for e in inj]), truth


# -- shape -------------------------------------------------------------------

def test_features_stay_in_the_unit_interval(clean_features):
    assert clean_features.min().min() >= 0.0
    assert clean_features.max().max() <= 1.0


def test_cn0_sigma_never_trusted_below_the_quantisation_floor(cal):
    assert cal.cn0_sigma.min() >= CN0_SIGMA_FLOOR - 1e-12


# -- causality: the whole measurement rests on this --------------------------

def test_scoring_is_causal(day, cal):
    """An epoch's score must not change when the future changes. Truncating the
    replay must leave every earlier score bit-identical."""
    n = 400
    full = FeatureExtractor(cal).run(day[:n + 50])
    short = FeatureExtractor(cal).run(day[:n])
    for a, b in zip(full[:n], short):
        assert a["features"] == b["features"]


def test_extractor_is_reproducible(day, cal):
    a = FeatureExtractor(cal).run(day[:200])
    b = FeatureExtractor(cal).run(day[:200])
    assert [x["features"] for x in a] == [x["features"] for x in b]


# -- aggregation -------------------------------------------------------------

def test_aggregate_reports_the_worst_constellation_not_the_diluted_sky():
    """One captured constellation must not be divided by the size of the sky."""
    s = pd.Series({**{f"G{i:02d}": 1.0 for i in range(1, 13)},
                   **{f"E{i:02d}": 0.0 for i in range(1, 30)}})
    assert aggregate(s) == pytest.approx(1.0)
    assert s.mean() < 0.3          # what the sky-wide mean would have said


def test_aggregate_ignores_constellations_with_too_few_satellites():
    s = pd.Series({**{f"S{i:02d}": 1.0 for i in range(1, 4)},
                   **{f"G{i:02d}": 0.0 for i in range(1, 13)}})
    assert aggregate(s) == pytest.approx(0.0)


# -- the §6a claims ----------------------------------------------------------

def test_cn0_fires_at_onset_then_fades(day, cal, floor):
    """§6a.1's stated weakness, reproduced: the anomaly is strongest at capture
    and goes quiet once the trailing mean absorbs the new level."""
    got, _ = attacked(day, cal, floor, SIMPLISTIC(onset=ONSET))
    onset = got[(got.index >= ONSET) & (got.index < ONSET + timedelta(minutes=5))]
    later = got[(got.index >= ONSET + timedelta(minutes=30))
                & (got.index < ONSET + timedelta(minutes=60))]
    assert onset["cn0_anomaly"].mean() > 3 * later["cn0_anomaly"].mean()


def test_pseudorange_residual_stays_elevated_after_cn0_fades(day, cal, floor,
                                                             clean_features):
    """§6a.2's stated role. This is the feature that carries the sustained
    detection once the C/N0 signal is gone."""
    got, _ = attacked(day, cal, floor, CARRY_OFF(onset=ONSET, carrier_rate_error=TEST_RATE))
    later = got[(got.index >= ONSET + timedelta(minutes=30))
                & (got.index < ONSET + timedelta(minutes=90))]
    assert later["pseudorange_residual"].mean() > clean_features[
        "pseudorange_residual"].quantile(0.99)


def test_meaconing_is_invisible_to_the_code_carrier_features(day, cal, floor,
                                                             clean_features):
    """A repeater preserves the code/carrier relationship, so features 2 and 3
    cannot see it and the C/N0 step fades. This is the concrete argument that
    the cross-constellation feature (§6a.4) is needed rather than optional —
    and the reason it is the wrong thing to put first on the cut list."""
    got, _ = attacked(day, cal, floor, MEACONING(onset=ONSET))
    later = got[(got.index >= ONSET + timedelta(minutes=30))
                & (got.index < ONSET + timedelta(minutes=90))]
    for f in ("pseudorange_residual", "code_carrier_divergence"):
        assert later[f].mean() <= clean_features[f].quantile(0.99)


def test_zero_rate_spoofer_is_invisible_to_features_2_and_3(day, cal, floor,
                                                            clean_features):
    """The ruling's stated consequence, asserted at the feature level: with a
    fully carrier-coherent spoofer the residual and divergence features see
    nothing, sustained."""
    got, _ = attacked(day, cal, floor,
                      CARRY_OFF(onset=ONSET, carrier_rate_error=0.0))
    later = got[(got.index >= ONSET + timedelta(minutes=30))
                & (got.index < ONSET + timedelta(minutes=90))]
    for f in ("pseudorange_residual", "code_carrier_divergence"):
        assert later[f].mean() <= clean_features[f].quantile(0.99)


def test_carry_off_separates_from_the_clean_day(day, cal, floor, clean_features):
    """The primary demo (§7 row 2) has to be detectable. d' over the sustained
    window, against the clean distribution of the same feature."""
    got, _ = attacked(day, cal, floor, CARRY_OFF(onset=ONSET, carrier_rate_error=TEST_RATE))
    att = got[(got.index >= ONSET) & (got.index < ONSET + timedelta(minutes=90))]
    f = "pseudorange_residual"
    d = abs(att[f].mean() - clean_features[f].mean()) / np.sqrt(
        (att[f].var() + clean_features[f].var()) / 2)
    assert d > 4.0


# -- confidence --------------------------------------------------------------

def test_weights_must_sum_to_one():
    with pytest.raises(ValueError):
        Weights(feature={n: 0.5 for n in FEATURE_NAMES})


def test_default_weights_are_flagged_untuned():
    assert Weights().tuned is False


def test_confidence_collapses_to_the_feature_half_without_geometry():
    feats = {n: 0.4 for n in FEATURE_NAMES}
    s = score(feats, None, Weights(beta=0.5))
    assert s["geometry_available"] is False
    assert s["beta"] == 1.0
    assert s["confidence"] == pytest.approx(0.6)


def test_geometry_lowers_the_weight_sensitive_fraction():
    feats = {n: 0.4 for n in FEATURE_NAMES}
    geom = {"information_ratio": 0.5}
    s = score(feats, geom, Weights(beta=0.6))
    assert s["geometry_available"] is True
    assert s["weight_sensitive_fraction"] == pytest.approx(0.6)
    assert s["confidence"] == pytest.approx(1 - (0.6 * 0.4 + 0.4 * 0.5))


def test_dirichlet_draws_are_valid_weightings():
    rng = np.random.default_rng(0)
    for _ in range(50):
        w = Weights.dirichlet(rng)
        assert sum(w.feature.values()) == pytest.approx(1.0)
        assert 0.0 <= w.beta <= 1.0


# -- the §5 contract ---------------------------------------------------------

def test_record_matches_the_contract_fixture():
    fixture = json.load(open("fixtures/epoch.json"))
    feats = {n: 0.3 for n in FEATURE_NAMES}
    r = record(datetime(2026, 8, 20, 0, 14, 30), feats,
               score(feats, None), n_sv=11)
    for key in fixture:
        assert key in r, f"§5 contract key {key!r} missing from the record"
    assert r["timestamp"].endswith("Z")
    assert 0.0 <= r["confidence"] <= 1.0
    assert set(r["position"]) == {"lat", "lon", "alt"}


def test_surveyed_position_is_flagged_as_not_a_solution():
    feats = {n: 0.0 for n in FEATURE_NAMES}
    r = record(datetime(2026, 8, 20, 0, 0), feats, score(feats, None), n_sv=11)
    assert r["position_source"] == "surveyed"
    r2 = record(datetime(2026, 8, 20, 0, 0), feats, score(feats, None), n_sv=11,
                position={"lat": 1.0, "lon": 2.0, "alt": 3.0})
    assert r2["position_source"] == "solution"


def test_composite_is_never_emitted_without_its_parts():
    """CLAUDE.md: never report the composite confidence without the sub-scores."""
    feats = {n: 0.3 for n in FEATURE_NAMES}
    r = record(datetime(2026, 8, 20, 0, 0), feats, score(feats, None), n_sv=11)
    assert set(r["features"]) == set(FEATURE_NAMES)
    assert "geometry" in r and "score_detail" in r
    assert r["score_detail"]["weights_tuned"] is False
