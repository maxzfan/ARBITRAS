"""E1 acceptance (TRACK_E.md): raster maths match hand computation on the
fixture; extent is None when the component touches the edge; unknown cells
are conservative (absorbed by the extent, ignored by the boundary search)."""
import math
from pathlib import Path

import numpy as np
import pytest

from backend.terrain.rastermap import RasterMap, UNKNOWN, m_per_deg

FIXTURE = Path("fixtures/terrain_fixture.json")


@pytest.fixture
def rmap():
    return RasterMap.from_fixture(FIXTURE)


def test_fixture_loads_with_antenna_on_grass(rmap):
    assert rmap.grid.shape == (20, 20)
    assert rmap.classes[rmap.class_at(0.0, 0.0)] == "grass"
    assert rmap.classes[rmap.class_at(40.0, 0.0)] == "paved"
    assert rmap.class_at(-80.0, 55.0) == rmap.classes.index("water")
    assert rmap.class_at(-80.0, -50.0) == UNKNOWN
    assert rmap.class_at(500.0, 0.0) is None


def test_footprint_is_a_point_mass_inside_one_class(rmap):
    q = rmap.footprint_posterior(0.0, 0.0, sigma_m=1.0)
    assert q is not None and q.shape == (7,)
    assert q[rmap.classes.index("grass")] > 0.999
    assert abs(q.sum() - 1.0) < 1e-9


def test_footprint_straddles_a_boundary(rmap):
    q = rmap.footprint_posterior(33.0, 0.0, sigma_m=6.0)
    g, p = rmap.classes.index("grass"), rmap.classes.index("paved")
    assert 0.05 < q[p] < 0.95 and q[g] + q[p] > 0.99


def test_footprint_ignores_unknown_cells(rmap):
    q = rmap.footprint_posterior(-80.0, -50.0, sigma_m=1.0)   # inside the unknown patch
    assert q is not None                                      # neighbours are labelled
    assert q[rmap.classes.index("grass")] > 0.99
    assert rmap.footprint_posterior(500.0, 0.0, sigma_m=1.0) is None


def test_extent_by_hand(rmap):
    e = rmap.consistent_extent_m(0.0, 0.0)
    assert e == pytest.approx(127.279 + 10.0 * math.sqrt(2) / 2, abs=0.01)


def test_extent_none_when_component_touches_edge(rmap):
    assert rmap.consistent_extent_m(40.0, 0.0) is None        # road runs to the ring
    assert rmap.consistent_extent_m(-80.0, -50.0) is None     # unknown cell: no claim


def test_nearest_boundary_by_hand(rmap):
    nb = rmap.nearest_boundary(0.0, 0.0)
    assert nb["class_beyond"] == "paved"
    assert nb["distance_m"] == pytest.approx(40.0)
    assert nb["bearing_deg"] == pytest.approx(90.0)


def test_boundary_distance_by_bearing(rmap):
    assert rmap.boundary_distance(0.0, 0.0, 90.0) == pytest.approx(35.0, abs=2.5)
    assert rmap.boundary_distance(0.0, 0.0, 180.0) == pytest.approx(80.0, abs=2.5)
    assert rmap.boundary_distance(0.0, 0.0, 0.0) == pytest.approx(85.0, abs=2.5)
    assert rmap.boundary_distance(0.0, 0.0, 270.0) == pytest.approx(100.0, abs=2.5)


def test_collapse_merges_classes(rmap):
    c = rmap.collapse([["paved", "building"]])
    assert "building" not in c.classes and len(c.classes) == 6
    assert c.classes[c.class_at(70.0, 0.0)] == "paved"
    assert c.nearest_boundary(0.0, 0.0)["class_beyond"] == "paved"
    assert c.map_id.endswith("+collapsed")


def test_npz_roundtrip_preserves_checksum(rmap, tmp_path):
    p = tmp_path / "m.npz"
    rmap.save_npz(p)
    back = RasterMap.load_npz(p)
    assert back.checksum() == rmap.checksum()
    assert back.classes == rmap.classes and back.signed is False


def test_metres_per_degree_match_the_console():
    """design.md §5: backend must not import console, so parity is asserted
    the way backend/correction/test_gate.py does it."""
    from console import mission
    lat_m, lon_m = m_per_deg(mission.LAT)
    assert lat_m == pytest.approx(mission.M_PER_DEG_LAT)
    assert lon_m == pytest.approx(mission.M_PER_DEG_LON)


def test_nearest_boundary_with_a_class_override_on_an_unknown_cell(rmap):
    assert rmap.nearest_boundary(-80.0, -50.0) is None              # unknown cell, no class
    nb = rmap.nearest_boundary(-80.0, -50.0, c0=rmap.classes.index("grass"))
    assert nb is not None and nb["class_beyond"] != "grass"
    # from (-80,-50) the nearest non-grass labelled cell is the west ring at e=-100
    assert nb["distance_m"] == pytest.approx(20.0) and nb["bearing_deg"] == pytest.approx(270.0)
