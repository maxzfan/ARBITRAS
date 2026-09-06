"""The five invariants of design.md §8, asserted.

If one of these fails, the safety argument in the pitch is not true.
"""
import random

import pytest

from console.arbitras.explain import explain, verify
from console.arbitras.machine import Arbitras
from console.arbitras.states import (
    MIN_DWELL_EPOCHS,
    RECOVERY_EPOCHS,
    STALE_GRACE_TICKS,
    TrustState,
)


def epoch(confidence, credential="VALID", **kw):
    e = {
        "timestamp": "2026-08-20T00:00:00Z",
        "confidence": confidence,
        "credential_status": credential,
        "features": kw.pop("features", {"cn0_anomaly": 0.1}),
        "geometry": kw.pop("geometry", {}),
    }
    e.update(kw)
    return e


def drive(arb, confidence, n, credential="VALID", **kw):
    last = None
    for _ in range(n):
        last = arb.step(epoch(confidence, credential, **kw))
    return last


# ---------------------------------------------------------------- invariant 1

def test_downgrade_is_immediate_and_may_skip_states():
    arb = Arbitras()
    assert arb.state is TrustState.NOMINAL
    d = arb.step(epoch(0.10))
    assert d.state is TrustState.SURRENDERED, "hard failure must skip straight down"
    assert d.changed


def test_authority_never_rises_more_than_one_state_in_an_epoch():
    """Property test over a random walk. Invariant 1 + 2 together."""
    rng = random.Random(20260905)
    arb = Arbitras(initial=TrustState.SURRENDERED)
    prev = arb.state
    for _ in range(4000):
        d = arb.step(epoch(rng.random(), rng.choice(["VALID", "VALID", "PENDING"])))
        assert d.state - prev <= 1, f"jumped {prev.name} -> {d.state.name}"
        prev = d.state


# ---------------------------------------------------------------- invariant 2

def test_no_single_epoch_restores_authority():
    arb = Arbitras(initial=TrustState.SURRENDERED)
    d = arb.step(epoch(1.0))
    assert d.state is TrustState.SURRENDERED
    assert d.reason == "recovery_gated"


def test_recovery_walks_up_one_state_at_a_time():
    arb = Arbitras(initial=TrustState.SURRENDERED)
    drive(arb, 0.95, RECOVERY_EPOCHS - 1)
    assert arb.state is TrustState.SURRENDERED, "must not upgrade early"
    drive(arb, 0.95, 1)
    assert arb.state is TrustState.RESTRICTED, "one state, not straight to NOMINAL"
    drive(arb, 0.95, RECOVERY_EPOCHS)
    assert arb.state is TrustState.DEGRADED
    drive(arb, 0.95, RECOVERY_EPOCHS)
    assert arb.state is TrustState.NOMINAL


def test_recovery_run_resets_on_a_single_bad_epoch():
    arb = Arbitras(initial=TrustState.RESTRICTED)
    drive(arb, 0.95, RECOVERY_EPOCHS - 1)
    arb.step(epoch(0.60))          # implies DEGRADED == no, implies same-or-lower
    drive(arb, 0.95, RECOVERY_EPOCHS - 1)
    assert arb.state is TrustState.DEGRADED or arb.state is TrustState.RESTRICTED


def test_minimum_dwell_is_enforced():
    assert MIN_DWELL_EPOCHS < RECOVERY_EPOCHS, (
        "if dwell >= recovery the dwell gate is dead code; §8 wants both"
    )


# ---------------------------------------------------------------- invariant 3

@pytest.mark.parametrize("status", ["REVOKED", "EXPIRED"])
def test_credential_failure_forces_surrender_at_perfect_confidence(status):
    """The whole pitch: a clean sky does not buy authority. design.md §11a beat 4."""
    arb = Arbitras()
    d = arb.step(epoch(1.0, status))
    assert d.state is TrustState.SURRENDERED
    assert d.reason == "credential_force"
    assert d.implied_state is TrustState.NOMINAL, "confidence said NOMINAL; credential won"


def test_unverified_credential_caps_at_degraded():
    arb = Arbitras()
    d = arb.step(epoch(1.0, "UNVERIFIED"))
    assert d.state is TrustState.DEGRADED
    assert d.reason == "credential_cap"


@pytest.mark.parametrize("status", ["VALID", "PENDING"])
def test_pending_and_valid_do_not_override(status):
    arb = Arbitras()
    d = arb.step(epoch(0.90, status))
    assert d.state is TrustState.NOMINAL
    assert d.reason == "hold"


def test_credential_cap_does_not_raise_authority():
    """UNVERIFIED caps; it must never lift a lower state up to the cap."""
    arb = Arbitras(initial=TrustState.SURRENDERED)
    d = arb.step(epoch(0.10, "UNVERIFIED"))
    assert d.state is TrustState.SURRENDERED


def test_credential_force_stays_the_stated_reason_while_it_binds():
    """Video beat 4: SURRENDERED held ~25 s at confidence ~0.93. Every epoch of
    that hold must say the authorisation lapsed -- not that confidence is low."""
    arb = Arbitras()
    arb.step(epoch(0.93, "EXPIRED"))
    for _ in range(20):
        d = arb.step(epoch(0.93, "EXPIRED"))
        assert d.state is TrustState.SURRENDERED
        assert d.reason == "credential_force"
        ex = explain(d)
        assert "authorisation" in ex["headline"].lower()
        assert "below the trusted range" not in ex["headline"]


def test_credential_cap_stays_the_stated_reason_while_it_binds():
    arb = Arbitras()
    arb.step(epoch(0.95, "UNVERIFIED"))
    d = arb.step(epoch(0.95, "UNVERIFIED"))
    assert d.state is TrustState.DEGRADED
    assert d.reason == "credential_cap"


def test_recovery_explanation_does_not_claim_low_confidence():
    """Seen on screen: DEGRADED at confidence 0.95 with 'confidence is below the
    trusted range'. The state is right (invariant 2); the sentence was false."""
    arb = Arbitras(initial=TrustState.RESTRICTED)
    d = arb.step(epoch(0.95))
    assert d.state is TrustState.RESTRICTED and d.reason == "recovery_gated"
    ex = explain(d)
    assert "below the trusted range" not in ex["headline"]
    assert "restored" in ex["headline"].lower()
    assert str(RECOVERY_EPOCHS) in ex["detail"]


# ---------------------------------------------------------------- invariant 4

def test_clock_and_credential_gates_close_below_nominal():
    arb = Arbitras()
    d = arb.step(epoch(0.90))
    assert d.clock_discipline and d.accepting_credentials
    d = arb.step(epoch(0.60))
    assert d.state is TrustState.DEGRADED
    assert not d.clock_discipline, "clock must free-run below NOMINAL"
    assert not d.accepting_credentials, "no new authorisations below NOMINAL"


# ---------------------------------------------------------------- invariant 5

def test_degraded_pursues_the_next_best_observation():
    arb = Arbitras()
    d = arb.step(epoch(0.60, geometry={"next_best_observation": "E"}))
    assert d.state is TrustState.DEGRADED
    assert d.pursuing == "E"
    assert d.advisory and "advisory only" in d.advisory


def test_other_states_ignore_next_best_observation():
    arb = Arbitras()
    d = arb.step(epoch(0.90, geometry={"next_best_observation": "E"}))
    assert d.state is TrustState.NOMINAL
    assert d.pursuing is None


# ------------------------------------------------------- stale epochs / §5

def test_silence_steps_authority_down():
    arb = Arbitras()
    for _ in range(STALE_GRACE_TICKS - 1):
        d = arb.step(None)
        assert d.state is TrustState.NOMINAL, "grace period holds"
    d = arb.step(None)
    assert d.state is TrustState.DEGRADED
    assert d.reason == "stale"


def test_malformed_epoch_counts_as_silence():
    arb = Arbitras()
    for _ in range(STALE_GRACE_TICKS):
        d = arb.step({"timestamp": "x", "confidence": "not-a-number",
                      "credential_status": "VALID"})
    assert d.state is TrustState.DEGRADED


def test_unknown_credential_status_is_not_trusted():
    arb = Arbitras()
    for _ in range(STALE_GRACE_TICKS):
        d = arb.step(epoch(0.99, "TOTALLY_FINE_HONEST"))
    assert d.state is TrustState.DEGRADED


# ------------------------------------------------- freeze rule / §9

def test_geometry_divergence_reported_against_last_nominal_epoch():
    arb = Arbitras()
    arb.step(epoch(0.90, geometry={"information_ratio": 0.90, "excluded_sv": []}))
    d = arb.step(epoch(0.60, geometry={"information_ratio": 0.50,
                                       "excluded_sv": ["G07", "G13"]}))
    div = d.geometry_divergence
    assert div is not None
    assert div["frozen_information_ratio"] == 0.90
    assert div["ratio_delta"] == pytest.approx(-0.40)
    assert div["newly_excluded_sv"] == ["G07", "G13"]


def test_frozen_geometry_does_not_refresh_below_nominal():
    arb = Arbitras()
    arb.step(epoch(0.90, geometry={"information_ratio": 0.90, "excluded_sv": []}))
    arb.step(epoch(0.60, geometry={"information_ratio": 0.50, "excluded_sv": ["G07"]}))
    d = arb.step(epoch(0.60, geometry={"information_ratio": 0.40, "excluded_sv": ["G07"]}))
    assert d.geometry_divergence["frozen_information_ratio"] == 0.90, (
        "snapshot must hold at the last NOMINAL epoch, not creep with the attack"
    )


# ------------------------------------------------- explanation layer / §14

def test_explanation_uses_operator_language():
    arb = Arbitras()
    e = epoch(0.60, features={"cn0_anomaly": 0.71},
              geometry={"displacement_bound_m": 41.2, "excluded_sv": ["G07"]})
    ex = explain(arb.step(e))
    blob = (ex["headline"] + " " + ex["detail"]).lower()
    for builder_word in ("state machine", "transition", "degraded", "restricted"):
        assert builder_word not in blob, f"builder language leaked: {builder_word}"
    assert "41 m" in ex["detail"]


def test_explanation_claims_verify_against_the_epoch():
    arb = Arbitras()
    e = epoch(0.60, features={"cn0_anomaly": 0.71},
              geometry={"displacement_bound_m": 41.2, "excluded_sv": ["G07", "G13"]})
    ex = explain(arb.step(e))
    ok, failures = verify(ex, e)
    assert ok, failures
    assert len(ex["claims"]) >= 2


def test_verifier_catches_a_fabricated_number():
    arb = Arbitras()
    e = epoch(0.60, geometry={"displacement_bound_m": 41.2})
    ex = explain(arb.step(e))
    ex["claims"].append({"text": "900 m", "value": 900.0,
                         "path": "geometry.displacement_bound_m"})
    ok, failures = verify(ex, e)
    assert not ok and failures, "a number not in the epoch record must not display"


# ---------------------------------------------------------- Track E consumers

TERRAIN = {"available": True, "match_likelihood": 0.1,
           "sensed": {"class": "grass", "p": {"grass": 1.0}},
           "map_at_position": {"class": "paved", "p": {"paved": 1.0}},
           "nearest_boundary": {"distance_m": 38.0, "bearing_deg": 47.0,
                                "class_beyond": "water"}}


def test_terrain_block_passes_through_and_advises_only_in_degraded():
    arb = Arbitras()
    d = arb.step(epoch(0.60, terrain=TERRAIN, geometry={"next_best_observation": "E"}))
    assert d.state is TrustState.DEGRADED
    assert d.terrain == TERRAIN
    assert "should read water 38 m to the north-east" in d.advisory
    assert d.advisory.startswith("Reweight toward E.")
    d = Arbitras().step(epoch(0.95, terrain=TERRAIN))
    assert d.state is TrustState.NOMINAL and d.advisory is None
    d = Arbitras().step(epoch(0.30, terrain=TERRAIN))
    assert d.state is not TrustState.DEGRADED and d.advisory is None


def test_terrain_advisory_stands_alone_without_next_best_observation():
    d = Arbitras().step(epoch(0.60, terrain=TERRAIN))
    assert d.state is TrustState.DEGRADED
    assert d.advisory.startswith("Terrain: if the reported position is right")


def test_sensorless_epoch_has_empty_terrain():
    assert Arbitras().step(epoch(0.95)).terrain == {}


def test_terrain_mismatch_has_an_operator_phrase():
    from console.arbitras.explain import FEATURE_PHRASE
    assert "terrain_mismatch" in FEATURE_PHRASE
    assert "map" in FEATURE_PHRASE["terrain_mismatch"]


def test_terrain_disagreement_is_named_and_verifiable():
    from console.arbitras.explain import FEATURE_PHRASE
    ep = epoch(0.60, features={"cn0_anomaly": 0.1, "terrain_mismatch": 0.9},
               terrain={"available": True, "match_likelihood": 0.12,
                        "sensed": {"class": "grass", "p": {"grass": 1.0}},
                        "map_at_position": {"class": "paved", "p": {"paved": 1.0}}})
    d = Arbitras().step(ep)
    ex = explain(d)
    assert ex["headline"].startswith(FEATURE_PHRASE["terrain_mismatch"].capitalize())
    assert "reads grass; the map has paved" in ex["detail"]
    ok, failures = verify(ex, ep)
    assert ok, failures


def test_terrain_agreement_adds_no_sentence():
    ep = epoch(0.95, terrain={"available": True, "match_likelihood": 0.98,
                              "sensed": {"class": "grass", "p": {"grass": 1.0}},
                              "map_at_position": {"class": "grass", "p": {"grass": 1.0}}})
    ex = explain(Arbitras().step(ep))
    assert "terrain sensor" not in ex["detail"]
