"""The demo beats: determinism, and the refusal to invent a threshold."""
from datetime import datetime

import pytest

from backend.beats import config as cfg


@pytest.fixture(autouse=True)
def _restore_console_thresholds():
    """cfg.apply_thresholds mutates console.arbiter.states module globals --
    that is deliberate at runtime (it is the single swap point, design.md §8)
    and pollution inside a test session. Snapshot and restore, or these tests
    silently re-threshold console/tests/test_machine.py when both suites run
    together.
    """
    from console.arbiter import states
    saved = dict(states.THRESHOLDS), states.THRESHOLD_PROVENANCE
    yield
    states.THRESHOLDS.clear()
    states.THRESHOLDS.update(saved[0])
    states.THRESHOLD_PROVENANCE = saved[1]


def test_ruled_thresholds_are_the_resolved_default(monkeypatch):
    """Ruled 2026-09-06 from backend.beats.thresholds."""
    for name in cfg.THRESHOLD_NAMES:
        monkeypatch.delenv(cfg.ENV[name], raising=False)
    t = cfg.require_thresholds()
    assert (t.nominal, t.degraded, t.restricted) == (0.8247, 0.50, 0.25)
    assert "measured clean p1" in t.provenance
    assert "carried from design.md" in t.provenance


def test_strict_mode_still_names_the_missing_value(monkeypatch):
    """The refusal path is kept: a future detector change must hit it rather
    than silently inheriting these values."""
    for name in cfg.THRESHOLD_NAMES:
        monkeypatch.delenv(cfg.ENV[name], raising=False)
    with pytest.raises(cfg.MissingThreshold) as exc:
        cfg.require_thresholds(allow_ruled=False)
    assert "nominal" in str(exc.value)
    with pytest.raises(cfg.MissingThreshold) as exc:
        cfg.require_thresholds(nominal=0.8, allow_ruled=False)
    assert "degraded" in str(exc.value)


def test_an_override_is_not_stamped_with_the_ruled_provenance(monkeypatch):
    for name in cfg.THRESHOLD_NAMES:
        monkeypatch.delenv(cfg.ENV[name], raising=False)
    t = cfg.require_thresholds(nominal=0.70)
    assert t.nominal == 0.70
    assert "OVERRIDDEN" in t.provenance
    assert "measured clean p1" not in t.provenance


def test_environment_overrides_the_ruled_values(monkeypatch):
    monkeypatch.setenv(cfg.ENV["nominal"], "0.8")
    monkeypatch.setenv(cfg.ENV["degraded"], "0.7")
    monkeypatch.setenv(cfg.ENV["restricted"], "0.6")
    t = cfg.require_thresholds()
    assert (t.nominal, t.degraded, t.restricted) == (0.8, 0.7, 0.6)
    assert "OVERRIDDEN" in t.provenance


def test_thresholds_must_be_strictly_decreasing():
    with pytest.raises(ValueError):
        cfg.Thresholds(nominal=0.6, degraded=0.7, restricted=0.5)


def test_supplied_thresholds_overwrite_the_stale_provenance_string():
    """A stale measured provenance must not be displayed next to numbers it
    did not produce."""
    from console.arbiter import states
    t = cfg.Thresholds(0.8, 0.7, 0.6, source="test")
    cfg.apply_thresholds(t)          # restored by the autouse fixture
    assert states.THRESHOLD_PROVENANCE == t.provenance
    assert states.THRESHOLDS[states.TrustState.NOMINAL] == 0.8


def test_build_is_deterministic():
    """Same input, same output. A beat that renders differently on the second
    run is not a beat."""
    from backend.beats.core import build
    w = (datetime(2026, 8, 20, 12, 25), datetime(2026, 8, 20, 12, 35))
    a = build(window=w, layer_on=True)
    b = build(window=w, layer_on=True)
    assert a.records == b.records
    assert a.provenance == b.provenance
    assert (a.displacement_m == b.displacement_m).all()


def test_layer_off_scores_nothing_and_still_emits_records():
    from backend.beats.core import build
    w = (datetime(2026, 8, 20, 12, 25), datetime(2026, 8, 20, 12, 35))
    off = build(window=w, layer_on=False, geometry=False)
    assert all(r["confidence"] == 1.0 for r in off.records)
    assert all(r["geometry"] is None for r in off.records)
    assert all(r["score_detail"]["combine_mode"] == "layer_off"
               for r in off.records)


def test_no_attack_window_says_so_in_its_provenance():
    from backend.beats.core import build
    w = (datetime(2026, 8, 20, 12, 0), datetime(2026, 8, 20, 12, 10))
    beat = build(window=w, onset=datetime(2026, 8, 20, 23, 59))
    assert "NO ATTACK" in beat.provenance
    assert beat.displacement_m.max() == pytest.approx(0.0, abs=1e-6)
