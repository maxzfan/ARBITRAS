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
        new_names: list[str] = []
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
