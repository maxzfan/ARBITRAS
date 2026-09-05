"""Unit tests for the sweep harness's stand-in geometry math. Synthetic LOS
only -- the sweep itself is gated and is not run here."""
import numpy as np
import pandas as pd
import pytest

from backend.measurement.sweep import build_h, information_ratio


def sky(svs, seed=1):
    """Synthetic unit LOS vectors, upper hemisphere."""
    rng = np.random.default_rng(seed)
    az = rng.uniform(0, 2 * np.pi, len(svs))
    el = rng.uniform(np.radians(10), np.radians(80), len(svs))
    v = np.c_[np.cos(el) * np.sin(az), np.cos(el) * np.cos(az), np.sin(el)]
    return pd.DataFrame(v, columns=["ex", "ey", "ez"], index=svs)


GPS = [f"G{i:02d}" for i in range(1, 9)]
GAL = [f"E{i:02d}" for i in range(1, 9)]


def test_h_has_one_clock_column_per_constellation_present():
    full = sky(GPS + GAL)
    assert build_h(full).shape == (16, 5)          # 3 + 2 clocks
    assert build_h(full.loc[GPS]).shape == (8, 4)  # GPS-only: E column dropped


def test_full_set_ratio_is_one():
    full = sky(GPS + GAL)
    r = information_ratio(full, full)
    assert r["raw"] == pytest.approx(1.0)
    assert r["norm"] == pytest.approx(1.0)


def test_excluding_satellites_lowers_the_ratio_monotonically():
    full = sky(GPS + GAL)
    last = 1.0
    for n_excl in (1, 2, 4, 6):
        trusted = full.drop(GPS[:n_excl])
        r = information_ratio(trusted, full)["norm"]
        assert 0.0 < r < last
        last = r


def test_normalised_ratio_stays_legible_where_raw_collapses():
    """The CLAUDE.md correction: the raw ratio decays with the (3+k)th power
    and reads 0.00 after a few exclusions; the normalised form does not."""
    full = sky(GPS + GAL)
    trusted = full.drop(GPS[:6])
    r = information_ratio(trusted, full)
    assert r["raw"] < 0.05
    assert r["norm"] > 5 * r["raw"]


def test_empty_constellation_drops_its_clock_column_instead_of_zeroing():
    """Meaconing response: distrust ALL of one constellation. The corrected
    form keeps a meaningful score; carrying the dead clock column would zero
    it by construction."""
    full = sky(GPS + GAL)
    trusted = full.loc[GAL]                        # every GPS SV excluded
    r = information_ratio(trusted, full)
    assert r["k_trusted"] == 1
    assert r["norm"] > 0.0


def test_underdetermined_subset_scores_zero():
    full = sky(GPS + GAL)
    trusted = full.loc[GPS[:3]]                    # 3 rows < 3+1 unknowns
    assert information_ratio(trusted, full)["norm"] == 0.0


# -- grid enumeration ---------------------------------------------------------

def test_grid_covers_both_domains_and_only_position_gets_bearings():
    from backend.measurement.sweep import SweepConfig
    cfg = SweepConfig(subset_sizes=(12, 4), carrier_rate_errors=(0.0, 0.1))
    cells = list(cfg.cells())
    pos = [c for c in cells if c[0] == "position"]
    clk = [c for c in cells if c[0] == "clock"]
    assert len(pos) == 2 * 2 * len(cfg.bearings_deg)
    assert len(clk) == 2 * 2
    assert all(c[3] is None for c in clk)
    assert {c[3] for c in pos} == set(cfg.bearings_deg)


def test_swept_bearings_are_eight_directions_45_apart():
    from backend.injector import SWEEP_BEARINGS_DEG, EAST_BEARING_DEG
    assert len(SWEEP_BEARINGS_DEG) == 8
    assert sorted(SWEEP_BEARINGS_DEG) == list(range(0, 360, 45))
    assert EAST_BEARING_DEG in SWEEP_BEARINGS_DEG


def test_sweep_is_gated_without_run_flag():
    import pytest as _pytest
    from backend.measurement.sweep import main
    with _pytest.raises(SystemExit):
        main([])
