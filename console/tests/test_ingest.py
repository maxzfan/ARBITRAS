"""The real-data demo streams (backend/demo.py) meet the §5 contract.

Skips if the streams have not been generated (`python -m backend.demo`).
"""
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from backend.demo import (ATTACK_EPOCHS, DISCLOSURE_LAG_INTERVALS, LEGIBLE_EPOCHS,
                          PENDING_EPOCHS, POST_EXPIRED_EPOCHS, POST_VALID_EPOCHS,
                          PRE_EPOCHS, T_INT_EPOCHS)
from console.arbiter.states import TrustState
from console.replay import arbitrate

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
    # PENDING is the disclosure lag and nothing else: derived from T_int x d
    # (design.md section 9), never a literal.
    assert PENDING_EPOCHS == T_INT_EPOCHS * DISCLOSURE_LAG_INTERVALS
    assert creds.count("PENDING") == PENDING_EPOCHS


def test_each_credential_state_is_legible_at_demo_rate(demo):
    """Video beat 4 (design.md section 11a/11b): each credential state must hold
    >= LEGIBLE_EPOCHS (3 s at the 15 epochs/s demo rate) so it reads on screen."""
    creds = [r["credential_status"] for r in demo]
    tail = creds[PRE_EPOCHS + ATTACK_EPOCHS:]
    assert tail.count("VALID") == POST_VALID_EPOCHS >= LEGIBLE_EPOCHS
    assert tail.count("PENDING") == PENDING_EPOCHS >= LEGIBLE_EPOCHS
    assert tail.count("EXPIRED") == POST_EXPIRED_EPOCHS >= LEGIBLE_EPOCHS
    # No attack epoch may carry a lapsed credential: the lapse is beat 4, after
    # the attack has stopped, under a clean sky.
    assert all(c == "VALID" for c in creds[:PRE_EPOCHS + ATTACK_EPOCHS])


def test_credential_lapse_forces_a_visible_drop(demo):
    """The credential-force transition must be a drop the viewer can see: the
    epoch before EXPIRED is not already SURRENDERED, the first EXPIRED epoch is
    SURRENDERED for reason credential_force, and it holds to the end."""
    creds = [r["credential_status"] for r in demo]
    i0 = creds.index("EXPIRED")
    decisions = arbitrate(demo)
    assert decisions[i0 - 1].state is not TrustState.SURRENDERED
    assert decisions[i0].state is TrustState.SURRENDERED
    assert decisions[i0].reason == "credential_force"
    assert all(d.state is TrustState.SURRENDERED for d in decisions[i0:])


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
    assert attack[0] == PRE_EPOCHS and len(attack) == ATTACK_EPOCHS
    end = PRE_EPOCHS + ATTACK_EPOCHS
    assert all(s == "CLEAN" for s in stages[:PRE_EPOCHS]) and all(s == "CLEAN" for s in stages[end:])
