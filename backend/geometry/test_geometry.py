"""Geometry track tests.

Synthetic tests need no data. Integration tests skip when data/ is absent
(it is gitignored; bootstrap.sh fetches it).
Run from repo root:  python -m pytest backend/geometry/test_geometry.py
"""
from __future__ import annotations

from datetime import datetime, timedelta
from math import cos, radians, sin, sqrt
from pathlib import Path

import numpy as np
import pytest

from backend.geometry.hmatrix import build_H, elevation_deg, unit_los
from backend.geometry.information import (displacement_bound_m,
                                          information_ratio,
                                          next_best_observation, norm_info,
                                          rank_one_gain)
from backend.geometry.sv_positions import kepler_to_ecef

RNG = np.random.default_rng(232)
DATA = Path("data/brdc_filtered.rnx")


def enu_sat(az_deg: float, el_deg: float, r: float = 2.0e7) -> np.ndarray:
    """Satellite position in a local frame from azimuth/elevation."""
    az, el = radians(az_deg), radians(el_deg)
    return r * np.array([cos(el) * sin(az), cos(el) * cos(az), sin(el)])


def synthetic_svs(specs: dict[str, tuple[float, float]]) -> dict[str, np.ndarray]:
    return {sv: enu_sat(az, el) for sv, (az, el) in specs.items()}


# ---------------------------------------------------------------- synthetic

def test_hand_computed_gdop():
    # Three SVs at el 30, az 0/120/240, one at zenith, single constellation.
    # By symmetry H'H is block diagonal:
    #   E,N diagonal entries 1.5 cos^2(30) = 1.125
    #   [U,clk] block [[1.75, 2.5], [2.5, 4]]  -> det 0.75
    # det(H'H) = 1.125^2 * 0.75 = 0.94921875
    sats = synthetic_svs({"G01": (0, 30), "G02": (120, 30),
                          "G03": (240, 30), "G04": (0, 90)})
    h, sv_ids, consts = build_H(sats, np.zeros(3))
    assert h.shape == (4, 4)
    assert consts == ["G"]
    assert np.isclose(np.linalg.det(h.T @ h), 0.94921875)


def test_clock_column_drop_is_automatic_and_continuous():
    sats = synthetic_svs({
        "G01": (0, 60), "G02": (90, 45), "G03": (180, 30),
        "G04": (270, 45), "G05": (45, 70), "G06": (200, 20),
        "E01": (30, 50), "E02": (150, 40), "E03": (300, 35),
    })
    h_full, _, consts_full = build_H(sats, np.zeros(3))
    assert h_full.shape == (9, 5) and consts_full == ["E", "G"]

    # Exclude ALL of Galileo (the meaconing response): the clock column
    # is never created, the matrix stays full rank, no det cliff.
    g_only = {sv: p for sv, p in sats.items() if sv[0] == "G"}
    h_g, _, consts_g = build_H(g_only, np.zeros(3))
    assert h_g.shape == (6, 4) and consts_g == ["G"]
    ratio = information_ratio(h_g, h_full)
    assert 0.0 < ratio <= 1.0


def test_information_ratio_is_one_at_zero_exclusions():
    sats = synthetic_svs({"G01": (0, 60), "G02": (90, 45), "G03": (180, 30),
                          "G04": (270, 45), "E01": (30, 50), "E02": (150, 40)})
    h, _, _ = build_H(sats, np.zeros(3))
    assert information_ratio(h, h) == 1.0


def test_information_ratio_underdetermined_is_zero():
    sats = synthetic_svs({"G01": (0, 60), "G02": (90, 45), "G03": (180, 30),
                          "G04": (270, 45), "G05": (45, 70)})
    h_full, _, _ = build_H(sats, np.zeros(3))
    h_three = h_full[:3]  # 3 rows < 4 unknowns
    assert information_ratio(h_three, h_full) == 0.0


def test_rank_one_identity():
    a = RNG.standard_normal((12, 5))
    g = a.T @ a
    h = RNG.standard_normal(5)
    lhs = np.linalg.det(g + np.outer(h, h))
    rhs = np.linalg.det(g) * rank_one_gain(np.linalg.inv(g), h)
    assert np.isclose(lhs, rhs, rtol=1e-9)


def test_displacement_bound_grows_as_geometry_degrades():
    # The position covariance eig_max(inv(H'H)[:3,:3]) grows monotonically
    # as rows are removed (information decreases — a theorem). The full
    # bound also multiplies by sqrt(chi2.ppf(., n-d)), which shrinks with
    # DOF, so the bound itself is only near-monotone; assert the theorem
    # exactly and the end-to-end increase for a realistic degradation.
    specs = {f"G{i:02d}": (az, el) for i, (az, el) in enumerate(
        [(0, 60), (40, 30), (90, 45), (140, 25), (180, 70),
         (220, 35), (270, 50), (310, 20), (20, 15), (160, 55)], start=1)}
    sats = synthetic_svs(specs)
    h, _, _ = build_H(sats, np.zeros(3))
    eigs, bounds = [], []
    for n in range(10, 5, -1):  # 10..6 rows, d=4 -> all overdetermined
        g = h[:n].T @ h[:n]
        eigs.append(np.linalg.eigvalsh(np.linalg.inv(g)[:3, :3])[-1])
        b = displacement_bound_m(h[:n], sigma_uere_m=1.0)
        assert b is not None and b > 0
        bounds.append(b)
    assert all(e2 >= e1 * (1 - 1e-12) for e1, e2 in zip(eigs, eigs[1:]))
    assert bounds[-1] > bounds[0]  # 4 SVs removed -> bound clearly wider


def test_displacement_bound_none_when_not_overdetermined():
    sats = synthetic_svs({"G01": (0, 60), "G02": (90, 45),
                          "G03": (180, 30), "G04": (270, 45)})
    h, _, _ = build_H(sats, np.zeros(3))  # n == d == 4
    assert displacement_bound_m(h, sigma_uere_m=1.0) is None


def test_next_best_prefers_spread_constellation():
    # Trusted GPS clustered in one quadrant; a spread Galileo group is
    # the observation that recovers the most information.
    sats = synthetic_svs({
        "G01": (10, 40), "G02": (25, 55), "G03": (40, 35),
        "G04": (15, 65), "G05": (35, 45),
        "E01": (120, 45), "E02": (210, 40), "E03": (300, 50), "E04": (180, 70),
    })
    nxt = next_best_observation(sats, ["G01", "G02", "G03", "G04", "G05"],
                                np.zeros(3))
    assert nxt == "E"


def test_next_best_none_without_candidates():
    sats = synthetic_svs({"G01": (0, 60), "G02": (90, 45),
                          "G03": (180, 30), "G04": (270, 45)})
    assert next_best_observation(sats, sorted(sats), np.zeros(3)) is None


def test_elevation_deg_zenith_and_horizon():
    rx = np.array([1112161.8802, -4842854.4026, 3985497.3830])  # USN8
    up = rx / np.linalg.norm(rx)
    assert np.isclose(elevation_deg(rx + up * 2.0e7, rx), 90.0)
    perp = np.cross(up, [0.0, 0.0, 1.0])
    perp /= np.linalg.norm(perp)
    assert abs(elevation_deg(rx + perp * 2.0e7, rx)) < 1e-6


def test_unit_los_is_unit():
    u = unit_los(np.array([2.6e7, 0, 0]), np.array([6.4e6, 0, 0]))
    assert np.isclose(np.linalg.norm(u), 1.0)
    assert u[0] > 0


def test_propagator_circular_orbit_radius():
    a = 26560e3
    eph = {"sqrtA": sqrt(a), "Eccentricity": 0.0, "M0": 0.3, "omega": 0.1,
           "Io": radians(55), "Omega0": 1.0, "DeltaN": 0.0, "OmegaDot": 0.0,
           "IDOT": 0.0, "Cuc": 0.0, "Cus": 0.0, "Crc": 0.0, "Crs": 0.0,
           "Cic": 0.0, "Cis": 0.0, "Toe": 0.0,
           "toc_gpst": datetime(2026, 8, 20, 12, 0, 0)}
    for dt_s in (0.0, 900.0, 3600.0):
        r = kepler_to_ecef(eph, eph["toc_gpst"] + timedelta(seconds=dt_s))
        assert np.isclose(np.linalg.norm(r), a, rtol=1e-9)


# -------------------------------------------------------------- needs data

needs_data = pytest.mark.skipif(not DATA.exists(),
                                reason="data/brdc_filtered.rnx absent")


@needs_data
def test_nav_records_load_and_dedupe():
    from backend.geometry.ephemeris import load_records
    records = load_records()
    svs = sorted(records)
    assert all(len(sv) == 3 and sv[0] in "GEC" for sv in svs)
    assert not any("_" in sv for sv in svs)          # Galileo deduped
    assert not any(sv.startswith("R") for sv in svs)  # GLONASS dropped
    by_const = {c: sum(1 for s in svs if s[0] == c) for c in "GEC"}
    assert by_const["G"] >= 25 and by_const["E"] >= 20 and by_const["C"] >= 15


@needs_data
def test_orbital_radii_sane():
    # Radius must lie in the record's own [a(1-e), a(1+e)]; nominal a per
    # constellation as a cross-check. E14/E18 are genuinely eccentric
    # (e~0.168, the 2014 launch anomaly pair) — physics, not a bug.
    from backend.geometry.ephemeris import (best_ephemeris, load_records,
                                            sv_positions_at)
    records = load_records()
    t = datetime(2026, 8, 20, 12, 0, 0)
    pos = sv_positions_at(records, t)
    # BeiDou has two Keplerian families: MEO (~27,900 km) and IGSO
    # (inclined geosync, ~42,164 km). Only GEO needs special handling
    # and GEO PRNs are dropped in ephemeris.py.
    nominal_a = {"G": (26560e3,), "E": (29600e3,), "C": (27900e3, 42164e3)}
    for sv, p in pos.items():
        rec = best_ephemeris(records, sv, t)
        a, e = rec["sqrtA"] ** 2, rec["Eccentricity"]
        r = np.linalg.norm(p)
        assert a * (1 - e) - 50e3 <= r <= a * (1 + e) + 50e3, \
            f"{sv}: r={r/1e3:.0f} km outside Keplerian range"
        if sv not in ("E14", "E18"):
            assert any(abs(a - nom) < 800e3 for nom in nominal_a[sv[0]]), \
                f"{sv}: a={a/1e3:.0f} km far from nominal"


@needs_data
def test_positions_continuous_over_30s():
    from backend.geometry.ephemeris import load_records, sv_positions_at
    records = load_records()
    t0 = datetime(2026, 8, 20, 12, 0, 0)
    p0 = sv_positions_at(records, t0)
    p1 = sv_positions_at(records, t0 + timedelta(seconds=30))
    for sv in p0:
        if sv in p1:
            step = np.linalg.norm(p1[sv] - p0[sv])
            assert step < 200e3, f"{sv} moved {step/1e3:.0f} km in 30 s"


@needs_data
def test_propagator_matches_gnss_lib_py_oracle():
    # Independent implementation check: gnss-lib-py (Stanford NavLab)
    # computes GPS SV states from the same broadcast records. Agreement
    # verified at 0.0 m; assert <10 m to allow record-selection slack.
    # (find_sv_states returns several rows per SV — one per ephemeris
    # record — so compare against the nearest.)
    from datetime import timezone
    import gnss_lib_py as glp
    from backend.geometry.ephemeris import best_ephemeris, load_records

    svs = ["G05", "G12", "G25", "G29"]
    t_utc = datetime(2026, 8, 20, 11, 59, 42, tzinfo=timezone.utc)
    t_gpst = datetime(2026, 8, 20, 12, 0, 0)  # same instant, GPS time

    nav = glp.RinexNav("data/brdc_filtered.rnx", satellites=svs)
    states = glp.find_sv_states(glp.datetime_to_gps_millis(t_utc), nav)
    records = load_records()
    for sv in svs:
        prn = int(sv[1:])
        theirs = [np.array([states["x_sv_m", i], states["y_sv_m", i],
                            states["z_sv_m", i]])
                  for i in range(len(states["sv_id"]))
                  if int(states["sv_id", i]) == prn]
        ours = kepler_to_ecef(best_ephemeris(records, sv, t_gpst), t_gpst)
        assert min(np.linalg.norm(ours - th) for th in theirs) < 10.0, sv


@needs_data
def test_next_best_readmits_dropped_galileo_on_real_geometry():
    # Meaconing response drops ALL of Galileo; next_best_observation must
    # recommend readmitting E — a whole constellation's worth of geometry
    # plus its clock column beats any partial-constellation alternative.
    from backend.geometry.ephemeris import load_records, sv_positions_at
    from backend.geometry.information import next_best_observation
    rx = np.array([1112161.8802, -4842854.4026, 3985497.3830])
    pos = sv_positions_at(load_records(), datetime(2026, 8, 20, 12, 0, 0))
    visible = {sv: p for sv, p in pos.items()
               if elevation_deg(p, rx) > 10.0}
    trusted = sorted(sv for sv in visible if sv[0] != "E")
    assert next_best_observation(visible, trusted, rx) == "E"


@needs_data
def test_displacement_bound_widens_on_real_geometry():
    # With sigma parameterised at 1.0 m the bound is pure geometry: the
    # full visible set gives a few metres; stripping 6 GPS SVs must widen
    # it (design.md: geometry dominates the shrinking chi2 threshold).
    from backend.geometry.ephemeris import load_records, sv_positions_at
    from backend.geometry.information import displacement_bound_m
    rx = np.array([1112161.8802, -4842854.4026, 3985497.3830])
    pos = sv_positions_at(load_records(), datetime(2026, 8, 20, 12, 0, 0))
    visible = {sv: p for sv, p in pos.items()
               if elevation_deg(p, rx) > 10.0}
    h_full, _, _ = build_H(visible, rx)
    gps = sorted((sv for sv in visible if sv[0] == "G"),
                 key=lambda sv: -elevation_deg(visible[sv], rx))
    h_cut, _, _ = build_H({s: p for s, p in visible.items()
                           if s not in set(gps[:6])}, rx)
    b_full = displacement_bound_m(h_full, 1.0)
    b_cut = displacement_bound_m(h_cut, 1.0)
    assert b_full is not None and b_cut is not None
    assert 1.0 < b_full < 50.0, b_full
    assert b_cut > b_full, (b_cut, b_full)


@needs_data
def test_engine_emits_contract_shape_from_fixture():
    # End-to-end: fixtures/epoch.json inputs -> exact §5 geometry block.
    # Fixture timestamps are UTC ("Z"); the engine works in GPST (+18 s).
    import json
    from backend.geometry.engine import GeometryEngine
    fx = json.loads(Path("fixtures/epoch.json").read_text())
    t_gpst = datetime.fromisoformat(
        fx["timestamp"].replace("Z", "")) + timedelta(seconds=18)
    eng = GeometryEngine(sigma_uere_m=1.0)
    block = eng.compute(t_gpst, excluded_sv=fx["geometry"]["excluded_sv"])
    assert set(block) == {"information_ratio", "excluded_sv",
                          "displacement_bound_m", "next_best_observation"}
    assert 0.0 <= block["information_ratio"] <= 1.0
    assert block["excluded_sv"] == sorted(fx["geometry"]["excluded_sv"])
    assert isinstance(block["displacement_bound_m"], float)
    assert block["next_best_observation"] in (None, "G", "E", "C")
    # sigma unset -> bound honestly None, everything else unchanged
    eng_nosigma = GeometryEngine()
    block2 = eng_nosigma.compute(t_gpst,
                                 excluded_sv=fx["geometry"]["excluded_sv"])
    assert block2["displacement_bound_m"] is None
    assert block2["information_ratio"] == block["information_ratio"]


@needs_data
def test_usn8_visible_gps_count_in_band():
    from backend.geometry.ephemeris import load_records, sv_positions_at
    rx = np.array([1112161.8802, -4842854.4026, 3985497.3830])
    pos = sv_positions_at(load_records(), datetime(2026, 8, 20, 12, 0, 0))
    vis_g = [sv for sv, p in pos.items()
             if sv[0] == "G" and elevation_deg(p, rx) > 10.0]
    assert 6 <= len(vis_g) <= 12, vis_g
