"""The extended contract fixture round-trips through the console.

D4 acceptance (TRACK_D.md): after fixtures/epoch.json grew
`geometry.correction` and `features.by_sv`, the arbiter must still ingest
it, explain() must still produce only verifiable claims (design.md §14 —
verify() failing is what fires the console banner), and nothing in the
fixture may read as synthetic.
"""
import json
from pathlib import Path

from console.arbiter.explain import explain, verify
from console.replay import arbitrate

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "epoch.json"


def _epochs(n=12):
    e = json.loads(FIXTURE.read_text())
    return [dict(e) for _ in range(n)]


def test_fixture_replays_with_zero_verify_failures():
    epochs = _epochs()
    decisions = arbitrate(epochs)
    assert len(decisions) == len(epochs)
    for d, e in zip(decisions, epochs):
        ex = explain(d)
        ok, failures = verify(ex, e)
        assert ok, failures                       # a failure = a banner fire


def test_fixture_carries_no_synthetic_flag():
    assert not any(e.get("_synthetic") for e in _epochs())


def test_by_sv_never_becomes_the_named_cause():
    """features.by_sv is a nested map, not a feature score; explain() must
    pick the diverging signal from the four scalars only and every claimed
    number must still resolve against the epoch."""
    epochs = _epochs(1)
    d = arbitrate(epochs)[0]
    assert isinstance(d.features.get("by_sv"), dict)
    ex = explain(d)
    assert "by_sv" not in ex["headline"]
    for c in ex["claims"]:
        assert not c["path"].startswith("features.by_sv")
    ok, failures = verify(ex, epochs[0])
    assert ok, failures
