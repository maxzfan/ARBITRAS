"""Route kinematics (presentation frame) -- console/mission.py."""
import math
import pytest
from console import mission as M


def test_route_length_matches_segment_sum():
    segs = [math.hypot(b[0]-a[0], b[1]-a[1]) for a, b in zip(M.ROUTE_ENU, M.ROUTE_ENU[1:])]
    assert M.route_length() == pytest.approx(sum(segs))
    assert 700 < M.route_length() < 1000, "last-tactical-mile scale"


def test_route_point_endpoints_and_clamping():
    L = M.route_length()
    assert M.route_point(0)[:2] == pytest.approx(M.ROUTE_ENU[0])
    assert M.route_point(L)[:2] == pytest.approx(M.ROUTE_ENU[-1])
    assert M.route_point(-50)[:2] == pytest.approx(M.ROUTE_ENU[0])
    assert M.route_point(L + 500)[:2] == pytest.approx(M.ROUTE_ENU[-1])


def test_route_point_hits_every_waypoint():
    acc = 0.0
    for a, b in zip(M.ROUTE_ENU, M.ROUTE_ENU[1:]):
        acc += math.hypot(b[0]-a[0], b[1]-a[1])
        assert M.route_point(acc)[:2] == pytest.approx(b, abs=1e-9)


def test_arc_length_is_monotone_and_integrates_to_length():
    ds, s, total = 0.25, 0.0, 0.0
    prev = M.route_point(0)[:2]
    while s < M.route_length():
        s += ds
        cur = M.route_point(s)[:2]
        step = math.hypot(cur[0]-prev[0], cur[1]-prev[1])
        assert step <= ds + 1e-9, "never moves further than ds along the route"
        total += step; prev = cur
    assert total == pytest.approx(M.route_length(), rel=1e-3)


def test_heading_convention_is_math_angle_ccw_from_east():
    e, n, h = M.route_point(1.0)          # first leg heads ENE
    a, b = M.ROUTE_ENU[0], M.ROUTE_ENU[1]
    assert h == pytest.approx(math.atan2(b[1]-a[1], b[0]-a[0]))
    assert 0 < h < math.pi / 2


def test_lateral_offset_sign_left_positive():
    # First leg heads ENE; a point north of it is to the LEFT of travel.
    assert M.lateral_offset(50, 60) > 0
    assert M.lateral_offset(50, -30) < 0
    on_route = M.route_point(80)[:2]
    assert abs(M.lateral_offset(*on_route)) < 1e-9
    # magnitude: perpendicular distance from a horizontal-ish leg is ~ vertical gap
    assert abs(M.lateral_offset(50, 60)) == pytest.approx(
        abs(60 - (30 * 50 / 140)) * math.cos(math.atan2(30, 140)), rel=1e-6)


def test_enu_latlon_round_trip_under_1mm():
    for e, n in M.ROUTE_ENU + [(-123.4, 456.7), (1000.0, -1000.0)]:
        lat, lon = M.enu_to_latlon(e, n)
        e2, n2 = M.latlon_to_enu(lat, lon)
        assert math.hypot(e2-e, n2-n) < 1e-3


def test_as_dict_shape_and_frame_flag():
    d = M.as_dict()
    for k in ("route", "route_length_m", "speed_m_per_epoch", "corridor_half_width_m",
              "heading_convention", "route_is_presentation_frame", "surveyed",
              "alert_limit_m", "m_per_deg_lat", "m_per_deg_lon"):
        assert k in d, k
    assert d["route_is_presentation_frame"] is True
    assert len(d["route"]) == len(M.ROUTE_ENU)
    assert all({"e", "n", "lat", "lon"} <= set(w) for w in d["route"])
    assert d["corridor_half_width_m"] == M.ALERT_LIMIT_M
    assert d["route_length_m"] == pytest.approx(M.route_length(), abs=1e-3)
    gs = d["ground_station"]
    assert {"e", "n", "lat", "lon"} <= set(gs)
    assert 100 < math.hypot(gs["e"], gs["n"]) < 150, "ground station ~125 m off the route start"


def test_demo_window_traverses_most_of_route():
    # 360-epoch demo (12:00-15:00 UTC) should cover the bulk of the route, not overshoot wildly.
    covered = 360 * M.ROUTE_SPEED_M_PER_EPOCH
    assert 0.85 * M.route_length() <= covered <= 1.05 * M.route_length()
