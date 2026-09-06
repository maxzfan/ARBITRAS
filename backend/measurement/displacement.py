"""Empirical swept displacement vs the analytic bound (Claim 2a closure).

Per injected-run epoch the stream gives the believed and clean-truth
positions (`_solution.displacement_m`, the differential-WLS distance) and
the analytic `geometry.displacement_bound_m` over the same epoch's
trusted geometry. The bound is a bound on UNDETECTED displacement: it
claims nothing once the arbitras has left NOMINAL. Claim 2a therefore
holds iff empirical <= bound at every epoch whose ARBITRATED state is
NOMINAL — checked explicitly and printed, not eyeballed off a plot.
design.md §10: "If the empirical number ever exceeds the bound, the
bound is wrong — that check is worth running explicitly."

Epoch format:
  (t: datetime, empirical_m: float, bound_m: float | None)
None bounds (not overdetermined) are excluded from the check and
reported separately — no residual test exists there, so the bound
makes no claim.

Run over the injected replay stream:
  python -m backend.measurement.displacement out/carryoff.jsonl
"""
from __future__ import annotations

import json
from datetime import datetime
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


def main(argv=None) -> None:
    import argparse

    from console.replay import arbitrate
    from console.arbitras.states import TrustState

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("stream", help="injected replay JSONL (out/carryoff.jsonl)")
    ap.add_argument("--plot", default=str(OUT))
    args = ap.parse_args(argv)

    with open(args.stream) as fh:
        recs = [json.loads(l) for l in fh if l.strip()]
    decisions = arbitrate(recs)

    def _ep(r):
        t = datetime.fromisoformat(r["timestamp"].replace("Z", ""))
        emp = r.get("_solution", {}).get("displacement_m")
        return (t, emp, r["geometry"].get("displacement_bound_m"))

    every = [_ep(r) for r in recs if "_solution" in r]
    # The bound only claims epochs the arbitras still trusts (NOMINAL).
    nominal = [_ep(r) for r, d in zip(recs, decisions)
               if "_solution" in r and d.state is TrustState.NOMINAL]
    result = check_epochs(nominal)
    print(f"Claim 2a over ARBITRATED-NOMINAL epochs "
          f"({len(nominal)} of {len(every)} solved epochs):")
    print(report(result))

    # §10 headline: displacement at the epoch immediately preceding the
    # first transition out of NOMINAL after the attack begins.
    first = next((i for i, d in enumerate(decisions)
                  if d.state is not TrustState.NOMINAL), None)
    if first is not None and first > 0:
        t, emp, bnd = _ep(recs[first - 1])
        print(f"max adversarial displacement (epoch before first "
              f"transition, {t}): empirical {emp:.2f} m, analytic bound "
              f"{bnd if bnd is None else round(bnd, 1)} m")

    plot(every, Path(args.plot))
    print(f"wrote {args.plot} (full run, both lines; the bound makes no "
          f"claim after the arbitras leaves NOMINAL)")


if __name__ == "__main__":
    main()
