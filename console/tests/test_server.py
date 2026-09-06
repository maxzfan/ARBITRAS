"""Tests for the transport and replay layers.

These exist because a tick-based stale timeout shipped undetected: the arbitras
was correct and fully tested, and the bug lived entirely in follow(). Nothing
below touches an observable either -- this is all §5 contract shape.
"""
import json
import threading
import time
from pathlib import Path

import pytest

from console.arbitras.machine import Arbitras
from console.arbitras.states import TrustState
from console.replay import arbitrate, false_surrender_rate, time_to_alert
from console.server import decide, follow, read_epochs


def line(confidence, credential="VALID", bound=None):
    e = {
        "timestamp": "2026-08-20T00:00:00Z",
        "confidence": confidence,
        "credential_status": credential,
        "features": {"cn0_anomaly": 0.1},
        "geometry": {} if bound is None else {"displacement_bound_m": bound},
    }
    return json.dumps(e)


def collect(path, stale_after, seconds, poll=0.01):
    """Drain follow() on a daemon thread for `seconds` and return what it yielded."""
    got = []
    t = threading.Thread(
        target=lambda: [got.append(x) for x in follow(path, stale_after, poll)],
        daemon=True,
    )
    t.start()
    time.sleep(seconds)
    return got


# --------------------------------------------------------------- follow() timing

def test_follow_does_not_report_silence_while_producer_is_healthy(tmp_path):
    """THE REGRESSION TEST. A producer slower than the poll interval is not stale.

    Before the fix, follow() yielded None on every poll timeout, so a healthy
    producer emitting every 0.3 s was driven to SURRENDERED on clean data.
    """
    p = tmp_path / "live.jsonl"
    p.write_text("")

    def produce():
        for _ in range(4):
            time.sleep(0.25)
            with open(p, "a") as fh:
                fh.write(line(0.92) + "\n")

    threading.Thread(target=produce, daemon=True).start()
    got = collect(p, stale_after=2.0, seconds=1.4)

    assert got, "no epochs came through at all"
    assert None not in got, (
        f"healthy producer reported as silent: {got.count(None)} false stale signals"
    )

    arb = Arbitras()
    for e in got:
        d = arb.step(e)
    assert d.state is TrustState.NOMINAL, "clean data must not cost authority"


def test_follow_reports_silence_once_per_window(tmp_path):
    """Silence must still reach the arbitras -- once per window, not once per poll."""
    p = tmp_path / "live.jsonl"
    p.write_text("")
    got = collect(p, stale_after=0.2, seconds=0.75)
    nones = [g for g in got if g is None]
    assert 2 <= len(nones) <= 5, f"expected ~3 stale signals in 0.75s, got {len(nones)}"


def test_follow_yields_none_for_a_malformed_line(tmp_path):
    p = tmp_path / "live.jsonl"
    p.write_text("")
    threading.Thread(
        target=lambda: (time.sleep(0.1),
                        open(p, "a").write("{not json\n")),
        daemon=True,
    ).start()
    got = collect(p, stale_after=5.0, seconds=0.5)
    assert got == [None], f"malformed line is evidence, not a skip: {got}"


# ------------------------------------------------------------------ read_epochs

def test_read_epochs_preserves_malformed_lines_as_missing(tmp_path):
    p = tmp_path / "run.jsonl"
    p.write_text(line(0.9) + "\n" + "{broken\n" + "\n" + line(0.8) + "\n")
    rows = read_epochs(p)
    assert len(rows) == 3, "blank lines skipped, malformed kept"
    assert rows[1] is None


# ----------------------------------------------------------------------- decide

def test_layer_off_suppresses_arbitration_for_video_beat_2():
    """design.md §11a beat 2: the layer is OFF and nothing alarms."""
    arb = Arbitras()
    epoch = json.loads(line(0.05))
    payload = decide(arb, epoch, layer_on=False)
    assert payload["state"] == "NOMINAL", "beat 2 must show no alarm"
    assert payload["explanation"] is None
    assert payload["layer_on"] is False
    # Every product of the trust layer leaves the wire. Showing confidence or the
    # feature bars under a badge reading OFF contradicts the whole beat.
    assert payload["confidence"] is None
    assert payload["features"] == {}
    assert payload["geometry"] == {}, "no sky in this epoch -> nothing to keep"
    assert payload["geometry_divergence"] is None


def test_layer_off_keeps_satellite_positions_but_not_trust_flags():
    """Positions are what the receiver sees; trusted flags are what the layer
    decides. Beat 2 shows the full constellation with nothing dark."""
    arb = Arbitras()
    epoch = json.loads(line(0.05))
    epoch["geometry"] = {
        "information_ratio": 0.2, "excluded_sv": ["G07"], "displacement_bound_m": 90.0,
        "sky": [{"sv": "G07", "az": 10.0, "el": 40.0, "trusted": False},
                {"sv": "E11", "az": 200.0, "el": 25.0, "trusted": True}],
    }
    payload = decide(arb, epoch, layer_on=False)
    assert set(payload["geometry"]) == {"sky"}, "everything but positions is stripped"
    assert [s["sv"] for s in payload["geometry"]["sky"]] == ["G07", "E11"]
    assert all(s["trusted"] for s in payload["geometry"]["sky"])
    assert payload["geometry"]["sky"][0]["az"] == 10.0


def test_layer_on_arbitrates_and_explains():
    arb = Arbitras()
    epoch = json.loads(line(0.05))
    payload = decide(arb, epoch, layer_on=True)
    assert payload["state"] == "SURRENDERED"
    assert payload["explanation_verified"] is True
    assert payload["explanation"]["headline"]


def test_unverifiable_claim_is_withheld_from_display(monkeypatch):
    """§14: the console must not print a number it cannot source."""
    import console.server as srv

    def fake_explain(d):
        return {"headline": "Something diverged.", "detail": "bound is 900 m",
                "claims": [{"text": "900 m", "value": 900.0,
                            "path": "geometry.displacement_bound_m"}]}

    monkeypatch.setattr(srv, "explain", fake_explain)
    payload = srv.decide(Arbitras(), json.loads(line(0.4, bound=41.2)), layer_on=True)
    assert payload["explanation_verified"] is False
    assert "900" not in payload["explanation"]["detail"]
    assert payload["explanation_failures"]


def test_threshold_provenance_is_stamped_on_every_epoch():
    """So a placeholder threshold cannot reach the video unnoticed."""
    payload = decide(Arbitras(), json.loads(line(0.9)), layer_on=True)
    assert payload["threshold_provenance"]
    assert payload["thresholds"]["NOMINAL"]


# ----------------------------------------------------------------------- replay

def test_false_surrender_rate_is_zero_on_a_clean_stream():
    epochs = [json.loads(line(0.92)) for _ in range(300)]
    m = false_surrender_rate(arbitrate(epochs))
    assert m["fsr"] == 0.0
    assert m["downgrade_events"] == 0
    assert m["epochs"] == 300


def test_false_surrender_rate_counts_epochs_and_events_separately():
    """§10 is epoch-weighted but must be able to report event count too."""
    epochs = ([json.loads(line(0.92))] * 20
              + [json.loads(line(0.30))] * 10
              + [json.loads(line(0.92))] * 200)
    m = false_surrender_rate(arbitrate(epochs))
    assert m["epochs_below_nominal"] > 10, "hysteresis holds it down after recovery"
    assert m["downgrade_events"] == 1, "one excursion, however long"


def test_time_to_alert_measures_from_injection():
    epochs = [json.loads(line(0.92))] * 50 + [json.loads(line(0.20))] * 50
    tta = time_to_alert(arbitrate(epochs), injection_epoch=50)
    assert tta == 0, "an abrupt drop alerts on the first injected epoch"


def test_time_to_alert_is_none_when_never_alerted():
    epochs = [json.loads(line(0.92))] * 100
    assert time_to_alert(arbitrate(epochs), injection_epoch=50) is None
