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
def nav():
    from backend.rinex import ephemeris
    return ephemeris.load_nav()


@pytest.fixture(scope="module")
def resid(day, nav):
    from backend.rinex.solve import residual_panel
    return residual_panel(day, nav)


@pytest.fixture(scope="module")
def cal(day, floor, resid):
    return fit(day, floor, resid_panel=resid)


@pytest.fixture(scope="module")
def win(day):
    return [e for e in day
            if datetime(2026, 8, 20, 12, 0) <= e.time < datetime(2026, 8, 20, 14, 0)]


@pytest.fixture(scope="module")
def win_clean_features(win, cal, nav):
    from backend.rinex.solve import residual_panel
    rp = residual_panel(win, nav)
    return frame(FeatureExtractor(cal).run(win, resid_panel=rp),
                 [e.time for e in win])


@pytest.fixture(scope="module")
def clean_features(day, cal, resid):
    return frame(FeatureExtractor(cal).run(day, resid_panel=resid),
                 [e.time for e in day])


def attacked(day, cal, floor, spoof, nav=None):
    from backend.rinex.solve import residual_panel
    inj, truth = inject(day, spoof, floor, nav=nav)
    rp = residual_panel(inj, nav, use_cache=False) if nav is not None else None
    return frame(FeatureExtractor(cal).run(inj, resid_panel=rp),
                 [e.time for e in inj]), truth


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


def test_post_fit_residual_sees_a_coordinated_position_walk(
        win, cal, floor, nav, win_clean_features):
    """The reason feature 2 was reimplemented: the post-fit residual carries
    geometry, so it responds to the displacement itself -- even when the
    spoofer is perfectly coherent and features 1 and 3 see nothing."""
    got, _ = attacked(win, cal, floor,
                      CARRY_OFF(onset=ONSET, carrier_rate_error=0.0), nav=nav)
    later = got[got.index >= ONSET + timedelta(minutes=15)]
    assert later["pseudorange_residual"].mean() > win_clean_features[
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


def test_coherent_spoofer_is_invisible_to_the_divergence_feature(
        win, cal, floor, nav, win_clean_features):
    """Feature 3 responds to carrier_rate_error * t, which is independent of
    displacement: a perfectly coherent spoofer is invisible to it however far
    it moves the vehicle. Feature 2 is deliberately NOT in this list any more
    -- that is what the reimplementation bought."""
    got, _ = attacked(win, cal, floor,
                      CARRY_OFF(onset=ONSET, carrier_rate_error=0.0), nav=nav)
    later = got[got.index >= ONSET + timedelta(minutes=15)]
    f = "code_carrier_divergence"
    assert later[f].mean() <= win_clean_features[f].quantile(0.99)


def test_carry_off_separates_from_the_clean_day(win, cal, floor, nav,
                                                win_clean_features):
    """The primary demo (§7 row 2) has to be detectable on the post-fit
    residual alone, with a coherent spoofer."""
    got, _ = attacked(win, cal, floor,
                      CARRY_OFF(onset=ONSET, carrier_rate_error=0.0), nav=nav)
    att = got[got.index >= ONSET]
    f = "pseudorange_residual"
    d = abs(att[f].mean() - win_clean_features[f].mean()) / np.sqrt(
        (att[f].var() + win_clean_features[f].var()) / 2)
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
        if key == "terrain":
            continue        # Track E's optional block: present only when the channel ran
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


# -- excluded_sv ---------------------------------------------------------------


def test_no_rule_means_no_exclusions(day, cal):
    from backend.detection import flagged_sv
    per_sv = FeatureExtractor(cal).run(day[:60])[-1]["per_sv"]
    assert flagged_sv(per_sv, cal.z_sat, None) == []


def test_flat_fallback_is_armed_without_a_mask(day, cal):
    from backend.detection import FLAT_K5, flagged_sv
    per_sv = FeatureExtractor(cal).run(day[:200])[-1]["per_sv"]
    assert FLAT_K5.armed is True
    got = flagged_sv(per_sv, cal.z_sat, FLAT_K5)
    norm = (per_sv / pd.Series(cal.z_sat)).max(axis=1).dropna()
    assert got == sorted(norm.index[norm >= 5.0])


def test_exclusion_rule_names_only_saturated_satellites(day, cal):
    from backend.detection import flagged_sv
    per_sv = FeatureExtractor(cal).run(day[:200])[-1]["per_sv"]
    for k in (1.0, 2.0, 3.0):
        got = flagged_sv(per_sv, cal.z_sat, k)
        norm = (per_sv / pd.Series(cal.z_sat)).max(axis=1).dropna()
        assert got == sorted(norm.index[norm >= k])
        assert set(got) <= set(per_sv.index)



def test_replay_emits_detector_derived_excluded_sv(day, cal, nav):
    """The list must come from per-SV scores, not from what the injector did --
    and it must be an INPUT to Track C's geometry block, so the information
    ratio and the exclusion list describe the same satellite set."""
    from backend.geometry.engine import geometry_for as track_c
    from backend.replay import run

    seen_lists = []

    def spy(ep, excluded_sv=None):
        seen_lists.append(list(excluded_sv or []))
        return track_c(ep, excluded_sv=excluded_sv)

    recs = run(day[:150], cal, geometry_for=spy, nav=nav, exclusion=1.0)
    # the rule fires on clean sky at k=1.0, and whatever it named was handed
    # to the geometry provider rather than written on top of its output
    assert any(seen_lists)
    for r, handed in zip(recs, seen_lists):
        assert r["geometry"]["excluded_sv"] == handed

    off = run(day[:150], cal)
    assert all(r["geometry"] is None for r in off)


# -- the §5 contract doc must match the emitted shape ------------------------

def test_contract_doc_and_fixture_match_the_emitted_record():
    """Open across three sessions. Asserted now so it cannot drift again:
    the JSON block in design.md §5, fixtures/epoch.json and a live record must
    agree on every key path. `geometry` subkeys are exempt while the block is
    null -- that is Track C's, not ours to fill."""
    import json
    import pathlib
    import re

    doc = pathlib.Path("docs/design.md").read_text()
    spec = json.loads(re.search(
        r"# 5\. INTERFACE CONTRACT.*?```json\n(.*?)\n```", doc, re.S).group(1))
    fixture = json.loads(pathlib.Path("fixtures/epoch.json").read_text())

    feats = {n: 0.3 for n in FEATURE_NAMES}
    live = record(datetime(2026, 8, 20, 0, 0), feats, score(feats, None),
                  n_sv=11)

    def paths(o, p=""):
        out = set()
        if isinstance(o, dict):
            for k, v in o.items():
                out.add(p + k)
                out |= paths(v, p + k + ".")
        return out

    # features.by_sv (TRACK_D.md contract extension 1) is additive and
    # optional — emitted only when the caller passes it — and its per-SV
    # keys are data, not schema. Exempt like the geometry subkeys. The same
    # holds for Track E's `terrain` block and `features.terrain_mismatch`
    # (tracks/TRACK_E.md): present only when the channel ran.
    geom = lambda ps: {x for x in ps if not x.startswith("geometry.")
                       and not x.startswith("features.by_sv")
                       and not x.startswith("terrain")
                       and x != "features.terrain_mismatch"
                       and not x.startswith("score_detail.weights.")}
    assert geom(paths(spec)) == geom(paths(live))
    assert geom(paths(spec)) == geom(paths(fixture))
    assert set(spec["features"]) == set(FEATURE_NAMES)
    assert set(fixture["features"]) - {"by_sv", "terrain_mismatch"} == set(FEATURE_NAMES)


# --- Track E seams (tracks/TRACK_E.md E3) ----------------------------------
from backend.detection import OPTIONAL_FEATURE_NAMES, anomaly, scored_features


def test_optional_feature_is_not_in_the_base_set():
    assert OPTIONAL_FEATURE_NAMES == ("terrain_mismatch",)
    assert not set(OPTIONAL_FEATURE_NAMES) & set(FEATURE_NAMES)


def test_weights_equal_over_five_names():
    w = Weights.equal(FEATURE_NAMES + OPTIONAL_FEATURE_NAMES)
    assert len(w.feature) == 5 and all(abs(v - 0.2) < 1e-12 for v in w.feature.values())


def test_missing_optional_feature_renormalises_not_reads_zero():
    w = Weights.equal(FEATURE_NAMES + OPTIONAL_FEATURE_NAMES)
    four = {n: 0.4 for n in FEATURE_NAMES}
    assert anomaly(four, w) == pytest.approx(0.4)          # not 0.32
    assert scored_features(four, w) == list(FEATURE_NAMES)
    five = dict(four, terrain_mismatch=1.0)
    assert anomaly(five, w) == pytest.approx(0.4 * 0.8 + 1.0 * 0.2)
    assert scored_features(five, w) == list(FEATURE_NAMES + OPTIONAL_FEATURE_NAMES)


def test_nan_feature_is_not_scored():
    w = Weights()
    feats = {n: 0.5 for n in FEATURE_NAMES}
    feats["cross_constellation"] = float("nan")
    assert anomaly(feats, w) == pytest.approx(0.5)
    assert "cross_constellation" not in scored_features(feats, w)


def test_score_reports_features_scored():
    feats = {n: 0.3 for n in FEATURE_NAMES}
    assert score(feats, None, Weights())["features_scored"] == list(FEATURE_NAMES)


def test_dirichlet_over_named_features():
    rng = np.random.default_rng(0)
    w = Weights.dirichlet(rng, names=FEATURE_NAMES + OPTIONAL_FEATURE_NAMES)
    assert set(w.feature) == set(FEATURE_NAMES + OPTIONAL_FEATURE_NAMES)
    assert abs(sum(w.feature.values()) - 1.0) < 1e-9


def test_record_carries_terrain_only_when_given():
    feats = {n: 0.3 for n in FEATURE_NAMES}
    s = score(feats, None, Weights())
    t = datetime(2026, 8, 20, 0, 0)
    plain = record(t, feats, s, n_sv=10)
    assert "terrain" not in plain
    assert plain["score_detail"]["features_scored"] == list(FEATURE_NAMES)
    with_t = record(t, feats, s, n_sv=10, terrain={"available": True})
    assert with_t["terrain"] == {"available": True}

# -- the elevation mask is upstream, not an exclusion exemption --------------

def test_mask_drops_low_satellites_from_the_epoch_entirely(day, nav):
    """Ruled: masked satellites are neither trusted, nor excludable, nor
    available as cover. So they must be gone before anything scores."""
    from backend.rinex import ephemeris
    from backend.rinex.solve import EL_MASK_DEG, masked_epoch
    from backend.detection.emit import USN8_ECEF

    ep = day[0]
    m = masked_epoch(ep, nav, cutoff_deg=EL_MASK_DEG)
    el = ephemeris.elevations_at(ep.time, list(ep.df.index), USN8_ECEF, nav)
    for sv in el.index:
        if el[sv] < EL_MASK_DEG:
            assert sv not in m.df.index
        else:
            assert sv in m.df.index
    # satellites with no Kepler ephemeris (GLONASS, SBAS) are kept, not
    # silently dropped -- they are not in H either way
    assert all(sv in m.df.index for sv in ep.df.index if sv not in el.index)


def test_exclusion_rule_has_no_elevation_term(day, cal):
    """The safe-harbour path is gone: no satellite is exempt from flagging."""
    from backend.detection import ExclusionRule, flagged_sv
    import inspect

    assert not hasattr(ExclusionRule(k=3.0), "elevation_mask_deg")
    assert "elev" not in inspect.signature(flagged_sv).parameters


def test_ruled_exclusion_k_is_three(day, cal):
    from backend.detection import RULED_K3
    assert RULED_K3.k == 3.0
    assert RULED_K3.armed is True


# -- combine modes: both available, neither picked ---------------------------

def test_both_combine_modes_are_available_and_sum_is_the_default():
    from backend.detection.confidence import COMBINE_MODES, anomaly, score
    feats = dict(zip(FEATURE_NAMES, (0.1, 0.9, 0.1, 0.1)))
    assert COMBINE_MODES == ("weighted_sum", "max")
    assert score(feats, None)["combine_mode"] == "weighted_sum"
    w = Weights()
    assert anomaly(feats, w, "max") == pytest.approx(0.9)
    assert anomaly(feats, w, "weighted_sum") == pytest.approx(0.3)
    with pytest.raises(ValueError):
        anomaly(feats, w, "median")


def test_max_mode_is_weight_free():
    """It has no weights, so nothing about it is weight-sensitive."""
    from backend.detection.confidence import score
    feats = dict(zip(FEATURE_NAMES, (0.1, 0.9, 0.1, 0.1)))
    s = score(feats, {"information_ratio": 0.8}, Weights(beta=0.6), mode="max")
    assert s["weight_sensitive_fraction"] == 0.0
    assert score(feats, {"information_ratio": 0.8},
                 Weights(beta=0.6))["weight_sensitive_fraction"] == 0.6


def test_calibration_caches_distinguish_masked_from_unmasked_streams(day, nav):
    """The defect that cost a set of measurements: span-keyed caches collide
    across masked/unmasked and clean/injected variants of the same span."""
    from backend.detection.cross import _compensated_frame
    from backend.rinex.solve import masked_epoch, residual_panel

    win = day[:80]
    masked = [masked_epoch(e, nav) for e in win]
    assert [e.time for e in masked] == [e.time for e in win]      # same span
    assert sum(e.n_sv for e in masked) < sum(e.n_sv for e in win)  # different data

    a, _ = _compensated_frame(win, nav)
    b, _ = _compensated_frame(masked, nav)
    assert not a.equals(b)

    assert not residual_panel(win, nav).equals(residual_panel(masked, nav))
