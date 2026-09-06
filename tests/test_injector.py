"""Injector behaviour, checked against the real clean day.

These are the properties the §7 attack model asserts. Each one is a claim we
would have to defend at a whiteboard, so each one is a test.
"""
from datetime import datetime

import numpy as np
import pytest

from backend.injector import (CAPTURE, CLEAN, WALK, CARRY_OFF,
                              CLOCK_CARRY_OFF, MEACONING, SIMPLISTIC,
                              enu_basis, epoch_interval_s, inject)
from backend.rinex import noise
from backend.rinex.loader import load_obs

OBS = "data/USN800USA_R_20262320000_01D_30S_MO.crx.gz"
ONSET = datetime(2026, 8, 20, 12, 30)
# Test value for the divergence rate. NOT the demo pin, which is
# picked by hand from the printed arithmetic and is still pending.
TEST_RATE = 0.02  # m/s


@pytest.fixture(scope="module")
def day():
    return load_obs(OBS, systems="GERCS")


@pytest.fixture(scope="module")
def window(day):
    return [e for e in day
            if datetime(2026, 8, 20, 12, 0) <= e.time < datetime(2026, 8, 20, 13, 0)]


@pytest.fixture(scope="module")
def floor(day):
    return noise.measure(day)


@pytest.fixture(scope="module")
def nav():
    from backend.rinex import ephemeris
    return ephemeris.load_nav()


def cmc(ep, sv, band=1):
    return ep.df.at[sv, f"code_{band}"] - ep.df.at[sv, f"phase_m_{band}"]


def walk_index(truth, offset: int = 10) -> int:
    """Index of a WALK epoch `offset` epochs into the walk."""
    walk = np.flatnonzero((truth["stage"] == WALK).to_numpy())
    return int(walk[offset])


def gps_sv(window):
    return next(s for s in window[0].df.index
                if s.startswith("G") and all(s in e.df.index for e in window))


def test_clean_stream_is_not_mutated(window, floor):
    before = [e.df.copy() for e in window]
    inject(window, CARRY_OFF(onset=ONSET, carrier_rate_error=TEST_RATE), floor)
    for b, e in zip(before, window):
        assert b.equals(e.df), "injector mutated the clean replay in place"


def test_epochs_before_onset_are_untouched(window, floor):
    inj, truth = inject(window, CARRY_OFF(onset=ONSET, carrier_rate_error=TEST_RATE), floor)
    for a, b, t in zip(window, inj, truth.itertuples()):
        if t.stage == CLEAN:
            assert a.df.equals(b.df)


def test_cn0_step_is_persistent_not_a_decaying_spike(window, floor):
    """§7 stage 2's 'drop back' is the detector's rolling mean catching up, not
    the signal fading. The injected step must still be there at the end."""
    sp = CARRY_OFF(onset=ONSET, carrier_rate_error=TEST_RATE)
    inj, truth = inject(window, sp, floor)
    sv = gps_sv(window)
    walk = [(a, b) for a, b, t in zip(window, inj, truth.itertuples())
            if t.stage == WALK]
    for a, b in (walk[0], walk[len(walk) // 2], walk[-1]):
        assert b.df.at[sv, "cn0_1"] - a.df.at[sv, "cn0_1"] == pytest.approx(
            sp.power_db, abs=1e-9)


def test_carry_off_power_is_a_few_sigma_of_the_measured_floor(floor):
    """design.md §4: a 1-3 dB spoofer is 2-6 sigma out. Against the conservative
    0.5 dB p90 figure, not the quantisation-limited median."""
    assert 2.0 <= CARRY_OFF(onset=ONSET, carrier_rate_error=TEST_RATE).power_db / 0.5 <= 6.0


def test_clock_domain_walk_off_matches_the_specified_rate(window, floor):
    """Clock domain: the rate is m/s of UNIFORM range drift."""
    sp = CLOCK_CARRY_OFF(onset=ONSET, carrier_rate_error=TEST_RATE)
    dt = epoch_interval_s(window)
    inj, truth = inject(window, sp, floor)
    walk = truth[truth["stage"] == WALK]["range_offset_m"]
    step = np.diff(walk.to_numpy())
    assert np.allclose(step, sp.walk_off_mps * dt)


def test_capture_occupies_at_most_one_epoch_at_30s_sampling(window, floor):
    """Capture takes ~10 s (§7) and the file is sampled at 30 s. This is the
    reason the C/N0 feature cannot carry detection on its own."""
    _, truth = inject(window, CARRY_OFF(onset=ONSET, carrier_rate_error=TEST_RATE), floor)
    assert (truth["stage"] == CAPTURE).sum() <= 1


def test_meaconing_leaves_code_minus_carrier_untouched(window, floor):
    """A repeater re-radiates the authentic signal, so the code/carrier
    relationship survives intact. Meaconing must be invisible to feature 3 —
    that is what makes it the cross-constellation scenario."""
    sp = MEACONING(onset=ONSET)
    inj, truth = inject(window, sp, floor)
    sv = gps_sv(window)
    for a, b, t in zip(window, inj, truth.itertuples()):
        assert cmc(b, sv) - cmc(a, sv) == pytest.approx(0.0, abs=1e-6)
    assert truth["range_offset_m"].max() == sp.common_bias_m


def test_meaconing_biases_only_its_target_constellation(window, floor):
    inj, truth = inject(window, MEACONING(onset=ONSET, svs="G"), floor)
    a, b = window[-1], inj[-1]
    for sv in a.df.index:
        d = b.df.at[sv, "code_1"] - a.df.at[sv, "code_1"]
        assert (d > 0) == sv.startswith("G")


def test_simplistic_moves_every_tracked_satellite(window, floor):
    inj, truth = inject(window, SIMPLISTIC(onset=ONSET), floor)
    for a, b, t in zip(window, inj, truth.itertuples()):
        if t.stage == CLEAN:
            continue
        assert t.n_spoofed == len(a.df)          # every tracked SV, every epoch
        assert (b.df["code_1"] - a.df["code_1"]).min() > 0


def test_code_carrier_divergence_is_linear_at_the_specified_rate(window, floor):
    """Divergence is carrier_rate_error * t after lift-off: linear, not a
    random walk, not noise inflation (ruling of 2026-09-05)."""
    sp = CLOCK_CARRY_OFF(onset=ONSET, carrier_rate_error=TEST_RATE,
                         liftoff_transient_sigma=0.0)
    dt = epoch_interval_s(window)
    inj, truth = inject(window, sp, floor)
    sv = gps_sv(window)
    walk = [(a, b, t) for a, b, t in zip(window, inj, truth.itertuples())
            if t.stage == WALK]
    d = [cmc(b, sv) - cmc(a, sv) for a, b, _ in walk]
    # absolute level is rate * time-since-liftoff, exactly
    expect = [sp.carrier_rate_error
              * max(0.0, (a.time - ONSET).total_seconds() - sp.capture_s)
              for a, _, _ in walk]
    assert np.allclose(d, expect, atol=1e-9)
    # and therefore the per-epoch increment is rate * dt, constant
    assert np.allclose(np.diff(d), sp.carrier_rate_error * dt, atol=1e-9)


def test_zero_rate_spoofer_is_invisible_to_code_minus_carrier(window, floor):
    """carrier_rate_error = 0.0 is a supported case: a fully carrier-coherent
    spoofer. Code and carrier stay coherent through the ENTIRE walk-off --
    including no lift-off transient -- while the walk-off itself continues."""
    sp = CLOCK_CARRY_OFF(onset=ONSET, carrier_rate_error=0.0)
    inj, truth = inject(window, sp, floor)
    sv = gps_sv(window)
    for a, b, t in zip(window, inj, truth.itertuples()):
        assert cmc(b, sv) - cmc(a, sv) == pytest.approx(0.0, abs=1e-9)
    walk = truth[truth["stage"] == WALK]["range_offset_m"]
    assert walk.iloc[-1] > 0          # the attack is still walking


def test_phase_stays_consistent_with_phase_in_metres(window, floor):
    inj, _ = inject(window, CARRY_OFF(onset=ONSET, carrier_rate_error=TEST_RATE), floor)
    df = inj[-1].df
    assert np.allclose(df["phase_m_1"], df["phase_1"] * df["lam_1"], equal_nan=True)


# -- target selection: resolved at capture, held fixed -------------------------

def test_carry_off_target_freezes_at_capture(day, floor):
    """A GPS satellite that rises after onset is never spoofed: the spoofer
    committed to its channel set at capture."""
    sp = CARRY_OFF(onset=ONSET, carrier_rate_error=TEST_RATE)                    # default target: all_gps
    inj, truth = inject(day, sp, floor)
    capture_ep = next(e for e in day if e.time >= ONSET)
    frozen = {sv for sv in capture_ep.df.index if sv.startswith("G")}
    spoofed_ever = set()
    for row in truth[truth["stage"] != CLEAN]["spoofed_sv"]:
        spoofed_ever |= set(row.split(",")) if row else set()
    assert spoofed_ever == frozen
    # and satellites tracked later but not at capture stayed clean
    later = day[-1]
    risen = [sv for sv in later.df.index
             if sv.startswith("G") and sv not in frozen]
    if risen:                                       # 11 h later: expect several
        a, b = day[-1], inj[-1]
        for sv in risen:
            assert b.df.at[sv, "code_1"] == a.df.at[sv, "code_1"]


def test_top_n_by_elevation_selects_n_gps_and_holds_them(window, floor):
    from backend.injector import top_n_by_elevation
    sp = CARRY_OFF(onset=ONSET, carrier_rate_error=TEST_RATE, target_svs=top_n_by_elevation(4))
    inj, truth = inject(window, sp, floor)
    sets = {frozenset(r.split(",")) for r in
            truth[truth["stage"] != CLEAN]["spoofed_sv"] if r}
    assert len(sets) == 1                           # one committed channel set
    chosen = next(iter(sets))
    assert len(chosen) == 4 and all(sv.startswith("G") for sv in chosen)
    # and they are the top of the sky at capture, checked independently
    from backend.detection.emit import USN8_ECEF
    from backend.rinex import ephemeris
    cap = next(e for e in window if e.time >= ONSET)
    gps = [sv for sv in cap.df.index if sv.startswith("G")]
    el = ephemeris.elevations_at(cap.time, gps, USN8_ECEF)
    assert set(chosen) == set(el.sort_values(ascending=False).index[:4])


def test_explicit_target_list_is_used_verbatim(window, floor):
    sp = CARRY_OFF(onset=ONSET, carrier_rate_error=TEST_RATE, target_svs=["G05", "G21"])
    inj, truth = inject(window, sp, floor)
    active = truth[truth["stage"] != CLEAN]
    assert set(active["spoofed_sv"].iloc[-1].split(",")) == {"G05", "G21"}


def test_walk_off_rate_unchanged_by_target_selection(window, floor):
    """Decision 1 constraint: subset choice never touches the ~1 m/s rate."""
    from backend.injector import top_n_by_elevation
    dt = epoch_interval_s(window)
    for target in ("all_gps", ["G05", "G21"], top_n_by_elevation(4)):
        for maker in (CARRY_OFF, CLOCK_CARRY_OFF):
            sp = maker(onset=ONSET, carrier_rate_error=TEST_RATE,
                       target_svs=target)
            assert sp.walk_off_mps == 1.0
        sp = CLOCK_CARRY_OFF(onset=ONSET, carrier_rate_error=TEST_RATE,
                             target_svs=target)
        _, truth = inject(window, sp, floor)
        walk = truth[truth["stage"] == WALK]["range_offset_m"].to_numpy()
        assert np.allclose(np.diff(walk), sp.walk_off_mps * dt)


def test_tracked_satellites_compute_above_the_horizon(day):
    """Ephemeris sanity: whatever the receiver tracks must be in the sky."""
    from backend.detection.emit import USN8_ECEF
    from backend.rinex import ephemeris
    ep = next(e for e in day if e.time == ONSET)
    gps = [sv for sv in ep.df.index if sv.startswith("G")]
    el = ephemeris.elevations_at(ep.time, gps, USN8_ECEF)
    assert len(el) == len(gps)
    assert el.min() > 0.0


def test_ephemeris_ranges_are_consistent_with_pseudoranges(day):
    """Computed geometric range agrees with the observed pseudorange to within
    receiver-clock scale. Catches any sign/rotation/week error at a glance."""
    from backend.detection.emit import USN8_ECEF
    from backend.rinex import ephemeris
    ep = next(e for e in day if e.time == ONSET)
    gps = [sv for sv in ep.df.index if sv.startswith("G")]
    pos = ephemeris.positions_at(ep.time, gps)
    rng = np.linalg.norm(pos.to_numpy() - np.array(USN8_ECEF), axis=1)
    diff = np.abs(ep.df.loc[pos.index, "code_1"].to_numpy() - rng)
    assert diff.max() < 1_000e3


def test_attack_window_is_half_open(window, floor):
    """dt == duration_s is the first CLEAN epoch after the attack, not its
    last attack epoch."""
    dt = epoch_interval_s(window)
    sp = CARRY_OFF(onset=ONSET, carrier_rate_error=TEST_RATE,
                   duration_s=10 * dt)
    _, truth = inject(window, sp, floor)
    active = truth[truth["stage"] != CLEAN]
    assert len(active) == 10
    last = active.index[-1]
    assert (last - ONSET).total_seconds() == (10 - 1) * dt


# -- position-domain walk (ruling of 2026-09-05) -------------------------------

def test_commanded_displacement_is_exactly_horizontal(window, floor, nav):
    """Ruled constraint: dp has no vertical component, by construction. Tested
    on the injected observables, not on the intent -- reconstruct dp from the
    per-satellite offsets and check its up component."""
    from backend.detection.emit import USN8_ECEF
    from backend.rinex import ephemeris
    sta = np.array(USN8_ECEF)
    _, _, up = enu_basis(sta)
    sp = CARRY_OFF(onset=ONSET, carrier_rate_error=0.0, bearing_deg=90.0)
    inj, truth = inject(window, sp, floor, nav=nav)
    i = walk_index(truth)
    a, b, t = window[i], inj[i], truth.iloc[i]
    assert t["stage"] == WALK and t["commanded_displacement_m"] > 0
    svs = [sv for sv in a.df.index if sv in t["spoofed_sv"].split(",")]
    pr = a.df.loc[svs, "code_1"]
    sat = ephemeris.positions_at(a.time, svs, nav,
                                 tx_delay_s={sv: pr[sv] / 299792458.0
                                             for sv in svs})
    los = sat[["x", "y", "z"]].to_numpy() - sta
    los /= np.linalg.norm(los, axis=1, keepdims=True)
    dy = (b.df.loc[sat.index, "code_1"] - a.df.loc[sat.index, "code_1"]).to_numpy()
    dp, *_ = np.linalg.lstsq(-los, dy, rcond=None)      # recover dp
    assert abs(dp @ up) < 1e-6 * np.linalg.norm(dp)
    assert np.linalg.norm(dp) == pytest.approx(t["commanded_displacement_m"],
                                               rel=1e-6)


def test_bearing_selects_the_commanded_direction(window, floor, nav):
    from backend.detection.emit import USN8_ECEF
    east, north, _ = enu_basis(np.array(USN8_ECEF))
    from backend.injector.spoof import bearing_unit_ecef
    for bearing, axis, other in ((90.0, east, north), (0.0, north, east)):
        v = bearing_unit_ecef(bearing, USN8_ECEF)
        assert v @ axis == pytest.approx(1.0, abs=1e-9)
        assert v @ other == pytest.approx(0.0, abs=1e-9)


def test_per_sv_range_rates_never_exceed_the_position_rate(window, floor, nav):
    """The ruling's arithmetic: each satellite's range rate is e_sv . v_hat
    times the position rate, so all of them are <= 1 m/s."""
    dt = epoch_interval_s(window)
    sp = CARRY_OFF(onset=ONSET, carrier_rate_error=0.0)
    inj, truth = inject(window, sp, floor, nav=nav)
    i = walk_index(truth)
    prev, cur = inj[i - 1], inj[i]
    common = [sv for sv in cur.df.index if sv in prev.df.index
              and sv in truth.iloc[i]["spoofed_sv"].split(",")]
    dclean = (window[i].df.loc[common, "code_1"]
              - window[i - 1].df.loc[common, "code_1"])
    dspoof = cur.df.loc[common, "code_1"] - prev.df.loc[common, "code_1"]
    rate = ((dspoof - dclean) / dt).abs()
    assert rate.max() <= sp.walk_off_mps + 1e-6
    assert rate.min() < sp.walk_off_mps          # geometry spreads them out


def test_position_walk_moves_the_solved_position(window, floor, nav):
    """The whole point of the split: the believed position actually moves."""
    from backend.rinex.solve import solve
    sp = CARRY_OFF(onset=ONSET, carrier_rate_error=0.0)
    inj, truth = inject(window, sp, floor, nav=nav)
    i = walk_index(truth, offset=30)
    a, b = solve(window[i], nav=nav), solve(inj[i], nav=nav)
    moved = np.linalg.norm(b["pos"] - a["pos"])
    assert moved > 5.0
    assert moved < truth.iloc[i]["commanded_displacement_m"]   # authentic pull-back


def test_clock_walk_leaves_the_solved_position_alone_while_the_set_holds(
        window, floor, nav):
    """The clock-domain attack corrupts time, not position -- for as long as the
    spoofed set is the whole constellation. Once the frozen channel set decays
    (satellites rise that the spoofer never captured) GPS is a MIX of spoofed
    and authentic ranges, the offset stops being uniform, and the position does
    move. Checked early, before the set has turned over."""
    from backend.rinex.solve import solve
    sp = CLOCK_CARRY_OFF(onset=ONSET, carrier_rate_error=0.0)
    inj, truth = inject(window, sp, floor, nav=nav)
    for off in (2, 5, 10):
        i = walk_index(truth, offset=off)
        gps_now = {sv for sv in window[i].df.index if sv.startswith("G")}
        if gps_now != set(truth.iloc[i]["spoofed_sv"].split(",")):
            continue                       # set already turned over
        a, b = solve(window[i], nav=nav), solve(inj[i], nav=nav)
        assert np.linalg.norm(b["pos"] - a["pos"]) < 5.0


# -- transients: demo only, never in measurement (ruling 2026-09-05) ----------

def test_transients_off_removes_both_spikes_and_nothing_else(window, floor, nav):
    on_e, on_t = inject(window, CARRY_OFF(onset=ONSET, carrier_rate_error=0.0136,
                                          transients=True), floor, nav=nav)
    off_e, off_t = inject(window, CARRY_OFF(onset=ONSET, carrier_rate_error=0.0136,
                                            transients=False), floor, nav=nav)
    # the lift-off CMC transient exists on exactly one epoch
    d = (on_t["cmc_divergence_m"] - off_t["cmc_divergence_m"]).abs()
    assert (d > 1e-9).sum() == 1
    assert d.max() == pytest.approx(6.0 * floor.cmc_sigma, rel=1e-9)
    # the capture C/N0 jitter exists on exactly the capture epoch
    cap = int(np.flatnonzero((on_t["stage"] == CAPTURE).to_numpy())[0])
    diff_cap = (on_e[cap].df["cn0_1"] - off_e[cap].df["cn0_1"]).abs()
    assert diff_cap.max() > 0
    # and every non-capture epoch is bit-identical between the two runs
    for i, (a, b) in enumerate(zip(on_e, off_e)):
        if i == cap:
            continue
        assert np.allclose(a.df["cn0_1"].to_numpy(), b.df["cn0_1"].to_numpy(),
                           equal_nan=True)


def test_transients_setting_is_recorded_in_the_truth_log(window, floor, nav):
    """Every reported number has to say which setting produced it."""
    for flag in (True, False):
        _, truth = inject(window, CARRY_OFF(onset=ONSET, carrier_rate_error=0.0,
                                            transients=flag), floor, nav=nav)
        assert set(truth["transients"]) == {flag}


def test_sweep_forces_transients_off(window, floor):
    """Measurement runs must never carry the injected spike."""
    import inspect

    from backend.measurement import sweep as sweep_mod
    src = inspect.getsource(sweep_mod.run_sweep)
    assert "transients=False" in src
