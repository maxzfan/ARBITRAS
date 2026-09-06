"""Headless replay of the arbitras over a contract stream.

    python -m console.replay out/fixture_stream.jsonl

This is the interface Track C's Dirichlet sweep (design.md §10) calls: it needs
to replay the arbitras over all 2,880 epochs once per weight draw, with no
server, no browser and no I/O per epoch.

    from console.replay import arbitrate, false_surrender_rate
    decisions = arbitrate(epochs)
    fsr = false_surrender_rate(decisions)      # continuity risk, §10
"""
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Iterable, Optional

from console.arbitras.machine import Arbitras
from console.arbitras.states import TrustState


def load(path) -> list:
    rows = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                rows.append(None)
    return rows


def arbitrate(epochs: Iterable) -> list:
    arb = Arbitras()
    return [arb.step(e) for e in epochs]


def false_surrender_rate(decisions) -> dict:
    """Continuity risk (design.md §10), epoch-weighted, with a state breakdown.

    FSR = epochs below NOMINAL / total epochs, on a CLEAN replay. Running this
    on an injected replay is meaningless -- the caller owns that distinction.

    Also returns the event count, because §10 says be ready to report it:
    "what an operator cares about is time under unnecessary restriction, not
    count of annoyances -- but be ready to report event count too."
    """
    n = len(decisions)
    if not n:
        return {"fsr": None, "epochs": 0}
    by_state = Counter(d.state.name for d in decisions)
    below = sum(1 for d in decisions if d.state is not TrustState.NOMINAL)
    events = sum(
        1 for i, d in enumerate(decisions)
        if d.state is not TrustState.NOMINAL
        and (i == 0 or decisions[i - 1].state is TrustState.NOMINAL)
    )
    return {
        "fsr": below / n,
        "epochs": n,
        "epochs_below_nominal": below,
        "downgrade_events": events,
        "by_state": dict(by_state),
    }


def time_to_alert(decisions, injection_epoch: int) -> Optional[int]:
    """Epochs from injection start to first transition out of NOMINAL (§10)."""
    for d in decisions[injection_epoch:]:
        if d.state is not TrustState.NOMINAL:
            return d.epoch_index - injection_epoch
    return None


def transitions(decisions) -> list:
    return [
        (d.epoch_index, d.previous_state.name, d.state.name, d.reason)
        for d in decisions if d.changed
    ]


def main():
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "out/fixture_stream.jsonl")
    epochs = load(path)
    decisions = arbitrate(epochs)

    print(f"source  {path}   {len(epochs)} epochs")
    synth = sum(1 for e in epochs if isinstance(e, dict) and e.get("_synthetic"))
    if synth:
        print(f"WARNING: {synth} epochs are SYNTHETIC. Not a measurement.")

    print("\nstate timeline")
    for i, prev, new, reason in transitions(decisions):
        ts = decisions[i].timestamp or "-"
        c = decisions[i].confidence
        print(f"  epoch {i:4d}  {ts}  {prev:11s} -> {new:11s}"
              f"  ({reason}, confidence {c if c is None else round(c,3)})")

    m = false_surrender_rate(decisions)
    print(f"\nepoch-weighted time below NOMINAL: {m['fsr']:.4f}"
          f"  ({m['epochs_below_nominal']}/{m['epochs']} epochs,"
          f" {m['downgrade_events']} events)")
    print(f"state breakdown: {m['by_state']}")
    print("\nNOTE: on a mixed stream this is not FSR. FSR is defined on a CLEAN"
          "\nreplay only (design.md §10).")


if __name__ == "__main__":
    main()
