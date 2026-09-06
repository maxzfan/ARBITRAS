"""E5 — every terrain number as a curve over the sensor model (TRACK_E.md
"Measurement plan"). Rescoring the shipped streams post hoc is exact for the
feature, the block and the composite (all pure functions of position, HDOP,
the map and the sensor draw), so the sweeps never re-run the solver. The
gate check needs hysteresis and is exercised by `backend.demo --terrain-map`.

    python -m backend.terrain.validate --map data/terrain_usn8.npz \\
        --pub data/terrain_map_pub.pem

Writes out/terrain_validation.json and docs/plots/terrain_{map,tta_polar,
sensor_quality,carryoff}.png. Every caption says SIMULATED.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from backend.detection import FEATURE_NAMES, OPTIONAL_FEATURE_NAMES, Weights, score
from backend.terrain.channel import TerrainChannel, apply_bound
from backend.terrain.rastermap import UNKNOWN, RasterMap
from backend.terrain.sensor import ConfusionSensor, confusion_from_diag

BEARINGS = tuple(range(0, 360, 45))
PLOTS = Path("docs/plots")
ATTACK_STAGES = ("CAPTURE", "LOCKED", "WALK")
SCORE_KEYS = ("feature_score", "geometry_deficit", "beta", "geometry_available",
              "weights_tuned", "weights", "weight_sensitive_fraction", "features_scored")


def load_jsonl(path) -> list[dict]:
    with open(path) as fh:
        return [json.loads(line) for line in fh]


def predicted_tta(rmap: RasterMap, walk_mps: float, epoch_s: float, window: int) -> dict:
    """TRACK_E.md step 8: r_T(theta) from the map at the antenna, and the
    first epoch this channel could fire at a 1 m/s walk."""
    out = {}
    for b in BEARINGS:
        r = rmap.boundary_distance(0.0, 0.0, float(b))
        out[b] = {"r_T_m": r, "epochs": None if r is None else r / (walk_mps * epoch_s) + window}
    return out


def rescore(records: list[dict], channel: TerrainChannel, weights: Weights) -> list[dict]:
    """Apply the channel to an existing stream and recompute the composite."""
    channel.reset()
    out = []
    for r in records:
        r = json.loads(json.dumps(r))                       # deep copy, no aliasing
        sol = r.get("_solution") or {}
        solved = r.get("position_source") != "surveyed" and "hdop" in sol
        t = channel.step(r["position"] if solved else None, sol.get("hdop") if solved else None)
        feats = {k: v for k, v in r["features"].items() if k != "by_sv"}
        if t["feature"] is not None:
            feats["terrain_mismatch"] = t["feature"]
        geom = apply_bound(r["geometry"], t["block"])
        s = score(feats, geom, weights)
        r["features"].update({k: round(v, 4) for k, v in feats.items()})
        r["geometry"] = geom
        r["terrain"] = t["block"]
        r["confidence"] = round(s["confidence"], 4)
        r["score_detail"] = {k: s[k] for k in SCORE_KEYS}
        out.append(r)
    return out


def calibrated_channel(rmap, clean, diag, window, sigma_uere, seed=20260820) -> TerrainChannel:
    sensor = ConfusionSensor(confusion_from_diag(len(rmap.classes), diag), rmap.classes, seed=seed)
    ch = TerrainChannel(rmap, sensor, sigma_uere_m=sigma_uere, window_epochs=window)
    ch.calibrate((r["_truth"]["lat"], r["_truth"]["lon"], r["_solution"]["hdop"])
                 for r in clean if "hdop" in (r.get("_solution") or {}))
    return ch


def _states(records) -> list[str]:
    from console.replay import arbitrate                # measurement-side import (precedent:
    return [d.state.name for d in arbitrate(records)]   # backend/measurement/displacement.py)


def attack_indices(records) -> list[int]:
    return [i for i, r in enumerate(records)
            if (r.get("_attack") or {}).get("stage") in ATTACK_STAGES]


FIRE_LEVEL = 0.5     # a windowed mismatch at or above half scale counts as a terrain fire


def first_fire(records, attack, level: float = FIRE_LEVEL):
    return next((i for i in attack
                 if records[i]["features"].get("terrain_mismatch", 0.0) >= level), None)


def dprime(clean_records, attack_records, attack_idx) -> float:
    """Separation of the emitted terrain feature, clean day vs attack window:
    (mean_a - mean_c) / sqrt((var_a + var_c) / 2) — the README's own d' form,
    weight-independent. The number the threshold session weighs the feature by."""
    c = np.array([r["features"].get("terrain_mismatch", np.nan) for r in clean_records], float)
    a = np.array([attack_records[i]["features"].get("terrain_mismatch", np.nan) for i in attack_idx], float)
    c, a = c[np.isfinite(c)], a[np.isfinite(a)]
    pooled = np.sqrt((a.var() + c.var()) / 2.0)
    return float((a.mean() - c.mean()) / pooled) if pooled > 0 else float("inf")


def scored_fraction(records, idx=None) -> float:
    """Share of the given epochs on which the channel made a claim at all —
    the believed position was solved, on the map, and on labelled ground."""
    idx = range(len(records)) if idx is None else idx
    return float(np.mean(["terrain_mismatch" in records[i]["features"] for i in idx]))


def fire_fraction(records, idx=None, level: float = FIRE_LEVEL) -> float:
    """Share of the SCORED epochs on which the terrain feature is at or above
    `level` — the channel's OWN alarm rate, independent of the weights. Epochs
    without a claim are not in the denominator; scored_fraction says how many."""
    idx = range(len(records)) if idx is None else idx
    vals = [records[i]["features"]["terrain_mismatch"] for i in idx
            if "terrain_mismatch" in records[i]["features"]]
    return float(np.mean([v >= level for v in vals])) if vals else float("nan")


def sensor_quality_curve(clean, carry, rmap, diags, windows, sigma_uere) -> list[dict]:
    from console.arbiter.states import THRESHOLDS, TrustState
    nominal = THRESHOLDS[TrustState.NOMINAL]
    w = Weights.equal(FEATURE_NAMES + OPTIONAL_FEATURE_NAMES)
    attack = attack_indices(carry)
    rows = []
    for window in windows:
        for diag in diags:
            ch = calibrated_channel(rmap, clean, diag, window, sigma_uere)
            c = rescore(clean, ch, w)
            k = rescore(carry, ch, w)
            raw_fsr = float(np.mean([r["confidence"] < nominal for r in c]))
            arb_fsr = float(np.mean([s != "NOMINAL" for s in _states(c)]))
            det = float(np.mean([k[i]["confidence"] < nominal for i in attack]))
            first = first_fire(k, attack)
            rows.append({"diag": diag, "window": window, "saturation": ch.saturation,
                         "floor": ch.floor, "fsr_raw": raw_fsr, "fsr_arbitrated": arb_fsr,
                         "attack_detection_fraction": det,
                         # the channel's own alarm rates, weight-independent
                         "terrain_clean_scored_fraction": scored_fraction(c),
                         "terrain_attack_scored_fraction": scored_fraction(k, attack),
                         "terrain_clean_fire_fraction": fire_fraction(c),
                         "terrain_attack_fire_fraction": fire_fraction(k, attack),
                         "terrain_dprime": dprime(c, k, attack),
                         "terrain_first_fire_epoch": None if first is None else first - attack[0],
                         "bound_source_terrain_fraction": float(np.mean(
                             [r["geometry"].get("bound_source") == "terrain" for r in k]))})
            print(f"  diag {diag:.2f} W {window}: composite FSR raw {raw_fsr:.4f} arb {arb_fsr:.4f} "
                  f"det {det:.3f} | terrain fires clean {rows[-1]['terrain_clean_fire_fraction']:.4f} "
                  f"attack {rows[-1]['terrain_attack_fire_fraction']:.3f} "
                  f"(scored {rows[-1]['terrain_attack_scored_fraction']:.2f}) "
                  f"d' {rows[-1]['terrain_dprime']:.2f} "
                  f"first {rows[-1]['terrain_first_fire_epoch']}", flush=True)
    return rows


def integrity_check(rescored, key: str = "displacement_bound_m") -> dict:
    """§10-style: empirical |D| <= bound at every arbitrated-NOMINAL epoch.
    `key` selects the combined bound (default) or `residual_bound_m`, so the
    terrain contribution can be separated from the pre-existing state."""
    states = _states(rescored)
    viol, n = [], 0
    for r, s in zip(rescored, states):
        b = r["geometry"].get(key)
        d = (r.get("_solution") or {}).get("displacement_m")
        if s == "NOMINAL" and b is not None and d is not None:
            n += 1
            if d > b:
                viol.append((r["timestamp"], d, b))
    return {"bound": key, "n_checked": n, "n_violations": len(viol),
            "violations": viol[:20], "ok": not viol and n > 0}


# ------------------------------------------------------------------- plots

def _plt():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    return plt


def plot_map(rmap: RasterMap, tracks: dict[str, np.ndarray], out: Path) -> None:
    plt = _plt()
    fig, ax = plt.subplots(figsize=(7.5, 7))
    g = np.ma.masked_equal(rmap.grid, UNKNOWN)
    e0, n0 = rmap.origin_enu
    ext = (e0, e0 + rmap.grid.shape[1] * rmap.cell_m, n0, n0 + rmap.grid.shape[0] * rmap.cell_m)
    im = ax.imshow(g, origin="lower", extent=ext, cmap="tab10", vmin=-0.5, vmax=9.5,
                   interpolation="nearest")
    cb = fig.colorbar(im, ax=ax, ticks=range(len(rmap.classes)), shrink=0.7)
    cb.ax.set_yticklabels(rmap.classes)
    for name, xy in tracks.items():
        ax.plot(xy[:, 0], xy[:, 1], lw=1.5, color="k", label=name)
    ax.plot(0, 0, "w+", ms=14, mew=3)
    ax.plot(0, 0, "k+", ms=12, mew=1.5, label="antenna (true position)")
    ax.set_xlabel("east, m"); ax.set_ylabel("north, m")
    ax.set_title(f"Pre-map {rmap.map_id}\nwhite = unlabelled; the map is real, the sensor is SIMULATED",
                 fontsize=10)
    ax.legend(loc="lower left", fontsize=8)
    fig.tight_layout(); out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=150); plt.close(fig)


def plot_tta_polar(pred: dict, measured: dict | None, out: Path) -> None:
    plt = _plt()
    fig = plt.figure(figsize=(6.5, 6.5)); ax = fig.add_subplot(111, projection="polar")
    ax.set_theta_zero_location("N"); ax.set_theta_direction(-1)
    th = [np.radians(b) for b in pred]; r = [pred[b]["r_T_m"] or np.nan for b in pred]
    ax.plot(th + th[:1], r + r[:1], "o-", label="predicted r_T from the map (m)")
    if measured:
        ax.plot([np.radians(measured["bearing_deg"])], [measured["r_m"]], "r*", ms=14,
                label=f"measured first fire on the carry-off ({measured['epochs']} epochs in)")
    ax.set_title("Terrain boundary distance by bearing — SIMULATED sensor", pad=18, fontsize=10)
    ax.legend(loc="lower right", fontsize=8, bbox_to_anchor=(1.2, -0.12))
    fig.tight_layout(); fig.savefig(out, dpi=150); plt.close(fig)


def plot_sensor_quality(rows: list[dict], out: Path) -> None:
    plt = _plt()
    fig, axes = plt.subplots(1, 3, figsize=(15, 4))
    for window in sorted({r["window"] for r in rows}):
        sub = sorted([r for r in rows if r["window"] == window], key=lambda r: r["diag"])
        d = [r["diag"] for r in sub]
        axes[0].plot(d, [r["terrain_clean_fire_fraction"] for r in sub], "o-", label=f"W={window}")
        axes[1].plot(d, [r["terrain_attack_fire_fraction"] for r in sub], "o-", label=f"W={window}")
        axes[2].plot(d, [r["terrain_dprime"] for r in sub], "o-", label=f"W={window}")
    axes[0].set_ylabel(f"clean-day fire rate (feature ≥ {FIRE_LEVEL})")
    axes[1].set_ylabel(f"attack-window fire rate (feature ≥ {FIRE_LEVEL}), scored epochs")
    axes[2].set_ylabel("d′, clean day vs attack window")
    axes[0].set_title("own false-alarm rate", fontsize=9)
    axes[1].set_title("own detection rate", fontsize=9)
    axes[2].set_title("separation of the emitted feature", fontsize=9)
    for ax in axes:
        ax.set_xlabel("confusion diagonal of the SIMULATED sensor"); ax.grid(alpha=0.3)
        ax.legend(fontsize=7)
    fig.suptitle("Sensor quality sweep — the terrain channel alone, rescored over the shipped "
                 "streams; SIMULATED sensor, fire level = half the clean-run p99", fontsize=10)
    fig.tight_layout(); fig.savefig(out, dpi=150); plt.close(fig)


def plot_carryoff(rescored: list[dict], lo: int, hi: int, out: Path, label: str) -> None:
    plt = _plt()
    seg = rescored[lo:hi]
    x = np.arange(len(seg))
    fig, ax1 = plt.subplots(figsize=(11, 4))
    ax1.plot(x, [(r.get("_solution") or {}).get("displacement_m", np.nan) for r in seg],
             color="tab:red", label="|believed − true| (measured)")
    ax1.plot(x, [r["geometry"].get("residual_bound_m") or np.nan for r in seg],
             color="tab:blue", label="residual bound")
    ax1.plot(x, [r["geometry"]["displacement_bound_m"] or np.nan for r in seg],
             color="tab:green", ls="--", label="combined bound (min)")
    ax1.set_ylabel("metres"); ax1.set_xlabel("epochs from window start"); ax1.grid(alpha=0.3)
    ax2 = ax1.twinx()
    ax2.plot(x, [r["features"].get("terrain_mismatch", np.nan) for r in seg],
             color="tab:purple", alpha=0.7, label="terrain_mismatch (SIMULATED)")
    ax2.set_ylim(0, 1.05); ax2.set_ylabel("feature [0,1]")
    h1, l1 = ax1.get_legend_handles_labels(); h2, l2 = ax2.get_legend_handles_labels()
    ax1.legend(h1 + h2, l1 + l2, loc="upper left", fontsize=8)
    ax1.set_title(f"Carry-off with the terrain channel — {label}", fontsize=10)
    fig.tight_layout(); fig.savefig(out, dpi=150); plt.close(fig)


# ------------------------------------------------------------------- main

def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--map", required=True)
    ap.add_argument("--pub", default="data/terrain_map_pub.pem")
    ap.add_argument("--clean", default="out/clean.jsonl")
    ap.add_argument("--carryoff", default="out/carryoff.jsonl")
    ap.add_argument("--sigma-uere", default="out/sigma_uere.json")
    ap.add_argument("--diags", default="0.6,0.7,0.8,0.9,0.95,0.99")
    ap.add_argument("--windows", default="1,4,8")
    ap.add_argument("--headline-diag", type=float, default=None,
                    help="diag for the timeline/polar plots; defaults to the median of --diags")
    ap.add_argument("--headline-window", type=int, default=1)
    ap.add_argument("--out", default="out/terrain_validation.json")
    args = ap.parse_args(argv)

    from backend.terrain.signing import load_verified
    rmap = load_verified(args.map, args.pub)
    clean, carry = load_jsonl(args.clean), load_jsonl(args.carryoff)
    sigma = json.loads(Path(args.sigma_uere).read_text())["sigma_uere_m"]
    diags = [float(x) for x in args.diags.split(",")]
    windows = [int(x) for x in args.windows.split(",")]
    hd = args.headline_diag if args.headline_diag is not None else float(np.median(diags))

    print("1. map statistics at the antenna")
    pred = predicted_tta(rmap, walk_mps=1.0, epoch_s=30.0, window=args.headline_window)
    extent = rmap.consistent_extent_m(0.0, 0.0)
    nearest = rmap.nearest_boundary(0.0, 0.0)
    print(f"   extent {extent}  nearest {nearest}  unlabelled {np.mean(rmap.grid == UNKNOWN):.1%}")
    for b in BEARINGS:
        print(f"   bearing {b:3d}: r_T {pred[b]['r_T_m']}  predicted epochs {pred[b]['epochs']}")

    print("2. sensor-quality sweep (rescoring the shipped streams)")
    rows = sensor_quality_curve(clean, carry, rmap, diags, windows, sigma)

    print(f"3. headline run: diag {hd} W {args.headline_window}")
    ch = calibrated_channel(rmap, clean, hd, args.headline_window, sigma)
    w = Weights.equal(FEATURE_NAMES + OPTIONAL_FEATURE_NAMES)
    k = rescore(carry, ch, w)
    chk = integrity_check(k)
    chk_res = integrity_check(k, key="residual_bound_m")
    for c in (chk_res, chk):
        print(f"   empirical <= {c['bound']} at arbitrated-NOMINAL epochs: "
              f"{c['n_checked'] - c['n_violations']}/{c['n_checked']} — "
              f"{'PASS' if c['ok'] else 'FAIL'}")
    for t, d, b in chk["violations"][:5]:
        print(f"     {t}: empirical {d:.2f} m > combined bound {b:.2f} m")
    attack = attack_indices(k)
    first = first_fire(k, attack)
    measured = None
    if first is not None:
        e, n = rmap.enu_from_lla(k[first]["position"]["lat"], k[first]["position"]["lon"])
        measured = {"epochs": first - attack[0], "r_m": float(np.hypot(e, n)),
                    "bearing_deg": float(np.degrees(np.arctan2(e, n)) % 360)}
        print(f"   terrain first fire: epoch {measured['epochs']} of the attack, "
              f"{measured['r_m']:.1f} m at bearing {measured['bearing_deg']:.0f}")
    track = np.array([rmap.enu_from_lla(r["position"]["lat"], r["position"]["lon"])
                      for r in k[attack[0]:attack[-1] + 1]])

    print("4. plots")
    PLOTS.mkdir(parents=True, exist_ok=True)
    plot_map(rmap, {"believed track under carry-off (measured)": track}, PLOTS / "terrain_map.png")
    plot_tta_polar(pred, measured, PLOTS / "terrain_tta_polar.png")
    plot_sensor_quality(rows, PLOTS / "terrain_sensor_quality.png")
    plot_carryoff(k, max(attack[0] - 30, 0), min(attack[-1] + 60, len(k)),
                  PLOTS / "terrain_carryoff.png",
                  f"SIMULATED sensor, confusion diagonal {hd}, W {args.headline_window}")

    result = {"map": {"id": rmap.map_id, "checksum": rmap.checksum(), "cell_m": rmap.cell_m,
                      "classes": rmap.classes, "extent_at_antenna_m": extent,
                      "nearest_boundary": nearest,
                      "unlabelled_fraction": float(np.mean(rmap.grid == UNKNOWN)),
                      "antenna_class": rmap.classes[rmap.class_at(0.0, 0.0)]},
              "predicted_tta": {str(b): v for b, v in pred.items()},
              "sensor_quality": rows,
              "headline": {"diag": hd, "window": args.headline_window,
                           "integrity_check_combined": chk,
                           "integrity_check_residual_only": chk_res,
                           "measured_first_fire": measured,
                           "terrain_clean_fire_fraction": fire_fraction(rescore(clean, ch, w)),
                           "terrain_attack_fire_fraction": fire_fraction(k, attack),
                           "terrain_attack_scored_fraction": scored_fraction(k, attack),
                           "terrain_dprime": dprime(rescore(clean, ch, w), k, attack)},
              "sigma_uere_m": sigma,
              "sensor": "SIMULATED (confusion matrix), not for the submission video"}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(result, indent=2, default=str))
    print(f"wrote {args.out} and {PLOTS}/terrain_*.png")


if __name__ == "__main__":
    main()
