"""Tests for real satellite sky geometry.

The bug these mostly guard against is not a crash. It is a skyplot full of
satellites that look completely plausible and are wrong -- duplicated Galileo
PRNs at identical azimuth and elevation, or a whole constellation silently
absent because its positions came back NaN. Both happened during development.
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.geometry.skyview import (
    ELEVATION_MASK_DEG,
    KEPLERIAN,
    _base_prn,
    sanity_check,
    sky_at,
)

T0 = datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc)
NOON = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def sky():
    return sky_at(NOON)


# ------------------------------------------------------------ the Galileo trap

def test_base_prn_strips_nav_message_suffix():
    """georinex splits Galileo by message type, not satellite."""
    assert _base_prn("E02") == "E02"
    assert _base_prn("E02_1") == "E02"
    assert _base_prn("E14_3") == "E14"
    assert _base_prn("G07") == "G07"


def test_no_duplicate_satellites(sky):
    names = [s["sv"] for s in sky]
    assert len(names) == len(set(names)), f"duplicated PRNs: {names}"


def test_galileo_is_not_quadrupled(sky):
    """E once showed 39 visible because sv_id 14 / 141 / 142 / 143 were distinct."""
    e = [s for s in sky if s["sv"].startswith("E")]
    assert 4 <= len(e) <= 18, f"{len(e)} Galileo visible -- dedupe is wrong"


def test_no_two_satellites_share_a_sky_position(sky):
    """The failure mode that looks fine: same satellite, four entries, one spot."""
    spots = {(s["az"], s["el"]) for s in sky}
    assert len(spots) == len(sky), "two satellites at an identical az/el"


# --------------------------------------------------------------- ranges/sanity

def test_elevation_within_mask_and_zenith(sky):
    assert all(ELEVATION_MASK_DEG <= s["el"] <= 90.0 for s in sky)


def test_azimuth_is_a_compass_bearing(sky):
    assert all(0.0 <= s["az"] < 360.0 for s in sky)


def test_visible_count_is_physically_plausible(sky):
    """Mid-latitude, 10 deg mask: order 8-14 per constellation. Not 2, not 40."""
    for prefix in KEPLERIAN.values():
        n = len([s for s in sky if s["sv"].startswith(prefix)])
        assert 4 <= n <= 20, f"{prefix}: {n} visible is not physical"


def test_every_declared_constellation_actually_appears(sky):
    """Guards the BeiDou failure: parsed fine, propagated to all-NaN, vanished."""
    present = {s["sv"][0] for s in sky}
    assert set(KEPLERIAN.values()) <= present


def test_sanity_check_passes():
    r = sanity_check(NOON)
    assert r["duplicate_prns"] == 0
    assert r["constellations_missing"] == []
    assert r["visible_total"] > 8


def test_sorted_by_descending_elevation(sky):
    els = [s["el"] for s in sky]
    assert els == sorted(els, reverse=True)


# ------------------------------------------------------------------- it moves

def test_the_sky_changes_over_the_run():
    """Satellites rise and set. A static sky means the time base is ignored."""
    a = {s["sv"] for s in sky_at(T0)}
    b = {s["sv"] for s in sky_at(T0 + timedelta(seconds=30 * 519))}
    assert a != b, "identical satellites 4.3 h apart -- propagation is frozen"


def test_elevation_moves_for_a_satellite_visible_at_both_ends():
    a = {s["sv"]: s["el"] for s in sky_at(T0)}
    b = {s["sv"]: s["el"] for s in sky_at(T0 + timedelta(seconds=30 * 200))}
    shared = set(a) & set(b)
    assert shared
    assert any(abs(a[sv] - b[sv]) > 1.0 for sv in shared)


# ------------------------------------------------------------ fixture contract

@pytest.fixture(scope="module")
def fixture_rows():
    p = Path("out/fixture_stream.jsonl")
    if not p.exists():
        pytest.skip("run `python -m console.fixture_stream` first")
    return [json.loads(l) for l in p.open() if l.strip()]


def test_every_epoch_carries_a_non_empty_sky(fixture_rows):
    assert all(r["geometry"]["sky"] for r in fixture_rows)


def test_trusted_flags_agree_with_excluded_sv(fixture_rows):
    for r in fixture_rows:
        g = r["geometry"]
        excluded = set(g["excluded_sv"])
        untrusted = {s["sv"] for s in g["sky"] if not s["trusted"]}
        assert untrusted == excluded, (
            f"epoch {r['timestamp']}: excluded={excluded} untrusted={untrusted}")


def test_excluded_satellites_are_actually_in_the_sky(fixture_rows):
    """An excluded SV that is below the horizon cannot be rendered going dark."""
    for r in fixture_rows:
        g = r["geometry"]
        names = {s["sv"] for s in g["sky"]}
        assert set(g["excluded_sv"]) <= names


def test_satellites_tracked_equals_the_trusted_count(fixture_rows):
    for r in fixture_rows:
        trusted = sum(1 for s in r["geometry"]["sky"] if s["trusted"])
        assert r["satellites_tracked"] == trusted


def test_fixture_is_still_stamped_synthetic(fixture_rows):
    """Real sky geometry must not launder the rest of the fixture into 'data'."""
    assert all(r["_synthetic"] is True for r in fixture_rows)
