"""Weighted least-squares single-point position from pseudoranges.

    from backend.geometry.solve import NavTables, solve_epoch, differential

    nav = NavTables.load()                       # broadcast Kepler + clock, G+E
    fix = solve_epoch(epoch, nav)                # -> Fix, or None if unsolvable
    d   = differential(clean_epoch, injected_epoch, nav)

This is the receiver's own navigation solution -- "what the receiver believes"
(design.md §5). It uses every tracked GPS/Galileo satellite regardless of what
the trust layer thinks of it, because the trust layer sits on top of this, not
inside it.

## Model

    P_i + c*dt_sv_i = |R(tau_i) s_i - x| + b_{sys(i)}

- `s_i` from Track A's validated Kepler propagator (`backend/rinex/ephemeris`)
  at the signal TRANSMIT time t_rx - P_i/c - dt_sv_i (a 70 ms flight moves a
  satellite ~270 m; ignoring it is a hundreds-of-metres error).
- `R(tau)` is the Sagnac rotation of the satellite position through the earth's
  rotation during flight (~30 m at 20 000 km; applied every iteration).
- `dt_sv` is the broadcast clock polynomial plus the relativistic eccentricity
  term, minus the band-1 group delay (GPS TGD; Galileo BGD chosen by the
  record's DataSrc clock reference). Ionosphere and troposphere are NOT
  modelled -- see "differential" below for why that is acceptable here.
- One clock column per constellation present, design.md §6b: H is n x (3+k),
  and a constellation with no satellites contributes no column, so the matrix
  is never rank-deficient by construction.
- Weights 1/sigma^2 with sigma = sigma0 / sin(elevation), elevation from the
  a-priori position; mask below 10 degrees.

## Differential displacement

`differential()` solves the SAME epoch twice -- once from the clean
pseudoranges, once from the injected ones -- with the same satellite set and
the same weights, and reports the difference. Ionosphere, troposphere,
ephemeris error and every other unmodelled bias is common to both solutions
and cancels in the difference; what remains is the position effect of exactly
what the injector changed. `_truth` in the demo stream is the clean solution
and `position` the injected one, so the displacement on screen is the honest
one, and on clean epochs it is zero to numerical precision.

A consequence worth knowing before reading numbers: a range offset applied
UNIFORMLY to every tracked satellite of one constellation is indistinguishable
from a receiver-clock shift and is absorbed entirely by that constellation's
clock column -- the position does not move. Only a spoofed SUBSET (or a
satellite that rises into an already-captured constellation, unspoofed) moves
the fix. That is physics, not a solver limitation, and the solver reports it
rather than hiding it.

Scope: G+E. GLONASS broadcasts state vectors, not Kepler elements; BeiDou's
propagation on this branch still carries the BDT-offset bug fixed on main.
Both are excluded explicitly rather than propagated wrongly.
"""
from __future__ import annotations

import pickle
import warnings
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from backend.detection.emit import USN8_ECEF, ecef_to_lla
from backend.rinex.ephemeris import (GM_OMEGA, KEPLER_FIELDS, MAX_EPH_AGE_S,
                                     TOE_OFFSET_S, _ensure_filtered,
                                     _kepler_ecef, seconds_of_week)

C_LIGHT = 299_792_458.0
OMEGA_E = 7.2921151467e-5           # rad/s, WGS-84 earth rotation
F_REL = -4.442807633e-10            # s/sqrt(m), IS-GPS-200 relativistic constant
SYSTEMS = "GE"
CACHE = Path(".cache/rinex/nav_clock_GE.pkl")

CLOCK_FIELDS = ("SVclockBias", "SVclockDrift", "SVclockDriftRate",
                "TGD", "BGDe5a", "BGDe5b", "DataSrc")


# --------------------------------------------------------------------------- frames

def enu_basis(lat_deg: float, lon_deg: float) -> np.ndarray:
    """Rows are the unit East, North, Up vectors in ECEF at (lat, lon)."""
    la, lo = np.radians(lat_deg), np.radians(lon_deg)
    return np.array([
        [-np.sin(lo), np.cos(lo), 0.0],
        [-np.sin(la) * np.cos(lo), -np.sin(la) * np.sin(lo), np.cos(la)],
        [np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)],
    ])


def enu_to_ecef(origin_ecef, e: float, n: float, u: float) -> np.ndarray:
    lla = ecef_to_lla(*origin_ecef)
    return np.asarray(origin_ecef, float) + enu_basis(lla["lat"], lla["lon"]).T @ [e, n, u]


def sagnac(sat_ecef: np.ndarray, rx_ecef: np.ndarray) -> np.ndarray:
    """Satellite position rotated through the earth's rotation during flight."""
    tau = np.linalg.norm(sat_ecef - rx_ecef) / C_LIGHT
    th = OMEGA_E * tau
    c, s = np.cos(th), np.sin(th)
    x, y, z = sat_ecef
    return np.array([c * x + s * y, -s * x + c * y, z])


def geometric_range(sat_ecef, rx_ecef) -> float:
    """|R(tau) s - x| -- the model range, Sagnac included. Tests use this too."""
    return float(np.linalg.norm(sagnac(np.asarray(sat_ecef, float),
                                       np.asarray(rx_ecef, float)) - rx_ecef))


# --------------------------------------------------------------------------- nav

class NavTables:
    """Broadcast Kepler elements AND clock terms per (sv, record time), G+E.

    Track A's `load_nav` cache carries only the 16 Kepler fields; the clock
    polynomial and group delays are read here from the same filtered file, so
    one broadcast record supplies both the orbit and the clock it belongs to.
    Galileo records are the base-PRN ones (E02, not E02_1): the same set Track
    A's `positions_at` looks up, so orbits agree with `geometry.sky`.
    """

    def __init__(self, table: pd.DataFrame):
        self._by_sv = {}
        for sv, g in table.groupby(level=0):
            g = g.droplevel(0).sort_index()
            secs = (g.index.values.astype("datetime64[s]").astype(np.int64))
            self._by_sv[sv] = (secs, g)

    @classmethod
    def load(cls, systems: str = SYSTEMS, cache: Path = CACHE) -> "NavTables":
        if cache.exists():
            with cache.open("rb") as fh:
                return cls(pickle.load(fh))
        import georinex as gr
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            ds = gr.load(str(_ensure_filtered()), use=set(systems))
        rows = []
        times = pd.to_datetime(ds["time"].values)
        for sv in np.asarray(ds["sv"].values, dtype=str):
            if sv[0] not in systems or "_" in sv:       # base PRN records only
                continue
            sub = ds.sel(sv=sv)
            for i, t in enumerate(times):
                rec = {f: float(sub[f].values[i]) for f in KEPLER_FIELDS if f in sub}
                if len(rec) < 16 or any(not np.isfinite(v) for v in rec.values()):
                    continue
                for f in CLOCK_FIELDS:
                    rec[f] = float(sub[f].values[i]) if f in sub else np.nan
                if not np.isfinite(rec["SVclockBias"]):
                    continue
                rec["sv"], rec["t"] = sv, t.to_pydatetime()
                rows.append(rec)
        table = pd.DataFrame(rows).set_index(["sv", "t"]).sort_index()
        cache.parent.mkdir(parents=True, exist_ok=True)
        with cache.open("wb") as fh:
            pickle.dump(table, fh, protocol=pickle.HIGHEST_PROTOCOL)
        return cls(table)

    def record(self, sv: str, t: datetime):
        """Nearest broadcast record within MAX_EPH_AGE_S, else None."""
        if sv not in self._by_sv:
            return None
        secs, g = self._by_sv[sv]
        ts = int(np.datetime64(t.replace(tzinfo=None), "s").astype(np.int64))
        i = int(np.argmin(np.abs(secs - ts)))
        if abs(secs[i] - ts) > MAX_EPH_AGE_S:
            return None
        return g.iloc[i], datetime.utcfromtimestamp(int(secs[i]))

    def satellite(self, sv: str, t_rx: datetime, pseudorange_m: float):
        """(ecef at transmit time, dt_sv seconds) for one satellite, or None.

        dt_sv = af0 + af1 dt + af2 dt^2 + relativistic - band-1 group delay.
        """
        hit = self.record(sv, t_rx)
        if hit is None or not np.isfinite(pseudorange_m):
            return None
        eph, toc = hit
        sysc = sv[0]
        t_tx = t_rx - timedelta(seconds=pseudorange_m / C_LIGHT)
        dt = (t_tx - toc).total_seconds()
        dt_sv = (eph["SVclockBias"] + eph["SVclockDrift"] * dt
                 + eph["SVclockDriftRate"] * dt * dt)
        dt_sv += F_REL * eph["Eccentricity"] * eph["sqrtA"] * np.sin(
            _ecc_anomaly(eph, t_tx, sysc))
        dt_sv -= _group_delay_s(eph, sysc)
        t_tx -= timedelta(seconds=float(dt_sv))
        return _kepler_ecef(eph, t_tx, sysc), float(dt_sv)


def _ecc_anomaly(eph, t: datetime, system: str) -> float:
    mu, _ = GM_OMEGA[system]
    a = eph["sqrtA"] ** 2
    e = eph["Eccentricity"]
    n = np.sqrt(mu / a ** 3) + eph["DeltaN"]
    tk = seconds_of_week(t) - TOE_OFFSET_S.get(system, 0.0) - eph["Toe"]
    if tk > 302400:
        tk -= 604800
    elif tk < -302400:
        tk += 604800
    m = eph["M0"] + n * tk
    ecc = m
    for _ in range(12):
        ecc = m + e * np.sin(ecc)
    return float(ecc)


def _group_delay_s(eph, system: str) -> float:
    """Band-1 single-frequency group delay for the clock reference in use."""
    if system == "G":
        v = eph.get("TGD", np.nan)
        return float(v) if np.isfinite(v) else 0.0
    if system == "E":
        src = eph.get("DataSrc", np.nan)
        src = int(src) if np.isfinite(src) else 0
        if src & 0x100:                      # clock referenced to E1,E5a
            v = eph.get("BGDe5a", np.nan)
        elif src & 0x200:                    # clock referenced to E1,E5b
            v = eph.get("BGDe5b", np.nan)
        else:
            v = np.nan
        return float(v) if np.isfinite(v) else 0.0
    return 0.0


# --------------------------------------------------------------------------- solver

@dataclass
class Fix:
    ecef: np.ndarray
    lla: dict
    clock_bias_m: dict                 # per constellation
    residuals_m: dict                  # per sv, after the final iteration
    H: np.ndarray                      # n x (3+k)
    svs: list
    systems: str
    n_sv: int
    k: int
    dop: dict                          # G, P, H, V
    converged: bool
    iterations: int
    residual_rms_m: float
    elevation_deg: dict = field(default_factory=dict)

    def meta(self) -> dict:
        """The out-of-contract `_solution` block for a stream record."""
        return {
            "method": "wls", "systems": self.systems, "n_sv": self.n_sv,
            "k": self.k, "converged": self.converged, "iterations": self.iterations,
            "gdop": round(self.dop["G"], 2), "pdop": round(self.dop["P"], 2),
            "hdop": round(self.dop["H"], 2), "vdop": round(self.dop["V"], 2),
            "residual_rms_m": round(self.residual_rms_m, 3),
            "clock_bias_m": {k: round(v, 2) for k, v in self.clock_bias_m.items()},
        }


def wls_fix(pseudoranges: dict, sat_ecef: dict, sat_clock_bias_s: dict,
            x0, elevation_mask_deg: float = 10.0, sigma0_m: float = 1.0,
            svs=None, max_iter: int = 10, tol_m: float = 1e-4) -> Fix | None:
    """Gauss-Newton WLS on H = [unit LOS | per-constellation clock columns].

    `svs` fixes the satellite set (for the differential solve); otherwise every
    satellite with a finite pseudorange, a position and a clock above the mask.
    Returns None when there are fewer satellites than unknowns.
    """
    x0 = np.asarray(x0, dtype=float)
    lla0 = ecef_to_lla(*x0)
    R = enu_basis(lla0["lat"], lla0["lon"])
    up = R[2]

    cands = svs if svs is not None else sorted(pseudoranges)
    use, el = [], {}
    for sv in cands:
        p = pseudoranges.get(sv)
        s = sat_ecef.get(sv)
        if p is None or s is None or sv not in sat_clock_bias_s or not np.isfinite(p):
            continue
        los = np.asarray(s, float) - x0
        e_deg = float(np.degrees(np.arcsin(np.clip(los @ up / np.linalg.norm(los), -1, 1))))
        if e_deg < elevation_mask_deg:
            continue
        use.append(sv)
        el[sv] = e_deg
    systems = "".join(sorted({sv[0] for sv in use}))
    k, n = len(systems), len(use)
    if n < 3 + k or k == 0:
        return None

    col = {s: 3 + i for i, s in enumerate(systems)}
    S = np.array([sat_ecef[sv] for sv in use], dtype=float)
    Pc = np.array([pseudoranges[sv] + C_LIGHT * sat_clock_bias_s[sv] for sv in use])
    w = np.array([np.sin(np.radians(el[sv])) ** 2 for sv in use]) / sigma0_m ** 2

    x, b = x0.copy(), np.zeros(k)
    H = np.zeros((n, 3 + k))
    converged, it = False, 0
    for it in range(1, max_iter + 1):
        rot = np.array([sagnac(S[i], x) for i in range(n)])
        d = rot - x
        rho = np.linalg.norm(d, axis=1)
        H[:, :3] = -d / rho[:, None]
        H[:, 3:] = 0.0
        for i, sv in enumerate(use):
            H[i, col[sv[0]]] = 1.0
        dz = Pc - (rho + np.array([b[col[sv[0]] - 3] for sv in use]))
        N = H.T @ (w[:, None] * H)
        dxb = np.linalg.solve(N, H.T @ (w * dz))
        x += dxb[:3]
        b += dxb[3:]
        if np.linalg.norm(dxb[:3]) < tol_m:
            converged = True
            break

    rot = np.array([sagnac(S[i], x) for i in range(n)])
    rho = np.linalg.norm(rot - x, axis=1)
    res = Pc - (rho + np.array([b[col[sv[0]] - 3] for sv in use]))

    Q = np.linalg.inv(H.T @ H)
    lla = ecef_to_lla(*x)
    Renu = enu_basis(lla["lat"], lla["lon"])
    Qenu = Renu @ Q[:3, :3] @ Renu.T
    dop = {"G": float(np.sqrt(np.trace(Q))), "P": float(np.sqrt(np.trace(Q[:3, :3]))),
           "H": float(np.sqrt(Qenu[0, 0] + Qenu[1, 1])), "V": float(np.sqrt(Qenu[2, 2]))}

    return Fix(ecef=x, lla=lla,
               clock_bias_m={s: float(b[col[s] - 3]) for s in systems},
               residuals_m={sv: float(r) for sv, r in zip(use, res)},
               H=H, svs=use, systems=systems, n_sv=n, k=k, dop=dop,
               converged=converged, iterations=it,
               residual_rms_m=float(np.sqrt(np.mean(res ** 2))), elevation_deg=el)


# --------------------------------------------------------------------------- epochs

def epoch_inputs(epoch, nav: NavTables, systems: str = SYSTEMS, band: int = 1):
    """(pseudoranges, sat_ecef, sat_clock) dicts for one Track A epoch."""
    df = epoch.df
    P, S, dt = {}, {}, {}
    for sv, row in df.iterrows():
        if row["system"] not in systems:
            continue
        p = row[f"code_{band}"]
        if not np.isfinite(p):
            continue
        got = nav.satellite(sv, epoch.time, float(p))
        if got is None:
            continue
        P[sv], (S[sv], dt[sv]) = float(p), got
    return P, S, dt


def solve_epoch(epoch, nav: NavTables, x0=USN8_ECEF, svs=None, **kw) -> Fix | None:
    P, S, dt = epoch_inputs(epoch, nav)
    return wls_fix(P, S, dt, x0, svs=svs, **kw)


def displacement(fix_from: Fix, fix_to: Fix) -> dict:
    """ENU metres from one fix to another, in the frame of `fix_from`."""
    R = enu_basis(fix_from.lla["lat"], fix_from.lla["lon"])
    d = R @ (fix_to.ecef - fix_from.ecef)
    return {"e": float(d[0]), "n": float(d[1]), "u": float(d[2]),
            "horizontal_m": float(np.hypot(d[0], d[1])), "norm_m": float(np.linalg.norm(d))}


def differential(clean_epoch, injected_epoch, nav: NavTables, x0=USN8_ECEF, **kw):
    """(clean Fix, injected Fix, displacement) with one satellite set and weights.

    The set is whatever the CLEAN epoch can solve with; the injected epoch is
    solved on exactly that set. Weights come from elevation at x0 in both, so
    the only thing that differs between the two solutions is the pseudoranges.
    """
    truth = solve_epoch(clean_epoch, nav, x0, **kw)
    if truth is None:
        return None, None, None
    believed = solve_epoch(injected_epoch, nav, x0, svs=truth.svs, **kw)
    if believed is None:
        return truth, None, None
    return truth, believed, displacement(truth, believed)


def error_from_surveyed(fix: Fix, surveyed_ecef=USN8_ECEF) -> dict:
    """ENU error of a fix against the surveyed station -- the solver's sanity check."""
    lla = ecef_to_lla(*surveyed_ecef)
    R = enu_basis(lla["lat"], lla["lon"])
    d = R @ (fix.ecef - np.asarray(surveyed_ecef, float))
    return {"e": float(d[0]), "n": float(d[1]), "u": float(d[2]),
            "horizontal_m": float(np.hypot(d[0], d[1])), "norm_m": float(np.linalg.norm(d))}
