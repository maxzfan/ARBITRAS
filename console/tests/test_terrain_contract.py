"""Track E contract extension (tracks/TRACK_E.md): the fixture's `terrain`
block, the fifth feature and the bound-source fields round-trip through the
arbiter and the explanation verifier with zero failures."""
import json
from pathlib import Path

from console.arbiter.explain import explain, verify
from console.arbiter.machine import Arbiter
from console.arbiter.states import TrustState

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "epoch.json"


def test_fixture_terrain_block_round_trips_with_zero_verifier_fires():
    ep = json.loads(FIXTURE.read_text())
    assert ep["terrain"]["available"] is True
    assert ep["terrain"]["sensor"]["source"] == "simulated"
    assert ep["geometry"]["bound_source"] in ("residual", "terrain")
    assert ep["geometry"]["residual_bound_m"] is not None
    assert "terrain_mismatch" in ep["features"]
    assert ep["geometry"]["correction"]["checks"]["terrain_consistent"] in (True, False, None)
    assert "terrain_mismatch" in ep["score_detail"]["features_scored"]
    d = Arbiter().step(ep)
    assert d.terrain == ep["terrain"]
    ex = explain(d)
    ok, failures = verify(ex, ep)
    assert ok, failures
    if d.state is TrustState.DEGRADED:
        assert "Terrain:" in (d.advisory or "")
