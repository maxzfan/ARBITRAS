"""Real-data demo streams for the console.

    python -m backend.demo            # -> out/clean.jsonl, out/carryoff.jsonl,
                                      #    out/demo.jsonl, docs/stream_provenance.md

Drives Track A's pipeline (loader -> noise floor -> calibration -> injector ->
features -> confidence -> §5 record) over the real USN8 day and fills the two
seams `backend.replay.run` leaves open:

  geometry      {"sky": real G+E az/el from broadcast ephemeris with a trusted
                 flag, "excluded_sv": satellites the detector distrusts} — the
                 Fisher-information fields stay null; they are Track C's.
  credential    a SCRIPTED schedule on the demo tail (VALID -> PENDING ->
                 EXPIRED) standing in for the TESLA layer until T1 lands.
                 T_int and d are venue-tuned (§9) so each state is legible
                 at the demo replay rate; PENDING is always T_int x d.
                 There is no "fading" phase: the §5 enum has nothing between
                 VALID and PENDING, and TESLA has no such state either -- a
                 held authorisation is VALID to the end of its window, then
                 absence of renewal is the revocation (fail-closed, §9).

Nothing in Track A's modules is edited. The feature extractor is driven
directly (not via replay.run) because that helper throws away `per_sv`, and the
distrusted set has to come from somewhere honest.

WHAT IS AND IS NOT MEASURED — read docs/stream_provenance.md, generated on
every run. In one line: features, confidence, satellites_tracked and the sky
are measured or propagated from data; the attack is injected with Track A's
§7 injector; the credential schedule is scripted; position is the SURVEYED
point because no position solution exists yet, so `_truth == position`
everywhere and the console's displacement readout reads 0 on this stream.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from backend.detection import (FEATURE_NAMES, FeatureExtractor, Weights, fit,
                               record, score, write_jsonl)
from backend.geometry.skyview import sky_at
from backend.injector import CARRY_OFF, CLEAN, inject, summarise
from backend.rinex import noise
from backend.rinex.loader import load_obs

OBS = "data/USN800USA_R_20262320000_01D_30S_MO.crx.gz"
SYSTEMS = "GERCS"
ONSET = datetime(2026, 8, 20, 12, 30)

# TEST VALUE, copied from tests/test_detection.py. Eric's CARRY_OFF deliberately
# has no default: the demo pin is "picked by hand from the printed arithmetic"
# and had not been given when this was written. Replace when it is.
CARRIER_RATE_ERROR_MPS = 0.02

EPOCH_S = 30
DEMO_RATE_EPS = 15.0     # §5 demo replay rate (10-20 epochs/s); legibility budget below
LEGIBLE_EPOCHS = 120     # 8.0 s on screen at DEMO_RATE_EPS -- the minimum a credential
                         # state must hold for video beat 4 to be readable (§11b):
                         # long enough to read the legend and understand PENDING
PRE_LAPSE_EPOCHS = 45    # 3.0 s before PENDING begins; reported for the threshold pick

# Team demo window built around Track A's code-default onset of 12:30, so every
# file on the team attacks at the same epoch. Lead-in and attack are unchanged
# from the 12:00-14:00 window agreed on 2026-09-05; the tail was extended to
# 16:15 so the credential lapse holds on screen. 510 epochs: 60 clean lead-in,
# 90 carry-off, 360 clean tail.
PRE_EPOCHS = 60          # beat 1: 12:00:00 -> 12:29:30
ATTACK_EPOCHS = 90       # beats 2/3: 12:30:00 -> 13:14:30

# TESLA parameters -- VENUE-TUNED, per design.md §9 ("Parameters (tune at venue)";
# known weaknesses: "interval length and disclosure lag become operational
# parameters tuned against comms reliability"). The §9 defaults (T_int 10, d 2
# -> PENDING 20 epochs) flash past in 1.3 s at DEMO_RATE_EPS and beat 4 is
# illegible. Tuned to T_int 60 x d 2 = 120 epochs (8.0 s). d stays at the §9
# default: d is the disclosure lag -- the forgery window a clock-dragging
# spoofer has to work with (§9 clock coupling) -- and lengthening it weakens the
# protocol for no display benefit; T_int is the renewal cadence and is the
# right knob. PENDING is always derived from T_int x d; never a literal.
T_INT_EPOCHS, DISCLOSURE_LAG_INTERVALS = 60, 2
PENDING_EPOCHS = T_INT_EPOCHS * DISCLOSURE_LAG_INTERVALS
# Tail composition. VALID must outlast the arbiter's recovery from RESTRICTED
# (~40 epochs under the placeholder thresholds) and hold >= LEGIBLE_EPOCHS;
# EXPIRED holds SURRENDERED-under-a-clean-sky >= LEGIBLE_EPOCHS.
POST_VALID_EPOCHS = 120   # 13:15:00 -> 14:14:30
POST_EXPIRED_EPOCHS = 120 # 15:15:00 -> 16:14:30
POST_EPOCHS = POST_VALID_EPOCHS + PENDING_EPOCHS + POST_EXPIRED_EPOCHS   # 360
for _name, _n in (("POST_VALID_EPOCHS", POST_VALID_EPOCHS),
                  ("PENDING_EPOCHS", PENDING_EPOCHS),
                  ("POST_EXPIRED_EPOCHS", POST_EXPIRED_EPOCHS)):
    assert _n >= LEGIBLE_EPOCHS, f"{_name}={_n} < LEGIBLE_EPOCHS={LEGIBLE_EPOCHS}"

# Distrust rule. A satellite is excluded on an epoch when its own pseudorange-
# residual |z| reaches the calibrated saturation scale — i.e. it is beyond the
# median per-satellite p99 tail of the clean day (features.Calibration). That
# is the only threshold already in the system; nothing new is invented here.
# The residual is used alone because it is the sustained detector (§6a.2);
# C/N0 saturates for one epoch at onset and would flicker satellites.
DISTRUST_FEATURE = "pseudorange_residual"

OUT = Path("out")
PROVENANCE = Path("docs/stream_provenance.md")


# --------------------------------------------------------------------------- io

def write_atomic(records, path: Path) -> Path:
    """Write to a sibling temp file, then os.replace() onto `path`.

    The console server opens out/demo.jsonl per SSE connection; a connection
    opened mid-write must never see a truncated stream (it would read as a
    shorter day, and a missing epoch is evidence of degradation under §5).
    """
    path = Path(path)
    tmp = path.with_name(f".{path.name}.tmp")
    write_jsonl(records, tmp)
    os.replace(tmp, path)
    return path


# --------------------------------------------------------------------------- seams

def distrusted(per_sv: pd.DataFrame, cal) -> list[str]:
    col = per_sv[DISTRUST_FEATURE].dropna()
    if col.empty:
        return []
    return sorted(col.index[(col / cal.z_sat[DISTRUST_FEATURE]) >= 1.0])


def geometry_block(sky: list, excluded: list[str]) -> dict:
    ex = set(excluded)
    return {
        "sky": [dict(s, trusted=s["sv"] not in ex) for s in sky],
        "excluded_sv": excluded,
        # Track C's. Null, not fabricated. The console renders dashes.
        "information_ratio": None,
        "displacement_bound_m": None,
        "next_best_observation": None,
    }


def credential_schedule(j_in_slice: int) -> str:
    """Scripted beat-4 schedule by index within the demo slice."""
    post = j_in_slice - (PRE_EPOCHS + ATTACK_EPOCHS)
    if post < POST_VALID_EPOCHS:
        return "VALID"
    if post < POST_VALID_EPOCHS + PENDING_EPOCHS:
        return "PENDING"
    return "EXPIRED"


# --------------------------------------------------------------------------- scoring

def score_stream(epochs, cal, sky_cache, truth: pd.DataFrame | None = None,
                 label: str = "") -> list[dict]:
    fx, w, out = FeatureExtractor(cal), Weights(), []
    n = len(epochs)
    for i, ep in enumerate(epochs):
        res = fx.step(ep)
        excluded = distrusted(res["per_sv"], cal)
        geom = geometry_block(sky_cache[ep.time], excluded)
        rec = record(ep.time, res["features"], score(res["features"], geom, w),
                     n_sv=ep.n_sv, geometry=geom, credential_status="VALID")
        # Replay ground truth for the console (underscore = out of contract).
        # There is no position solution yet, so truth IS the reported position.
        rec["_truth"] = {"lat": rec["position"]["lat"], "lon": rec["position"]["lon"]}
        if truth is not None:
            row = truth.loc[ep.time]
            rec["_attack"] = {
                "stage": row["stage"],
                "n_spoofed": int(row["n_spoofed"]),
                "range_offset_m": round(float(row["range_offset_m"]), 2),
                "cmc_divergence_m": round(float(row["cmc_divergence_m"]), 3),
            }
        out.append(rec)
        if i % 500 == 0:
            print(f"  {label} {i:5d}/{n}", flush=True)
    return out


def build_sky_cache(epochs) -> dict:
    cache, n = {}, len(epochs)
    for i, ep in enumerate(epochs):
        cache[ep.time] = sky_at(ep.time)
        if i % 500 == 0:
            print(f"  sky {i:5d}/{n}", flush=True)
    return cache


# --------------------------------------------------------------------------- main

def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--obs", default=OBS)
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    print("load", flush=True)
    clean = load_obs(args.obs, systems=SYSTEMS)
    floor = noise.measure(clean)
    print(" ", floor)
    cal = fit(clean, floor)
    print(" ", cal)

    print("sky (real ephemeris, G+E)", flush=True)
    sky = build_sky_cache(clean)

    print("clean replay", flush=True)
    clean_recs = score_stream(clean, cal, sky, label="clean")
    write_atomic(clean_recs, out / "clean.jsonl")

    print("carry-off replay", flush=True)
    # Spoof.stage() treats dt == duration_s as still under attack, so a duration
    # of exactly N*30 s covers N+1 epochs (CAPTURE at dt=0 plus N WALK). Trim by
    # a second so the attack is exactly ATTACK_EPOCHS and beat 4 starts clean.
    spoof = CARRY_OFF(onset=ONSET, carrier_rate_error=CARRIER_RATE_ERROR_MPS,
                      duration_s=ATTACK_EPOCHS * EPOCH_S - 1)
    injected, truth = inject(clean, spoof, floor)
    inj_recs = score_stream(injected, cal, sky, truth=truth, label="carry")
    write_atomic(inj_recs, out / "carryoff.jsonl")
    truth.to_csv(out / "carryoff_truth.csv")
    print(" ", summarise(truth))

    # Four-beat stitch: one contiguous slice of the SAME causal injected run,
    # so the detector's history through the attack is real, and the post-attack
    # tail is genuinely "attack stopped" rather than a splice from another run.
    onset_i = next(i for i, ep in enumerate(injected) if ep.time >= ONSET)
    lo, hi = onset_i - PRE_EPOCHS, onset_i + ATTACK_EPOCHS + POST_EPOCHS
    demo = [dict(r) for r in inj_recs[lo:hi]]
    for j, r in enumerate(demo):
        r["credential_status"] = credential_schedule(j)
    write_atomic(demo, out / "demo.jsonl")

    _write_provenance(clean, floor, cal, truth, demo, onset_i, lo, hi, clean_recs)
    print(f"\nwrote {out/'clean.jsonl'} ({len(clean_recs)}), "
          f"{out/'carryoff.jsonl'} ({len(inj_recs)}), "
          f"{out/'demo.jsonl'} ({len(demo)}), {PROVENANCE}")


def _write_provenance(clean, floor, cal, truth, demo, onset_i, lo, hi, clean_recs):
    active = truth[truth["stage"] != CLEAN]
    ex_clean = np.mean([len(r["geometry"]["excluded_sv"]) > 0 for r in clean_recs])
    creds = [r["credential_status"] for r in demo]
    n_pending = creds.count("PENDING")
    t0, t1 = demo[0]["timestamp"], demo[-1]["timestamp"]
    ta0 = demo[PRE_EPOCHS]["timestamp"]
    ta1 = demo[PRE_EPOCHS + ATTACK_EPOCHS - 1]["timestamp"]
    tv0 = demo[PRE_EPOCHS + ATTACK_EPOCHS]["timestamp"]
    ip = creds.index("PENDING")
    tp0 = demo[ip]["timestamp"]
    te0 = demo[creds.index("EXPIRED")]["timestamp"]
    pre = [r["confidence"] for r in demo[ip - PRE_LAPSE_EPOCHS:ip]]
    pre_min, pre_med = min(pre), float(np.median(pre))
    pre_below = sum(c < 0.75 for c in pre)
    pre_lapse = (f"**Threshold dependency for beat 4:** in the {PRE_LAPSE_EPOCHS} epochs "
                 f"({PRE_LAPSE_EPOCHS / DEMO_RATE_EPS:.0f} s) before PENDING begins, "
                 f"confidence min {pre_min:.3f} / median {pre_med:.3f}; "
                 f"{pre_below} of {PRE_LAPSE_EPOCHS} sit below the placeholder NOMINAL "
                 f"threshold 0.75. Whether the vehicle is steadily NOMINAL when the "
                 f"credential lapses depends on the 21:00 threshold pick, not on this stream.")
    md = f"""# Stream provenance — out/demo.jsonl, out/clean.jsonl, out/carryoff.jsonl

Generated by `python -m backend.demo`. Every field below is classified so that
nothing on screen can be mistaken for a measurement it is not (design.md §11b).

## Source

USN8 (US Naval Observatory), 2026-08-20, {len(clean)} epochs at {EPOCH_S} s,
systems {SYSTEMS}. {floor}
{cal}

## Field classification

| Field | Class | Source |
|---|---|---|
| `timestamp` | measured | RINEX epoch, GPS time, never rewritten |
| `features.*` (3) | measured / injected | Track A `FeatureExtractor`, calibrated on the clean day. On attack epochs the observables were modified by the injector before scoring |
| `confidence` | derived | `1 - anomaly` with equal placeholder weights, beta forced to 1 (no geometry half yet). `score_detail.weights_tuned` is false |
| `satellites_tracked` | measured | count of SVs with code or C/N0 on band 1 |
| `position` | **surveyed, not a solution** | `position_source: "surveyed"`. No least-squares solution exists without Track C's line-of-sight vectors. Displacement reads **0 m** on this stream |
| `_truth` | replay metadata | equals `position` on every epoch, for the same reason |
| `geometry.sky[]` | propagated | real az/el from `backend/geometry/skyview.py` (gnss-lib-py, G+E only, 10° mask). **A second, pseudorange-validated propagator with BeiDou exists in `backend/rinex/ephemeris.py` (Track A); convergence is an 18:30 checkpoint item.** |
| `geometry.sky[].trusted` / `geometry.excluded_sv` | derived | satellite's own `{DISTRUST_FEATURE}` |z| ≥ calibrated saturation (median per-SV clean p99). No new threshold. On the clean day {ex_clean:.1%} of epochs have ≥1 excluded SV |
| `geometry.information_ratio`, `displacement_bound_m`, `next_best_observation` | **null, awaiting Track C** | not fabricated |
| `credential_status` | **scripted** (demo.jsonl only) | VALID → PENDING ({n_pending} epochs = T_int {T_INT_EPOCHS} × d {DISCLOSURE_LAG_INTERVALS}) → EXPIRED. **T_int and d are venue-tuned protocol parameters (design.md §9)**: the §9 defaults (10 × 2 = 20 epochs) last {20 / DEMO_RATE_EPS:.1f} s at the {DEMO_RATE_EPS:.0f} epochs/s demo rate; tuned to {T_INT_EPOCHS} × {DISCLOSURE_LAG_INTERVALS} so every credential state holds ≥ {LEGIBLE_EPOCHS / DEMO_RATE_EPS:.0f} s on screen. Stands in for the live TESLA verifier until Track A's T1 lands. {pre_lapse} |
| `_attack` (carryoff/demo) | injector truth log | stage, n_spoofed, range_offset_m, cmc_divergence_m — what the attacker did, never seen by the detector |
| `score_detail` | derived | Track A's breakdown of the composite |

No record carries `_synthetic`.

## Windows

| Stream | Epochs | Content |
|---|---|---|
| clean.jsonl | {len(clean_recs)} | whole day, no injection, credential VALID |
| carryoff.jsonl | {len(clean_recs)} | whole day, carry-off {ta0} → {ta1} then clean |
| demo.jsonl | {len(demo)} | slice [{lo}, {hi}) of carryoff: {t0} → {t1} |

demo.jsonl beats: {PRE_EPOCHS} clean · {ATTACK_EPOCHS} attack ({ta0} → {ta1}) ·
{POST_EPOCHS} post-attack clean with credential {POST_VALID_EPOCHS} VALID ({tv0} → {tp0}) /
{n_pending} PENDING ({tp0} → {te0}) / {POST_EXPIRED_EPOCHS} EXPIRED ({te0} → {t1}).
At {DEMO_RATE_EPS:.0f} epochs/s: VALID tail {POST_VALID_EPOCHS / DEMO_RATE_EPS:.1f} s ·
PENDING {n_pending / DEMO_RATE_EPS:.1f} s · EXPIRED {POST_EXPIRED_EPOCHS / DEMO_RATE_EPS:.1f} s.

## Injector parameters (design.md §7 carry-off, Track A defaults)

{summarise(truth)}

power_db 2.0 · walk_off_mps 1.0 · target all GPS tracked at capture ·
capture_s 10 · duration_s {ATTACK_EPOCHS * EPOCH_S} ·
**carrier_rate_error {CARRIER_RATE_ERROR_MPS} m/s — the TEST value from
tests/test_detection.py, not the demo pin.** Eric left the pin deliberately
unset ("picked by hand from the printed arithmetic"); replace it when given.
Walk-off was not tuned (§7: venue).
"""
    PROVENANCE.parent.mkdir(parents=True, exist_ok=True)
    PROVENANCE.write_text(md)


if __name__ == "__main__":
    main()
