"""The real-data demo streams (backend/demo.py) meet the §5 contract.

Skips if the streams have not been generated (`python -m backend.demo`).
"""
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

DEMO = Path("out/demo.jsonl")
CLEAN = Path("out/clean.jsonl")
REQUIRED = {"timestamp", "confidence", "credential_status", "position",
            "features", "geometry", "satellites_tracked"}


def _load(p):
    if not p.exists():
        pytest.skip(f"{p} not generated; run python -m backend.demo")
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


@pytest.fixture(scope="module")
def demo():
    return _load(DEMO)


@pytest.fixture(scope="module")
def clean():
    return _load(CLEAN)


def test_every_record_has_the_contract_keys_and_truth(demo):
    for r in demo:
        assert REQUIRED <= r.keys()
        assert "_truth" in r and {"lat", "lon"} <= r["_truth"].keys()
        assert 0.0 <= r["confidence"] <= 1.0


def test_nothing_is_synthetic(demo, clean):
    assert not any("_synthetic" in r for r in demo)
    assert not any("_synthetic" in r for r in clean)


def test_sky_is_real_and_trusted_flags_match_excluded(demo):
    for r in demo:
        g = r["geometry"]
        assert g["sky"], "empty sky"
        svs = {s["sv"] for s in g["sky"]}
        dark = {s["sv"] for s in g["sky"] if not s["trusted"]}
        assert dark == set(g["excluded_sv"]) & svs
        for s in g["sky"]:
            assert 10.0 <= s["el"] <= 90.0 and 0.0 <= s["az"] < 360.0
            assert s["sv"][0] in "GE"


def test_track_c_fields_are_null_not_fabricated(demo):
    for r in demo:
        g = r["geometry"]
        assert g["information_ratio"] is None
        assert g["displacement_bound_m"] is None
        assert g["next_best_observation"] is None


def test_credential_schedule_is_ordered_with_the_tesla_lag(demo):
    creds = [r["credential_status"] for r in demo]
    order = [c for i, c in enumerate(creds) if i == 0 or c != creds[i - 1]]
    assert order == ["VALID", "PENDING", "EXPIRED"]
    assert creds.count("PENDING") == 20, "T_int 10 x d 2 (design.md section 9)"


def test_clean_epochs_have_truth_equal_to_position(clean):
    for r in clean:
        assert r["_truth"]["lat"] == r["position"]["lat"]
        assert r["_truth"]["lon"] == r["position"]["lon"]


def test_timestamps_are_contiguous_30s(demo):
    ts = [datetime.fromisoformat(r["timestamp"].replace("Z", "+00:00")) for r in demo]
    assert all(b - a == timedelta(seconds=30) for a, b in zip(ts, ts[1:]))


def test_attack_window_is_marked_and_bounded(demo):
    stages = [r.get("_attack", {}).get("stage") for r in demo]
    attack = [i for i, s in enumerate(stages) if s and s != "CLEAN"]
    assert attack, "no attack epochs in demo"
    assert attack[0] == 200 and len(attack) == 120
    assert all(s == "CLEAN" for s in stages[:200]) and all(s == "CLEAN" for s in stages[320:])
