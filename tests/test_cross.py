"""Position solver and the cross-constellation feature, on the real day."""
from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from backend.detection.cross import CrossConstellation, channels, fit_cross
from backend.detection.emit import USN8_ECEF
from backend.injector import (CARRY_OFF, MEACONING, SIMPLISTIC, inject,
                              top_n_by_elevation)
from backend.rinex import ephemeris, noise
from backend.rinex.loader import load_obs
from backend.rinex.solve import solve, solve_per_constellation

OBS = "data/USN800USA_R_20262320000_01D_30S_MO.crx.gz"
ONSET = datetime(2026, 8, 20, 12, 30)


@pytest.fixture(scope="module")
def day():
    return load_obs(OBS, systems="GERCS")


@pytest.fixture(scope="module")
def window(day):
    return [e for e in day
            if datetime(2026, 8, 20, 12, 0) <= e.time < datetime(2026, 8, 20, 14, 0)]


@pytest.fixture(scope="module")
def floor(day):
    return noise.measure(day)


@pytest.fixture(scope="module")
def nav():
    return ephemeris.load_nav()


@pytest.fixture(scope="module")
def xcal(window, nav):
    return fit_cross(window, nav)


def scores(stream, xcal, nav):
    xc = CrossConstellation(xcal, nav)
    return pd.Series({ep.time: xc.score(ep)["value"] for ep in stream})


# -- solver -------------------------------------------------------------------

def test_all_in_view_solution_lands_on_the_station(day, nav):
    """Broadcast-quality single point: within tens of metres of the surveyed
    ECEF, at epochs spread across the day."""
    sta = np.array(USN8_ECEF)
    for hh in (0, 6, 12, 18):
        ep = next(e for e in day if e.time == datetime(2026, 8, 20, hh, 30))
        sol = solve(ep, nav=nav)
        assert sol is not None
        assert np.linalg.norm(sol["pos"] - sta) < 25.0
        assert sol["resid_rms_m"] < 15.0


def test_single_constellation_solutions_solve_and_stay_sane(day, nav):
    sta = np.array(USN8_ECEF)
    ep = next(e for e in day if e.time == ONSET)
    sols = solve_per_constellation(ep, nav)
    for sysc in "GEC":
        assert sysc in sols
        assert np.linalg.norm(sols[sysc]["pos"] - sta) < 80.0


def test_uniform_offset_on_one_constellation_moves_its_clock_not_its_position(
        day, floor, nav):
    """The finding that shaped the feature: a range offset applied equally to
    every satellite of one constellation is absorbed by that constellation's
    clock unknown. The position barely moves; the inter-system clock moves by
    the full bias."""
    ep = next(e for e in day if e.time == ONSET)
    sp = MEACONING(onset=datetime(2026, 8, 20, 12, 0))    # active at ONSET
    inj, _ = inject([ep], sp, floor)
    a = solve_per_constellation(ep, nav)
    b = solve_per_constellation(inj[0], nav)
    dpos = np.linalg.norm(a["G"]["pos"] - b["G"]["pos"])
    dclk = ((b["all"]["clock_m"]["G"] - b["all"]["clock_m"]["E"])
            - (a["all"]["clock_m"]["G"] - a["all"]["clock_m"]["E"]))
    assert dpos < 5.0
    assert dclk == pytest.approx(sp.common_bias_m, abs=5.0)


# -- feature ------------------------------------------------------------------

def test_clean_idle_is_bounded(window, xcal, nav):
    v = scores(window, xcal, nav)
    assert v.mean() < 0.5
    assert v.quantile(0.9) < 0.8


def test_scorer_is_reproducible_after_reset(window, xcal, nav):
    a = scores(window[:60], xcal, nav)
    b = scores(window[:60], xcal, nav)
    assert (a == b).all()


def test_meaconing_is_caught_and_stays_caught(window, floor, xcal, nav):
    """The §7 scenario this feature exists for — and the gated baseline means
    it never fades, unlike the C/N0 anomaly."""
    inj, _ = inject(window, MEACONING(onset=ONSET), floor)
    v = scores(inj, xcal, nav)
    post = v[v.index >= ONSET]
    assert post.iloc[5:].min() > 0.9
    assert v.iloc[-1] > 0.9                      # 90 min later: not absorbed


def test_coherent_carry_off_is_caught_by_the_clock_channel(window, floor,
                                                           xcal, nav):
    """carrier_rate_error = 0 blinds features 2 and 3 (tested elsewhere).
    This is the feature that still sees it."""
    inj, _ = inject(window, CARRY_OFF(onset=ONSET, carrier_rate_error=0.0),
                    floor)
    v = scores(inj, xcal, nav)
    assert v[v.index >= ONSET].iloc[5:].mean() > 0.9


def test_subset_carry_off_is_caught(window, floor, xcal, nav):
    inj, _ = inject(window, CARRY_OFF(onset=ONSET, carrier_rate_error=0.0,
                                      target_svs=top_n_by_elevation(4)), floor)
    v = scores(inj, xcal, nav)
    assert v[v.index >= ONSET].iloc[5:].mean() > 0.9


def test_all_sky_bias_is_the_stated_blind_spot(window, floor, xcal, nav):
    """An attack consistent across every constellation shifts all clocks
    together: no channel moves. Stated in the module docstring; asserted here
    so nobody reads this feature as covering the simplistic scenario."""
    inj, _ = inject(window, SIMPLISTIC(onset=ONSET, carrier_rate_error=0.0),
                    floor)
    v = scores(inj, xcal, nav)
    clean = scores(window, xcal, nav)
    assert v[v.index >= ONSET].mean() < clean.quantile(0.99)


# -- residual bookkeeping the sweep depends on --------------------------------

def test_solver_per_sv_residuals_reproduce_its_own_rms(day, nav):
    """The sweep partitions these; a second implementation would drift."""
    ep = next(e for e in day if e.time == ONSET)
    sol = solve(ep, nav=nav)
    per = np.array(list(sol["resid_m"].values()))
    assert set(sol["resid_m"]) == set(sol["svs_used"])
    assert np.sqrt(np.mean(per ** 2)) == pytest.approx(sol["resid_rms_m"],
                                                       rel=1e-9)


def test_spoofed_constellation_only_solve_absorbs_a_coordinated_walk(
        window, floor, nav):
    """The §10 rationale, measured: a coordinated walk lies in the position
    columns of H, so a single-constellation solve takes it into its own
    position estimate and its residual does not grow. Any residual in the
    all-in-view solution is cross-constellation disagreement."""
    from backend.injector import CARRY_OFF as POS_CARRY_OFF
    sp = POS_CARRY_OFF(onset=ONSET, carrier_rate_error=0.0, bearing_deg=90.0)
    inj, truth = inject(window, sp, floor, nav=nav)
    walk = np.flatnonzero((truth["stage"] == "WALK").to_numpy())
    g_only, all_in = [], []
    for i in (walk[1], walk[10], walk[30]):
        g_only.append(solve(inj[i], systems="G", nav=nav)["resid_rms_m"])
        all_in.append(solve(inj[i], nav=nav)["resid_rms_m"])
    # GPS-only residual stays flat while the walk grows; all-in-view does not
    assert max(g_only) / min(g_only) < 2.0
    assert all_in[-1] > 3 * all_in[0]
