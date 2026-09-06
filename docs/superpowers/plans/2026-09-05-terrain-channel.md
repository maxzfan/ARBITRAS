# Terrain Channel (Track E) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a fifth, non-RF evidence channel — a simulated terrain-class sensor checked against a signed pre-map at the believed position — producing one feature, one map-derived displacement bound, one DEGRADED advisory input and one correction-gate check, off by default and byte-for-byte inert when off.

**Architecture:** New package `backend/terrain/` owns the raster maths (E1), the confusion-matrix simulator (E2) and the per-epoch channel (E3). Existing seams get additive hooks: an optional feature name and weight renormalisation in `backend/detection`, an `extra_checks` hook in `backend/correction`, a `terrain` passthrough plus one advisory sentence in `console/arbiter` (E4). `backend/terrain/validate.py` rescoring existing streams post hoc gives the E5 curves without re-running the solver per sweep point.

**Tech Stack:** Python 3.12, numpy, scipy.ndimage (flood fill), matplotlib (rasterising OSM polygons via `matplotlib.path`, plots), `cryptography` (Ed25519 map signature — to be added to `bootstrap.sh`), Overpass API over `urllib` for the map fetch.

**Spec:** `tracks/TRACK_E.md`

## Global Constraints

- Measurement domain only; the sensor emits a class posterior, never an image (CLAUDE.md, TRACK_E.md "What the sensor is").
- Library crypto only: Ed25519 from `cryptography`; never a primitive from scratch.
- Backend never imports console (design.md §5). Measurement/validation scripts may import console inside functions (precedent: `backend/measurement/displacement.py`).
- Thresholds derive from observed data: saturation = clean-run p99, gate floor = clean-run p0.1; window `W` defaults to 1 flagged untuned; the confusion diagonal is a swept parameter, never a default.
- A sensorless run (`terrain=None`) must produce identical `confidence`, `features`, `geometry` to today; the only additive change is `score_detail.features_scored`.
- Every terrain record carries `sensor.source: "simulated"`.
- Commit after every task with the session trailer `Claude-Session: https://claude.ai/code/session_013ULXp9U96B4umMvw37XWa3`.
- Run tests with `source .venv/bin/activate && python -m pytest <path> -q` (Python 3.12 venv; the full suite takes ~3 min).

---

### Task 1: Raster map maths (E1, fixture-backed)

**Files:**
- Create: `backend/terrain/__init__.py`
- Create: `backend/terrain/rastermap.py`
- Create: `fixtures/terrain_fixture.json`
- Test: `backend/terrain/test_rastermap.py`

**Interfaces:**
- Produces: `RasterMap` dataclass with `grid (rows,cols) int16`, `cell_m`, `origin_enu (e,n)` of the SW corner, `classes: list[str]`, `map_id`, `origin_lla (lat,lon)`, `attribution`, `signed`, `age_days`; methods `enu_from_lla(lat, lon)`, `class_at(e, n)`, `footprint_posterior(e, n, sigma_m, footprint_m=0.0) -> np.ndarray|None`, `consistent_extent_m(e, n) -> float|None`, `nearest_boundary(e, n) -> dict|None`, `boundary_distance(e, n, bearing_deg) -> float|None`, `collapse(groups) -> RasterMap`, `checksum() -> str`, `save_npz(path)`, `RasterMap.load_npz(path)`, `RasterMap.from_fixture(path)`. Constant `UNKNOWN = -1`. Function `m_per_deg(lat_deg) -> (m_per_deg_lat, m_per_deg_lon)`.

- [ ] **Step 1: Write the fixture raster**

`fixtures/terrain_fixture.json` — 20×20 cells of 10 m, SW corner at (−105, −105) so the antenna (0,0) is the centre of cell (10,10). Rows are listed **north to south** for readability and flipped on load. Class ids: 0 grass, 1 shrub, 2 tree_cover, 3 bare, 4 water, 5 paved, 6 building, −1 unknown.

```json
{
  "map_id": "fixture-20x20-v1",
  "cell_m": 10.0,
  "origin_enu": [-105.0, -105.0],
  "origin_lla": [38.92, -77.067],
  "classes": ["grass", "shrub", "tree_cover", "bare", "water", "paved", "building"],
  "attribution": "hand-drawn fixture",
  "rows_north_to_south": [
    [5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5],
    [5,4,4,4,4,0,0,0,0,0,0,0,0,0,5,5,6,6,6,5],
    [5,4,4,4,4,0,0,0,0,0,0,0,0,0,5,5,6,6,6,5],
    [5,4,4,4,4,0,0,0,0,0,0,0,0,0,5,5,6,6,6,5],
    [5,4,4,4,4,0,0,0,0,0,0,0,0,0,5,5,6,6,6,5],
    [5,0,0,0,0,0,0,0,0,0,0,0,0,0,5,5,6,6,6,5],
    [5,0,0,0,0,0,0,0,0,0,0,0,0,0,5,5,6,6,6,5],
    [5,0,0,0,0,0,0,0,0,0,0,0,0,0,5,5,6,6,6,5],
    [5,0,0,0,0,0,0,0,0,0,0,0,0,0,5,5,6,6,6,5],
    [5,0,0,0,0,0,0,0,0,0,0,0,0,0,5,5,6,6,6,5],
    [5,0,0,0,0,0,0,0,0,0,0,0,0,0,5,5,6,6,6,5],
    [5,0,0,0,0,0,0,0,0,0,0,0,0,0,5,5,6,6,6,5],
    [5,0,0,0,0,0,0,0,0,0,0,0,0,0,5,5,6,6,6,5],
    [5,0,-1,-1,0,0,0,0,0,0,0,0,0,0,5,5,6,6,6,5],
    [5,0,-1,-1,0,0,0,0,0,0,0,0,0,0,5,5,6,6,6,5],
    [5,0,0,0,0,0,0,0,0,0,0,0,0,0,5,5,6,6,6,5],
    [5,0,0,0,0,0,0,0,0,0,0,0,0,0,5,5,6,6,6,5],
    [5,0,0,0,0,0,0,0,6,6,6,6,0,0,5,5,6,6,6,5],
    [5,0,0,0,0,0,0,0,6,6,6,6,0,0,5,5,6,6,6,5],
    [5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5]
  ]
}
```

Hand-computed facts used by the tests (cell j covers e ∈ [−105+10j, −95+10j), row i covers n ∈ [−105+10i, −95+10i)): antenna cell (10,10) is grass; road col 14 begins at e = 35 (centre 40); south building rows 1–2 cols 8–11 begins at n = −85 from below (ray south hits it at 80 m); pond rows 15–18 cols 1–4; unknown patch rows 5–6 cols 2–3; paved ring on every edge, so the grass component is enclosed and its farthest cell from the antenna is (1,1) at (−90,−90), 127.279 m.

- [ ] **Step 2: Write the failing tests**

```python
# backend/terrain/test_rastermap.py
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
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest backend/terrain/test_rastermap.py -q`
Expected: ImportError (`backend.terrain` does not exist).

- [ ] **Step 4: Write the implementation**

`backend/terrain/__init__.py`:

```python
"""Track E — the terrain channel (tracks/TRACK_E.md). Simulated sensor,
signed pre-map, one feature, one map-derived bound, one advisory input, one
gate check. Off unless a map and a sensor are both supplied."""
```

`backend/terrain/rastermap.py`:

```python
"""Pre-map raster and the map-side maths of the terrain channel (TRACK_E.md E1).

Whiteboard derivations, in order of the spec:

  footprint posterior  q(c) = sum_x w(x) [map(x)==c] / sum_x w(x),
                       w ~ exp(-0.5 (max(|x-p|-rho_s, 0) / sigma)^2)
  extent               E(p) = max |x-p| over the connected same-class
                       component containing p (None if it touches the map edge)
  nearest boundary     argmin |x-p| over cells of a different class
  boundary distance    ray march along a bearing until the class changes

Unlabelled cells (UNKNOWN) are treated conservatively: they carry no weight
in the footprint (the map says nothing there), they are ABSORBED by the
extent (an attacker could hide there), and they never count as a boundary.

sigma is floored at half a cell: the map cannot resolve position below its
own cell size, so a tighter footprint would claim precision the map lacks.

Frame: local ENU metres from the surveyed antenna, the same frame as
console/mission.py. The §5 boundary forbids importing console, so the
metres-per-degree closed forms are repeated and pinned equal by test.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
from scipy import ndimage

UNKNOWN = -1


def m_per_deg(lat_deg: float) -> tuple[float, float]:
    lat = math.radians(lat_deg)
    return (111132.92 - 559.82 * math.cos(2 * lat), 111412.84 * math.cos(lat))


@dataclass
class RasterMap:
    grid: np.ndarray                  # (rows, cols) int16; row index grows NORTH, col index EAST
    cell_m: float
    origin_enu: tuple[float, float]   # (e, n) of the south-west corner, metres from the antenna
    classes: list[str]
    map_id: str
    origin_lla: tuple[float, float]   # (lat, lon) of the ENU origin — the antenna
    attribution: str = ""
    signed: bool = False
    age_days: float | None = None

    # ------------------------------------------------------------ coordinates
    def enu_from_lla(self, lat: float, lon: float) -> tuple[float, float]:
        m_lat, m_lon = m_per_deg(self.origin_lla[0])
        return ((lon - self.origin_lla[1]) * m_lon, (lat - self.origin_lla[0]) * m_lat)

    def cell_index(self, e: float, n: float) -> tuple[int, int] | None:
        j = math.floor((e - self.origin_enu[0]) / self.cell_m)
        i = math.floor((n - self.origin_enu[1]) / self.cell_m)
        rows, cols = self.grid.shape
        if 0 <= i < rows and 0 <= j < cols:
            return i, j
        return None

    def cell_centres(self, ii: np.ndarray, jj: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        return (self.origin_enu[0] + (jj + 0.5) * self.cell_m,
                self.origin_enu[1] + (ii + 0.5) * self.cell_m)

    def class_at(self, e: float, n: float) -> int | None:
        idx = self.cell_index(e, n)
        return None if idx is None else int(self.grid[idx])

    # ------------------------------------------------------------ E1 maths
    def footprint_posterior(self, e: float, n: float, sigma_m: float,
                            footprint_m: float = 0.0) -> np.ndarray | None:
        idx = self.cell_index(e, n)
        if idx is None:
            return None
        sigma = max(float(sigma_m), self.cell_m / 2.0)
        radius = 3.0 * sigma + footprint_m + self.cell_m
        r = int(math.ceil(radius / self.cell_m))
        i0, j0 = idx
        rows, cols = self.grid.shape
        i_lo, i_hi = max(0, i0 - r), min(rows, i0 + r + 1)
        j_lo, j_hi = max(0, j0 - r), min(cols, j0 + r + 1)
        ii, jj = np.mgrid[i_lo:i_hi, j_lo:j_hi]
        ce, cn = self.cell_centres(ii, jj)
        d_eff = np.maximum(np.hypot(ce - e, cn - n) - footprint_m, 0.0)
        w = np.exp(-0.5 * (d_eff / sigma) ** 2)
        sub = self.grid[i_lo:i_hi, j_lo:j_hi]
        labelled = sub != UNKNOWN
        if not labelled.any() or w[labelled].sum() <= 0.0:
            return None
        q = np.bincount(sub[labelled].astype(int), weights=w[labelled],
                        minlength=len(self.classes)).astype(float)
        return q / q.sum()

    def _component(self, i0: int, j0: int) -> np.ndarray | None:
        c0 = int(self.grid[i0, j0])
        if c0 == UNKNOWN:
            return None
        mask = (self.grid == c0) | (self.grid == UNKNOWN)
        labels, _ = ndimage.label(mask)
        return labels == labels[i0, j0]

    def consistent_extent_m(self, e: float, n: float) -> float | None:
        idx = self.cell_index(e, n)
        if idx is None:
            return None
        comp = self._component(*idx)
        if comp is None:
            return None
        rows, cols = self.grid.shape
        if comp[0, :].any() or comp[-1, :].any() or comp[:, 0].any() or comp[:, -1].any():
            return None                      # touches the window edge: no claim
        ii, jj = np.nonzero(comp)
        ce, cn = self.cell_centres(ii, jj)
        return float(np.hypot(ce - e, cn - n).max() + self.cell_m * math.sqrt(2) / 2)

    def nearest_boundary(self, e: float, n: float) -> dict | None:
        idx = self.cell_index(e, n)
        if idx is None:
            return None
        c0 = int(self.grid[idx])
        if c0 == UNKNOWN:
            return None
        other = (self.grid != c0) & (self.grid != UNKNOWN)
        if not other.any():
            return None
        ii, jj = np.nonzero(other)
        ce, cn = self.cell_centres(ii, jj)
        d = np.hypot(ce - e, cn - n)
        k = int(np.argmin(d))
        bearing = math.degrees(math.atan2(ce[k] - e, cn[k] - n)) % 360.0
        return {"distance_m": float(d[k]), "bearing_deg": float(bearing),
                "class_beyond": self.classes[int(self.grid[ii[k], jj[k]])]}

    def boundary_distance(self, e: float, n: float, bearing_deg: float,
                          step_m: float | None = None) -> float | None:
        c0 = self.class_at(e, n)
        if c0 is None or c0 == UNKNOWN:
            return None
        step = step_m or self.cell_m / 2.0
        de, dn = math.sin(math.radians(bearing_deg)), math.cos(math.radians(bearing_deg))
        s = step
        while True:
            c = self.class_at(e + s * de, n + s * dn)
            if c is None:
                return None
            if c != c0 and c != UNKNOWN:
                return float(s)
            s += step

    # ------------------------------------------------------------ collapse
    def collapse(self, groups: list[list[str]]) -> "RasterMap":
        """Merge classes the sensor cannot separate; each group keeps its
        first name. Unlisted classes are unchanged."""
        target = {}
        for g in groups:
            for name in g:
                target[name] = g[0]
        new_names = []
        for name in self.classes:
            t = target.get(name, name)
            if t not in new_names:
                new_names.append(t)
        remap = np.full(len(self.classes), UNKNOWN, dtype=np.int16)
        for old, name in enumerate(self.classes):
            remap[old] = new_names.index(target.get(name, name))
        grid = self.grid.copy()
        lab = grid != UNKNOWN
        grid[lab] = remap[grid[lab]]
        return replace(self, grid=grid, classes=new_names,
                       map_id=self.map_id + "+collapsed")

    # ------------------------------------------------------------ io
    def header(self) -> dict:
        return {"map_id": self.map_id, "cell_m": self.cell_m,
                "origin_enu": list(self.origin_enu), "origin_lla": list(self.origin_lla),
                "classes": list(self.classes), "attribution": self.attribution,
                "age_days": self.age_days}

    def checksum(self) -> str:
        h = hashlib.sha256()
        h.update(json.dumps(self.header(), sort_keys=True).encode())
        h.update(np.ascontiguousarray(self.grid, dtype=np.int16).tobytes())
        return h.hexdigest()

    def save_npz(self, path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez(path, grid=self.grid.astype(np.int16), header=json.dumps(self.header()))
        return path

    @classmethod
    def load_npz(cls, path) -> "RasterMap":
        z = np.load(path, allow_pickle=False)
        h = json.loads(str(z["header"]))
        return cls(grid=z["grid"].astype(np.int16), cell_m=float(h["cell_m"]),
                   origin_enu=tuple(h["origin_enu"]), classes=list(h["classes"]),
                   map_id=h["map_id"], origin_lla=tuple(h["origin_lla"]),
                   attribution=h.get("attribution", ""), signed=False,
                   age_days=h.get("age_days"))

    @classmethod
    def from_fixture(cls, path) -> "RasterMap":
        d = json.loads(Path(path).read_text())
        rows = np.array(d["rows_north_to_south"], dtype=np.int16)[::-1]
        return cls(grid=rows, cell_m=float(d["cell_m"]), origin_enu=tuple(d["origin_enu"]),
                   classes=list(d["classes"]), map_id=d["map_id"],
                   origin_lla=tuple(d["origin_lla"]), attribution=d.get("attribution", ""))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest backend/terrain/test_rastermap.py -q`
Expected: 11 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/terrain/__init__.py backend/terrain/rastermap.py backend/terrain/test_rastermap.py fixtures/terrain_fixture.json
git commit -m "terrain: E1 — raster map maths (footprint posterior, extent, nearest boundary, ray march) on a hand-drawn fixture"
```

---

### Task 2: Confusion-matrix sensor simulator (E2)

**Files:**
- Create: `backend/terrain/sensor.py`
- Test: `backend/terrain/test_sensor.py`

**Interfaces:**
- Produces: `confusion_from_diag(k: int, diag: float) -> np.ndarray` (row-stochastic k×k); `ConfusionSensor(M, classes, seed=20260820, footprint_m=0.0, sensor_id="sim-confusion-v0")` with `reset()`, `read(true_class: int) -> np.ndarray` (posterior over classes: draw `s ~ M[c,:]`, return `M[:, s] / M[:, s].sum()`), `last_reading: int|None`, `describe() -> dict` with keys `id, source ("simulated"), footprint_m, k, confusion_diag`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/terrain/test_sensor.py
import numpy as np
import pytest

from backend.terrain.sensor import ConfusionSensor, confusion_from_diag

CLASSES = ["grass", "paved", "water"]


def test_confusion_from_diag_is_row_stochastic():
    m = confusion_from_diag(3, 0.8)
    assert np.allclose(m.sum(axis=1), 1.0)
    assert np.allclose(np.diag(m), 0.8)
    assert np.allclose(m[0, 1], 0.1)


def test_identity_sensor_is_one_hot():
    s = ConfusionSensor(np.eye(3), CLASSES)
    for c in range(3):
        p = s.read(c)
        assert p[c] == 1.0 and p.sum() == 1.0 and s.last_reading == c


def test_posterior_is_bayes_over_the_column_for_the_drawn_reading():
    m = confusion_from_diag(3, 0.7)
    s = ConfusionSensor(m, CLASSES, seed=1)
    p = s.read(0)
    col = m[:, s.last_reading]
    assert np.allclose(p, col / col.sum())


def test_reset_replays_the_same_draws():
    s = ConfusionSensor(confusion_from_diag(3, 0.6), CLASSES, seed=7)
    a = [s.last_reading for _ in range(20) if s.read(0) is not None]
    s.reset()
    b = [s.last_reading for _ in range(20) if s.read(0) is not None]
    assert a == b and len(set(a)) > 1


def test_rejects_a_matrix_that_is_not_row_stochastic():
    with pytest.raises(ValueError):
        ConfusionSensor(np.ones((3, 3)), CLASSES)


def test_describe_is_stamped_simulated():
    d = ConfusionSensor(confusion_from_diag(3, 0.9), CLASSES, footprint_m=0.5).describe()
    assert d["source"] == "simulated" and d["k"] == 3
    assert d["confusion_diag"] == pytest.approx(0.9) and d["footprint_m"] == 0.5
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest backend/terrain/test_sensor.py -q`
Expected: ImportError on `backend.terrain.sensor`.

- [ ] **Step 3: Write the implementation**

```python
# backend/terrain/sensor.py
"""Simulated terrain sensor (TRACK_E.md E2).

There is no sensor. This stands in with a confusion matrix M[c_true, c_read]:
at each epoch it draws a hard reading s ~ M[c_true, :] and emits the posterior
a calibrated sensor would report, Bayes with a flat prior over the drawn
column: p_s(c) = M[c, s] / sum_c' M[c', s]. Seeded, so reset() replays the
identical draw sequence — the clean and injected replays then differ in the
terrain channel ONLY through the believed position.

Every describe() carries source "simulated". The diagonal of M is a swept
parameter (TRACK_E.md "Measurement plan"); this module has no default for it.
"""
from __future__ import annotations

import numpy as np


def confusion_from_diag(k: int, diag: float) -> np.ndarray:
    """Symmetric confusion: `diag` on the diagonal, the rest spread evenly."""
    if not 0.0 < diag <= 1.0:
        raise ValueError(f"diag must be in (0, 1], got {diag}")
    if k < 2:
        raise ValueError("need at least two classes")
    off = (1.0 - diag) / (k - 1)
    m = np.full((k, k), off)
    np.fill_diagonal(m, diag)
    return m


class ConfusionSensor:
    def __init__(self, M, classes: list[str], seed: int = 20260820,
                 footprint_m: float = 0.0, sensor_id: str = "sim-confusion-v0"):
        m = np.asarray(M, dtype=float)
        if m.ndim != 2 or m.shape[0] != m.shape[1] or m.shape[0] != len(classes):
            raise ValueError(f"M must be {len(classes)}x{len(classes)}, got {m.shape}")
        if (m < 0).any() or not np.allclose(m.sum(axis=1), 1.0):
            raise ValueError("M must be row-stochastic")
        self.M = m
        self.classes = list(classes)
        self.seed = seed
        self.footprint_m = float(footprint_m)
        self.sensor_id = sensor_id
        self.last_reading: int | None = None
        self.reset()

    def reset(self) -> None:
        self._rng = np.random.default_rng(self.seed)
        self.last_reading = None

    def read(self, true_class: int) -> np.ndarray:
        s = int(self._rng.choice(len(self.classes), p=self.M[int(true_class)]))
        self.last_reading = s
        col = self.M[:, s]
        return col / col.sum()

    def describe(self) -> dict:
        return {"id": self.sensor_id, "source": "simulated",
                "footprint_m": self.footprint_m, "k": len(self.classes),
                "confusion_diag": float(np.mean(np.diag(self.M)))}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest backend/terrain/test_sensor.py -q`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/terrain/sensor.py backend/terrain/test_sensor.py
git commit -m "terrain: E2 — confusion-matrix sensor simulator, seeded, stamped simulated"
```

---

### Task 3: Detection seams — optional feature, weight renormalisation, terrain in the record

**Files:**
- Modify: `backend/detection/features.py` (add `OPTIONAL_FEATURE_NAMES` next to `FEATURE_NAMES`, ~line 66)
- Modify: `backend/detection/confidence.py` (`Weights.equal`, `Weights.dirichlet(names=)`, `anomaly`, `scored_features`, `score`)
- Modify: `backend/detection/emit.py` (`record(..., terrain=None)`, `features_scored` in `score_detail`)
- Modify: `backend/detection/__init__.py` (export `OPTIONAL_FEATURE_NAMES`, `scored_features`)
- Test: `tests/test_detection.py` (append)

**Interfaces:**
- Produces: `OPTIONAL_FEATURE_NAMES = ("terrain_mismatch",)`; `Weights.equal(names) -> Weights`; `Weights.dirichlet(rng, alpha=1.0, beta=None, names=FEATURE_NAMES)`; `scored_features(features, w) -> list[str]` (names in `w.feature` whose value in `features` is present and finite); `anomaly()` renormalises over scored names; `score()` result gains `"features_scored"`; `record(..., terrain: dict | None = None)` adds a `terrain` key only when given and includes `features_scored` in `score_detail`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_detection.py`)

```python
# --- Track E seams (TRACK_E.md E3) -----------------------------------------
from backend.detection import OPTIONAL_FEATURE_NAMES, scored_features


def test_optional_feature_is_not_in_the_base_set():
    assert OPTIONAL_FEATURE_NAMES == ("terrain_mismatch",)
    assert not set(OPTIONAL_FEATURE_NAMES) & set(FEATURE_NAMES)


def test_weights_equal_over_five_names():
    w = Weights.equal(FEATURE_NAMES + OPTIONAL_FEATURE_NAMES)
    assert len(w.feature) == 5 and all(abs(v - 0.2) < 1e-12 for v in w.feature.values())


def test_missing_optional_feature_renormalises_not_reads_zero():
    w = Weights.equal(FEATURE_NAMES + OPTIONAL_FEATURE_NAMES)
    four = {n: 0.4 for n in FEATURE_NAMES}
    assert anomaly(four, w) == pytest.approx(0.4)          # not 0.32
    assert scored_features(four, w) == list(FEATURE_NAMES)
    five = dict(four, terrain_mismatch=1.0)
    assert anomaly(five, w) == pytest.approx(0.4 * 0.8 + 1.0 * 0.2)
    assert scored_features(five, w) == list(FEATURE_NAMES + OPTIONAL_FEATURE_NAMES)


def test_nan_feature_is_not_scored():
    w = Weights()
    feats = {n: 0.5 for n in FEATURE_NAMES}
    feats["cross_constellation"] = float("nan")
    assert anomaly(feats, w) == pytest.approx(0.5)
    assert "cross_constellation" not in scored_features(feats, w)


def test_score_reports_features_scored():
    feats = {n: 0.3 for n in FEATURE_NAMES}
    assert score(feats, None, Weights())["features_scored"] == list(FEATURE_NAMES)


def test_dirichlet_over_named_features():
    rng = np.random.default_rng(0)
    w = Weights.dirichlet(rng, names=FEATURE_NAMES + OPTIONAL_FEATURE_NAMES)
    assert set(w.feature) == set(FEATURE_NAMES + OPTIONAL_FEATURE_NAMES)
    assert abs(sum(w.feature.values()) - 1.0) < 1e-9


def test_record_carries_terrain_only_when_given():
    feats = {n: 0.3 for n in FEATURE_NAMES}
    s = score(feats, None, Weights())
    t = datetime(2026, 8, 20, 0, 0)
    plain = record(t, feats, s, n_sv=10)
    assert "terrain" not in plain
    assert plain["score_detail"]["features_scored"] == list(FEATURE_NAMES)
    with_t = record(t, feats, s, n_sv=10, terrain={"available": True})
    assert with_t["terrain"] == {"available": True}
```

Check the top of `tests/test_detection.py` for existing imports of `np`, `datetime`, `record`, `score`, `anomaly`, `Weights`, `pytest`; add whichever is missing.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_detection.py -q -k "optional or renormalises or nan_feature or features_scored or named_features or terrain_only"`
Expected: ImportError on `OPTIONAL_FEATURE_NAMES`.

- [ ] **Step 3: Implement**

`backend/detection/features.py`, after `FEATURE_NAMES`:

```python
# Track E (tracks/TRACK_E.md): features that exist only when their sensor
# does. Never in FEATURE_NAMES — a run without the sensor must score exactly
# as before. Weights renormalise over the features actually scored
# (confidence.anomaly), so an absent optional feature is not scored rather
# than quietly reading zero.
OPTIONAL_FEATURE_NAMES = ("terrain_mismatch",)
```

`backend/detection/confidence.py` — replace `dirichlet`, `anomaly`, and extend `score`; add `equal` and `scored_features`:

```python
    @classmethod
    def equal(cls, names) -> "Weights":
        """Untuned equal weights over an explicit feature set (Track E adds
        OPTIONAL_FEATURE_NAMES when its sensor is present)."""
        names = tuple(names)
        return cls(feature={n: 1.0 / len(names) for n in names}, tuned=False,
                   note=f"untuned placeholder: equal weights over {len(names)}, beta 0.5")

    @classmethod
    def dirichlet(cls, rng, alpha: float = 1.0, beta=None,
                  names=FEATURE_NAMES) -> "Weights":
        """One draw for the §10 sweep: feature weights from a Dirichlet, and a
        blend drawn uniformly unless one is pinned."""
        names = tuple(names)
        w = rng.dirichlet([alpha] * len(names))
        b = float(rng.uniform()) if beta is None else float(beta)
        return cls(feature=dict(zip(names, map(float, w))), beta=b,
                   tuned=False, note="Dirichlet draw (§10 sweep)")


def _finite(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and np.isfinite(v)


def scored_features(features: dict, w: Weights) -> list[str]:
    """Names in the weight vector that carry a finite value this epoch.
    Absent or NaN features are not scored (TRACK_E.md E3 seam)."""
    return [n for n in w.feature if _finite(features.get(n))]


def anomaly(features: dict, w: Weights) -> float:
    """Weighted mean of the tuned half over the features actually scored,
    weights renormalised to the scored subset. [0,1], higher = more anomalous."""
    scored = scored_features(features, w)
    total = sum(w.feature[n] for n in scored)
    if total <= 0.0:
        return 0.0
    return float(sum(w.feature[n] * features[n] for n in scored) / total)
```

In `score()`, add `"features_scored": scored_features(features, w),` to the returned dict.

`backend/detection/emit.py`:
- signature: `record(time, features, scored, n_sv, geometry=None, credential_status="VALID", position=None, by_sv=None, terrain=None)`
- score_detail key tuple gains `"features_scored"`.
- after building `rec` (before return): `if terrain is not None: rec["terrain"] = terrain`. Build `rec` as a local first, then return it.

`backend/detection/__init__.py`: import and export `OPTIONAL_FEATURE_NAMES` and `scored_features`.

- [ ] **Step 4: Run the detection tests**

Run: `python -m pytest tests/test_detection.py backend/correction console/tests -q`
Expected: all pass (the fixture/contract tests compare `features` to `FEATURE_NAMES`, unchanged).

- [ ] **Step 5: Commit**

```bash
git add backend/detection tests/test_detection.py
git commit -m "detection: Track E seams — OPTIONAL_FEATURE_NAMES, weights renormalise over scored features, features_scored, terrain passthrough in record()"
```

---

### Task 4: Per-epoch channel — feature, block, calibration, bound rule, gate check (E3 core)

**Files:**
- Create: `backend/terrain/channel.py`
- Test: `backend/terrain/test_channel.py`

**Interfaces:**
- Consumes: `RasterMap` (Task 1), `ConfusionSensor` (Task 2).
- Produces: `match_likelihood(q, p_s) -> (L, L_max)`; `mismatch(q, p_s) -> float`; `TerrainChannel(rmap, sensor, sigma_uere_m, true_enu=(0.0, 0.0), window_epochs=1, saturation=None, floor=None)` with `reset()`, `calibrate(samples: iterable[(lat, lon, hdop)]) -> dict`, `step(believed_lla: dict|None, hdop: float|None) -> dict` returning `{"feature": float|None, "block": dict}`, `gate_check(corrected_lla: dict|None) -> dict` returning `{"terrain_consistent": bool|None}`; `apply_bound(geom: dict, block: dict) -> dict`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/terrain/test_channel.py
"""E3 acceptance (TRACK_E.md): M = I gives mismatch exactly 0 at the true
position; across a boundary it is exactly 1 - p_s(c')/max p_s; calibration
sets saturation and floor from the clean run; the min rule names its source;
the gate check is null until calibrated."""
from pathlib import Path

import numpy as np
import pytest

from backend.terrain.channel import (TerrainChannel, apply_bound,
                                     match_likelihood, mismatch)
from backend.terrain.rastermap import RasterMap
from backend.terrain.sensor import ConfusionSensor, confusion_from_diag

FIXTURE = Path("fixtures/terrain_fixture.json")
LAT0, LON0 = 38.92, -77.067


@pytest.fixture
def rmap():
    return RasterMap.from_fixture(FIXTURE)


def lla_at(rmap, e, n):
    from backend.terrain.rastermap import m_per_deg
    m_lat, m_lon = m_per_deg(LAT0)
    return {"lat": LAT0 + n / m_lat, "lon": LON0 + e / m_lon, "alt": 58.0}


def test_match_likelihood_and_mismatch_by_hand():
    q = np.array([1.0, 0.0, 0.0])
    p = np.array([0.7, 0.2, 0.1])
    L, Lmax = match_likelihood(q, p)
    assert L == pytest.approx(0.7) and Lmax == pytest.approx(0.7)
    assert mismatch(q, p) == pytest.approx(0.0)
    q2 = np.array([0.0, 1.0, 0.0])
    assert mismatch(q2, p) == pytest.approx(1 - 0.2 / 0.7)


def test_identity_sensor_reads_zero_at_the_true_position(rmap):
    ch = TerrainChannel(rmap, ConfusionSensor(np.eye(7), rmap.classes), sigma_uere_m=1.9)
    for _ in range(5):
        out = ch.step(lla_at(rmap, 0.3, -0.5), hdop=0.8)
        assert out["feature"] == 0.0
        b = out["block"]
        assert b["available"] and b["sensed"]["class"] == "grass"
        assert b["map_at_position"]["class"] == "grass"
        assert b["match_likelihood"] == pytest.approx(1.0)
        assert b["sensor"]["source"] == "simulated"


def test_walked_across_a_boundary_reads_one_minus_ratio(rmap):
    m = confusion_from_diag(7, 0.9)
    ch = TerrainChannel(rmap, ConfusionSensor(m, rmap.classes, seed=3), sigma_uere_m=0.1)
    out = ch.step(lla_at(rmap, 70.0, 0.0), hdop=0.5)      # believed inside the building block
    p_s = ch.last_posterior
    c_building = rmap.classes.index("building")
    expected = 1 - p_s[c_building] / p_s.max()
    assert out["feature"] == pytest.approx(expected)
    assert out["block"]["map_at_position"]["class"] == "building"


def test_identity_sensor_walked_reads_exactly_one(rmap):
    ch = TerrainChannel(rmap, ConfusionSensor(np.eye(7), rmap.classes), sigma_uere_m=0.1)
    assert ch.step(lla_at(rmap, 70.0, 0.0), hdop=0.5)["feature"] == 1.0


def test_window_is_a_trailing_mean(rmap):
    ch = TerrainChannel(rmap, ConfusionSensor(np.eye(7), rmap.classes),
                        sigma_uere_m=0.1, window_epochs=4)
    for _ in range(4):
        ch.step(lla_at(rmap, 0.0, 0.0), hdop=0.5)
    assert ch.step(lla_at(rmap, 70.0, 0.0), hdop=0.5)["feature"] == pytest.approx(0.25)
    assert ch.step(lla_at(rmap, 70.0, 0.0), hdop=0.5)["feature"] == pytest.approx(0.5)


def test_off_map_or_unsolved_epoch_is_not_scored(rmap):
    ch = TerrainChannel(rmap, ConfusionSensor(np.eye(7), rmap.classes), sigma_uere_m=1.0)
    out = ch.step(None, None)
    assert out["feature"] is None and out["block"]["match_likelihood"] is None
    assert out["block"]["available"] is True
    far = ch.step(lla_at(rmap, 5000.0, 0.0), hdop=0.5)
    assert far["feature"] is None and far["block"]["map_at_position"] is None


def test_calibrate_sets_saturation_and_floor_then_resets(rmap):
    m = confusion_from_diag(7, 0.8)
    ch = TerrainChannel(rmap, ConfusionSensor(m, rmap.classes, seed=5), sigma_uere_m=1.9)
    lla = lla_at(rmap, 0.0, 0.0)
    stats = ch.calibrate([(lla["lat"], lla["lon"], 0.8)] * 400)
    assert 0.0 < ch.saturation <= 1.0 and 0.0 < ch.floor <= 1.0
    assert stats["n"] == 400 and stats["saturation"] == ch.saturation
    assert ch.gate_check(lla) == {"terrain_consistent": None}   # no posterior since reset
    ch.step(lla, hdop=0.8)
    assert ch.gate_check(lla)["terrain_consistent"] in (True, False)
    first = [ch.sensor.last_reading]
    ch.reset()
    ch.step(lla, hdop=0.8)
    assert ch.sensor.last_reading == first[0]                    # same draw sequence


def test_gate_check_is_true_at_truth_and_false_across_boundary(rmap):
    ch = TerrainChannel(rmap, ConfusionSensor(np.eye(7), rmap.classes), sigma_uere_m=0.1,
                        floor=0.5)
    ch.step(lla_at(rmap, 0.0, 0.0), hdop=0.5)
    assert ch.gate_check(lla_at(rmap, 0.0, 0.0)) == {"terrain_consistent": True}
    assert ch.gate_check(lla_at(rmap, 70.0, 0.0)) == {"terrain_consistent": False}
    assert ch.gate_check(None) == {"terrain_consistent": None}


def test_apply_bound_min_rule_names_its_source():
    g = apply_bound({"displacement_bound_m": 14.0}, {"available": True, "consistent_extent_m": 9.5})
    assert g["displacement_bound_m"] == 9.5 and g["bound_source"] == "terrain"
    assert g["residual_bound_m"] == 14.0
    g = apply_bound({"displacement_bound_m": 14.0}, {"available": True, "consistent_extent_m": 210.0})
    assert g["displacement_bound_m"] == 14.0 and g["bound_source"] == "residual"
    g = apply_bound({"displacement_bound_m": None}, {"available": True, "consistent_extent_m": 210.0})
    assert g["displacement_bound_m"] == 210.0 and g["bound_source"] == "terrain"
    g = apply_bound({"displacement_bound_m": 14.0}, {"available": True, "consistent_extent_m": None})
    assert g["displacement_bound_m"] == 14.0 and g["bound_source"] == "residual"
    untouched = apply_bound({"displacement_bound_m": 14.0}, None)
    assert untouched == {"displacement_bound_m": 14.0}


def test_saturation_scales_the_feature(rmap):
    ch = TerrainChannel(rmap, ConfusionSensor(np.eye(7), rmap.classes), sigma_uere_m=0.1,
                        saturation=0.5)
    assert ch.step(lla_at(rmap, 70.0, 0.0), hdop=0.5)["feature"] == 1.0   # clipped
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest backend/terrain/test_channel.py -q`
Expected: ImportError on `backend.terrain.channel`.

- [ ] **Step 3: Write the implementation**

```python
# backend/terrain/channel.py
"""The per-epoch terrain channel (TRACK_E.md E3).

    q      map posterior under the believed position's footprint   (rastermap)
    p_s    sensor posterior                                          (sensor)
    L      = sum_c q(c) p_s(c)          match likelihood
    L_max  = max_c p_s(c)               the best any position could score
    m_t    = 1 - L / L_max              per-epoch mismatch, [0, 1]

feature  = clip(mean of m_t over the trailing W epochs / saturation, 0, 1)
           saturation = p99 of the windowed value on the clean calibration
           run (Track A's rule); W = 1 until the threshold session picks it,
           flagged untuned in the block.
extent   = the class-consistent extent from the map at the believed position
           (TRACK_E.md step 5); apply_bound() takes min with the residual
           bound and names the source (step 6).
gate     = terrain_consistent: L at the CORRECTED position >= floor, where
           floor = p0.1 of L on the clean run (mirror of Track D's p99.9).

Footprint sigma = hdop * sigma_UERE (isotropic horizontal; HDOP^2 is the
trace of the horizontal block of inv(H'H), so this is the spec's Sigma
collapsed to its trace — one number the stream already carries). The map
floors it at half a cell.

The true position is where the sensor is simulated: the antenna, (0, 0) ENU,
on the real replay. The believed position is looked up on the map. Nothing
here is frozen: the believed position is the hypothesis under test, not the
evaluator (TRACK_E.md "The borrowed-state principle, applied here").
"""
from __future__ import annotations

from collections import deque

import numpy as np

from backend.terrain.rastermap import UNKNOWN, RasterMap
from backend.terrain.sensor import ConfusionSensor

WINDOW_UNTUNED = "untuned: W=1, no smoothing until the threshold session"


def match_likelihood(q: np.ndarray, p_s: np.ndarray) -> tuple[float, float]:
    return float(np.dot(q, p_s)), float(np.max(p_s))


def mismatch(q: np.ndarray, p_s: np.ndarray) -> float:
    L, Lmax = match_likelihood(q, p_s)
    if Lmax <= 0.0:
        return 0.0
    return float(np.clip(1.0 - L / Lmax, 0.0, 1.0))


def _dist(classes: list[str], p: np.ndarray) -> dict:
    return {"class": classes[int(np.argmax(p))],
            "p": {classes[i]: round(float(v), 4) for i, v in enumerate(p) if v > 0.0}}


class TerrainChannel:
    def __init__(self, rmap: RasterMap, sensor: ConfusionSensor, sigma_uere_m: float,
                 true_enu: tuple[float, float] = (0.0, 0.0), window_epochs: int = 1,
                 saturation: float | None = None, floor: float | None = None):
        if sensor.classes != rmap.classes:
            raise ValueError("sensor and map must share one class list (collapse the map first)")
        self.rmap = rmap
        self.sensor = sensor
        self.sigma_uere_m = float(sigma_uere_m)
        self.true_enu = tuple(true_enu)
        self.window_epochs = int(window_epochs)
        if self.window_epochs < 1:
            raise ValueError("window_epochs >= 1")
        self.saturation = saturation
        self.floor = floor
        self.saturation_provenance = "uncalibrated" if saturation is None else "supplied"
        self.floor_provenance = "uncalibrated" if floor is None else "supplied"
        true_class = rmap.class_at(*self.true_enu)
        if true_class is None or true_class == UNKNOWN:
            raise ValueError("the true position must sit on a labelled map cell")
        self._true_class = int(true_class)
        self.reset()

    # ------------------------------------------------------------ state
    def reset(self) -> None:
        self.sensor.reset()
        self._window: deque = deque(maxlen=self.window_epochs)
        self.last_posterior: np.ndarray | None = None
        self._last_hdop: float | None = None

    # ------------------------------------------------------------ lookups
    def _lookup(self, lla: dict | None, hdop: float | None):
        """Map posterior under the footprint at a believed position, or None."""
        if lla is None or hdop is None or not np.isfinite(hdop):
            return None, None
        e, n = self.rmap.enu_from_lla(lla["lat"], lla["lon"])
        q = self.rmap.footprint_posterior(e, n, sigma_m=float(hdop) * self.sigma_uere_m,
                                          footprint_m=self.sensor.footprint_m)
        return q, (e, n)

    # ------------------------------------------------------------ per epoch
    def step(self, believed_lla: dict | None, hdop: float | None) -> dict:
        p_s = self.sensor.read(self._true_class)
        self.last_posterior = p_s
        self._last_hdop = hdop
        q, enu = self._lookup(believed_lla, hdop)

        L = m_t = extent = nb = None
        map_dist = None
        if q is not None:
            L, _ = match_likelihood(q, p_s)
            m_t = mismatch(q, p_s)
            self._window.append(m_t)
            map_dist = _dist(self.rmap.classes, q)
            extent = self.rmap.consistent_extent_m(*enu)
            nb = self.rmap.nearest_boundary(*enu)

        feature = None
        if len(self._window):
            raw = float(np.mean(self._window))
            if self.saturation is not None and self.saturation > 0.0:
                raw = raw / self.saturation
            feature = float(np.clip(raw, 0.0, 1.0))

        block = {
            "available": True,
            "sensed": _dist(self.rmap.classes, p_s),
            "map_at_position": map_dist,
            "match_likelihood": None if L is None else round(L, 4),
            "consistent_extent_m": None if extent is None else round(extent, 1),
            "nearest_boundary": None if nb is None else {
                "distance_m": round(nb["distance_m"], 1),
                "bearing_deg": round(nb["bearing_deg"], 1),
                "class_beyond": nb["class_beyond"]},
            "map": {"id": self.rmap.map_id, "cell_m": self.rmap.cell_m,
                    "classes": len(self.rmap.classes), "signed": bool(self.rmap.signed),
                    "age_days": self.rmap.age_days, "attribution": self.rmap.attribution},
            "sensor": {**self.sensor.describe(),
                       "window_epochs": self.window_epochs,
                       "window_provenance": (WINDOW_UNTUNED if self.window_epochs == 1
                                             else "chosen at the threshold session"),
                       "saturation": self.saturation,
                       "saturation_provenance": self.saturation_provenance,
                       "floor": self.floor, "floor_provenance": self.floor_provenance},
        }
        return {"feature": feature, "block": block}

    def gate_check(self, corrected_lla: dict | None) -> dict:
        """Check 6 for the correction gate: L at the corrected fix >= floor.
        None (not evaluated) without a floor, a posterior, or a fix."""
        if self.floor is None or self.last_posterior is None or corrected_lla is None:
            return {"terrain_consistent": None}
        q, _ = self._lookup(corrected_lla, self._last_hdop)
        if q is None:
            return {"terrain_consistent": None}
        L, _ = match_likelihood(q, self.last_posterior)
        return {"terrain_consistent": bool(L >= self.floor)}

    # ------------------------------------------------------------ calibration
    def calibrate(self, samples, q_saturation: float = 99.0, q_floor: float = 0.1) -> dict:
        """Run the channel over the clean day (lat, lon, hdop) and fit the
        saturation (p99 of the windowed mismatch) and the gate floor (p0.1 of
        L). Thresholds derive from observed data — here, the clean run of the
        simulated sensor at the antenna. Resets afterwards so the scored
        replay sees the identical draw sequence."""
        self.reset()
        raw, Ls = [], []
        for lat, lon, hdop in samples:
            out = self.step({"lat": lat, "lon": lon}, hdop)
            if out["feature"] is not None and self.saturation is None:
                raw.append(float(np.mean(self._window)))
            elif out["feature"] is not None:
                raw.append(float(np.mean(self._window)))
            if out["block"]["match_likelihood"] is not None:
                Ls.append(out["block"]["match_likelihood"])
        n = len(raw)
        if n == 0:
            raise ValueError("calibration saw no scoreable epoch")
        sat = float(np.percentile(raw, q_saturation))
        self.saturation = sat if sat > 0.0 else 1.0     # a perfect sensor: no scaling
        self.saturation_provenance = f"p{q_saturation:g} of clean-run windowed mismatch, n={n}"
        self.floor = float(np.percentile(Ls, q_floor))
        self.floor_provenance = f"p{q_floor:g} of clean-run match likelihood, n={len(Ls)}"
        stats = {"n": n, "saturation": self.saturation, "floor": self.floor,
                 "mismatch_p50": float(np.percentile(raw, 50)),
                 "mismatch_p99": float(np.percentile(raw, 99)),
                 "mismatch_mean": float(np.mean(raw)),
                 "L_p0.1": self.floor, "L_p50": float(np.percentile(Ls, 50))}
        self.reset()
        return stats


def apply_bound(geom: dict, block: dict | None) -> dict:
    """TRACK_E.md step 6: displacement_bound_m = min(residual, extent), source
    named. Untouched when the channel is absent — a sensorless geometry block
    is byte-identical to today's."""
    if not block or not block.get("available"):
        return geom
    residual = geom.get("displacement_bound_m")
    extent = block.get("consistent_extent_m")
    geom["residual_bound_m"] = residual
    if extent is not None and (residual is None or extent < residual):
        geom["displacement_bound_m"] = extent
        geom["bound_source"] = "terrain"
    else:
        geom["bound_source"] = "residual"
    return geom
```

Note on `calibrate`: the two `raw.append` branches are identical on purpose in the draft; collapse them to one `if out["feature"] is not None:` when implementing.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest backend/terrain -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/terrain/channel.py backend/terrain/test_channel.py
git commit -m "terrain: E3 — per-epoch channel: mismatch feature, block, clean-run calibration, min bound rule, gate check"
```

---

### Task 5: Correction gate hook — check 6 via `extra_checks`

**Files:**
- Modify: `backend/correction/emit.py` (`_emit`, `correction_block`, `CorrectionEmitter`)
- Test: `backend/correction/test_emit.py` (append)

**Interfaces:**
- Produces: `correction_block(..., extra_checks=None)` and `CorrectionEmitter(..., extra_checks=None)` where `extra_checks: Callable[[dict | None], dict]` receives the corrected LLA (or None on fail-closed paths) and returns check entries merged into `checks` **before** the gate steps.

- [ ] **Step 1: Write the failing test** (append to `backend/correction/test_emit.py`; reuse whatever fixture that file already uses to build a passing `correction_block` call — read the file first and copy its simplest passing-epoch setup)

```python
def test_extra_checks_are_merged_before_the_gate_steps():
    """Track E check 6: a False extra check revokes; None leaves the gate
    untouched; the key is emitted either way."""
    from backend.correction.gate import Gate
    from backend.correction.emit import _emit
    checks = {"pl_under_al": True, "redundancy": True, "residual_test": True,
              "continuity": None, "cross_constellation": None}
    seen = []
    def extra(lla):
        seen.append(lla)
        return {"terrain_consistent": False}
    g = Gate()
    for _ in range(12):
        blk = _emit(g, checks, 3.0, {"lat": 1.0, "lon": 2.0, "alt": 3.0}, {"G01": 1.0},
                    15.0, extra_checks=extra)
    assert blk["checks"]["terrain_consistent"] is False and blk["correction_ok"] is False
    assert seen[-1] == {"lat": 1.0, "lon": 2.0, "alt": 3.0}
    g = Gate()
    for _ in range(12):
        blk = _emit(g, checks, 3.0, {"lat": 1.0, "lon": 2.0, "alt": 3.0}, {"G01": 1.0},
                    15.0, extra_checks=lambda lla: {"terrain_consistent": None})
    assert blk["checks"]["terrain_consistent"] is None and blk["correction_ok"] is True
    blk = _emit(Gate(), checks, 3.0, None, {}, 15.0)
    assert "terrain_consistent" not in blk["checks"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest backend/correction/test_emit.py -q -k extra_checks`
Expected: TypeError (unexpected keyword `extra_checks`).

- [ ] **Step 3: Implement**

In `backend/correction/emit.py`:
- `_emit(gate, checks, pl_m, corrected_lla, weights, alert_limit_m, extra_checks=None)`: first line `if extra_checks is not None: checks = {**checks, **extra_checks(corrected_lla)}`.
- `correction_block(..., drift_bound_m=None, dr_ecef=None, extra_checks=None)`: pass `extra_checks=extra_checks` at every `_emit(...)` call site (there are four: two early fail-closed returns, the redundancy-failure return, and the success return).
- `CorrectionEmitter.__init__(..., context_fn=None, extra_checks=None)`: store; pass through in `__call__`.
- Docstring: one paragraph under "## Purity and state": "`extra_checks` (Track E) is called with the corrected LLA, or None on fail-closed paths, and its entries are merged into `checks` before the gate steps — so a sixth check participates in hysteresis rather than being bolted on after `correction_ok` was decided."

- [ ] **Step 4: Run the correction tests**

Run: `python -m pytest backend/correction -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/correction/emit.py backend/correction/test_emit.py
git commit -m "correction: extra_checks hook so Track E's terrain_consistent enters the gate before hysteresis"
```

---

### Task 6: Console consumers — arbiter passthrough, DEGRADED advisory, explanation phrase

**Files:**
- Modify: `console/arbiter/machine.py` (`Decision.terrain`, `_emit`, `step`)
- Modify: `console/arbiter/explain.py` (`FEATURE_PHRASE`, terrain sentence + claim)
- Test: `console/tests/test_machine.py` (append), `console/tests/test_explain.py` (create if absent; check for an existing explain test file first and append there instead)

**Interfaces:**
- Produces: `Decision.terrain: dict` (the epoch's `terrain` block or `{}`); in DEGRADED, `Decision.advisory` gains the sentence `Terrain: if the reported position is right, the sensor should read <class> <d> m to the <compass>.` when `terrain.nearest_boundary` exists; `explain()` adds the phrase for `terrain_mismatch`, and when sensed and map classes differ adds `The terrain sensor reads <a>; the map has <b> at the reported position.` with a claim on `terrain.match_likelihood`.

- [ ] **Step 1: Write the failing tests**

Append to `console/tests/test_machine.py` (it already imports `Arbiter`, `TrustState`; check for an `epoch(...)` helper and reuse it, else build dicts inline as below):

```python
def _ep(conf, terrain=None, geometry=None):
    e = {"timestamp": "t", "confidence": conf, "credential_status": "VALID"}
    if geometry is not None:
        e["geometry"] = geometry
    if terrain is not None:
        e["terrain"] = terrain
    return e


TERRAIN = {"available": True, "match_likelihood": 0.1,
           "sensed": {"class": "grass", "p": {"grass": 1.0}},
           "map_at_position": {"class": "paved", "p": {"paved": 1.0}},
           "nearest_boundary": {"distance_m": 38.0, "bearing_deg": 47.0,
                                "class_beyond": "water"}}


def test_terrain_block_passes_through_and_advises_only_in_degraded():
    arb = Arbiter()
    d = arb.step(_ep(0.60, TERRAIN, geometry={"next_best_observation": "E"}))
    assert d.state is TrustState.DEGRADED
    assert d.terrain == TERRAIN
    assert "should read water 38 m to the north-east" in d.advisory
    arb = Arbiter()
    d = arb.step(_ep(0.95, TERRAIN))
    assert d.state is TrustState.NOMINAL and d.advisory is None
    arb = Arbiter()
    d = arb.step(_ep(0.30, TERRAIN))
    assert d.state is not TrustState.DEGRADED and d.advisory is None


def test_sensorless_epoch_has_empty_terrain():
    assert Arbiter().step(_ep(0.95)).terrain == {}
```

Explain test (new or appended):

```python
from console.arbiter.explain import FEATURE_PHRASE, explain, verify
from console.arbiter.machine import Arbiter


def test_terrain_mismatch_has_an_operator_phrase():
    assert "terrain_mismatch" in FEATURE_PHRASE
    assert "map" in FEATURE_PHRASE["terrain_mismatch"]


def test_terrain_disagreement_is_named_and_verifiable():
    ep = {"timestamp": "t", "confidence": 0.60, "credential_status": "VALID",
          "features": {"cn0_anomaly": 0.1, "terrain_mismatch": 0.9},
          "terrain": {"available": True, "match_likelihood": 0.12,
                      "sensed": {"class": "grass", "p": {"grass": 1.0}},
                      "map_at_position": {"class": "paved", "p": {"paved": 1.0}}}}
    d = Arbiter().step(ep)
    ex = explain(d)
    assert ex["headline"].startswith(FEATURE_PHRASE["terrain_mismatch"].capitalize())
    assert "reads grass; the map has paved" in ex["detail"]
    ok, failures = verify(ex, ep)
    assert ok, failures
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest console/tests -q -k "terrain"`
Expected: AttributeError `Decision has no attribute terrain` / assertion on phrase.

- [ ] **Step 3: Implement**

`console/arbiter/machine.py`:
- `Decision`: add `terrain: dict = field(default_factory=dict)` after `geometry`.
- Add helper near the top:

```python
_COMPASS = ("north", "north-east", "east", "south-east",
            "south", "south-west", "west", "north-west")


def compass(bearing_deg: float) -> str:
    return _COMPASS[int(((float(bearing_deg) % 360.0) + 22.5) // 45.0) % 8]
```

- `_emit(..., features, geometry, terrain)`: inside the DEGRADED branch, after the existing advisory assembly:

```python
            nb = (terrain or {}).get("nearest_boundary")
            if nb:
                line = (f"Terrain: if the reported position is right, the sensor "
                        f"should read {nb['class_beyond']} {nb['distance_m']:.0f} m "
                        f"to the {compass(nb['bearing_deg'])}.")
                advisory = f"{advisory} {line}" if advisory else line
```

  and `terrain=terrain or {}` in the `Decision(...)` constructor.
- `step()`: the stale path passes `{}` for terrain; the live path passes `epoch.get("terrain") or {}`.

`console/arbiter/explain.py`:
- `FEATURE_PHRASE["terrain_mismatch"] = "the ground under the vehicle does not match the map at the reported position"`.
- After the geometry paragraph, before the advisory:

```python
    # --- terrain (Track E): the most operator-legible sentence available ---
    t = d.terrain or {}
    L = t.get("match_likelihood")
    sensed = (t.get("sensed") or {}).get("class")
    mapped = (t.get("map_at_position") or {}).get("class")
    if L is not None and sensed and mapped and sensed != mapped:
        parts.append(f"The terrain sensor reads {sensed}; the map has {mapped} "
                     f"at the reported position.")
        claims.append(_claim(f"{L:.2f}", L, "terrain.match_likelihood"))
```

- [ ] **Step 4: Run the console tests**

Run: `python -m pytest console/tests -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add console/arbiter/machine.py console/arbiter/explain.py console/tests
git commit -m "console: Track E consumers — terrain passthrough, DEGRADED boundary advisory, explanation phrase with a verifiable claim"
```

---

### Task 7: Map signature (Ed25519, library crypto) and `bootstrap.sh`

**Files:**
- Create: `backend/terrain/signing.py`
- Modify: `bootstrap.sh` (add `"cryptography"` to the pip install line)
- Test: `backend/terrain/test_signing.py`

**Interfaces:**
- Produces: `generate_keypair(priv_path, pub_path)`; `sign_file(path, priv_path) -> Path` (writes `<path>.sig`, 64 raw bytes); `verify_file(path, pub_path, sig_path=None) -> tuple[bool, str]`; `load_verified(path, pub_path) -> RasterMap` (raises `ValueError` with the reason if unverified; sets `signed=True`). Import of `cryptography` happens inside functions so an environment without it fails with a clear message rather than at import.

- [ ] **Step 1: Install the library and write the failing tests**

```bash
source .venv/bin/activate && pip install -q cryptography
```

Edit `bootstrap.sh`: `pip install -q "gnss-lib-py==1.0.4" "georinex==1.16.1" "cryptography"`.

```python
# backend/terrain/test_signing.py
"""The map is a credential-class input (TRACK_E.md): unsigned or tampered
maps are not consulted. Library crypto only — Ed25519 from `cryptography`."""
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("cryptography")

from backend.terrain.rastermap import RasterMap
from backend.terrain.signing import (generate_keypair, load_verified, sign_file,
                                     verify_file)

FIXTURE = Path("fixtures/terrain_fixture.json")


def test_sign_verify_roundtrip_and_tamper(tmp_path):
    priv, pub = tmp_path / "k.pem", tmp_path / "k.pub"
    generate_keypair(priv, pub)
    m = RasterMap.from_fixture(FIXTURE)
    p = m.save_npz(tmp_path / "map.npz")
    sig = sign_file(p, priv)
    assert sig.exists() and sig.stat().st_size == 64
    ok, why = verify_file(p, pub)
    assert ok, why
    loaded = load_verified(p, pub)
    assert loaded.signed is True and loaded.checksum() == m.checksum()
    # tamper one cell, re-save under the same name: signature no longer holds
    m.grid[0, 0] = 6
    m.save_npz(p)
    ok, why = verify_file(p, pub)
    assert not ok and "signature" in why
    with pytest.raises(ValueError):
        load_verified(p, pub)


def test_missing_signature_is_a_named_reason(tmp_path):
    priv, pub = tmp_path / "k.pem", tmp_path / "k.pub"
    generate_keypair(priv, pub)
    p = RasterMap.from_fixture(FIXTURE).save_npz(tmp_path / "map.npz")
    ok, why = verify_file(p, pub)
    assert not ok and "no signature" in why
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest backend/terrain/test_signing.py -q`
Expected: ImportError on `backend.terrain.signing`.

- [ ] **Step 3: Implement**

```python
# backend/terrain/signing.py
"""Ed25519 signature over the map file (TRACK_E.md "The pre-map").

The map is loaded at mission issuance and signed with the same key class
that signs the TESLA anchor: one asymmetric operation, library crypto only
(CLAUDE.md). A map that does not verify is not consulted. On the replay the
same machine generates and verifies the key — a stand-in for the mission
issuer, documented as such in the stream provenance, exactly like the
scripted credential schedule.
"""
from __future__ import annotations

from pathlib import Path

from backend.terrain.rastermap import RasterMap


def _ed25519():
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ed25519
    except ImportError as e:                      # pragma: no cover
        raise ImportError("map signing needs `cryptography` (bash bootstrap.sh)") from e
    return serialization, ed25519


def generate_keypair(priv_path, pub_path) -> tuple[Path, Path]:
    serialization, ed25519 = _ed25519()
    key = ed25519.Ed25519PrivateKey.generate()
    priv_path, pub_path = Path(priv_path), Path(pub_path)
    priv_path.parent.mkdir(parents=True, exist_ok=True)
    priv_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()))
    pub_path.write_bytes(key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    return priv_path, pub_path


def sig_path_for(path) -> Path:
    path = Path(path)
    return path.with_name(path.name + ".sig")


def sign_file(path, priv_path) -> Path:
    serialization, _ = _ed25519()
    key = serialization.load_pem_private_key(Path(priv_path).read_bytes(), password=None)
    sig = key.sign(Path(path).read_bytes())
    out = sig_path_for(path)
    out.write_bytes(sig)
    return out


def verify_file(path, pub_path, sig_path=None) -> tuple[bool, str]:
    serialization, _ = _ed25519()
    from cryptography.exceptions import InvalidSignature
    path = Path(path)
    sig_path = Path(sig_path) if sig_path else sig_path_for(path)
    if not sig_path.exists():
        return False, f"no signature file at {sig_path}"
    if not Path(pub_path).exists():
        return False, f"no public key at {pub_path}"
    pub = serialization.load_pem_public_key(Path(pub_path).read_bytes())
    try:
        pub.verify(sig_path.read_bytes(), path.read_bytes())
    except InvalidSignature:
        return False, "signature does not verify against the map bytes"
    return True, "verified"


def load_verified(path, pub_path, sig_path=None) -> RasterMap:
    ok, why = verify_file(path, pub_path, sig_path)
    if not ok:
        raise ValueError(f"map not consulted: {why}")
    m = RasterMap.load_npz(path)
    m.signed = True
    return m
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest backend/terrain/test_signing.py -q`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/terrain/signing.py backend/terrain/test_signing.py bootstrap.sh
git commit -m "terrain: Ed25519 map signature via cryptography; unsigned maps are not consulted; bootstrap installs cryptography"
```

---

### Task 8: Fetch and rasterise a real pre-map from OpenStreetMap (E1, the risk item)

**Files:**
- Create: `backend/terrain/fetch_map.py`
- Test: `backend/terrain/test_fetch_map.py` (rasteriser only, synthetic elements, no network)

**Interfaces:**
- Produces: `CLASSES = ["grass", "shrub", "tree_cover", "bare", "water", "paved", "building"]`; `classify(tags: dict) -> tuple[str, float|None] | None` returning `(class_name, line_half_width_m_or_None)`; `rasterise(elements: list[dict], origin_lla, radius_m, cell_m) -> RasterMap` where elements are Overpass `out geom` ways (each with `tags` and `geometry: [{lat, lon}]`; relation members flattened by the fetcher); `overpass_query(lat, lon, radius_m) -> str`; `fetch(lat, lon, radius_m, cache_path) -> list[dict]`; CLI `python -m backend.terrain.fetch_map --radius 600 --cell 5 --out data/terrain_usn8.npz --sign`.

- [ ] **Step 1: Write the failing rasteriser tests**

```python
# backend/terrain/test_fetch_map.py
"""Rasteriser acceptance on synthetic Overpass-shaped elements: polygons fill
their cells, lines get their half-width, later classes overwrite earlier
ones, unmapped stays UNKNOWN. No network."""
import numpy as np

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
    assert m.classes[m.class_at(0, -56)] == "grass"          # outside the 3 m half-width
    assert m.class_at(130, 130) == UNKNOWN                    # nothing mapped there
    assert m.grid.shape == (60, 60)
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest backend/terrain/test_fetch_map.py -q`
Expected: ImportError.

- [ ] **Step 3: Implement**

```python
# backend/terrain/fetch_map.py
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

    python -m backend.terrain.fetch_map --radius 600 --cell 5 \
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
ATTRIBUTION = "© OpenStreetMap contributors, ODbL 1.0 (rasterised by backend/terrain/fetch_map.py)"
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
    if nat in ("wood",) or lu == "forest":
        return "tree_cover", None
    if nat == "tree_row":
        return "tree_cover", 2.0
    if nat in ("scrub", "heath"):
        return "shrub", None
    if nat in ("sand", "bare_rock", "scree", "beach", "shingle") or \
            lu in ("construction", "brownfield", "quarry"):
        return "bare", None
    if nat in ("grassland",) or lu in ("grass", "meadow", "recreation_ground", "cemetery",
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
                                 headers={"User-Agent": "ARBITER-terrain-fetch/0.1"})
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
                t = 0.0 if L2 == 0 else np.clip(((pe - a[0]) * ab[0] + (pn - a[1]) * ab[1]) / L2, 0, 1)
                d = np.hypot(pe - (a[0] + t * ab[0]), pn - (a[1] + t * ab[1]))
                sub = np.zeros(box.sum(), dtype=bool)
                sub[d <= hw] = True
                near[box] |= sub
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
    print("  cells by class:", counts)
    print("  antenna class:", m.classes[m.class_at(0, 0)] if m.class_at(0, 0) != UNKNOWN else "UNKNOWN")
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
```

- [ ] **Step 4: Run the rasteriser tests, then the real fetch**

Run: `python -m pytest backend/terrain/test_fetch_map.py -q` — expected 2 passed.
Run: `python -m backend.terrain.fetch_map --sign` — expected: a grid, class counts, the antenna's class, eight boundary distances. **Cut rule (TRACK_E.md):** if the antenna cell is UNKNOWN or every boundary distance is None, stop and report; the raster is the risk.

- [ ] **Step 5: Commit** (the npz, cache and keys live under gitignored `data/`)

```bash
git add backend/terrain/fetch_map.py backend/terrain/test_fetch_map.py
git commit -m "terrain: OSM pre-map fetch + rasteriser (polygons, buffered lines, priority order), signed on write"
```

---

### Task 9: Pipeline wiring — `backend/demo.py` terrain flags, block assembly, provenance rows

**Files:**
- Modify: `backend/demo.py` (`score_stream(..., terrain=None)`, `main()` flags, provenance)
- Modify: `fixtures/epoch.json` (add the `terrain` block, `features.terrain_mismatch`, `geometry.bound_source`, `geometry.residual_bound_m`, `geometry.correction.checks.terrain_consistent`, `score_detail.features_scored`)
- Modify: `docs/stream_provenance.md` (regenerated by demo; the template gains three rows when terrain is on)
- Test: `tests/test_stream.py` (append: fixture round-trips through `console.replay` with zero verifier fires — copy the pattern already in that file or in `console/tests/test_ingest.py`), `backend/terrain/test_pipeline.py` (byte-identity of a sensorless `score_stream` is covered by the existing streams: assert `record()` without terrain has no `terrain`/`bound_source` keys)

**Interfaces:**
- Consumes: `TerrainChannel`, `apply_bound` (Task 4); `CorrectionEmitter(extra_checks=...)` (Task 5); `Weights.equal`, `OPTIONAL_FEATURE_NAMES`, `record(terrain=)` (Task 3); `load_verified` (Task 7).
- Produces: `python -m backend.demo --terrain-map data/terrain_usn8.npz --terrain-pub data/terrain_map_pub.pem --terrain-diag 0.85 --terrain-window 1 --out out/terrain` writes `clean.jsonl`, `carryoff.jsonl`, `demo.jsonl` and `stream_provenance.md` **inside `--out`** (never over the shipped streams or `docs/`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_stream.py` (or wherever the fixture→replay round-trip test lives; search for `fixtures/epoch.json` under `tests/` and `console/tests/`):

```python
def test_fixture_terrain_block_round_trips_with_zero_verifier_fires():
    import json
    from console.arbiter.explain import explain, verify
    from console.arbiter.machine import Arbiter
    ep = json.loads(open("fixtures/epoch.json").read())
    assert ep["terrain"]["available"] is True
    assert ep["geometry"]["bound_source"] in ("residual", "terrain")
    assert "terrain_mismatch" in ep["features"]
    assert ep["geometry"]["correction"]["checks"]["terrain_consistent"] in (True, False, None)
    d = Arbiter().step(ep)
    ok, failures = verify(explain(d), ep)
    assert ok, failures
```

`backend/terrain/test_pipeline.py`:

```python
"""E3 acceptance: a sensorless record is unchanged apart from the additive
score_detail.features_scored; terrain on adds exactly the specified keys."""
from datetime import datetime

from backend.detection import FEATURE_NAMES, Weights, record, score


def test_sensorless_record_has_no_terrain_keys():
    feats = {n: 0.2 for n in FEATURE_NAMES}
    geom = {"information_ratio": 0.9, "displacement_bound_m": 12.0}
    r = record(datetime(2026, 8, 20), feats, score(feats, geom, Weights()), n_sv=9, geometry=geom)
    assert "terrain" not in r
    assert "bound_source" not in r["geometry"] and "residual_bound_m" not in r["geometry"]
    assert set(r["features"]) == set(FEATURE_NAMES)
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_stream.py backend/terrain/test_pipeline.py -q -k "terrain or sensorless"`
Expected: the fixture test fails on the missing `terrain` key; the pipeline test passes already (it guards the byte-identity going forward).

- [ ] **Step 3: Implement**

`backend/demo.py`:

1. Imports: `from backend.detection import OPTIONAL_FEATURE_NAMES` (extend the existing import list) and `from backend.terrain.channel import apply_bound`.

2. `score_stream(..., corrector=None, terrain=None, weights=None)`: replace `fx, w, out = FeatureExtractor(cal), Weights(), []` with `fx, w, out = FeatureExtractor(cal), (weights or Weights()), []`. After `geom = geometry_block(...)` and before the corrector:

```python
        sol = positions.get(ep.time) if positions else None
        solved = sol is not None and sol["believed"] is not None
        t_block = None
        if terrain is not None:
            t_out = terrain.step(dict(sol["believed"].lla) if solved else None,
                                 sol["believed"].dop["H"] if solved else None)
            t_block = t_out["block"]
            if t_out["feature"] is not None:
                feats["terrain_mismatch"] = t_out["feature"]
```

   (move the existing `sol`/`solved` lines up to here). Keep the corrector call as is — its `extra_checks` was bound at construction (step 4 below) and reads `terrain.gate_check`. After the corrector: `geom = apply_bound(geom, t_block)`. Pass `terrain=t_block` to `record(...)`.

3. `main()` flags:

```python
    ap.add_argument("--terrain-map", default=None,
                    help="signed pre-map .npz (TRACK_E.md); off unless given")
    ap.add_argument("--terrain-pub", default="data/terrain_map_pub.pem")
    ap.add_argument("--terrain-diag", type=float, default=None,
                    help="confusion-matrix diagonal of the SIMULATED sensor; required with --terrain-map")
    ap.add_argument("--terrain-window", type=int, default=1)
    ap.add_argument("--terrain-seed", type=int, default=20260820)
```

4. In `main()` after `set_sigma_uere(...)` and after `positions, sanity = solve_positions(...)`:

```python
    terrain = None
    if args.terrain_map:
        if args.terrain_diag is None:
            ap.error("--terrain-diag is required with --terrain-map (no default: swept parameter)")
        from backend.terrain.channel import TerrainChannel
        from backend.terrain.sensor import ConfusionSensor, confusion_from_diag
        from backend.terrain.signing import load_verified
        rmap = load_verified(args.terrain_map, args.terrain_pub)     # raises: not consulted
        sensor = ConfusionSensor(confusion_from_diag(len(rmap.classes), args.terrain_diag),
                                 rmap.classes, seed=args.terrain_seed)
        terrain = TerrainChannel(rmap, sensor, sigma_uere_m=su["sigma_uere_m"],
                                 window_epochs=args.terrain_window)
        print("terrain calibration (clean day, simulated sensor at the antenna)", flush=True)
        t_stats = terrain.calibrate(
            (s["truth"].lla["lat"], s["truth"].lla["lon"], s["truth"].dop["H"])
            for s in positions.values() if s is not None)
        print(f"  {t_stats}")
        if PROVENANCE.parent == Path("docs"):
            pass  # provenance for terrain runs is redirected below
    weights = Weights.equal(FEATURE_NAMES + OPTIONAL_FEATURE_NAMES) if terrain else Weights()
    corrector = CorrectionEmitter(nav, extra_checks=(terrain.gate_check if terrain else None))
```

   (replace the existing `corrector = CorrectionEmitter(nav)` line; import `FEATURE_NAMES` if not already imported). Before each replay add `if terrain: terrain.reset()` next to `xc.reset(); corrector.reset()`, and pass `terrain=terrain, weights=weights` to both `score_stream` calls.

5. Provenance redirection: at the top of `main()` after `out.mkdir`, `prov_path = PROVENANCE if not args.terrain_map else out / "stream_provenance.md"`; pass `prov_path` into `_write_provenance` (add a `path` parameter with default `PROVENANCE`) and, when terrain is on, append these rows to the field table inside `_write_provenance` (add a `terrain_rows: str = ""` parameter and interpolate it after the `geometry.correction` row):

```python
TERRAIN_ROWS = """| `features.terrain_mismatch` | **simulated** / derived | Track E (`backend/terrain/channel.py`): 1 − L/L_max between the SIMULATED sensor posterior (confusion diagonal {diag}, seed {seed}, `backend/terrain/sensor.py`) and the map posterior under the believed position's footprint (sigma = HDOP × sigma_UERE); trailing window {window} epoch(s) ({window_prov}); saturation {sat:.4f} = {sat_prov} |
| `terrain.*` | map: **public** raster; sensor: **simulated** | map `{map_id}` checksum {checksum} ({attribution}), Ed25519-verified against `{pub}` — a stand-in for mission issuance, same status as the scripted credential schedule; `consistent_extent_m` and `nearest_boundary` are derived from the map alone |
| `geometry.displacement_bound_m` (terrain run) | derived | min(residual bound, `terrain.consistent_extent_m`); `geometry.bound_source` names the binding channel and `geometry.residual_bound_m` preserves the pure geometry number |
| `geometry.correction.checks.terrain_consistent` | derived from a **calibrated** floor | L at the corrected fix ≥ {floor:.4f} = {floor_prov} |
"""
```

   Format it in `main()` from `terrain`, `rmap`, `t_stats`, `args` and pass it through. Also add a line under "## Source" of the generated provenance: `terrain channel: SIMULATED sensor — not for the submission video (TRACK_E.md).`

6. Update `fixtures/epoch.json` with the contract extension from TRACK_E.md (values illustrative, consistent: `terrain_mismatch: 0.62`, `bound_source: "residual"`, `residual_bound_m: 41.2`, `terrain_consistent: null`, `features_scored` listing the five, plus the `terrain` block verbatim from the spec with `"sensor": {"id": "sim-confusion-v0", "source": "simulated", "footprint_m": 0.0, "k": 7, "confusion_diag": 0.85, "window_epochs": 1, "window_provenance": "untuned: W=1, no smoothing until the threshold session", "saturation": 0.9412, "saturation_provenance": "p99 of clean-run windowed mismatch, n=2880", "floor": 0.1176, "floor_provenance": "p0.1 of clean-run match likelihood, n=2880"}`).

- [ ] **Step 4: Run the tests, then the real pipeline once**

Run: `python -m pytest tests/test_stream.py backend/terrain console/tests -q` — expected all pass.
Run: `python -m backend.demo --terrain-map data/terrain_usn8.npz --terrain-diag 0.85 --out out/terrain` — expected: calibration stats printed, three streams and a provenance file under `out/terrain/`. Then confirm the shipped streams are untouched: `git status` shows no change under `docs/` from this run, and `python -m backend.demo` (no flags) regenerates `out/*.jsonl` identical to before apart from `score_detail.features_scored` — check with:

```bash
python - <<'PY'
import json
a=[json.loads(l) for l in open('out/clean.jsonl')]
print(len(a), a[0]['score_detail'].get('features_scored'), 'terrain' in a[0], a[0]['geometry'].get('bound_source'))
PY
```

- [ ] **Step 5: Commit**

```bash
git add backend/demo.py fixtures/epoch.json tests/test_stream.py backend/terrain/test_pipeline.py docs/stream_provenance.md
git commit -m "demo: --terrain-map wiring (calibrate on the clean day, feature + block + min bound + gate check), outputs and provenance under --out, fixture extended"
```

---

### Task 10: Validation and plots (E5)

**Files:**
- Create: `backend/terrain/validate.py`
- Test: `backend/terrain/test_validate.py` (the rescoring function on three synthetic records; predicted TTA on the fixture)

**Interfaces:**
- Consumes: streams `out/clean.jsonl`, `out/carryoff.jsonl` (records carry `position`, `_solution.hdop`, `features`, `geometry.information_ratio`, `geometry.displacement_bound_m`, `_solution.displacement_m`, `_attack.stage`); `TerrainChannel`; console `arbitrate` (imported inside the function, precedent `backend/measurement/displacement.py`).
- Produces: `rescore(records, channel, weights) -> list[dict]` (new records with `terrain`, `features.terrain_mismatch`, recomputed `confidence`/`score_detail`, `geometry` via `apply_bound`); `predicted_tta(rmap, walk_mps, epoch_s, window) -> dict[bearing -> (r_T, epochs)]`; `sensor_quality_curve(clean, carryoff, rmap, diags, windows, sigma_uere) -> list[dict]`; CLI `python -m backend.terrain.validate --map data/terrain_usn8.npz --pub data/terrain_map_pub.pem` writing `out/terrain_validation.json` and four plots to `docs/plots/terrain_*.png`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/terrain/test_validate.py
from pathlib import Path

import numpy as np

from backend.detection import FEATURE_NAMES, OPTIONAL_FEATURE_NAMES, Weights
from backend.terrain.channel import TerrainChannel
from backend.terrain.rastermap import RasterMap, m_per_deg
from backend.terrain.sensor import ConfusionSensor
from backend.terrain.validate import predicted_tta, rescore

FIXTURE = Path("fixtures/terrain_fixture.json")
LAT0, LON0 = 38.92, -77.067


def rec(e, n, conf=0.9):
    m_lat, m_lon = m_per_deg(LAT0)
    return {"timestamp": "t", "confidence": conf, "credential_status": "VALID",
            "position": {"lat": LAT0 + n / m_lat, "lon": LON0 + e / m_lon, "alt": 0.0},
            "position_source": "wls_differential",
            "features": {k: 0.0 for k in FEATURE_NAMES},
            "geometry": {"information_ratio": 0.9, "displacement_bound_m": 14.0},
            "satellites_tracked": 10,
            "_solution": {"hdop": 0.8, "displacement_m": float(np.hypot(e, n))},
            "_truth": {"lat": LAT0, "lon": LON0}}


def test_rescore_adds_terrain_and_recomputes_confidence():
    rmap = RasterMap.from_fixture(FIXTURE)
    ch = TerrainChannel(rmap, ConfusionSensor(np.eye(7), rmap.classes), sigma_uere_m=1.0)
    w = Weights.equal(FEATURE_NAMES + OPTIONAL_FEATURE_NAMES)
    out = rescore([rec(0, 0), rec(0, 0), rec(70, 0)], ch, w)
    assert out[0]["features"]["terrain_mismatch"] == 0.0
    assert out[2]["features"]["terrain_mismatch"] == 1.0
    assert out[2]["confidence"] < out[0]["confidence"]
    assert out[0]["geometry"]["bound_source"] in ("residual", "terrain")
    assert out[2]["terrain"]["map_at_position"]["class"] == "building"
    assert out[0]["score_detail"]["features_scored"][-1] == "terrain_mismatch"


def test_predicted_tta_from_the_fixture():
    rmap = RasterMap.from_fixture(FIXTURE)
    t = predicted_tta(rmap, walk_mps=1.0, epoch_s=30.0, window=1)
    assert set(t) == {0, 45, 90, 135, 180, 225, 270, 315}
    assert t[90]["r_T_m"] == 35.0 and t[90]["epochs"] == 35.0 / 30.0 + 1
    assert t[0]["r_T_m"] == 85.0
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest backend/terrain/test_validate.py -q` — expected ImportError.

- [ ] **Step 3: Implement**

```python
# backend/terrain/validate.py
"""E5 — every terrain number as a curve over the sensor model (TRACK_E.md
"Measurement plan"). Rescoring the shipped streams post hoc is exact for the
feature, the block and the composite (all pure functions of position, HDOP,
the map and the sensor draw), so the sweeps never re-run the solver. The
gate check needs hysteresis and is exercised by `backend.demo --terrain-map`.

    python -m backend.terrain.validate --map data/terrain_usn8.npz \
        --pub data/terrain_map_pub.pem

Writes out/terrain_validation.json and docs/plots/terrain_{map,tta_polar,
sensor_quality,carryoff}.png. Every caption says SIMULATED.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from backend.detection import FEATURE_NAMES, OPTIONAL_FEATURE_NAMES, Weights, score
from backend.terrain.channel import TerrainChannel, apply_bound
from backend.terrain.rastermap import UNKNOWN, RasterMap
from backend.terrain.sensor import ConfusionSensor, confusion_from_diag

BEARINGS = tuple(range(0, 360, 45))
PLOTS = Path("docs/plots")


def load_jsonl(path) -> list[dict]:
    with open(path) as fh:
        return [json.loads(l) for l in fh]


def predicted_tta(rmap: RasterMap, walk_mps: float, epoch_s: float, window: int) -> dict:
    out = {}
    for b in BEARINGS:
        r = rmap.boundary_distance(0.0, 0.0, float(b))
        out[b] = {"r_T_m": r, "epochs": None if r is None else r / (walk_mps * epoch_s) + window}
    return out


def rescore(records: list[dict], channel: TerrainChannel, weights: Weights) -> list[dict]:
    channel.reset()
    out = []
    for r in records:
        r = json.loads(json.dumps(r))                       # deep copy, no aliasing
        sol = r.get("_solution") or {}
        solved = r.get("position_source") != "surveyed" and "hdop" in sol
        t = channel.step(r["position"] if solved else None, sol.get("hdop") if solved else None)
        feats = {k: v for k, v in r["features"].items() if k != "by_sv"}
        if t["feature"] is not None:
            feats["terrain_mismatch"] = t["feature"]
        geom = apply_bound(r["geometry"], t["block"])
        s = score(feats, geom, weights)
        r["features"].update({k: round(v, 4) for k, v in feats.items()})
        r["geometry"] = geom
        r["terrain"] = t["block"]
        r["confidence"] = round(s["confidence"], 4)
        r["score_detail"] = {k: s[k] for k in ("feature_score", "geometry_deficit", "beta",
                                               "geometry_available", "weights_tuned", "weights",
                                               "weight_sensitive_fraction", "features_scored")}
        out.append(r)
    return out


def calibrated_channel(rmap, clean, diag, window, sigma_uere, seed=20260820) -> TerrainChannel:
    sensor = ConfusionSensor(confusion_from_diag(len(rmap.classes), diag), rmap.classes, seed=seed)
    ch = TerrainChannel(rmap, sensor, sigma_uere_m=sigma_uere, window_epochs=window)
    ch.calibrate((r["_truth"]["lat"], r["_truth"]["lon"], r["_solution"]["hdop"])
                 for r in clean if "hdop" in (r.get("_solution") or {}))
    return ch


def _arbitrated(records) -> list:
    from console.replay import arbitrate                       # measurement-side import (precedent)
    return arbitrate(records)


def _state_names(decisions) -> list[str]:
    return [d.state.name if hasattr(d, "state") else d["state"] for d in decisions]


def sensor_quality_curve(clean, carry, rmap, diags, windows, sigma_uere) -> list[dict]:
    from console.arbiter.states import THRESHOLDS, TrustState
    nominal = THRESHOLDS[TrustState.NOMINAL]
    w = Weights.equal(FEATURE_NAMES + OPTIONAL_FEATURE_NAMES)
    attack = [i for i, r in enumerate(carry) if (r.get("_attack") or {}).get("stage") in ("CAPTURE", "LOCKED", "WALK")]
    rows = []
    for window in windows:
        for diag in diags:
            ch = calibrated_channel(rmap, clean, diag, window, sigma_uere)
            c = rescore(clean, ch, w)
            k = rescore(carry, ch, w)
            raw_fsr = float(np.mean([r["confidence"] < nominal for r in c]))
            arb_fsr = float(np.mean([s != "NOMINAL" for s in _state_names(_arbitrated(c))]))
            det = float(np.mean([k[i]["confidence"] < nominal for i in attack]))
            first = next((i for i in attack if k[i]["features"].get("terrain_mismatch", 0.0) >= 0.5), None)
            rows.append({"diag": diag, "window": window, "saturation": ch.saturation,
                         "floor": ch.floor, "fsr_raw": raw_fsr, "fsr_arbitrated": arb_fsr,
                         "attack_detection_fraction": det,
                         "terrain_first_fire_epoch": None if first is None else first - attack[0],
                         "bound_source_terrain_fraction": float(np.mean(
                             [r["geometry"].get("bound_source") == "terrain" for r in k]))})
            print(f"  diag {diag:.2f} W {window}: FSR raw {raw_fsr:.4f} arb {arb_fsr:.4f} "
                  f"det {det:.3f} first-fire {rows[-1]['terrain_first_fire_epoch']}", flush=True)
    return rows


def integrity_check(rescored) -> dict:
    """§10-style: empirical |D| <= combined bound at every arbitrated-NOMINAL epoch."""
    states = _state_names(_arbitrated(rescored))
    viol, n = [], 0
    for r, s in zip(rescored, states):
        b = r["geometry"].get("displacement_bound_m")
        d = (r.get("_solution") or {}).get("displacement_m")
        if s == "NOMINAL" and b is not None and d is not None:
            n += 1
            if d > b:
                viol.append((r["timestamp"], d, b))
    return {"n_checked": n, "violations": viol, "ok": not viol and n > 0}


# ------------------------------------------------------------------- plots

def plot_map(rmap: RasterMap, tracks: dict[str, np.ndarray], out: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(7, 7))
    g = np.ma.masked_equal(rmap.grid, UNKNOWN)
    e0, n0 = rmap.origin_enu
    ext = (e0, e0 + rmap.grid.shape[1] * rmap.cell_m, n0, n0 + rmap.grid.shape[0] * rmap.cell_m)
    im = ax.imshow(g, origin="lower", extent=ext, cmap="tab10", vmin=-0.5, vmax=9.5, interpolation="nearest")
    cb = fig.colorbar(im, ax=ax, ticks=range(len(rmap.classes)), shrink=0.7)
    cb.ax.set_yticklabels(rmap.classes)
    for name, xy in tracks.items():
        ax.plot(xy[:, 0], xy[:, 1], lw=1.5, label=name)
    ax.plot(0, 0, "k+", ms=12, mew=2, label="antenna (true)")
    ax.set_xlabel("east, m"); ax.set_ylabel("north, m")
    ax.set_title(f"Pre-map {rmap.map_id}\n(unlabelled = white; map is real, sensor is SIMULATED)")
    ax.legend(loc="lower left", fontsize=8)
    fig.tight_layout(); out.parent.mkdir(parents=True, exist_ok=True); fig.savefig(out, dpi=150); plt.close(fig)


def plot_tta_polar(pred: dict, measured: dict | None, out: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig = plt.figure(figsize=(6, 6)); ax = fig.add_subplot(111, projection="polar")
    ax.set_theta_zero_location("N"); ax.set_theta_direction(-1)
    th = [np.radians(b) for b in pred]; r = [pred[b]["r_T_m"] or np.nan for b in pred]
    ax.plot(th + th[:1], r + r[:1], "o-", label="predicted r_T from the map")
    if measured:
        ax.plot([np.radians(measured["bearing_deg"])], [measured["r_m"]], "r*", ms=14,
                label=f"measured first fire, carry-off ({measured['epochs']} epochs)")
    ax.set_title("Terrain boundary distance by bearing (m) — SIMULATED sensor", pad=18)
    ax.legend(loc="lower right", fontsize=8, bbox_to_anchor=(1.15, -0.1))
    fig.tight_layout(); fig.savefig(out, dpi=150); plt.close(fig)


def plot_sensor_quality(rows: list[dict], out: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 2, figsize=(11, 4))
    for window in sorted({r["window"] for r in rows}):
        sub = sorted([r for r in rows if r["window"] == window], key=lambda r: r["diag"])
        d = [r["diag"] for r in sub]
        axes[0].plot(d, [r["fsr_arbitrated"] for r in sub], "o-", label=f"W={window}")
        axes[1].plot(d, [r["attack_detection_fraction"] for r in sub], "o-", label=f"W={window}")
    axes[0].set_ylabel("false surrender rate (arbitrated)"); axes[1].set_ylabel("attack-window detection fraction")
    for ax in axes:
        ax.set_xlabel("confusion diagonal of the SIMULATED sensor"); ax.grid(alpha=0.3); ax.legend()
    fig.suptitle("Sensor quality sweep — equal untuned weights over five features, beta 0.5")
    fig.tight_layout(); fig.savefig(out, dpi=150); plt.close(fig)


def plot_carryoff(rescored: list[dict], lo: int, hi: int, out: Path, label: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    seg = rescored[lo:hi]
    x = np.arange(len(seg))
    fig, ax1 = plt.subplots(figsize=(11, 4))
    ax1.plot(x, [(r.get("_solution") or {}).get("displacement_m", np.nan) for r in seg], color="tab:red", label="|believed − true| (measured)")
    ax1.plot(x, [r["geometry"].get("residual_bound_m", r["geometry"].get("displacement_bound_m")) or np.nan for r in seg], color="tab:blue", label="residual bound")
    ax1.plot(x, [r["geometry"]["displacement_bound_m"] or np.nan for r in seg], color="tab:green", ls="--", label="combined bound (min)")
    ax1.set_ylabel("metres"); ax1.set_xlabel("epochs from window start"); ax1.grid(alpha=0.3)
    ax2 = ax1.twinx()
    ax2.plot(x, [r["features"].get("terrain_mismatch", np.nan) for r in seg], color="tab:purple", alpha=0.7, label="terrain_mismatch (SIMULATED)")
    ax2.set_ylim(0, 1.05); ax2.set_ylabel("feature [0,1]")
    h1, l1 = ax1.get_legend_handles_labels(); h2, l2 = ax2.get_legend_handles_labels()
    ax1.legend(h1 + h2, l1 + l2, loc="upper left", fontsize=8)
    ax1.set_title(f"Carry-off with the terrain channel — {label}")
    fig.tight_layout(); fig.savefig(out, dpi=150); plt.close(fig)


# ------------------------------------------------------------------- main

def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--map", required=True)
    ap.add_argument("--pub", default="data/terrain_map_pub.pem")
    ap.add_argument("--clean", default="out/clean.jsonl")
    ap.add_argument("--carryoff", default="out/carryoff.jsonl")
    ap.add_argument("--sigma-uere", default="out/sigma_uere.json")
    ap.add_argument("--diags", default="0.6,0.7,0.8,0.9,0.95,0.99")
    ap.add_argument("--windows", default="1,4,8")
    ap.add_argument("--headline-diag", type=float, default=None,
                    help="diag for the timeline/polar plots; defaults to the median of --diags")
    ap.add_argument("--headline-window", type=int, default=1)
    ap.add_argument("--out", default="out/terrain_validation.json")
    args = ap.parse_args(argv)

    from backend.terrain.signing import load_verified
    rmap = load_verified(args.map, args.pub)
    clean, carry = load_jsonl(args.clean), load_jsonl(args.carryoff)
    sigma = json.loads(Path(args.sigma_uere).read_text())["sigma_uere_m"]
    diags = [float(x) for x in args.diags.split(",")]
    windows = [int(x) for x in args.windows.split(",")]
    hd = args.headline_diag if args.headline_diag is not None else float(np.median(diags))

    print("1. map statistics at the antenna")
    pred = predicted_tta(rmap, walk_mps=1.0, epoch_s=30.0, window=args.headline_window)
    extent = rmap.consistent_extent_m(0.0, 0.0)
    print(f"   extent {extent}  nearest {rmap.nearest_boundary(0.0, 0.0)}")
    for b in BEARINGS:
        print(f"   bearing {b:3d}: r_T {pred[b]['r_T_m']}  predicted epochs {pred[b]['epochs']}")

    print("2. sensor-quality sweep (rescoring the shipped streams)")
    rows = sensor_quality_curve(clean, carry, rmap, diags, windows, sigma)

    print(f"3. headline run: diag {hd} W {args.headline_window}")
    ch = calibrated_channel(rmap, clean, hd, args.headline_window, sigma)
    w = Weights.equal(FEATURE_NAMES + OPTIONAL_FEATURE_NAMES)
    k = rescore(carry, ch, w)
    chk = integrity_check(k)
    print(f"   empirical <= combined bound at arbitrated-NOMINAL epochs: "
          f"{chk['n_checked'] - len(chk['violations'])}/{chk['n_checked']} — "
          f"{'PASS' if chk['ok'] else 'FAIL'}")
    attack = [i for i, r in enumerate(k) if (r.get("_attack") or {}).get("stage") in ("CAPTURE", "LOCKED", "WALK")]
    first = next((i for i in attack if k[i]["features"].get("terrain_mismatch", 0.0) >= 0.5), None)
    measured = None
    if first is not None:
        e, n = rmap.enu_from_lla(k[first]["position"]["lat"], k[first]["position"]["lon"])
        measured = {"epochs": first - attack[0], "r_m": float(np.hypot(e, n)),
                    "bearing_deg": float(np.degrees(np.arctan2(e, n)) % 360)}
        print(f"   terrain first fire: epoch {measured['epochs']} of the attack, "
              f"{measured['r_m']:.1f} m at bearing {measured['bearing_deg']:.0f}")
    track = np.array([rmap.enu_from_lla(r["position"]["lat"], r["position"]["lon"]) for r in k[attack[0]:attack[-1] + 1]])

    print("4. plots")
    PLOTS.mkdir(parents=True, exist_ok=True)
    plot_map(rmap, {"believed track under carry-off (measured)": track}, PLOTS / "terrain_map.png")
    plot_tta_polar(pred, measured, PLOTS / "terrain_tta_polar.png")
    plot_sensor_quality(rows, PLOTS / "terrain_sensor_quality.png")
    plot_carryoff(k, max(attack[0] - 30, 0), min(attack[-1] + 60, len(k)),
                  PLOTS / "terrain_carryoff.png", f"SIMULATED sensor diag {hd}, W {args.headline_window}")

    result = {"map": {"id": rmap.map_id, "checksum": rmap.checksum(), "cell_m": rmap.cell_m,
                      "classes": rmap.classes, "extent_at_antenna_m": extent,
                      "nearest_boundary": rmap.nearest_boundary(0.0, 0.0)},
              "predicted_tta": {str(b): v for b, v in pred.items()},
              "sensor_quality": rows, "headline": {"diag": hd, "window": args.headline_window,
                                                    "integrity_check": chk, "measured_first_fire": measured},
              "sigma_uere_m": sigma, "sensor": "SIMULATED (confusion matrix), not for the submission video"}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(result, indent=2, default=str))
    print(f"wrote {args.out} and {PLOTS}/terrain_*.png")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the tests, then the validation**

Run: `python -m pytest backend/terrain/test_validate.py -q` — expected 2 passed.
Run: `python -m backend.terrain.validate --map data/terrain_usn8.npz` — expected: the four plots, the JSON, a PASS/FAIL line for the integrity check. If FAIL, the bound is wrong: stop and investigate before touching the README.

- [ ] **Step 5: Commit**

```bash
git add backend/terrain/validate.py backend/terrain/test_validate.py docs/plots/terrain_*.png
git commit -m "terrain: E5 — validation: predicted TTA by bearing, sensor-quality sweep by rescoring shipped streams, integrity check on the combined bound, four plots (all labelled simulated)"
```

---

### Task 11: Documentation — README roadmap section, TRACK_E resolution, CLAUDE.md pointer

**Files:**
- Modify: `README.md` (new section "Roadmap: terrain channel (Track E) — simulated" after the Track D section; add limitations 14–16 from TRACK_E.md "What goes in the README limitations")
- Modify: `tracks/TRACK_E.md` (append "# TRACK RESOLUTION" with what landed, the OSM substitution for WorldCover, the static-antenna simulation instead of the route frame, and the numbers from `out/terrain_validation.json`)
- Modify: `CLAUDE.md` (Layout: add `backend/terrain/` line; Conventions: "terrain figures are curves over the confusion diagonal, every record stamped simulated")

- [ ] **Step 1: Write the README section** — copy the numbers from `out/terrain_validation.json` (extent at the antenna, nearest boundary, predicted vs measured first-fire epochs, the sensor-quality table at W=1 and the best W, the integrity-check count). Every number appears with "simulated sensor, confusion diagonal d" beside it. Embed the four plots. State the two substitutions plainly: OSM instead of WorldCover (no GeoTIFF reader), sensor simulated at the static antenna (the route is a console presentation frame and never enters the channel).

- [ ] **Step 2: Append the resolution to TRACK_E.md** in the style of TRACK_D.md's resolution block, numbered, with commit refs.

- [ ] **Step 3: Run the full suite**

Run: `python -m pytest -q` — expected all pass (~3 min).

- [ ] **Step 4: Commit**

```bash
git add README.md tracks/TRACK_E.md CLAUDE.md
git commit -m "docs: Track E resolution, README roadmap section for the simulated terrain channel, CLAUDE.md layout pointer"
```

---

## Self-review

**Spec coverage.** Map maths (steps 1, 5, 7, 8) → Task 1. Sensor → Task 2. Feature, calibration, bound rule, gate check (steps 3, 4, 6) → Tasks 3–5. Contract extension and byte-identity → Tasks 3, 9. Consumers (arbiter, explanation) → Task 6. Signed map → Task 7. Real raster and the cut rule → Task 8. Provenance rows → Task 9. Measurement plan items 1–4 → Task 10; item 5 (map-resolution sweep) is covered by re-running Task 8 with `--cell 10 --out data/terrain_usn8_c10.npz` and Task 10 with `--map` pointing at it — stated in Task 11's README section as done or not done. README limitations → Task 11. Web console rendering is not in TRACK_E.md's E1–E5 and is not planned here.

**Type consistency.** `TerrainChannel.step(believed_lla: dict|None, hdop: float|None)` used identically in Tasks 4, 9, 10; `gate_check(corrected_lla)` bound as `extra_checks` in Task 9 matches Task 5's `Callable[[dict|None], dict]`; `apply_bound(geom, block)` returns the same dict object in Tasks 4, 9, 10; `Weights.equal(names)` and `OPTIONAL_FEATURE_NAMES` from Task 3 used in Tasks 9, 10; `record(..., terrain=)` from Task 3 used in Task 9; `load_verified(path, pub)` from Task 7 used in Tasks 9, 10.
