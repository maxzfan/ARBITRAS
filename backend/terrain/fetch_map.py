"""Fetch and rasterise the pre-map around the antenna from OpenStreetMap.

TRACK_E.md names ESA WorldCover / NLCD; neither has a reader in this
environment (no rasterio/GDAL), so the pre-map is OSM features rasterised
here with numpy + matplotlib.path. It is a public map with a checksum, not a
land-cover product — say so in the provenance. Data © OpenStreetMap
contributors, ODbL.

Classes and drawing order (later overwrites earlier):
    grass < shrub < tree_cover < bare < water < paved < building
Everything unmapped stays UNKNOWN (conservative, see rastermap.py).

Limitations, stated: relation multipolygons contribute only their closed
outer members; inner rings are ignored; highway half-widths are per-class
nominal values, not measured.

    python -m backend.terrain.fetch_map --radius 600 --cell 5 \\
        --out data/terrain_usn8.npz --sign
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
from matplotlib.path import Path as MplPath

from backend.detection.emit import SURVEYED
from backend.terrain.rastermap import UNKNOWN, RasterMap, m_per_deg

CLASSES = ["grass", "shrub", "tree_cover", "bare", "water", "paved", "building"]
ATTRIBUTION = ("© OpenStreetMap contributors, ODbL 1.0 "
               "(rasterised by backend/terrain/fetch_map.py)")
OVERPASS = "https://overpass-api.de/api/interpreter"

# nominal half-widths, metres, for line features
HIGHWAY_HW = {"motorway": 6.0, "trunk": 6.0, "primary": 5.0, "secondary": 4.5,
              "tertiary": 4.0, "residential": 3.0, "unclassified": 3.0,
              "service": 2.5, "living_street": 3.0, "footway": 1.0, "path": 1.0,
              "cycleway": 1.0, "pedestrian": 2.0, "track": 1.5, "steps": 1.0}
WATERWAY_HW = {"river": 6.0, "stream": 1.5, "canal": 4.0, "drain": 1.0, "ditch": 0.8}


def classify(tags: dict) -> tuple[str, float | None] | None:
    if not tags:
        return None
    if "building" in tags:
        return "building", None
    if tags.get("amenity") == "parking":
        return "paved", None
    hw = tags.get("highway")
    if hw:
        if tags.get("area") == "yes":
            return "paved", None
        return "paved", HIGHWAY_HW.get(hw, 2.5)
    ww = tags.get("waterway")
    if ww in ("riverbank", "dock"):
        return "water", None
    if ww in WATERWAY_HW:
        return "water", WATERWAY_HW[ww]
    nat, lu, le = tags.get("natural"), tags.get("landuse"), tags.get("leisure")
    if nat == "water" or lu in ("reservoir", "basin"):
        return "water", None
    if nat == "wood" or lu == "forest":
        return "tree_cover", None
    if nat == "tree_row":
        return "tree_cover", 2.0
    if nat in ("scrub", "heath"):
        return "shrub", None
    if nat in ("sand", "bare_rock", "scree", "beach", "shingle") or \
            lu in ("construction", "brownfield", "quarry"):
        return "bare", None
    if nat == "grassland" or lu in ("grass", "meadow", "recreation_ground", "cemetery",
                                    "village_green", "farmland", "greenfield") or \
            le in ("park", "garden", "pitch", "golf_course", "playground", "common"):
        return "grass", None
    return None


def overpass_query(lat: float, lon: float, radius_m: float) -> str:
    m_lat, m_lon = m_per_deg(lat)
    s, n = lat - radius_m / m_lat, lat + radius_m / m_lat
    w, e = lon - radius_m / m_lon, lon + radius_m / m_lon
    bbox = f"({s:.6f},{w:.6f},{n:.6f},{e:.6f})"
    keys = ['"building"', '"highway"', '"landuse"', '"natural"', '"leisure"',
            '"waterway"', '"amenity"="parking"']
    body = "".join(f"way[{k}]{bbox};relation[{k}]{bbox};" for k in keys)
    return f"[out:json][timeout:90];({body});out geom;"


def fetch(lat: float, lon: float, radius_m: float, cache_path) -> list[dict]:
    cache_path = Path(cache_path)
    if cache_path.exists():
        return json.loads(cache_path.read_text())["elements"]
    data = urllib.parse.urlencode({"data": overpass_query(lat, lon, radius_m)}).encode()
    req = urllib.request.Request(OVERPASS, data=data,
                                 headers={"User-Agent": "ARBITRAS-terrain-fetch/0.1"})
    with urllib.request.urlopen(req, timeout=120) as r:
        payload = json.loads(r.read().decode())
    payload["fetched_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(payload))
    return payload["elements"]


def _flatten(elements: list[dict]) -> list[dict]:
    """Ways as-is; relations contribute their closed outer members with the
    relation's tags."""
    out = []
    for el in elements:
        if el.get("type") == "way" and el.get("geometry"):
            out.append(el)
        elif el.get("type") == "relation":
            for mem in el.get("members", []):
                if mem.get("type") == "way" and mem.get("role", "outer") == "outer" \
                        and mem.get("geometry"):
                    g = mem["geometry"]
                    if len(g) >= 4 and g[0] == g[-1]:
                        out.append({"type": "way", "tags": el.get("tags", {}), "geometry": g})
    return out


def rasterise(elements: list[dict], origin_lla, radius_m: float, cell_m: float) -> RasterMap:
    lat0, lon0 = origin_lla
    m_lat, m_lon = m_per_deg(lat0)
    n_cells = int(np.ceil(2 * radius_m / cell_m))
    origin = (-radius_m, -radius_m)
    grid = np.full((n_cells, n_cells), UNKNOWN, dtype=np.int16)
    ii, jj = np.mgrid[0:n_cells, 0:n_cells]
    ce = origin[0] + (jj + 0.5) * cell_m
    cn = origin[1] + (ii + 0.5) * cell_m
    pts = np.column_stack([ce.ravel(), cn.ravel()])

    order = {name: k for k, name in enumerate(CLASSES)}
    items = []
    for el in _flatten(elements):
        c = classify(el.get("tags", {}))
        if c is None:
            continue
        name, hw = c
        xy = np.array([((p["lon"] - lon0) * m_lon, (p["lat"] - lat0) * m_lat)
                       for p in el["geometry"]])
        closed = len(xy) >= 4 and np.allclose(xy[0], xy[-1])
        if hw is None and not closed:
            continue                                  # an area tag on an open way
        items.append((order[name], name, hw, xy))
    items.sort(key=lambda t: t[0])

    for _, name, hw, xy in items:
        cid = CLASSES.index(name)
        if hw is None:
            inside = MplPath(xy).contains_points(pts).reshape(grid.shape)
            grid[inside] = cid
        else:
            near = np.zeros(grid.shape, dtype=bool)
            for a, b in zip(xy[:-1], xy[1:]):
                lo = np.minimum(a, b) - hw - cell_m
                hi = np.maximum(a, b) + hw + cell_m
                box = (ce >= lo[0]) & (ce <= hi[0]) & (cn >= lo[1]) & (cn <= hi[1])
                if not box.any():
                    continue
                ab = b - a
                L2 = float(ab @ ab)
                pe, pn = ce[box], cn[box]
                if L2 == 0:
                    t = np.zeros_like(pe)
                else:
                    t = np.clip(((pe - a[0]) * ab[0] + (pn - a[1]) * ab[1]) / L2, 0, 1)
                d = np.hypot(pe - (a[0] + t * ab[0]), pn - (a[1] + t * ab[1]))
                near[box] |= d <= hw
            grid[near] = cid

    return RasterMap(grid=grid, cell_m=float(cell_m), origin_enu=origin, classes=list(CLASSES),
                     map_id=f"osm-{lat0:.4f}_{lon0:.4f}-r{int(radius_m)}-c{cell_m:g}",
                     origin_lla=(lat0, lon0), attribution=ATTRIBUTION)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--radius", type=float, default=600.0)
    ap.add_argument("--cell", type=float, default=5.0)
    ap.add_argument("--out", default="data/terrain_usn8.npz")
    ap.add_argument("--cache", default="data/terrain_usn8_osm.json")
    ap.add_argument("--sign", action="store_true",
                    help="generate data/terrain_map_{priv,pub}.pem if absent and sign the map")
    args = ap.parse_args(argv)
    lat, lon = SURVEYED["lat"], SURVEYED["lon"]
    els = fetch(lat, lon, args.radius, args.cache)
    m = rasterise(els, (lat, lon), args.radius, args.cell)
    out = m.save_npz(args.out)
    counts = {name: int((m.grid == k).sum()) for k, name in enumerate(m.classes)}
    counts["unknown"] = int((m.grid == UNKNOWN).sum())
    print(f"wrote {out}  {m.grid.shape} cells of {m.cell_m} m  checksum {m.checksum()[:12]}")
    print("  elements:", len(els), " cells by class:", counts)
    c0 = m.class_at(0, 0)
    print("  antenna class:", "UNKNOWN" if c0 == UNKNOWN else m.classes[c0])
    for b in range(0, 360, 45):
        print(f"  boundary distance bearing {b:3d}: {m.boundary_distance(0, 0, b)}")
    print("  extent at antenna:", m.consistent_extent_m(0, 0), " nearest:", m.nearest_boundary(0, 0))
    if args.sign:
        from backend.terrain.signing import generate_keypair, sign_file
        priv, pub = Path("data/terrain_map_priv.pem"), Path("data/terrain_map_pub.pem")
        if not priv.exists():
            generate_keypair(priv, pub)
        print("  signed:", sign_file(out, priv))


if __name__ == "__main__":
    main()
