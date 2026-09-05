"""Apply a Spoof to a clean epoch stream.

    floor = noise.measure(clean)
    injected, truth = inject(clean, CARRY_OFF(onset=t0), floor)

`injected` is a new epoch list — the clean one is never mutated, because the
same clean stream is replayed for the false-surrender rate (§10) in the same
process. `truth` is a per-epoch record of what the attacker actually did, which
is what the measurement layer compares the believed position against; the
detector never sees it.

What gets modified, per spoofed satellite per band:

    cn0     += power_db                     persistent step (see spoof.py)
    cn0     += capture jitter               CAPTURE stage only
    code    += range offset                 the walk-off itself
    phase   += range offset - divergence    the spoofer's carrier does not
                                            perfectly match its own code

Divergence is linear: `carrier_rate_error * t` metres at t seconds after
lift-off (ruling of 2026-09-05). The spoofer commands a code delay and applies
carrier Doppler from its own range-rate model; a mismatch between those two
models diverges linearly — not a random walk, not IID noise inflation. At
`carrier_rate_error = 0.0` the spoofer is fully carrier-coherent: code and
carrier agree through the entire walk-off, no lift-off CMC transient fires,
and features 2 and 3 see nothing by construction.
"""
from __future__ import annotations

import copy
from datetime import datetime

import numpy as np
import pandas as pd

from ..rinex.loader import Epoch
from ..rinex.noise import NoiseFloor
from .spoof import (CLEAN, CAPTURE, LOCKED, WALK, Spoof, bearing_unit_ecef)

C_LIGHT = 299_792_458.0


def epoch_interval_s(epochs) -> float:
    """Median sampling interval of a replay, in seconds."""
    t = pd.Series([ep.time for ep in epochs])
    return float(t.diff().dt.total_seconds().median())


def inject(epochs, spoof: Spoof, floor: NoiseFloor, bands=(1, 2),
           nav=None, sta_ecef=None):
    """Return (injected_epochs, truth_frame).

    `nav` is required for position-domain scenarios (line-of-sight vectors);
    it is loaded on demand if not supplied. `sta_ecef` is the true receiver
    position the displacement is commanded from — the injector knows truth,
    the detector never does."""
    rng = np.random.default_rng(spoof.seed)
    position_mode = spoof.walk_mode == "position"
    if position_mode:
        from ..detection.emit import USN8_ECEF
        from ..rinex import ephemeris
        sta = np.asarray(sta_ecef if sta_ecef is not None else USN8_ECEF,
                         dtype=float)
        if nav is None:
            nav = ephemeris.load_nav()
        if spoof.bearing_deg is None:
            raise ValueError("position-domain walk needs a bearing_deg")
        v_hat = bearing_unit_ecef(spoof.bearing_deg, sta)

    out, rows, liftoff_done = [], [], False
    frozen = None                    # target set, resolved once at capture
    for ep in epochs:
        stage, since = spoof.stage(ep.time)
        df = ep.df.copy(deep=True)
        if stage == CLEAN:
            mask = np.zeros(len(df), dtype=bool)
        elif spoof.target is not None:
            if frozen is None:
                frozen = spoof.resolve_target(ep)
            mask = df.index.isin(frozen)
        else:
            mask = spoof.select(df)
        n = int(mask.sum())

        offset = spoof.range_offset_m(ep.time)
        walked_s = spoof.walked_s(ep.time)
        divergence = spoof.carrier_rate_error * walked_s

        # Position domain: the uniform part is the common bias only; the walk
        # itself is a per-satellite projection of the commanded displacement.
        per_sv_offset = None
        commanded_m = 0.0
        if position_mode and n:
            commanded_m = spoof.displacement_m(ep.time)
            dp = commanded_m * v_hat
            svs = list(df.index[mask])
            pr = df.loc[svs, "code_1"]
            sat = ephemeris.positions_at(
                ep.time, svs, nav,
                tx_delay_s={sv: pr[sv] / C_LIGHT for sv in svs
                            if np.isfinite(pr[sv])})
            los = sat[["x", "y", "z"]].to_numpy() - sta
            los /= np.linalg.norm(los, axis=1, keepdims=True)
            # A receiver truly displaced by dp sees its range change by
            # -e_sv . dp; injecting that is what walks the solution.
            per_sv_offset = pd.Series(offset - los @ dp, index=sat.index)
            # Satellites without usable ephemeris keep the common bias alone.
            per_sv_offset = per_sv_offset.reindex(svs).fillna(offset)

        if n:
            if (stage == WALK and not liftoff_done and spoof.transients
                    and spoof.carrier_rate_error > 0):
                # §7 stage 3: distortion spikes briefly again at lift-off. A
                # fully coherent spoofer (rate 0) produces no code/carrier
                # distortion, so no transient either.
                divergence += spoof.liftoff_transient_sigma * floor.cmc_sigma
                liftoff_done = True

            jitter = (rng.normal(0.0,
                                 spoof.capture_jitter_sigma * floor.cn0_sigma,
                                 n)
                      if stage == CAPTURE and spoof.transients else 0.0)

            off = offset if per_sv_offset is None else per_sv_offset.to_numpy()
            for b in bands:
                cn0, code, ph_m, lam = (f"cn0_{b}", f"code_{b}",
                                        f"phase_m_{b}", f"lam_{b}")
                df.loc[mask, cn0] = df.loc[mask, cn0] + spoof.power_db + jitter
                df.loc[mask, code] = df.loc[mask, code] + off
                df.loc[mask, ph_m] = df.loc[mask, ph_m] + off - divergence
                df.loc[mask, f"phase_{b}"] = df.loc[mask, ph_m] / df.loc[mask, lam]

        out.append(Epoch(time=ep.time, df=df))
        rows.append({
            "time": ep.time, "stage": stage, "scenario": spoof.name,
            "walk_mode": spoof.walk_mode, "transients": spoof.transients,
            "n_spoofed": n,
            "range_offset_m": offset if n else 0.0,
            # Commanded displacement. What the attack ASKED for; the achieved
            # figure comes from the solved position and is measured, never
            # assumed equal to this (see backend/measurement).
            "commanded_displacement_m": commanded_m,
            "bearing_deg": (spoof.bearing_deg
                            if position_mode and n else float("nan")),
            "power_db": spoof.power_db if n else 0.0,
            "cmc_divergence_m": divergence if n else 0.0,
            "spoofed_sv": ",".join(df.index[mask]) if n else "",
        })

    return out, pd.DataFrame(rows).set_index("time")


def summarise(truth: pd.DataFrame) -> str:
    """One-line description of what an injected replay contained."""
    active = truth[truth["stage"] != CLEAN]
    if active.empty:
        return "no attack epochs in this replay"
    stages = active["stage"].value_counts().to_dict()
    mode = active["walk_mode"].iloc[0]
    walk = (f"max commanded displacement "
            f"{active['commanded_displacement_m'].max():.1f} m "
            f"on bearing {active['bearing_deg'].iloc[-1]:.0f} deg"
            if mode == "position" else
            f"max uniform range offset {active['range_offset_m'].max():.1f} m")
    return (f"{active['scenario'].iloc[0]} ({mode} domain, transients "
            f"{'ON' if active['transients'].iloc[0] else 'OFF'}): "
            f"{len(active)} attack epochs {stages}, "
            f"{active['n_spoofed'].max()} SV at peak, {walk}, "
            f"max code-carrier divergence "
            f"{active['cmc_divergence_m'].max():.2f} m")
