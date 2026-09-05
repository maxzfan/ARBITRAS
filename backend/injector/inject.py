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

The divergence rate is one measured clean code-minus-carrier sigma per epoch
per unit of `carrier_mismatch_sigma`. Expressing it against the floor rather
than in metres per second means the injected attack is always a stated number
of sigma out of the noise, on this station, on this day — the same convention
§4 uses for C/N0 ("a 1-3 dB spoofer is 2-6 sigma out").
"""
from __future__ import annotations

import copy
from datetime import datetime

import numpy as np
import pandas as pd

from ..rinex.loader import Epoch
from ..rinex.noise import NoiseFloor
from .spoof import CLEAN, CAPTURE, LOCKED, WALK, Spoof


def epoch_interval_s(epochs) -> float:
    """Median sampling interval of a replay, in seconds."""
    t = pd.Series([ep.time for ep in epochs])
    return float(t.diff().dt.total_seconds().median())


def inject(epochs, spoof: Spoof, floor: NoiseFloor, bands=(1, 2)):
    """Return (injected_epochs, truth_frame)."""
    dt_s = epoch_interval_s(epochs)
    rng = np.random.default_rng(spoof.seed)

    # Code/carrier divergence rate: `carrier_mismatch_sigma` sigma of clean
    # code-minus-carrier noise accumulated per epoch of walk-off.
    diverge_mps = spoof.carrier_mismatch_sigma * floor.cmc_sigma / dt_s

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
        walked_s = max(0.0, since - spoof.capture_s - spoof.liftoff_delay_s)
        divergence = diverge_mps * walked_s

        if n:
            if stage == WALK and not liftoff_done:
                # §7 stage 3: distortion spikes briefly again at lift-off.
                divergence += spoof.liftoff_transient_sigma * floor.cmc_sigma
                liftoff_done = True

            jitter = (rng.normal(0.0, spoof.capture_jitter_sigma * floor.cn0_sigma, n)
                      if stage == CAPTURE else 0.0)

            for b in bands:
                cn0, code, ph_m, lam = (f"cn0_{b}", f"code_{b}",
                                        f"phase_m_{b}", f"lam_{b}")
                df.loc[mask, cn0] = df.loc[mask, cn0] + spoof.power_db + jitter
                df.loc[mask, code] = df.loc[mask, code] + offset
                df.loc[mask, ph_m] = df.loc[mask, ph_m] + offset - divergence
                df.loc[mask, f"phase_{b}"] = df.loc[mask, ph_m] / df.loc[mask, lam]

        out.append(Epoch(time=ep.time, df=df))
        rows.append({
            "time": ep.time, "stage": stage, "scenario": spoof.name,
            "n_spoofed": n, "range_offset_m": offset if n else 0.0,
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
    return (f"{active['scenario'].iloc[0]}: {len(active)} attack epochs "
            f"{stages}, {active['n_spoofed'].max()} SV at peak, "
            f"max range offset {active['range_offset_m'].max():.1f} m, "
            f"max code-carrier divergence {active['cmc_divergence_m'].max():.2f} m")
