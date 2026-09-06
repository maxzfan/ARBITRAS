"""Real-data demo streams for the console.

    python -m backend.demo            # -> out/clean.jsonl, out/carryoff.jsonl,
                                      #    out/demo.jsonl, docs/stream_provenance.md

Drives Track A's pipeline (loader -> noise floor -> calibration -> injector ->
features -> confidence -> §5 record) over the real USN8 day and fills the two
seams `backend.replay.run` leaves open:

  geometry      Track C's engine (backend/geometry/engine.py): information
                 ratio, analytic displacement bound and next-best observation
                 from the real H matrix, plus the sky with a trusted flag.
                 `excluded_sv` (the detector's distrust set) is ours; the
                 bound is non-null because sigma_UERE is MEASURED from clean
                 post-fit residuals (backend/measurement/sigma_uere.py) and
                 fed to set_sigma_uere() before any stream is scored.
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
§7 injector; the credential schedule is scripted; position is a weighted
least-squares solution from the (injected) pseudoranges and `_truth` the same
solution from the clean ones (backend/geometry/solve.py), so the displacement
on screen is exactly the position effect of what the injector did.

A physics fact that decides the demo target: a range offset applied to EVERY
tracked GPS satellite is a receiver-clock shift and moves the position by
0.0000 m -- the clock column absorbs it. Track A's `all_gps` carry-off is
therefore a timing attack, not a position attack. The demo defaults to Eric's
`top_n_by_elevation(6)` rule (§7 "walk-off on an SV subset"; the middle value
of his 12/8/6/4 sweep), which displaces the fix by tens to hundreds of metres.
`--target all_gps` restores the whole-constellation variant; both are real.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from backend.detection import (FEATURE_NAMES, CrossConstellation,
                               FeatureExtractor, Weights, fit, fit_cross,
                               record, score, write_jsonl)
from backend.geometry.engine import compute_geometry_block, set_sigma_uere
from backend.geometry.solve import (NavTables, displacement, error_from_surveyed,
                                    solve_epoch)
from backend.injector import (CARRY_OFF, CLEAN, DEMO_CARRIER_RATE_ERROR,
                              inject, summarise, top_n_by_elevation)
from backend.measurement.sigma_uere import load_or_measure
from backend.rinex import ephemeris, noise
from backend.rinex.loader import load_obs
from backend.rinex.solve import (EL_MASK_DEG, masked_epoch, residual_panel,
                                 solve_per_constellation)

OBS = "data/USN800USA_R_20262320000_01D_30S_MO.crx.gz"
SYSTEMS = "GERCS"
ONSET = datetime(2026, 8, 20, 12, 30)

# The demo pin, ruled by hand 2026-09-05 at k = 2 sigma / t = 30 s. This was a
# test value (0.02) copied from the test suite while the pin was pending; the
# pin has since been given, so it is used from its single definition rather
# than duplicated here. Chosen for demo legibility, not as a physical claim.
CARRIER_RATE_ERROR_MPS = DEMO_CARRIER_RATE_ERROR

# Spoofed subset (design.md §7: "walk-off on an SV subset"). See the module
# docstring: `all_gps` is absorbed by the GPS clock column and moves the fix by
# exactly nothing; a strict subset moves it. 6 is the middle of Track A's
# 12/8/6/4 sweep and Eric's own rule for what a single transmitter takes first.
TARGETS = {"top6": top_n_by_elevation(6), "top8": top_n_by_elevation(8),
           "top4": top_n_by_elevation(4), "all_gps": "all_gps"}
DEFAULT_TARGET = "top6"

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


def geometry_block(t: datetime, excluded: list[str], tracked: list[str]) -> dict:
    """Track C's engine block from the real H matrix: information ratio, sky
    (with trusted flags consistent with `excluded`), next-best observation,
    and the analytic displacement bound — non-null once main() has called
    set_sigma_uere() with the MEASURED clean-day residual RMS."""
    return compute_geometry_block(t, excluded, tracked_sv=tracked)


def credential_schedule(j_in_slice: int) -> str:
    """Scripted beat-4 schedule by index within the demo slice."""
    post = j_in_slice - (PRE_EPOCHS + ATTACK_EPOCHS)
    if post < POST_VALID_EPOCHS:
        return "VALID"
    if post < POST_VALID_EPOCHS + PENDING_EPOCHS:
        return "PENDING"
    return "EXPIRED"


# --------------------------------------------------------------------------- scoring

def solve_positions(clean, injected, nav: NavTables) -> tuple[dict, dict]:
    """Differential WLS per epoch: truth from clean pseudoranges, believed from
    injected, one satellite set and one weight vector per epoch.

    Returns ({time: {"truth": Fix, "believed": Fix | None, "d": disp | None}},
             sanity) where sanity is the clean-fix error against the surveyed
    station -- the number that says the solver is right before anything is
    read off the displacement.
    """
    out, errs, gdop = {}, [], []
    n = len(clean)
    for i, (ce, ie) in enumerate(zip(clean, injected)):
        assert ce.time == ie.time
        truth = solve_epoch(ce, nav)
        if truth is None:
            out[ce.time] = None
            continue
        believed = solve_epoch(ie, nav, svs=truth.svs)
        d = displacement(truth, believed) if believed is not None else None
        out[ce.time] = {"truth": truth, "believed": believed, "d": d}
        errs.append(error_from_surveyed(truth))
        gdop.append(truth.dop["G"])
        if i % 500 == 0:
            print(f"  solve {i:5d}/{n}", flush=True)
    h = np.array([e["horizontal_m"] for e in errs])
    u = np.array([e["u"] for e in errs])
    sanity = {
        "n": len(errs), "unsolvable": n - len(errs),
        "horizontal_p50": float(np.median(h)), "horizontal_p95": float(np.percentile(h, 95)),
        "horizontal_max": float(h.max()),
        "up_p50": float(np.median(u)), "up_p95": float(np.percentile(np.abs(u), 95)),
        "gdop_p50": float(np.median(gdop)), "gdop_max": float(max(gdop)),
    }
    # Kilometres here would mean the propagator, clock or Sagnac path is wrong;
    # refuse to emit a stream whose "truth" is that far off the survey marker.
    assert sanity["horizontal_p50"] < 30.0, sanity
    assert abs(sanity["up_p50"]) < 60.0, sanity
    return out, sanity


def score_stream(epochs, cal, truth: pd.DataFrame | None = None,
                 label: str = "", positions: dict | None = None,
                 xc: CrossConstellation | None = None, nav_xc=None) -> list[dict]:
    fx, w, out = FeatureExtractor(cal), Weights(), []
    n = len(epochs)
    for i, ep in enumerate(epochs):
        # Feature 2 is the post-fit pseudorange residual (2026-09-05 ruling),
        # so it needs the solution -- without it the feature is not scored at
        # all and the composite silently loses its strongest channel. One solve
        # per epoch, shared with the cross-constellation feature below.
        sols = (solve_per_constellation(ep, nav_xc)
                if nav_xc is not None else {})
        res = fx.step(ep, resid=(sols.get("all") or {}).get("resid_m"))
        feats = res["features"]
        if xc is not None and nav_xc is not None:
            # §6a.4, wired exactly as backend.replay.run does it: Eric's
            # absolute per-constellation WLS (backend/rinex/solve.py) feeds
            # the streaming cross-constellation scorer. The caller must
            # xc.reset() before each replay so no state leaks across runs.
            feats["cross_constellation"] = xc.score(ep, sols)["value"]
        excluded = distrusted(res["per_sv"], cal)
        geom = geometry_block(ep.time, excluded, list(ep.df.index))
        sol = positions.get(ep.time) if positions else None
        solved = sol is not None and sol["believed"] is not None
        rec = record(ep.time, feats, score(feats, geom, w),
                     n_sv=ep.n_sv, geometry=geom, credential_status="VALID",
                     position=dict(sol["believed"].lla) if solved else None)
        # Replay ground truth for the console (underscore = out of contract):
        # the WLS fix from the CLEAN pseudoranges at this epoch. `position` is
        # the fix from the injected ones. Same satellites, same weights.
        if solved:
            rec["position_source"] = "wls_differential"
            rec["_truth"] = {"lat": sol["truth"].lla["lat"], "lon": sol["truth"].lla["lon"]}
            rec["_solution"] = dict(sol["believed"].meta(),
                                    displacement_m=round(sol["d"]["horizontal_m"], 3))
        else:
            # No fix at this epoch: the surveyed point, flagged as such (emit.py).
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


# --------------------------------------------------------------------------- main

def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--obs", default=OBS)
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--target", choices=sorted(TARGETS), default=DEFAULT_TARGET,
                    help="spoofed GPS subset rule for the carry-off (see docstring)")
    args = ap.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    print("load", flush=True)
    raw = load_obs(args.obs, systems=SYSTEMS)
    floor = noise.measure(raw)
    print(" ", floor)

    # 5 degree elevation mask, applied upstream of scoring, solving and
    # geometry (2026-09-06 ruling). Track C's engine is set to the same angle
    # so the information-ratio denominator is taken over the same set.
    nav_xc = ephemeris.load_nav()
    from backend.geometry.engine import set_el_mask_deg
    set_el_mask_deg(EL_MASK_DEG)
    clean = [masked_epoch(e, nav_xc) for e in raw]
    print(f"  elevation mask {EL_MASK_DEG:g} deg applied "
          f"({sum(e.n_sv for e in raw) - sum(e.n_sv for e in clean)} "
          f"satellite-epochs dropped)")

    print("post-fit residual panel (feature 2)", flush=True)
    resid = residual_panel(clean, nav_xc)
    print(f"  {resid.shape[0]} epochs x {resid.shape[1]} SV")
    cal = fit(clean, floor, resid_panel=resid)
    print(" ", cal)

    print("cross-constellation calibration (§6a.4, per-constellation WLS)", flush=True)
    xc = CrossConstellation(fit_cross(clean, nav_xc), nav_xc)
    print(" ", xc.cal)

    print("sigma_UERE (measured clean-day post-fit residual RMS)", flush=True)
    nav = NavTables.load()
    su = load_or_measure(cache=out / "sigma_uere.json", epochs=clean, nav=nav)
    set_sigma_uere(su["sigma_uere_m"])
    print(f"  sigma_UERE {su['sigma_uere_m']:.3f} m "
          f"({su['n_residuals']} residuals, {su['n_epochs_solved']} clean epochs, "
          f"every {su['every_n']}th) -> geometry displacement bound is live")

    print("carry-off injection", flush=True)
    # Spoof.stage() treats dt == duration_s as still under attack, so a duration
    # of exactly N*30 s covers N+1 epochs (CAPTURE at dt=0 plus N WALK). Trim by
    # a second so the attack is exactly ATTACK_EPOCHS and beat 4 starts clean.
    spoof = CARRY_OFF(onset=ONSET, carrier_rate_error=CARRIER_RATE_ERROR_MPS,
                      duration_s=ATTACK_EPOCHS * EPOCH_S - 1,
                      target_svs=TARGETS[args.target])
    injected, truth = inject(clean, spoof, floor, nav=nav_xc)
    injected = [masked_epoch(e, nav_xc) for e in injected]
    truth.to_csv(out / "carryoff_truth.csv")
    print(" ", summarise(truth))

    print("position solutions (WLS, G+E, differential clean vs injected)", flush=True)
    positions, sanity = solve_positions(clean, injected, nav)
    print(f"  clean fix vs survey: horizontal p50 {sanity['horizontal_p50']:.2f} m "
          f"p95 {sanity['horizontal_p95']:.2f} m; up p50 {sanity['up_p50']:+.1f} m; "
          f"GDOP p50 {sanity['gdop_p50']:.2f}; unsolvable {sanity['unsolvable']}")
    # Clean stream: believed and truth are the same clean fix, so |D| == 0.
    clean_pos = {t: (None if s is None else {"truth": s["truth"], "believed": s["truth"],
                                             "d": displacement(s["truth"], s["truth"])})
                 for t, s in positions.items()}

    print("clean replay", flush=True)
    xc.reset()
    clean_recs = score_stream(clean, cal, label="clean", positions=clean_pos,
                              xc=xc, nav_xc=nav_xc)
    write_atomic(clean_recs, out / "clean.jsonl")

    print("carry-off replay", flush=True)
    xc.reset()
    inj_recs = score_stream(injected, cal, truth=truth, label="carry",
                            positions=positions, xc=xc, nav_xc=nav_xc)
    write_atomic(inj_recs, out / "carryoff.jsonl")

    # Four-beat stitch: one contiguous slice of the SAME causal injected run,
    # so the detector's history through the attack is real, and the post-attack
    # tail is genuinely "attack stopped" rather than a splice from another run.
    onset_i = next(i for i, ep in enumerate(injected) if ep.time >= ONSET)
    lo, hi = onset_i - PRE_EPOCHS, onset_i + ATTACK_EPOCHS + POST_EPOCHS
    demo = [dict(r) for r in inj_recs[lo:hi]]
    for j, r in enumerate(demo):
        r["credential_status"] = credential_schedule(j)
    write_atomic(demo, out / "demo.jsonl")

    dprof = _displacement_profile(demo, truth)
    print("  displacement over the attack window:", dprof["summary"])
    _write_provenance(clean, floor, cal, truth, demo, onset_i, lo, hi, clean_recs,
                      sanity, dprof, args.target, su)
    print(f"\nwrote {out/'clean.jsonl'} ({len(clean_recs)}), "
          f"{out/'carryoff.jsonl'} ({len(inj_recs)}), "
          f"{out/'demo.jsonl'} ({len(demo)}), {PROVENANCE}")


def _displacement_profile(demo, truth) -> dict:
    """|D| per epoch over the demo slice, against the injector's own range offset."""
    d = np.array([r.get("_solution", {}).get("displacement_m", np.nan) for r in demo])
    src = [r.get("position_source") for r in demo]
    a0, a1 = PRE_EPOCHS, PRE_EPOCHS + ATTACK_EPOCHS
    atk = d[a0:a1]
    off = np.array([truth.loc[datetime.fromisoformat(r["timestamp"].replace("Z", "+00:00"))
                              .replace(tzinfo=None), "range_offset_m"] for r in demo[a0:a1]])
    first = next((a0 + i for i, v in enumerate(atk) if v > 1.0), None)
    pk = int(np.nanargmax(atk)) if np.isfinite(atk).any() else 0
    walk = off > 0
    ratio = float(np.nanmedian(atk[walk] / off[walk])) if walk.any() else float("nan")
    prof = {
        "solved_fraction": src.count("wls_differential") / len(src),
        "clean_lead_in_max_m": float(np.nanmax(d[:a0])),
        "post_attack_max_m": float(np.nanmax(d[a1:])),
        "first_over_1m_idx": first,
        "first_over_1m_ts": demo[first]["timestamp"] if first is not None else None,
        "peak_m": float(atk[pk]), "peak_idx": a0 + pk, "peak_ts": demo[a0 + pk]["timestamp"],
        "peak_offset_m": float(off[pk]),
        "end_m": float(atk[-1]), "end_offset_m": float(off[-1]),
        "ratio_median": ratio,
    }
    prof["summary"] = (f"solved {prof['solved_fraction']:.1%}; lead-in max {prof['clean_lead_in_max_m']:.4f} m; "
                       f"first >1 m at idx {first} ({prof['first_over_1m_ts']}); "
                       f"peak {prof['peak_m']:.1f} m at idx {prof['peak_idx']} (range offset {prof['peak_offset_m']:.0f} m); "
                       f"end {prof['end_m']:.1f} m / {prof['end_offset_m']:.0f} m; "
                       f"median |D|/offset {ratio:.3f}; post-attack max {prof['post_attack_max_m']:.4f} m")
    return prof


def _write_provenance(clean, floor, cal, truth, demo, onset_i, lo, hi, clean_recs,
                      sanity, dprof, target_name, su):
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
| `features.*` (3 of 4) | measured / injected | Track A `FeatureExtractor` (C/N0, pseudorange residual, code-minus-carrier), calibrated on the clean day. On attack epochs the observables were modified by the injector before scoring |
| `features.cross_constellation` | derived | §6a.4 streaming scorer (`backend/detection/cross.py`) fed by **Eric's absolute per-constellation WLS** (`backend/rinex/solve.py`): per-constellation position disagreement + inter-system clock channels, calibrated on the clean day, `xc.reset()` before each replay |
| `confidence` | derived | `1 - (beta*anomaly + (1-beta)*(1-information_ratio))` with equal placeholder weights and placeholder beta 0.5. `score_detail.weights_tuned` is false |
| `satellites_tracked` | measured | count of SVs with code or C/N0 on band 1 |
| `position` | **solved** (`wls_differential`, {dprof['solved_fraction']:.1%} of demo epochs) | weighted least-squares single-point fix from the receiver's own band-1 pseudoranges as the receiver saw them (injected on attack epochs), G+E, one clock per constellation, Sagnac and SV-clock corrected, no atmosphere model — `backend/geometry/solve.py`. Epochs without a fix fall back to the surveyed point, flagged `position_source: "surveyed"` |
| `_truth` | replay metadata | the SAME solver on the CLEAN pseudoranges at the same epoch, same satellites, same weights. Atmosphere and ephemeris error are common to both fixes and cancel in the difference, so `position − _truth` is exactly the injector's effect on the fix. On clean epochs it is {dprof['clean_lead_in_max_m']:.4f} m |
| `_solution` | replay metadata | n_sv, k, DOPs, residual RMS, per-constellation clock bias, `displacement_m` — the fix's own quality figures |
| `geometry.sky[]` | propagated | real az/el from Track C's engine (`backend/geometry/engine.py`, own Keplerian propagator, 10° mask); trusted flags consistent with `excluded_sv` by construction |
| `geometry.sky[].trusted` / `geometry.excluded_sv` | derived | satellite's own `{DISTRUST_FEATURE}` |z| ≥ calibrated saturation (median per-SV clean p99). No new threshold. On the clean day {ex_clean:.1%} of epochs have ≥1 excluded SV |
| `geometry.information_ratio` | derived | Track C's normalised D-optimality ratio `det(H'H)^(1/(3+k))` on trusted vs full H (`backend/geometry/information.py`). No free parameter |
| `geometry.displacement_bound_m` | derived from a **measured** input | analytic chi-square bound `sigma_UERE * sqrt(T * lambda_max)`; sigma_UERE = **{su['sigma_uere_m']:.3f} m**, the MEASURED clean-day post-fit residual RMS ({su['n_residuals']} residuals, every {su['every_n']}th epoch — `backend/measurement/sigma_uere.py`, cached with provenance in `out/sigma_uere.json`) |
| `geometry.next_best_observation` | derived | rank-one determinant update over visible-but-untrusted groups (CONVERGE identity) |
| `credential_status` | **scripted** (demo.jsonl only) | VALID → PENDING ({n_pending} epochs = T_int {T_INT_EPOCHS} × d {DISCLOSURE_LAG_INTERVALS}) → EXPIRED. **T_int and d are venue-tuned protocol parameters (design.md §9)**: the §9 defaults (10 × 2 = 20 epochs) last {20 / DEMO_RATE_EPS:.1f} s at the {DEMO_RATE_EPS:.0f} epochs/s demo rate; tuned to {T_INT_EPOCHS} × {DISCLOSURE_LAG_INTERVALS} so every credential state holds ≥ {LEGIBLE_EPOCHS / DEMO_RATE_EPS:.0f} s on screen. Stands in for the live TESLA verifier until Track A's T1 lands. {pre_lapse} |
| `_attack` (carryoff/demo) | injector truth log | stage, n_spoofed, range_offset_m, cmc_divergence_m — what the attacker did, never seen by the detector |
| `score_detail` | derived | Track A's breakdown of the composite |

No record carries `_synthetic`.

## Solver layering — which number comes from which solver

Two position solvers and one geometry engine coexist on purpose (18:30
checkpoint item 2); they answer different questions and none is redundant:

1. **Absolute per-constellation WLS** — `backend/rinex/solve.py` (Eric).
   Iono-free dual-frequency code, Saastamoinen troposphere, 5° mask; solves
   all-in-view plus each constellation alone (3.9–12.3 m from the surveyed
   marker). **Feeds:** `features.cross_constellation` — the per-constellation
   position-disagreement and inter-system clock channels are differences of
   ITS solutions. (In `backend.replay` streams it also supplies the believed
   `position`, `position_source: "solution"`; in these demo streams it does
   not — see 2.)
2. **Differential WLS** — `backend/geometry/solve.py` (Track B). Single-band,
   G+E, no atmosphere model, {sanity['horizontal_p50']:.2f} m median horizontal
   vs the surveyed marker; the clean and injected fixes share one satellite
   set and one weight vector so everything unmodelled cancels in the
   difference. **Feeds:** the stream's `position`
   (`position_source: "wls_differential"`), `_truth`,
   `_solution.displacement_m` (the EMPIRICAL displacement on screen), and the
   **sigma_UERE measurement** ({su['sigma_uere_m']:.3f} m clean post-fit
   residual RMS, `backend/measurement/sigma_uere.py`).
3. **Geometry engine** — `backend/geometry/` (Track C). Fisher information
   over the line-of-sight matrix H. **Feeds:** `geometry.information_ratio`
   (normalised D-optimality ratio), `geometry.displacement_bound_m` (the
   ANALYTIC bound, taking sigma_UERE measured from solver 2 as its only
   empirical input), `geometry.next_best_observation`, and `geometry.sky`.

So: the cross-constellation feature comes from solver 1; the believed
position and the measured displacement come from solver 2; the information
ratio and the displacement *bound* come from the geometry engine, calibrated
by solver 2's residuals. The empirical displacement (2) and the analytic
bound (3) are independent derivations that the §10 empirical-vs-bound check
plots on one axis.

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

## Position solution — is the solver right, and what did the attack do to the fix

Solver check, clean day, {sanity['n']} epochs ({sanity['unsolvable']} unsolvable):
clean fix minus surveyed USN8 marker — horizontal p50 **{sanity['horizontal_p50']:.2f} m**,
p95 {sanity['horizontal_p95']:.2f} m, max {sanity['horizontal_max']:.2f} m; vertical p50
{sanity['up_p50']:+.1f} m (the unmodelled ionosphere + troposphere, as expected for a
single-frequency fix; it cancels in the differential). GDOP p50 {sanity['gdop_p50']:.2f},
max {sanity['gdop_max']:.2f}.

Displacement `|position − _truth|` over demo.jsonl: {dprof['summary']}.

**Why the subset matters.** A range offset applied to every tracked GPS
satellite is indistinguishable from a receiver-clock shift and is absorbed
entirely by the GPS clock column: `--target all_gps` displaces the fix by
0.0000 m at 2660 m of range offset. It is a timing attack. The demo uses
`{target_name}` (Track A's `top_n_by_elevation` rule; §7 "walk-off on an SV
subset"), under which the same walk-off moves the fix by the amount above.
The direction is set by the geometry of the spoofed subset, not chosen.

## Injector parameters (design.md §7 carry-off, Track A defaults except the subset)

{summarise(truth)}

power_db 2.0 · walk_off_mps 1.0 · target `{target_name}` resolved at capture and held ·
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
