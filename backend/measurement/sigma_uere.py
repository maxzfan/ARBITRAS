"""Measured sigma_UERE — the clean-day post-fit residual RMS.

    python -m backend.measurement.sigma_uere        # prints the value, writes
                                                    # out/sigma_uere.json

`displacement_bound_m` (backend/geometry/information.py) is
sigma_UERE * sqrt(T * lambda_max): the analytic bound scales linearly with the
per-measurement error, so the number fed in must be MEASURED, not guessed —
the "no guessed numbers" convention (CLAUDE.md / design.md §10). This module
is that measurement.

What is measured: the RMS of the post-fit pseudorange residuals
(`Fix.residuals_m`) of the differential-family WLS solver
(`backend/geometry/solve.py::solve_epoch`) run on CLEAN-day epochs — the same
solver whose clean fix sits ~0.73 m median from the surveyed USN8 marker, and
the same residuals the geometry bound's chi-square acceptance test is defined
over. Expected magnitude ≈ 2.1 m (the clean end of the 18:30 checkpoint's
"267 m attack vs 2.1 m clean" observation).

Sampling: every `EVERY_N`th epoch of the day (default 30 → one fix per
15 minutes, 96 fixes, ~2,000 residuals over the full geometry rotation).
Residual statistics vary with satellite geometry on a scale of hours, not
seconds, so a 15-minute stride samples the whole range of geometries the day
offers while keeping the measurement to seconds of runtime instead of the
minutes a full 2,880-epoch solve costs. The stride and sample sizes are
recorded in the cache so the provenance is auditable.

Cache: `out/sigma_uere.json` carries the value plus provenance (obs file,
stride, epoch/residual counts, timestamp). `load_or_measure()` reads it if
present so `backend.demo` does not re-solve on every regeneration; delete the
file to force a re-measurement.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from backend.geometry.solve import NavTables, solve_epoch

# Same file and system set as backend.demo, so load_obs() hits the same cache.
OBS = "data/USN800USA_R_20262320000_01D_30S_MO.crx.gz"
SYSTEMS = "GERCS"
EVERY_N = 30                    # one epoch per 15 min; see module docstring
CACHE = Path("out/sigma_uere.json")


def measure_sigma_uere(epochs=None, nav: NavTables | None = None,
                       obs_path: str = OBS, every_n: int = EVERY_N,
                       cache: Path | None = CACHE) -> dict:
    """RMS of clean post-fit residuals over every `every_n`th epoch.

    `epochs`/`nav` may be passed in by a caller that already has them loaded
    (backend.demo); otherwise the clean day and nav tables are loaded here.
    Returns {"sigma_uere_m": float, ...provenance...} and writes it to `cache`
    unless cache is None.
    """
    if epochs is None:
        from backend.rinex.loader import load_obs
        epochs = load_obs(obs_path, systems=SYSTEMS)
    nav = nav or NavTables.load()

    sample = epochs[::every_n]
    residuals, solved = [], 0
    for ep in sample:
        fix = solve_epoch(ep, nav)
        if fix is None:
            continue
        residuals.extend(fix.residuals_m.values())
        solved += 1
    if not residuals:
        raise RuntimeError(f"no epoch in the {len(sample)}-epoch sample solved; "
                           f"cannot measure sigma_UERE")

    r = np.asarray(residuals, dtype=float)
    result = {
        "sigma_uere_m": float(np.sqrt(np.mean(r ** 2))),
        "definition": "RMS of post-fit pseudorange residuals (Fix.residuals_m), "
                      "backend/geometry/solve.py WLS, clean day",
        "obs": str(obs_path),
        "every_n": int(every_n),
        "n_epochs_sampled": len(sample),
        "n_epochs_solved": solved,
        "n_residuals": int(r.size),
        "residual_abs_p50_m": float(np.median(np.abs(r))),
        "residual_abs_p95_m": float(np.percentile(np.abs(r), 95)),
        "measured_at": datetime.now(timezone.utc).isoformat(),
    }
    if cache is not None:
        cache = Path(cache)
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(result, indent=2) + "\n")
    return result


def load_or_measure(cache: Path | None = CACHE, **kw) -> dict:
    """Cached value with provenance if present, else measure and cache."""
    if cache is not None and Path(cache).exists():
        return json.loads(Path(cache).read_text())
    return measure_sigma_uere(cache=cache, **kw)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--obs", default=OBS)
    ap.add_argument("--every-n", type=int, default=EVERY_N)
    ap.add_argument("--cache", default=str(CACHE))
    ap.add_argument("--fresh", action="store_true",
                    help="ignore an existing cache and re-measure")
    args = ap.parse_args(argv)
    cache = Path(args.cache)
    if args.fresh and cache.exists():
        cache.unlink()
    res = load_or_measure(cache=cache, obs_path=args.obs, every_n=args.every_n)
    print(f"sigma_UERE = {res['sigma_uere_m']:.3f} m  "
          f"({res['n_residuals']} residuals from {res['n_epochs_solved']} clean "
          f"epochs, every {res['every_n']}th of the day; |res| p50 "
          f"{res['residual_abs_p50_m']:.2f} m p95 {res['residual_abs_p95_m']:.2f} m)")
    print(f"cache: {cache}")


if __name__ == "__main__":
    main()
