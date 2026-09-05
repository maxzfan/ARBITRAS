"""Generate a SYNTHETIC development stream in the §5 contract shape.

    python -m console.fixture_stream            # -> out/fixture_stream.jsonl

WHY THIS EXISTS: so the console can be built and demonstrated end to end before
Track A's detector produces real output. design.md §14 wants track B unblocked
from hour one.

WHAT IT IS NOT: data. Every record carries "_synthetic": true, and the console
renders a loud SYNTHETIC banner whenever it sees that flag. design.md §11b:
"Only what actually ran. No mockups, no fake data." This file exists to be
replaced by Track A's real output before anything is recorded.

The numbers here are shaped to exercise every branch of the arbiter. They are
not measurements and no number from this file may appear in the video, the
README, or the slides.
"""
import json
import math
import random
from pathlib import Path

SEED = 20260905
N_CLEAN = 200        # beat 1
N_ATTACK = 200       # beat 3 (and beat 2 with the layer off)
N_CREDENTIAL = 120   # beat 4: clean sky, authorisation lapses

LAT0, LON0, ALT0 = 38.9207, -77.0669, 58.3
ALL_SV = ["G07", "G13", "G21", "G30", "E11", "E19"]


def _iso(i):
    total = i * 30
    return (f"2026-08-20T{total // 3600 % 24:02d}:"
            f"{total // 60 % 60:02d}:{total % 60:02d}Z")


def generate(path: Path):
    rng = random.Random(SEED)
    rows = []

    def row(i, confidence, features, geometry, credential, believed, truth):
        return {
            "timestamp": _iso(i),
            "confidence": round(max(0.0, min(1.0, confidence)), 4),
            "credential_status": credential,
            "position": {"lat": round(believed[0], 7), "lon": round(believed[1], 7),
                         "alt": ALT0},
            "features": {k: round(max(0.0, min(1.0, v)), 4) for k, v in features.items()},
            "geometry": geometry,
            "satellites_tracked": 11 - len(geometry.get("excluded_sv", [])),
            # Out-of-contract replay metadata. A real vehicle does not have this;
            # a replay does, because we injected the attack. Needed for video
            # beat 2 (believed vs true track separating) -- see NOTE below.
            "_truth": {"lat": round(truth[0], 7), "lon": round(truth[1], 7)},
            "_synthetic": True,
        }

    # -- beat 1: clean ----------------------------------------------------
    for i in range(N_CLEAN):
        rows.append(row(
            i,
            0.90 + rng.gauss(0, 0.02),
            {"cn0_anomaly": abs(rng.gauss(0.06, 0.03)),
             "pseudorange_residual": abs(rng.gauss(0.05, 0.02)),
             "code_carrier_divergence": abs(rng.gauss(0.04, 0.02)),
             "cross_constellation": abs(rng.gauss(0.05, 0.02))},
            {"information_ratio": round(0.94 + rng.gauss(0, 0.01), 4),
             "excluded_sv": [], "displacement_bound_m": round(8 + rng.gauss(0, 0.6), 1),
             "next_best_observation": None},
            "VALID",
            (LAT0, LON0), (LAT0, LON0),
        ))

    # -- beats 2 and 3: intermediate carry-off, 1-3 dB (design.md §7) -----
    for j in range(N_ATTACK):
        i = N_CLEAN + j
        t = j / N_ATTACK
        # C/N0 spikes at onset then goes quiet ~10 s in as the loops lock (§6a);
        # the residual stays elevated, which is why one feature is insufficient.
        onset = math.exp(-((j - 4) ** 2) / 8.0)
        cn0 = 0.06 + 0.75 * onset + 0.20 * math.exp(-j / 25.0)
        resid = 0.05 + 0.80 * min(1.0, t * 1.9)
        ccd = 0.04 + 0.55 * min(1.0, t * 1.5)
        xc = 0.05 + 0.30 * min(1.0, t * 1.2)
        excluded = [s for k, s in enumerate(ALL_SV) if t > (k + 1) / (len(ALL_SV) + 1.5)]
        rows.append(row(
            i,
            0.90 - 0.80 * min(1.0, t * 1.35) + rng.gauss(0, 0.012),
            {"cn0_anomaly": cn0, "pseudorange_residual": resid,
             "code_carrier_divergence": ccd, "cross_constellation": xc},
            {"information_ratio": round(max(0.05, 0.94 - 0.75 * t), 4),
             "excluded_sv": excluded,
             "displacement_bound_m": round(8 + 120 * t ** 1.5, 1),
             "next_best_observation": "E" if excluded and excluded[0][0] == "G" else "G"},
            "VALID",
            (LAT0, LON0),                                    # believed: unmoved
            (LAT0 - 0.00045 * t * 1.0, LON0 + 0.00062 * t),  # true: walking off
        ))

    # -- beat 4: clean sky, perfect fix, authorisation lapses -------------
    for j in range(N_CREDENTIAL):
        i = N_CLEAN + N_ATTACK + j
        cred = "VALID" if j < 30 else ("PENDING" if j < 45 else "EXPIRED")
        rows.append(row(
            i,
            0.93 + rng.gauss(0, 0.015),
            {"cn0_anomaly": abs(rng.gauss(0.05, 0.02)),
             "pseudorange_residual": abs(rng.gauss(0.04, 0.02)),
             "code_carrier_divergence": abs(rng.gauss(0.04, 0.02)),
             "cross_constellation": abs(rng.gauss(0.04, 0.02))},
            {"information_ratio": round(0.95 + rng.gauss(0, 0.008), 4),
             "excluded_sv": [], "displacement_bound_m": round(7 + rng.gauss(0, 0.5), 1),
             "next_best_observation": None},
            cred,
            (LAT0, LON0), (LAT0, LON0),
        ))

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    return len(rows)


if __name__ == "__main__":
    out = Path("out/fixture_stream.jsonl")
    n = generate(out)
    print(f"wrote {n} SYNTHETIC epochs -> {out}")
    print("NOT DATA. Replace with Track A output before recording anything.")
