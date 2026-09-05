"""RINEX observation file -> one flat dataframe per epoch.

The per-epoch frame is the only thing tracks B and C consume from track A's
front end (TRACK_A.md §1). Everything system-specific — which observable code
carries C/N0, which frequency the carrier is on — is resolved here, so no
downstream module ever sees a RINEX code.

    from backend.rinex.loader import load_obs, epochs

    obs = load_obs("data/USN800USA_R_20262320000_01D_30S_MO.crx.gz", systems="G")
    for ep in epochs(obs):
        ep.time          # datetime, GPS time
        ep.df            # DataFrame indexed by sv: G03, G04, ...

Frame columns, all float except `system`:

    system            constellation letter
    code_1  code_2    pseudorange, metres
    phase_1 phase_2   carrier phase, cycles
    cn0_1   cn0_2     carrier-to-noise density, dB-Hz
    lam_1   lam_2     carrier wavelength, metres
    phase_m_1/2       carrier phase in metres (phase * lambda)

Band 1 is the L1-class signal, band 2 the second civil/legacy band; the
per-system mapping is in bands.py.
"""
from __future__ import annotations

import hashlib
import pickle
import warnings
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from . import bands

CACHE = Path(".cache/rinex")


@dataclass
class Epoch:
    """One observation epoch: a timestamp and the satellites visible at it."""
    time: datetime
    df: pd.DataFrame

    @property
    def n_sv(self) -> int:
        return len(self.df)

    def system(self, sysc: str) -> pd.DataFrame:
        return self.df[self.df["system"] == sysc]


def _cache_key(path: Path, systems: str, tlim) -> Path:
    raw = f"{path.resolve()}|{path.stat().st_mtime_ns}|{systems}|{tlim}"
    return CACHE / (hashlib.sha1(raw.encode()).hexdigest()[:16] + ".pkl")


def load_obs(path, systems: str = "GERCS", tlim=None, use_cache: bool = True):
    """Load a RINEX 3 observation file into a list of per-epoch frames.

    `systems` is a string of constellation letters, e.g. "G" or "GERC".
    `tlim` is an optional (start, end) datetime pair passed to georinex.
    Parsing a full 2,880-epoch day takes minutes, so the result is pickled
    under .cache/rinex and reused.
    """
    path = Path(path)
    systems = "".join(dict.fromkeys(systems))
    key = _cache_key(path, systems, tlim)
    if use_cache and key.exists():
        with key.open("rb") as fh:
            return pickle.load(fh)

    import georinex as gr

    with warnings.catch_warnings():
        # xarray emits one FutureWarning per epoch from inside georinex.
        warnings.simplefilter("ignore", FutureWarning)
        ds = gr.load(str(path), use=set(systems), tlim=tlim)

    eps = _to_epochs(ds, systems)
    if use_cache:
        CACHE.mkdir(parents=True, exist_ok=True)
        with key.open("wb") as fh:
            pickle.dump(eps, fh, protocol=pickle.HIGHEST_PROTOCOL)
    return eps


def _column(ds, code: str, sv_index) -> np.ndarray:
    """Pull one observable code as a (time, sv) array, NaN where absent."""
    if code not in ds:
        return np.full((ds.sizes["time"], len(sv_index)), np.nan)
    return np.asarray(ds[code].values, dtype=float)


def _to_epochs(ds, systems: str) -> list[Epoch]:
    sv = np.asarray(ds["sv"].values, dtype=str)
    sys_of = np.array([s[0] for s in sv])
    times = pd.to_datetime(ds["time"].values).to_pydatetime()

    # Assemble one (time, sv) plane per canonical slot, filling each satellite's
    # column from the observable code its own constellation uses.
    slots = ("code", "phase", "cn0")
    plane = {f"{s}_{b}": np.full((len(times), len(sv)), np.nan)
             for s in slots for b in (1, 2)}
    lam = {f"lam_{b}": np.full(len(sv), np.nan) for b in (1, 2)}

    for sysc in systems:
        mask = sys_of == sysc
        if not mask.any():
            continue
        for b, codes in enumerate(bands.CODES[sysc], start=1):
            for slot, code in zip(slots, codes):
                plane[f"{slot}_{b}"][:, mask] = _column(ds, code, sv)[:, mask]
        w1, w2 = bands.wavelengths(sysc)
        lam["lam_1"][mask], lam["lam_2"][mask] = w1, w2

    out = []
    for i, t in enumerate(times):
        cols = {"system": sys_of}
        for name, arr in plane.items():
            cols[name] = arr[i]
        cols.update(lam)
        df = pd.DataFrame(cols, index=pd.Index(sv, name="sv"))
        df["phase_m_1"] = df["phase_1"] * df["lam_1"]
        df["phase_m_2"] = df["phase_2"] * df["lam_2"]
        # A satellite with no code and no C/N0 on band 1 is not being tracked.
        df = df[df["code_1"].notna() | df["cn0_1"].notna()]
        out.append(Epoch(time=t, df=df))
    return out


def epochs(eps):
    """Iterate epochs. Accepts either a loaded list or a path."""
    if isinstance(eps, (str, Path)):
        eps = load_obs(eps)
    yield from eps
