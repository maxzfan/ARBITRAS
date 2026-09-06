"""RECON stationary deduction (backend/missions_recon.py) on a synthetic 5-record fixture.

Two NOMINAL epochs, then a 100 m eastward step of the believed fix with a
250 m jump in the GPS clock and four GPS satellites dropped: confidence 0.3
puts the arbitras in SURRENDERED (progress gain 0 -> stationary in the frame).
"""
import math

import pytest

from backend import missions_recon as MR
from console.mission import LAT, LON, M_PER_DEG_LAT, M_PER_DEG_LON

PL = 5.0


def _rec(j, conf, east_m=0.0, clock_jump_m=0.0, gps_trusted=True, position=True):
    lat, lon = LAT, LON
    sky = [{"sv": f"G0{i}", "az": 10.0 * i, "el": 40.0, "trusted": gps_trusted} for i in (1, 2, 3, 4)]
    sky += [{"sv": "E01", "az": 200.0, "el": 50.0, "trusted": True},
            {"sv": "C01", "az": 300.0, "el": 30.0, "trusted": True}]
    return {
        "timestamp": f"2026-08-20T12:{j:02d}:00Z",
        "confidence": conf,
        "credential_status": "VALID",
        "position": {"lat": lat, "lon": lon + east_m / M_PER_DEG_LON, "alt": 70.0} if position else None,
        "_truth": {"lat": lat, "lon": lon},
        "features": {},
        "geometry": {
            "information_ratio": 1.0 if gps_trusted else 0.8,
            "excluded_sv": [] if gps_trusted else ["G01", "G02", "G03", "G04"],
            "sky": sky,
            "correction": {"corrected_position": {"lat": lat + 0.5 / M_PER_DEG_LAT, "lon": lon, "alt": 70.0},
                           "protection_level_m": PL, "correction_ok": True},
        },
        "_solution": {"clock_bias_m": {"G": 70.0 + clock_jump_m, "E": 70.0}, "displacement_m": abs(east_m)},
    }


@pytest.fixture
def fixture():
    return [_rec(0, 0.9), _rec(1, 0.9),
            _rec(2, 0.3, east_m=100.0, clock_jump_m=250.0, gps_trusted=False),
            _rec(3, 0.3, east_m=100.0, clock_jump_m=250.0, gps_trusted=False),
            _rec(4, 0.3, east_m=100.0, clock_jump_m=250.0, gps_trusted=False)]


def _blocks(recs):
    return [r["geometry"]["correction"][MR.BLOCK] for r in recs]


def test_block_on_every_record_and_additive(fixture):
    before = [dict(r["geometry"]["correction"]) for r in fixture]
    out = MR.stationary_deduction(fixture, {})
    assert out is fixture
    for r, b in zip(out, before):
        corr = r["geometry"]["correction"]
        assert MR.BLOCK in corr
        assert {k: v for k, v in corr.items() if k != MR.BLOCK} == b     # nothing else touched


def test_anchor_is_last_nominal_fix_and_refreshed_while_nominal(fixture):
    b = _blocks(MR.stationary_deduction(fixture, {}))
    assert [x["anchored"] for x in b] == [False, False, True, True, True]
    assert [x["anchor_epoch"] for x in b] == [0, 1, 1, 1, 1]
    assert b[1]["deduced_offset"]["mag_m"] == 0.0
    assert b[2]["anchor"] == pytest.approx({"lat": LAT, "lon": LON})
    # frame: epoch 0 at s=0, 2.2 m per NOMINAL epoch, gain 0 in SURRENDERED
    assert [x["frame"]["s_m"] for x in b] == [0.0, 2.2, 2.2, 2.2, 2.2]
    assert [x["frame"]["stationary"] for x in b] == [False, False, True, True, True]
    assert b[2]["frame"]["reason"] == "halted:SURRENDERED"


def test_deduced_offset_bearing_and_agreement(fixture):
    b = _blocks(MR.stationary_deduction(fixture, {}))
    off = b[2]["deduced_offset"]
    assert off["mag_m"] == pytest.approx(100.0, abs=0.05)
    assert off["e"] == pytest.approx(100.0, abs=0.05) and abs(off["n"]) < 0.05
    assert off["bearing_deg"] == pytest.approx(90.0, abs=0.1)
    # the anchor (truth) vs the corrected fix placed 0.5 m north of it
    assert b[2]["agreement_m"] == pytest.approx(0.5, abs=0.02)
    assert b[1]["agreement_m"] == pytest.approx(0.5, abs=0.02)


def test_stationary_readouts(fixture):
    b = _blocks(MR.stationary_deduction(fixture, {}))
    assert b[2]["drag"] is None                        # first epoch of the window: nothing to measure yet
    assert b[4]["drag"]["epochs"] == 2 and b[4]["drag"]["since_epoch"] == 2
    assert b[4]["drag"]["rate_m_per_epoch"] == 0.0     # the fix did not move between 2 and 4
    assert b[4]["emitter_bearing_deg"] == pytest.approx(90.0, abs=0.1)
    assert "exceeds PL" in b[4]["bearing_gate"]
    assert b[4]["path_delay_m"] == pytest.approx(250.0, abs=0.1)
    assert b[4]["emitter_standoff_estimate_m"] == b[4]["path_delay_m"]
    assert b[4]["path_delay_source"].startswith("_solution.clock_bias_m")
    assert b[1]["path_delay_m"] is None and b[1]["emitter_bearing_deg"] is None
    assert b[4]["attribution"]["constellation"] == "G"
    assert "excluded_sv" in b[4]["attribution"]["source"]


def test_report_tag(fixture):
    b = _blocks(MR.stationary_deduction(fixture, {}))
    assert b[0]["report_tag"] == "NOMINAL · on G+E+C · PL 5.0 m"
    assert b[3]["report_tag"] == "SURRENDERED · on E+C · G dropped · PL 5.0 m · clock in holdover"


def test_bearing_gated_inside_protection_level():
    recs = [_rec(0, 0.9), _rec(1, 0.3, east_m=0.5, gps_trusted=False), _rec(2, 0.3, east_m=0.5, gps_trusted=False)]
    b = _blocks(MR.stationary_deduction(recs, {}))
    assert b[2]["anchored"] and b[2]["frame"]["stationary"]
    assert b[2]["emitter_bearing_deg"] is None
    assert "inside" in b[2]["bearing_gate"]
    assert b[2]["emitter_standoff_estimate_m"] is None


def test_missing_position_does_not_crash():
    recs = [_rec(0, 0.9), _rec(1, 0.3, position=False), _rec(2, 0.3, east_m=50.0)]
    b = _blocks(MR.stationary_deduction(recs, {}))
    assert b[1]["deduced_offset"] is None and b[1]["anchored"] is True
    assert b[2]["deduced_offset"]["mag_m"] == pytest.approx(50.0, abs=0.05)


def test_op_dwell_from_speed_and_props():
    # 2.2 m/epoch along the RECON loop with no halt: OP-1 (s 165.6) around epoch 75
    s = MR.progress(["NOMINAL"] * 90)
    inside = [j for j in range(90) if MR.dwell_prop(s[j]) and MR.dwell_prop(s[j])["label"] == "OP-1"]
    assert inside and 68 <= inside[0] <= 76 and 75 in inside
    assert MR.dwell_prop(s[30]) is None


def test_section_write_is_idempotent(tmp_path, fixture):
    recs = MR.stationary_deduction(fixture, {})
    m = MR.measure(recs, pre=2, attack=3)
    doc = tmp_path / "prov.md"
    doc.write_text("# head\n\n## other\n\nx\n")
    MR.write_section(doc, MR.section(m))
    MR.write_section(doc, MR.section(m))
    text = doc.read_text()
    assert text.count(MR.SECTION) == 1 and "## other" in text
    assert m["anchor_epoch"] == 1 and m["anchored_attack"] == 3
