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

ONE EXCEPTION, AND IT IS NARROW: `geometry.sky` is REAL. Satellite azimuth and
elevation are propagated from the BRDC00IGS broadcast ephemeris for 2026-08-20
at each epoch's own timestamp (see backend/geometry/skyview.py). The satellites
are where they actually were. Everything else in this file -- confidence, the
four features, information_ratio, displacement_bound_m, the credential
schedule, the injected displacement -- remains synthetic, and the record still
carries "_synthetic": true for exactly that reason.
"""
import json
import math
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

SEED = 20260905
N_CLEAN = 200        # beat 1
N_ATTACK = 200       # beat 3 (and beat 2 with the layer off)
N_CREDENTIAL = 120   # beat 4: clean sky, authorisation lapses

from backend.geometry.skyview import sky_at
from console.mission import ALT, LAT, LON, M_PER_DEG_LAT, M_PER_DEG_LON

# USN8's SURVEYED position, from the ECEF in design.md §4. Truth is fixed --
# this is a static reference station, so the spoofer moves what the receiver
# BELIEVES, not where the antenna is. (The earlier fixture had this backwards,
# which made the plot read as a vehicle driving away from a stationary fix.)
LAT0, LON0, ALT0 = LAT, LON, ALT
EPOCH0 = datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc)
ELEV_MASK = 10.0
N_SPOOFED_SV = 6      # satellites the injector walks off, highest-elevation first

# Displacement the injector achieves by the end of the attack window, in metres.
# Deliberately past mission.ALERT_LIMIT_M so the alert limit is visibly crossed.
MAX_DISPLACEMENT_M = 64.0
BEARING_DEG = 118.0        # east-southeast, arbitrary


def _spoof_targets(sky_start, sky_end, n=N_SPOOFED_SV):
    """Which satellites the injector attacks.

    Chosen from those actually visible for the WHOLE attack window, highest
    elevation first -- a spoofer goes after the satellites the receiver is
    tracking most strongly, and a target that sets halfway through would make
    the excluded_sv list reference satellites that are not in the sky.
    """
    common = {s["sv"] for s in sky_start} & {s["sv"] for s in sky_end}
    el = {s["sv"]: s["el"] for s in sky_start}
    return sorted(common, key=lambda sv: -el[sv])[:n]


def _displaced(t):
    """Believed position at attack progress t in [0,1]. Walk-off, not a jump."""
    d = MAX_DISPLACEMENT_M * min(1.0, t * 1.35)
    br = math.radians(BEARING_DEG)
    return (LAT0 + (d * math.cos(br)) / M_PER_DEG_LAT,
            LON0 + (d * math.sin(br)) / M_PER_DEG_LON)


def _iso(i):
    total = i * 30
    return (f"2026-08-20T{total // 3600 % 24:02d}:"
            f"{total // 60 % 60:02d}:{total % 60:02d}Z")


def generate(path: Path):
    rng = random.Random(SEED)
    rows = []

    # The attack runs epochs N_CLEAN .. N_CLEAN+N_ATTACK. Pick its targets from
    # satellites genuinely visible across that whole span.
    attack_sv = _spoof_targets(
        sky_at(EPOCH0 + timedelta(seconds=30 * N_CLEAN), mask_deg=ELEV_MASK),
        sky_at(EPOCH0 + timedelta(seconds=30 * (N_CLEAN + N_ATTACK - 1)),
               mask_deg=ELEV_MASK),
    )

    def row(i, confidence, features, geometry, credential, believed, truth):
        # Real sky at this epoch's own timestamp, with trust flags applied.
        sky = sky_at(EPOCH0 + timedelta(seconds=30 * i), mask_deg=ELEV_MASK)
        excluded = set(geometry.get("excluded_sv", []))
        geometry = dict(geometry)
        geometry["sky"] = [{**s, "trusted": s["sv"] not in excluded} for s in sky]
        trusted = sum(1 for s in geometry["sky"] if s["trusted"])
        return {
            "timestamp": _iso(i),
            "confidence": round(max(0.0, min(1.0, confidence)), 4),
            "credential_status": credential,
            "position": {"lat": round(believed[0], 7), "lon": round(believed[1], 7),
                         "alt": ALT0},
            "features": {k: round(max(0.0, min(1.0, v)), 4) for k, v in features.items()},
            "geometry": geometry,
            # A receiver tracks what it sees; trust is the arbiter's business.
            # §5 says this field is receiver output, so it is the visible count.
            "satellites_tracked": len(sky),
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
        excluded = [s for k, s in enumerate(attack_sv)
                    if t > (k + 1) / (len(attack_sv) + 1.5)]
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
            _displaced(t),        # believed: dragged off by the spoofer
            (LAT0, LON0),         # truth: surveyed, fixed
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
