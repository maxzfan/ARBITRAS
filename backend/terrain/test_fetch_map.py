"""Rasteriser acceptance on synthetic Overpass-shaped elements: polygons fill
their cells, lines get their half-width, later classes overwrite earlier
ones, unmapped stays UNKNOWN. No network."""
from backend.terrain.fetch_map import CLASSES, classify, rasterise
from backend.terrain.rastermap import UNKNOWN, m_per_deg

LAT0, LON0 = 38.92, -77.067


def ll(e, n):
    m_lat, m_lon = m_per_deg(LAT0)
    return {"lat": LAT0 + n / m_lat, "lon": LON0 + e / m_lon}


def square(e0, n0, e1, n1, tags):
    return {"type": "way", "tags": tags,
            "geometry": [ll(e0, n0), ll(e1, n0), ll(e1, n1), ll(e0, n1), ll(e0, n0)]}


def test_classify_maps_tags_to_classes():
    assert classify({"building": "yes"}) == ("building", None)
    assert classify({"highway": "residential"}) == ("paved", 3.0)
    assert classify({"natural": "water"}) == ("water", None)
    assert classify({"leisure": "park"}) == ("grass", None)
    assert classify({"natural": "wood"}) == ("tree_cover", None)
    assert classify({"shop": "bakery"}) is None


def test_polygon_line_priority_and_unknown():
    els = [
        square(-100, -100, 100, 100, {"leisure": "park"}),
        square(20, -20, 60, 20, {"building": "yes"}),
        {"type": "way", "tags": {"highway": "residential"},
         "geometry": [ll(-100, -60), ll(100, -60)]},
    ]
    m = rasterise(els, (LAT0, LON0), radius_m=150, cell_m=5.0)
    assert m.classes == CLASSES
    assert m.classes[m.class_at(0, 0)] == "grass"
    assert m.classes[m.class_at(40, 0)] == "building"
    assert m.classes[m.class_at(0, -60)] == "paved"
    assert m.classes[m.class_at(0, -52)] == "grass"          # cell centre 7.5 m off the line, outside the 3 m half-width
    assert m.class_at(130, 130) == UNKNOWN                    # nothing mapped there
    assert m.grid.shape == (60, 60)


def test_relation_outer_members_carry_the_relation_tags():
    rel = {"type": "relation", "tags": {"natural": "water"},
           "members": [{"type": "way", "role": "outer",
                        "geometry": square(-50, -50, 50, 50, {})["geometry"]},
                       {"type": "way", "role": "inner",
                        "geometry": square(-10, -10, 10, 10, {})["geometry"]}]}
    m = rasterise([rel], (LAT0, LON0), radius_m=100, cell_m=5.0)
    assert m.classes[m.class_at(30, 30)] == "water"
