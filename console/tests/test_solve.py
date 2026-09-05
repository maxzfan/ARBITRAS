"""The WLS position solver (backend/geometry/solve.py).

Synthetic geometry first -- known satellites, known receiver, exact
pseudoranges -- so a wrong sign or a missing Sagnac term fails loudly here
rather than showing up as a plausible-looking 30 m bias on real data. Then the
regenerated demo stream, if present.
"""
import json
import math
from pathlib import Path

import numpy as np
import pytest

from backend.detection.emit import USN8_ECEF
from backend.geometry.solve import (enu_basis, enu_to_ecef, geometric_range,
                                    wls_fix)

DEMO = Path("out/demo.jsonl")
RANGE_M = 22_000_000.0

# (system, azimuth deg, elevation deg): a spread sky, nothing below the mask
SKY = [("G", 20, 70), ("G", 110, 45), ("G", 200, 30), ("G", 290, 55),
       ("G", 60, 20), ("E", 150, 65), ("E", 240, 40), ("E", 330, 25)]


def _sats(sky=SKY):
    out = {}
    for i, (sysc, az, el) in enumerate(sky):
        a, e = math.radians(az), math.radians(el)
        enu = RANGE_M * np.array([math.cos(e) * math.sin(a), math.cos(e) * math.cos(a),
                                  math.sin(e)])
        out[f"{sysc}{i + 1:02d}"] = enu_to_ecef(USN8_ECEF, *enu)
    return out


def _pseudoranges(sats, rx_ecef, clock_m: dict, sat_clock_s: dict):
    """Exact model pseudoranges: P = |R(tau)s - x| + b_sys - c*dt_sv."""
    from backend.geometry.solve import C_LIGHT
    return {sv: geometric_range(s, rx_ecef) + clock_m[sv[0]] - C_LIGHT * sat_clock_s[sv]
            for sv, s in sats.items()}


def test_recovers_a_known_position_and_clocks_to_a_millimetre():
    sats = _sats()
    rx = enu_to_ecef(USN8_ECEF, 10.0, -20.0, 5.0)
    clocks = {"G": 1234.5, "E": 1250.0}
    dt_sv = {sv: 1e-4 * (i + 1) for i, sv in enumerate(sats)}
    fix = wls_fix(_pseudoranges(sats, rx, clocks, dt_sv), sats, dt_sv, x0=USN8_ECEF)
    assert fix is not None and fix.converged
    assert np.linalg.norm(fix.ecef - rx) < 1e-3
    assert abs(fix.clock_bias_m["G"] - 1234.5) < 1e-3
    assert abs(fix.clock_bias_m["E"] - 1250.0) < 1e-3
    assert fix.residual_rms_m < 1e-6


def test_one_clock_column_per_constellation_present():
    """design.md §6b: H is n x (3+k); a constellation with no satellites has no
    column, so the matrix is never rank-deficient."""
    sats = _sats()
    rx = np.asarray(USN8_ECEF, float)
    dt_sv = {sv: 0.0 for sv in sats}
    both = wls_fix(_pseudoranges(sats, rx, {"G": 100.0, "E": 130.0}, dt_sv),
                   sats, dt_sv, x0=USN8_ECEF)
    assert both.H.shape == (8, 5) and both.k == 2 and both.systems == "EG"

    gps_only = [sv for sv in sats if sv.startswith("G")]
    g = wls_fix(_pseudoranges(sats, rx, {"G": 100.0, "E": 130.0}, dt_sv),
                sats, dt_sv, x0=USN8_ECEF, svs=gps_only)
    assert g.H.shape == (5, 4) and g.k == 1 and g.systems == "G"
    assert np.linalg.norm(g.ecef - rx) < 1e-3, "dropping E must not degrade the G solve"
    assert np.all(np.isfinite(np.linalg.inv(g.H.T @ g.H)))


def test_uniform_offset_on_a_whole_constellation_moves_the_clock_not_the_fix():
    """A range offset common to every satellite of one constellation is a
    receiver-clock shift by construction. The position must not move. This is
    the physics behind why the all-GPS carry-off displaces the clock."""
    sats = _sats()
    rx = np.asarray(USN8_ECEF, float)
    dt_sv = {sv: 0.0 for sv in sats}
    P = _pseudoranges(sats, rx, {"G": 0.0, "E": 0.0}, dt_sv)
    spoofed = {sv: p + (500.0 if sv.startswith("G") else 0.0) for sv, p in P.items()}
    fix = wls_fix(spoofed, sats, dt_sv, x0=USN8_ECEF)
    assert np.linalg.norm(fix.ecef - rx) < 1e-3
    assert abs(fix.clock_bias_m["G"] - 500.0) < 1e-3
    assert abs(fix.clock_bias_m["E"]) < 1e-3


def test_subset_bias_moves_the_fix_by_the_linear_geometry_prediction():
    sats = _sats()
    rx = np.asarray(USN8_ECEF, float)
    dt_sv = {sv: 0.0 for sv in sats}
    P = _pseudoranges(sats, rx, {"G": 0.0, "E": 0.0}, dt_sv)
    clean = wls_fix(P, sats, dt_sv, x0=USN8_ECEF)
    # Three of the five GPS satellites -- a strict subset of one constellation.
    # (Biasing all three Galileo satellites would be absorbed by the E clock
    # column and move nothing, which is the point of the test above.)
    biased_svs = [sv for sv in clean.svs if sv.startswith("G")][:3]
    Pb = {sv: p + (30.0 if sv in biased_svs else 0.0) for sv, p in P.items()}
    biased = wls_fix(Pb, sats, dt_sv, x0=USN8_ECEF, svs=clean.svs)

    moved = biased.ecef - clean.ecef
    assert np.linalg.norm(moved) > 1.0, "a 3-of-8 bias must displace the fix"

    # Linearised prediction from the same H and weights: dx = (H'WH)^-1 H'W d
    H = clean.H
    w = np.array([math.sin(math.radians(clean.elevation_deg[sv])) ** 2 for sv in clean.svs])
    d = np.array([30.0 if sv in biased_svs else 0.0 for sv in clean.svs])
    pred = np.linalg.solve(H.T @ (w[:, None] * H), H.T @ (w * d))[:3]
    assert np.linalg.norm(moved - pred) < 0.05


def test_too_few_satellites_returns_none():
    allsats = _sats()
    sats = {sv: allsats[sv] for sv in ("G01", "G02", "G03", "E06")}   # 4 < 3 + 2
    dt_sv = {sv: 0.0 for sv in sats}
    P = _pseudoranges(sats, USN8_ECEF, {"G": 0.0, "E": 0.0}, dt_sv)
    assert wls_fix(P, sats, dt_sv, x0=USN8_ECEF) is None


def test_enu_basis_is_orthonormal():
    R = enu_basis(38.92, -77.07)
    assert np.allclose(R @ R.T, np.eye(3), atol=1e-12)


# --------------------------------------------------------------- the demo stream

@pytest.fixture(scope="module")
def demo():
    if not DEMO.exists():
        pytest.skip("out/demo.jsonl not generated; run python -m backend.demo")
    return [json.loads(l) for l in DEMO.read_text().splitlines() if l.strip()]


def _d_m(r):
    from console.mission import M_PER_DEG_LAT, M_PER_DEG_LON
    b, t = r["position"], r["_truth"]
    return math.hypot((b["lat"] - t["lat"]) * M_PER_DEG_LAT,
                      (b["lon"] - t["lon"]) * M_PER_DEG_LON)


def test_demo_positions_are_solved_not_surveyed(demo):
    src = [r.get("position_source") for r in demo]
    frac = src.count("wls_differential") / len(src)
    assert frac >= 0.95, f"only {frac:.1%} of epochs carry a WLS solution"
    solved = [r for r in demo if r["position_source"] == "wls_differential"]
    assert all("_solution" in r and r["_solution"]["converged"] for r in solved)


def test_demo_clean_lead_in_has_no_displacement(demo):
    from backend.demo import PRE_EPOCHS
    lead = [r for r in demo[:PRE_EPOCHS] if r["position_source"] == "wls_differential"]
    assert lead, "no solved epochs in the lead-in"
    assert max(_d_m(r) for r in lead) < 1.0
