"""Route-constrained terrain fix (backend/missions_casevac.py) on a synthetic
raster and route: three class bands across a straight 200 m route, a receiver
that never moves, a believed position walked ahead along the route from epoch
30 at 3 m/epoch (the frame moves the true point at 2 m/epoch)."""
import math

import numpy as np
import pytest

from backend.missions_casevac import (RouteFix, augment, default_window, enu_to_lla, project_onto_route,
                                      route_point, route_schedule)
from backend.terrain.rastermap import UNKNOWN, RasterMap
from backend.terrain.sensor import ConfusionSensor, confusion_from_diag

CLASSES = ["grass", "paved", "building"]
ROUTE = ((0.0, 0.0), (200.0, 0.0))
SPEED = 2.0
GAIN = {"NOMINAL": 1.0, "DEGRADED": 1.0, "RESTRICTED": 1.0, "SURRENDERED": 0.0}
CCP = (200.0, 0.0)
CCP_R = 15.0


def banded_map(bands=((40.0, 1), (90.0, 2), (140.0, 0))) -> RasterMap:
    """60 x 60 cells of 5 m from (-50, -50): grass, then `bands` = (e_start, class)."""
    grid = np.zeros((60, 60), dtype=np.int16)
    for e0, c in bands:
        j0 = int((e0 + 50.0) / 5.0)
        grid[:, j0:] = c
    return RasterMap(grid=grid, cell_m=5.0, origin_enu=(-50.0, -50.0), classes=CLASSES,
                     map_id="synthetic-bands", origin_lla=(38.92, -77.066))


def records(rmap: RasterMap, n=100, onset=30, walk_mps=3.0):
    truth = enu_to_lla(rmap, 0.0, 0.0)                    # the receiver never moves
    recs = []
    for j in range(n):
        d = 0.0 if j < onset else walk_mps * (j - onset)
        pos = enu_to_lla(rmap, d, 0.0)                    # walked east = along the route
        recs.append({"position": dict(pos, alt=0.0), "_truth": dict(truth),
                     "terrain": {"available": True}})
    return recs


def sensor(diag: float, seed: int = 7) -> ConfusionSensor:
    return ConfusionSensor(confusion_from_diag(len(CLASSES), diag), CLASSES, seed=seed)


# ----------------------------------------------------------------------------- schedule

def test_schedule_transitions_and_unknown_hold():
    rmap = banded_map()
    rmap.grid[:, 22:25] = UNKNOWN                          # a hole inside the paved band
    sched = route_schedule(rmap, ROUTE)
    assert [(s, a, b) for s, a, b in sched["transitions"]] == [(40.0, 0, 1), (90.0, 1, 2), (140.0, 2, 0)]
    assert sched["unknown_fraction"] > 0.0
    assert default_window(sched["transitions"]) is None    # no pair repeats


def test_route_maths():
    assert route_point(ROUTE, 50.0) == (50.0, 0.0)
    assert route_point(ROUTE, 999.0) == (200.0, 0.0)       # clamped
    s, off = project_onto_route(ROUTE, 60.0, 4.0)
    assert (s, off) == (60.0, 4.0)
    loop = ((0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 0.0))
    assert project_onto_route(loop, 0.0, 0.0)[0] == 0.0    # tie: start beats end
    rmap = banded_map()
    lla = enu_to_lla(rmap, 123.4, -56.7)
    e, n = rmap.enu_from_lla(lla["lat"], lla["lon"])
    assert math.isclose(e, 123.4, abs_tol=1e-6) and math.isclose(n, -56.7, abs_tol=1e-6)


# ----------------------------------------------------------------------------- the fix

def test_perfect_sensor_pins_every_boundary_and_measures_the_walk():
    rmap = banded_map()
    fix = RouteFix(rmap, ROUTE, sensor(1.0), SPEED, GAIN, CCP, CCP_R, lead_in_epochs=30, epochs_total=100)
    assert fix.N == 1                                       # zero false transitions, zero analytic rate
    recs = records(rmap)
    summary = fix.run(recs, ["NOMINAL"] * len(recs), onset_index=30)
    pins = summary["pins"]
    assert [p["epoch"] for p in pins] == [20, 45, 70]       # s = 40, 90, 140 at 2 m/epoch
    assert [p["s_m"] for p in pins] == [40.0, 90.0, 140.0]
    assert summary["unmatched_transitions"] == []
    assert summary["false_pins_lead_in"] == []
    b45 = recs[45]["terrain"]["route_fix"]
    assert b45["pinned"] and b45["pin_this_epoch"] and b45["transition"]["from"] == "paved"
    assert math.isclose(b45["s_pinned_m"], 90.0, abs_tol=1e-9)
    assert math.isclose(b45["s_believed_m"], 90.0 + 45.0, abs_tol=1e-6)   # 15 epochs x 3 m ahead
    assert math.isclose(b45["correction_m"], -45.0, abs_tol=1e-6)
    assert b45["_error_vs_truth_m"] == 0.0
    # every epoch after the first pin: the fix is exact (odometry exact in the frame)
    errs = [r["terrain"]["route_fix"]["_error_vs_truth_m"] for r in recs[20:]]
    assert max(errs) == 0.0
    # the believed pin reaches the ring long before the fix does; at_ccp waits for the fix
    assert summary["first_believed_in_ring_epoch"] in (55, 56)   # 5j - 90 = 185 at j = 55: a float tie on the ring
    assert summary["first_at_ccp_epoch"] == 93                # s_pinned >= 185, pin at 70 still fresh (K)
    assert fix.K == 26                                        # longest gap 50 m / 2 m + N
    assert not any(r["terrain"]["route_fix"]["at_ccp"] for r in recs[:93])
    # the pin at 70 is fresh through 96 (K = 26) and stale from 97: no boundary
    # lies between 140 m and the CCP, so the gate drops even though the frame's
    # odometry is exact -- the rule is stated, not tuned to the fixture
    assert all(r["terrain"]["route_fix"]["at_ccp"] for r in recs[93:97])
    assert not any(r["terrain"]["route_fix"]["at_ccp"] for r in recs[97:])
    assert recs[97]["terrain"]["route_fix"]["pin_fresh"] is False
    assert recs[97]["terrain"]["route_fix"]["ccp_range_m"] <= CCP_R
    # contract shape
    b = recs[99]["terrain"]["route_fix"]
    assert b["available"] and b["sensor_source"] == "simulated"
    assert set(b["position"]) == {"lat", "lon"}
    e, n = rmap.enu_from_lla(**b["position"])
    assert math.isclose(e, 198.0, abs_tol=1e-3) and abs(n) < 1e-3   # lat/lon rounded to 1e-9 deg
    assert recs[0]["terrain"]["route_fix"]["params"]["N_consecutive"] == 1
    assert recs[1]["terrain"]["route_fix"]["params"] is None


def test_halt_freezes_odometry_but_not_the_believed_walk():
    rmap = banded_map()
    fix = RouteFix(rmap, ROUTE, sensor(1.0), SPEED, GAIN, CCP, CCP_R, lead_in_epochs=30, epochs_total=100)
    recs = records(rmap)
    states = ["NOMINAL"] * 40 + ["SURRENDERED"] * 30 + ["NOMINAL"] * 30
    fix.run(recs, states, onset_index=30)
    b60 = recs[60]["terrain"]["route_fix"]
    assert math.isclose(b60["_s_frame_m"], 78.0)             # epoch 39's 78 m; epoch 40 is already SURRENDERED
    assert math.isclose(b60["s_pinned_m"], 78.0)             # carried by zero odometry
    assert b60["s_believed_m"] > 150.0                        # the believed pin keeps walking
    assert b60["correction_m"] < -70.0


def test_noisy_sensor_needs_a_sustained_run_and_stays_within_its_uncertainty():
    rmap = banded_map()
    fix = RouteFix(rmap, ROUTE, sensor(0.85), SPEED, GAIN, CCP, CCP_R, lead_in_epochs=30, epochs_total=100)
    assert fix.N >= 2
    assert fix.n_choice["table"][fix.N]["false_transitions_lead_in"] == 0
    assert fix.n_choice["table"][fix.N]["analytic_expected_per_replay"] < 0.1
    recs = records(rmap)
    summary = fix.run(recs, ["NOMINAL"] * len(recs), onset_index=30)
    assert summary["false_pins_lead_in"] == []
    assert len(summary["pins"]) >= 2
    for p in summary["pins"]:
        b = recs[p["epoch"]]["terrain"]["route_fix"]
        # a glitch that reads the OLD class after the crossing delays the crossing
        # estimate by up to N-1 epochs beyond the stated half-width
        assert b["_error_vs_truth_m"] <= p["uncertainty_m"] + fix.N * SPEED + 1e-9


def test_uniform_map_has_no_boundaries_and_never_pins(capsys):
    rmap = banded_map(bands=())
    fix = RouteFix(rmap, ROUTE, sensor(1.0), SPEED, GAIN, CCP, CCP_R, lead_in_epochs=30, epochs_total=100)
    assert fix.sched["transitions"] == []
    recs = records(rmap)
    summary = fix.run(recs, ["NOMINAL"] * len(recs), onset_index=30)
    assert summary["pins"] == []
    assert not any(r["terrain"]["route_fix"]["available"] for r in recs)
    assert not any(r["terrain"]["route_fix"]["at_ccp"] for r in recs)


def test_augment_without_the_channel_marks_unavailable():
    class Spec:
        pre_epochs = 60
    recs = [{"position": None, "_truth": None} for _ in range(3)]
    out = augment(recs, {"rmap": None, "terrain": None, "spec": Spec()})
    assert all(r["terrain"]["route_fix"]["available"] is False for r in out)
    assert all(r["terrain"]["route_fix"]["sensor_source"] == "simulated" for r in out)
