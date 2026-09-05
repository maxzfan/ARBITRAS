"""Empirical swept displacement vs the analytic bound (Claim 2a closure).

Per injected-run epoch Track A's manifest gives the believed and true
positions; the empirical displacement is their distance. The analytic
bound is displacement_bound_m over the same epoch's trusted geometry.
Claim 2a holds iff empirical <= bound at every epoch — checked
explicitly and printed, not eyeballed off a plot.

Epoch format (agreed with Track A at 21:00, plumbed overnight):
  (t: datetime, empirical_m: float, bound_m: float | None)
None bounds (not overdetermined) are excluded from the check and
reported separately — no residual test exists there, so the bound
makes no claim.

Run (once the injected-run manifest exists):
  python -m backend.measurement.displacement <manifest.json>
"""
from __future__ import annotations

from pathlib import Path

OUT = Path("docs/plots/displacement_empirical_vs_bound.png")


def check_epochs(epochs: list[tuple]) -> dict:
    """Explicit Claim 2a check: empirical <= bound wherever a bound exists."""
    bounded = [(t, e, b) for t, e, b in epochs if b is not None]
    violations = [(t, e, b) for t, e, b in bounded if e > b]
    return {
        "n_total": len(epochs),
        "n_bounded": len(bounded),
        "n_pass": len(bounded) - len(violations),
        "violations": violations,
        "ok": not violations and bool(bounded),
    }


def report(result: dict) -> str:
    n, nb = result["n_total"], result["n_bounded"]
    unbounded = f" ({n - nb} epochs unbounded, excluded)" if nb < n else ""
    if result["ok"]:
        return f"empirical <= bound {result['n_pass']}/{nb} — PASS{unbounded}"
    lines = [f"empirical <= bound {result['n_pass']}/{nb} — "
             f"FAIL{unbounded}"]
    for t, e, b in result["violations"][:5]:
        lines.append(f"  {t}: empirical {e:.1f} m > bound {b:.1f} m")
    return "\n".join(lines)


def plot(epochs: list[tuple], out: Path = OUT) -> None:
    """One axis: empirical displacement and analytic bound over the run."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    ts = [t for t, _, _ in epochs]
    emp = [e for _, e, _ in epochs]
    bnd = [b for _, _, b in epochs]
    fig, ax = plt.subplots(figsize=(11, 4))
    ax.plot(ts, emp, lw=1.2, color="tab:red",
            label="empirical displacement |believed − true|")
    ax.plot(ts, bnd, lw=1.2, color="tab:blue",
            label="analytic bound (chi-square residual consistency)")
    ax.set_ylabel("metres")
    ax.set_xlabel("GPS time")
    ax.set_title("Injected run — swept displacement vs derived bound")
    ax.legend(loc="upper left")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=150)
