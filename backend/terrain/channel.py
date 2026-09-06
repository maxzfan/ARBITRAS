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

        L = extent = nb = None
        map_dist = None
        if q is not None:
            L, _ = match_likelihood(q, p_s)
            self._window.append(mismatch(q, p_s))
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
            if out["feature"] is not None:
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
