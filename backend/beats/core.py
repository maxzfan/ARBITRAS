"""Shared, deterministic stream builder for the demo beats.

One function, `build`, produces everything a beat renders: per-epoch §5
records plus the believed and true positions at each epoch. Deterministic by
construction and asserted so by `backend/beats/test_determinism` --
same input, same bytes, every run.

Determinism comes from four places, all of which had to be pinned:

- the injector's RNG is seeded from `Spoof.seed`;
- the position solve is a fixed-iteration least squares with no random start;
- every calibration is fitted on the clean day and cached under a key that
  FINGERPRINTS the observables, so a masked stream can never pick up an
  unmasked stream's calibration (that defect cost a set of measurements once);
- the causal feature baselines are warmed over the same pre-onset epochs in
  every run, so the first scored epoch never depends on where the slice began.

`truth` is the position solved from the CLEAN observables at the same epoch,
not the surveyed coordinate. That is the honest comparison: it cancels the
solver's own metre-scale bias, so the separation on screen is exactly the
position effect of what the injector did and nothing else.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np

from ..detection import (CrossConstellation, FeatureExtractor, Weights, fit,
                         fit_cross, flagged_sv, record, score)
from ..detection.emit import USN8_ECEF, ecef_to_lla
from ..detection import RULED_K3
from ..injector import CARRY_OFF, enu_basis, inject
from ..rinex import ephemeris, noise
from ..rinex.loader import load_obs
from ..rinex.solve import (EL_MASK_DEG, masked_epoch, residual_panel,
                           solve_per_constellation)
from . import config as cfg


@dataclass
class Beat:
    """One built beat: records, tracks and the settings that produced them."""
    records: list                  # §5 contract objects, layer ON
    times: list
    believed_enu: np.ndarray       # (n, 3) metres from truth, E/N/U
    displacement_m: np.ndarray     # (n,) horizontal separation
    confidence: np.ndarray
    truth_lla: list
    believed_lla: list
    onset_index: int
    provenance: str


# Keyed on the observation path only: `nav` is derived from a fixed file and
# every other input is a constant, so the path identifies the result. Cached
# because re-fitting the 2,880-epoch calibration once per beat dominated the
# runtime of the beat suite (8m48s -> seconds).
_CLEAN_CACHE: dict = {}


def _clean_day(obs: str, nav):
    if obs not in _CLEAN_CACHE:
        _CLEAN_CACHE[obs] = _clean_day_uncached(obs, nav)
    return _CLEAN_CACHE[obs]


def _clean_day_uncached(obs: str, nav):
    day = [masked_epoch(e, nav) for e in load_obs(obs, systems="GERCS")]
    floor = noise.measure(load_obs(obs, systems="GERCS"))
    cal = fit(day, floor, resid_panel=residual_panel(day, nav))
    xcal = fit_cross(day, nav)
    return day, floor, cal, xcal


def build(obs: str = cfg.OBS, window=cfg.WINDOW, onset: datetime = cfg.ONSET,
          layer_on: bool = True, credential_for=None,
          geometry: bool = True) -> Beat:
    """Build one beat's streams. `layer_on=False` still emits records, but with
    confidence pinned at 1.0 and no geometry -- the trust layer switched off is
    a vehicle that computes a position and asks no questions of it."""
    nav = ephemeris.load_nav()
    from ..geometry.engine import geometry_for as track_c_geometry_for
    from ..geometry.engine import set_el_mask_deg
    set_el_mask_deg(EL_MASK_DEG)

    day, floor, cal, xcal = _clean_day(obs, nav)

    spoof = CARRY_OFF(onset=onset,
                      carrier_rate_error=cfg.CARRIER_RATE_ERROR,
                      bearing_deg=cfg.BEARING_DEG,
                      transients=cfg.TRANSIENTS)
    injected_all, truth_log = inject(day, spoof, floor, nav=nav)
    injected_all = [masked_epoch(e, nav) for e in injected_all]

    w0, w1 = window
    keep = [i for i, e in enumerate(day) if w0 <= e.time < w1]
    warm = keep[0]

    fx = FeatureExtractor(cal)
    xc = CrossConstellation(xcal, nav)
    east, north, up = enu_basis(np.array(USN8_ECEF))

    recs, times, enu, conf, t_lla, b_lla = [], [], [], [], [], []
    for i in keep:
        ep_c, ep_i = day[i], injected_all[i]
        sol_c = solve_per_constellation(ep_c, nav).get("all")
        sols_i = solve_per_constellation(ep_i, nav)
        sol_i = sols_i.get("all")

        feats = fx.step(ep_i, resid=(sol_i or {}).get("resid_m"))
        f = dict(feats["features"])
        f["cross_constellation"] = xc.score(ep_i, sols_i)["value"]

        if layer_on:
            excluded = flagged_sv(feats["per_sv"], cal.z_sat, RULED_K3)
            geom = (track_c_geometry_for(ep_i, excluded_sv=excluded)
                    if geometry else None)
            scored = score(f, geom, Weights(), mode=cfg.COMBINE_MODE)
        else:
            # Layer OFF: no features are consulted and nothing is scored. The
            # record still carries them so the two runs are comparable epoch
            # for epoch, but confidence is 1.0 -- the vehicle is certain.
            geom = None
            scored = {"confidence": 1.0, "feature_score": 0.0,
                      "geometry_deficit": 0.0, "geometry_available": False,
                      "beta": 1.0, "weights_tuned": False,
                      "weights": dict(Weights().feature),
                      "weight_sensitive_fraction": 0.0,
                      "combine_mode": "layer_off"}

        cred = credential_for(ep_i, len(recs)) if credential_for else "VALID"
        pos = ecef_to_lla(*sol_i["pos"]) if sol_i else None
        recs.append(record(ep_i.time, f, scored, n_sv=ep_i.n_sv,
                           geometry=geom, credential_status=cred,
                           position=pos))
        times.append(ep_i.time)
        conf.append(scored["confidence"])
        if sol_c is not None and sol_i is not None:
            d = sol_i["pos"] - sol_c["pos"]
            enu.append([float(d @ east), float(d @ north), float(d @ up)])
            t_lla.append(ecef_to_lla(*sol_c["pos"]))
            b_lla.append(ecef_to_lla(*sol_i["pos"]))
        else:
            enu.append([np.nan] * 3)
            t_lla.append(None)
            b_lla.append(None)

    enu = np.asarray(enu, dtype=float)
    onset_index = int(np.searchsorted([t for t in times], onset))
    attacked = w0 <= onset < w1
    attack = (f"carry-off position walk bearing {cfg.BEARING_DEG:g} deg, "
              f"rate {spoof.walk_off_mps:g} m/s, carrier_rate_error "
              f"{cfg.CARRIER_RATE_ERROR:g} m/s, transients "
              f"{'ON' if cfg.TRANSIENTS else 'OFF'}, onset {onset:%H:%M}"
              if attacked else
              "NO ATTACK — clean observables throughout")
    prov = (f"USN8 {w0:%Y-%m-%d %H:%M}-{w1:%H:%M} | {attack} | "
            f"mask {EL_MASK_DEG:g} deg, combine {cfg.COMBINE_MODE}, "
            f"layer {'ON' if layer_on else 'OFF'}")
    return Beat(records=recs, times=times, believed_enu=enu,
                displacement_m=np.linalg.norm(enu[:, :2], axis=1),
                confidence=np.asarray(conf, dtype=float),
                truth_lla=t_lla, believed_lla=b_lla,
                onset_index=onset_index, provenance=prov)


def write_jsonl(records, path):
    import json
    from pathlib import Path
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w") as fh:
        for r in records:
            fh.write(json.dumps(r, sort_keys=True) + "\n")
    return p
