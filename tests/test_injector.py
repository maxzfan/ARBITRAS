"""Injector behaviour, checked against the real clean day.

These are the properties the §7 attack model asserts. Each one is a claim we
would have to defend at a whiteboard, so each one is a test.
"""
from datetime import datetime

import numpy as np
import pytest

from backend.injector import (CAPTURE, CLEAN, WALK, CARRY_OFF, MEACONING,
                              SIMPLISTIC, epoch_interval_s, inject)
from backend.rinex import noise
from backend.rinex.loader import load_obs

OBS = "data/USN800USA_R_20262320000_01D_30S_MO.crx.gz"
ONSET = datetime(2026, 8, 20, 12, 30)


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


def cmc(ep, sv, band=1):
    return ep.df.at[sv, f"code_{band}"] - ep.df.at[sv, f"phase_m_{band}"]


def gps_sv(window):
    return next(s for s in window[0].df.index
                if s.startswith("G") and all(s in e.df.index for e in window))


def test_clean_stream_is_not_mutated(window, floor):
    before = [e.df.copy() for e in window]
    inject(window, CARRY_OFF(onset=ONSET), floor)
    for b, e in zip(before, window):
        assert b.equals(e.df), "injector mutated the clean replay in place"


def test_epochs_before_onset_are_untouched(window, floor):
    inj, truth = inject(window, CARRY_OFF(onset=ONSET), floor)
    for a, b, t in zip(window, inj, truth.itertuples()):
        if t.stage == CLEAN:
            assert a.df.equals(b.df)


def test_cn0_step_is_persistent_not_a_decaying_spike(window, floor):
    """§7 stage 2's 'drop back' is the detector's rolling mean catching up, not
    the signal fading. The injected step must still be there at the end."""
    sp = CARRY_OFF(onset=ONSET)
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
    assert 2.0 <= CARRY_OFF(onset=ONSET).power_db / 0.5 <= 6.0


def test_walk_off_matches_the_specified_rate(window, floor):
    sp = CARRY_OFF(onset=ONSET)
    dt = epoch_interval_s(window)
    inj, truth = inject(window, sp, floor)
    walk = truth[truth["stage"] == WALK]["range_offset_m"]
    step = np.diff(walk.to_numpy())
    assert np.allclose(step, sp.walk_off_mps * dt)


def test_capture_occupies_at_most_one_epoch_at_30s_sampling(window, floor):
    """Capture takes ~10 s (§7) and the file is sampled at 30 s. This is the
    reason the C/N0 feature cannot carry detection on its own."""
    _, truth = inject(window, CARRY_OFF(onset=ONSET), floor)
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


def test_code_carrier_divergence_is_measured_in_sigma_of_the_floor(window, floor):
    """The injected divergence rate is `carrier_mismatch_sigma` sigma of the
    clean code-minus-carrier noise per epoch — not a number in metres."""
    sp = CARRY_OFF(onset=ONSET, liftoff_transient_sigma=0.0)
    inj, truth = inject(window, sp, floor)
    sv = gps_sv(window)
    walk = [(a, b) for a, b, t in zip(window, inj, truth.itertuples())
            if t.stage == WALK]
    d = [cmc(b, sv) - cmc(a, sv) for a, b in walk]
    per_epoch = np.diff(d)
    # Sign: the spoofer's carrier lags its own code, so code-minus-carrier grows.
    assert np.allclose(per_epoch,
                       sp.carrier_mismatch_sigma * floor.cmc_sigma, atol=1e-9)


def test_phase_stays_consistent_with_phase_in_metres(window, floor):
    inj, _ = inject(window, CARRY_OFF(onset=ONSET), floor)
    df = inj[-1].df
    assert np.allclose(df["phase_m_1"], df["phase_1"] * df["lam_1"], equal_nan=True)


# -- target selection: resolved at capture, held fixed -------------------------

def test_carry_off_target_freezes_at_capture(day, floor):
    """A GPS satellite that rises after onset is never spoofed: the spoofer
    committed to its channel set at capture."""
    sp = CARRY_OFF(onset=ONSET)                    # default target: all_gps
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
    sp = CARRY_OFF(onset=ONSET, target_svs=top_n_by_elevation(4))
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
    sp = CARRY_OFF(onset=ONSET, target_svs=["G05", "G21"])
    inj, truth = inject(window, sp, floor)
    active = truth[truth["stage"] != CLEAN]
    assert set(active["spoofed_sv"].iloc[-1].split(",")) == {"G05", "G21"}


def test_walk_off_rate_unchanged_by_target_selection(window, floor):
    """Decision 1 constraint: subset choice never touches the ~1 m/s rate."""
    from backend.injector import top_n_by_elevation
    dt = epoch_interval_s(window)
    for target in ("all_gps", ["G05", "G21"], top_n_by_elevation(4)):
        sp = CARRY_OFF(onset=ONSET, target_svs=target)
        assert sp.walk_off_mps == 1.0
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
