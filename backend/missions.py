"""Per-mission stream generation for the Track F console (tracks/TRACK_F.md §3).

    python -m backend.missions --mission combat          # -> out/combat.jsonl
    python -m backend.missions --mission all             # all four

Load, noise floor, calibration (WITH the clean-day residual panel, F-0a),
cross-constellation calibration, nav tables and sigma_UERE are computed once
(`load_shared`, the same calls as backend/demo.py main()). Each mission then
runs inject -> differential WLS positions -> score -> credential schedule ->
slice on its own window and writes `out/<name>.jsonl`, `out/<name>_truth.csv`
and `docs/stream_provenance_<name>.md`.

Only the slice (plus a warm-up the detector's trailing windows need, dropped
from the file) is solved and scored, so four missions cost four demo windows,
not four days. The §5 contract is unchanged: no record carries a mission
field; the mission's frame rides on /mission?name= (console/missions).

Mission-specific correction logic (RECON's stationary deduction, CASEVAC's
route-constrained terrain fix) enters through `MissionSpec.augment`, a
post-pass over the records, so each mission's owner edits their own module
and this file stays shared.
"""
from __future__ import annotations

import argparse
import inspect
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

import numpy as np

from backend.correction.emit import CorrectionEmitter
from backend.demo import (CARRIER_RATE_ERROR_MPS, EPOCH_S, OBS, SYSTEMS, distrusted,
                          score_stream, solve_positions, write_atomic)
from backend.detection import (FEATURE_NAMES, OPTIONAL_FEATURE_NAMES, CrossConstellation,
                               Weights, fit, fit_cross)
from backend.geometry.engine import set_sigma_uere
from backend.geometry.solve import NavTables
from backend.injector import (CARRY_OFF, CLEAN, REPEATER_OFFSET, SIMPLISTIC_POSITION,
                              Spoof, inject, summarise, top_n_by_elevation)
from backend.measurement.sigma_uere import load_or_measure
from backend.rinex import ephemeris, noise
from backend.rinex.loader import load_obs
from backend.rinex.solve import residual_panel

ONSET = datetime(2026, 8, 20, 12, 30)      # the team's attack epoch, every stream
OUT = Path("out")
DOCS = Path("docs")
# Detector warm-up before the slice: the longest trailing window in the
# system (features.FeatureConfig.cmc_window and cross.CrossCal.window are
# both 40) plus min_history. Read from the configs, not guessed.
WARMUP_EPOCHS = 40 + 5
# TESLA demo parameters as backend/demo.py: T_int 60 x d 2 -> PENDING 120.
PENDING_EPOCHS = 120


# --------------------------------------------------------------------------- spec

@dataclass(frozen=True)
class MissionSpec:
    name: str
    make_spoof: Callable[..., Spoof]          # (onset, duration_s) -> Spoof
    pre_epochs: int
    attack_epochs: int
    post_epochs: int
    credential: str                           # "lapse" | "renewal" | "valid"
    exclusion: str = "k1"                     # "k1" | "constellation_first"
    alert_limit_m: float = 15.0
    terrain: bool = False                     # Track E channel on (signed map in data/)
    terrain_diag: float | None = None         # SIMULATED sensor confusion diagonal, stated
    terrain_window: int = 1
    onset: datetime = ONSET
    warmup_epochs: int = WARMUP_EPOCHS
    augment: Callable[[list, dict], list] | None = None   # (records, ctx) -> records
    notes: str = ""

    @property
    def slice_epochs(self) -> int:
        return self.pre_epochs + self.attack_epochs + self.post_epochs


# --------------------------------------------------------------------------- credential

def credential_for(spec: MissionSpec, j: int) -> str:
    """Scripted schedule by index in the slice (stands in for TESLA, as demo.py).

    lapse    VALID -> PENDING (120) -> EXPIRED from 120 epochs after the attack
             ends: the beat-4 lapse of the original demo (LOGISTICS).
    renewal  VALID -> PENDING [270, 390) -> VALID: the next interval's MAC
             arrives, the key is disclosed 120 epochs later, the chain verifies.
             The positive direction of the credential coupling (RECON, COMBAT).
    valid    VALID throughout (CASEVAC: the credential beat is not this mission's).
    """
    if spec.credential == "valid":
        return "VALID"
    if spec.credential == "renewal":
        return "PENDING" if 270 <= j < 270 + PENDING_EPOCHS else "VALID"
    post = j - (spec.pre_epochs + spec.attack_epochs)
    if post < 120:
        return "VALID"
    if post < 120 + PENDING_EPOCHS:
        return "PENDING"
    return "EXPIRED"


# --------------------------------------------------------------------------- exclusion rules

def odd_constellation(xc_result: dict | None, cal, level: float | None = None) -> str | None:
    """The constellation that disagrees with the other two, or None.

    On the clock channels (then the position channels) of the streaming
    cross-constellation scorer: constellation S is the odd one out when both
    channels touching S are at or above `level` x their calibrated scale and
    the channel not touching S is below. `level` defaults to the calibration's
    own feature saturation (clean-day p99.9 of the max-over-channels statistic,
    the level at which the feature reads 1.0) -- no new number. Verified in
    TRACK_F_RECON.md: 0 fires on 2,880 clean epochs; "G" on 90/90 meaconing
    epochs. Needs all three pairs present (three solvable constellations).
    """
    if not xc_result:
        return None
    # Only evaluated when the feature itself is saturated (its clean-day
    # p99.9 level): the pattern test below then attributes an alarm the
    # detector already raised, it never raises one.
    if xc_result.get("value", 0.0) < 1.0:
        return None
    zs = xc_result.get("channels") or {}
    hot_level = 1.0            # at or beyond the channel's clean-day p99 scale
    quiet_level = 0.5          # clearly inside it
    for kind in ("clk", "pos"):
        r = {}
        for pair in ("GE", "GC", "EC"):
            name = f"{kind}_{pair}_m"
            if name not in zs or name not in cal.scale:
                r = None
                break
            r[pair] = abs(zs[name]) / cal.scale[name]
        if r is None:
            continue
        for sysc, touching, other in (("G", ("GE", "GC"), "EC"),
                                      ("E", ("GE", "EC"), "GC"),
                                      ("C", ("GC", "EC"), "GE")):
            if all(r[p] >= hot_level for p in touching) and r[other] < quiet_level:
                return sysc
    return None


def make_exclusion(kind: str, xc: CrossConstellation):
    """-> exclude(per_sv, cal, xc_result, ep) for demo.score_stream."""
    if kind == "k1":
        return lambda per_sv, cal, xc_result, ep: distrusted(per_sv, cal)
    if kind == "constellation_first":
        from backend.detection import FeatureConfig
        hold_epochs = FeatureConfig().cmc_window     # the residual feature's trailing window (40)
        state = {"hold": 0}

        def rule(per_sv, cal, xc_result, ep):
            # A whole-constellation disagreement is attributed to that
            # constellation and nothing else: the per-SV residual rule would
            # cascade onto authentic satellites (TRACK_F.md F-0c). Otherwise k=1,
            # EXCEPT for one trailing window after a constellation exclusion
            # clears: the residual feature's baseline then still holds the
            # attack's residuals, so its per-SV z-scores are not evidence about
            # the satellites (measured: 27 of 45 excluded on clean data for the
            # 30 epochs after the repeater stopped). Window length is the
            # feature's own, read from FeatureConfig, not chosen here.
            odd = odd_constellation(xc_result, xc.cal)
            if odd:
                state["hold"] = hold_epochs
                return sorted(sv for sv in ep.df.index if sv.startswith(odd))
            if state["hold"] > 0:
                state["hold"] -= 1
                return []
            return distrusted(per_sv, cal)
        return rule
    raise ValueError(f"unknown exclusion rule {kind!r}")


# --------------------------------------------------------------------------- shared state

@dataclass
class Shared:
    clean: list
    floor: object
    cal: object
    nav_xc: object
    xc: CrossConstellation
    nav: NavTables
    sigma: dict
    onset_index: dict = field(default_factory=dict)

    def onset_i(self, t: datetime) -> int:
        if t not in self.onset_index:
            self.onset_index[t] = next(i for i, ep in enumerate(self.clean) if ep.time >= t)
        return self.onset_index[t]


def load_shared(obs: str = OBS, out: Path = OUT) -> Shared:
    print("load", flush=True)
    clean = load_obs(obs, systems=SYSTEMS)
    floor = noise.measure(clean)
    print(" ", floor)
    nav_xc = ephemeris.load_nav()
    cal = fit(clean, floor, resid_panel=residual_panel(clean, nav_xc))     # F-0a
    print(" ", cal)
    xc = CrossConstellation(fit_cross(clean, nav_xc), nav_xc)
    print(" ", xc.cal)
    nav = NavTables.load()
    su = load_or_measure(cache=out / "sigma_uere.json", epochs=clean, nav=nav)
    set_sigma_uere(su["sigma_uere_m"])
    print(f"  sigma_UERE {su['sigma_uere_m']:.3f} m", flush=True)
    return Shared(clean, floor, cal, nav_xc, xc, nav, su)


# --------------------------------------------------------------------------- terrain (Track E)

def make_terrain(spec: MissionSpec, sh: Shared, positions: dict,
                 map_path: str = "data/terrain_usn8.npz", pub: str = "data/terrain_map_pub.pem"):
    """Track E channel for a mission: signed map, SIMULATED sensor with the
    mission's stated confusion diagonal, calibrated on the slice's clean fixes
    (the demo calibrates on the whole day; the slice is what this stream has)."""
    from backend.terrain.channel import TerrainChannel
    from backend.terrain.sensor import ConfusionSensor, confusion_from_diag
    from backend.terrain.signing import load_verified
    rmap = load_verified(map_path, pub)          # raises: an unsigned map is not consulted
    sensor = ConfusionSensor(confusion_from_diag(len(rmap.classes), spec.terrain_diag),
                             rmap.classes, seed=20260820)
    terrain = TerrainChannel(rmap, sensor, sigma_uere_m=sh.sigma["sigma_uere_m"],
                             window_epochs=spec.terrain_window)
    stats = terrain.calibrate(
        (s["truth"].lla["lat"], s["truth"].lla["lon"], s["truth"].dop["H"])
        for s in positions.values() if s is not None)
    print(f"  terrain: map {rmap.map_id} signed={rmap.signed} diag {spec.terrain_diag} -> {stats}")
    return terrain, rmap


# --------------------------------------------------------------------------- run one mission

def run_mission(spec: MissionSpec, sh: Shared, out: Path = OUT, docs: Path = DOCS) -> Path:
    onset_i = sh.onset_i(spec.onset)
    lo = onset_i - spec.pre_epochs - spec.warmup_epochs
    hi = onset_i + spec.attack_epochs + spec.post_epochs
    assert lo >= 0 and hi <= len(sh.clean), (spec.name, lo, hi)
    clean_slice = sh.clean[lo:hi]
    print(f"\n== {spec.name}: epochs [{lo}, {hi}) = {hi - lo} incl. {spec.warmup_epochs} warm-up; "
          f"onset {spec.onset:%H:%M}", flush=True)

    # Spoof.stage() treats dt == duration_s as still under attack, so trim by a
    # second (as demo.py) to make the attack exactly attack_epochs long.
    spoof = spec.make_spoof(spec.onset, spec.attack_epochs * EPOCH_S - 1)
    injected, truth = inject(clean_slice, spoof, sh.floor)
    print(" ", summarise(truth), flush=True)

    print("  positions (WLS, G+E, differential clean vs injected)", flush=True)
    positions, sanity = solve_positions(clean_slice, injected, sh.nav)
    print(f"  clean fix vs survey: horizontal p50 {sanity['horizontal_p50']:.2f} m "
          f"max {sanity['horizontal_max']:.2f} m; unsolvable {sanity['unsolvable']}")

    terrain = rmap = None
    if spec.terrain:
        terrain, rmap = make_terrain(spec, sh, positions)
    weights = Weights.equal(FEATURE_NAMES + OPTIONAL_FEATURE_NAMES) if terrain else Weights()
    kw = {"extra_checks": terrain.gate_check} if terrain else {}
    if "alert_limit_m" in inspect.signature(CorrectionEmitter).parameters:
        kw["alert_limit_m"] = spec.alert_limit_m
    corrector = CorrectionEmitter(sh.nav, **kw)
    exclude = make_exclusion(spec.exclusion, sh.xc)

    sh.xc.reset()
    corrector.reset()
    if terrain:
        terrain.reset()
    recs = score_stream(injected, sh.cal, truth=truth, label=spec.name, positions=positions,
                        xc=sh.xc, nav_xc=sh.nav_xc, corrector=corrector, terrain=terrain,
                        weights=weights, exclude=exclude)
    recs = [dict(r) for r in recs[spec.warmup_epochs:]]          # drop the warm-up
    for j, r in enumerate(recs):
        r["credential_status"] = credential_for(spec, j)
    ctx = {"spec": spec, "shared": sh, "positions": positions, "truth": truth,
           "terrain": terrain, "rmap": rmap, "onset_index": spec.pre_epochs}
    if spec.augment is not None:
        recs = spec.augment(recs, ctx)

    out.mkdir(parents=True, exist_ok=True)
    path = write_atomic(recs, out / f"{spec.name}.jsonl")
    truth.iloc[spec.warmup_epochs:].to_csv(out / f"{spec.name}_truth.csv")
    _write_provenance(spec, recs, truth, sanity, spoof, docs / f"stream_provenance_{spec.name}.md")
    print(f"  wrote {path} ({len(recs)} epochs)")
    return path


def _profile(recs: list, pre: int, attack: int) -> dict:
    d = np.array([(r.get("_solution") or {}).get("displacement_m", np.nan) for r in recs], dtype=float)
    conf = np.array([r.get("confidence", np.nan) for r in recs], dtype=float)
    excl = np.array([len((r.get("geometry") or {}).get("excluded_sv") or []) for r in recs])
    ok = np.array([bool(((r.get("geometry") or {}).get("correction") or {}).get("correction_ok")) for r in recs])
    a0, a1 = pre, pre + attack
    atk = d[a0:a1]
    return {
        "lead_in_max_m": float(np.nanmax(d[:a0])) if a0 else 0.0,
        "attack_first_over_1m": next((int(a0 + i) for i, v in enumerate(atk) if v > 1.0), None),
        "attack_peak_m": float(np.nanmax(atk)) if np.isfinite(atk).any() else None,
        "attack_end_m": float(atk[-1]) if len(atk) else None,
        "post_max_m": float(np.nanmax(d[a1:])) if len(d) > a1 else None,
        "conf_lead_in_min": float(np.nanmin(conf[:a0])) if a0 else None,
        "conf_attack_p50": float(np.nanmedian(conf[a0:a1])),
        "conf_attack_p95": float(np.nanpercentile(conf[a0:a1], 95)),
        "excluded_attack_max": int(excl[a0:a1].max()),
        "excluded_attack_median": float(np.median(excl[a0:a1])),
        "correction_ok_attack": f"{int(ok[a0:a1].sum())}/{a1 - a0}",
        "correction_ok_post": f"{int(ok[a1:].sum())}/{len(ok) - a1}",
    }


def _write_provenance(spec, recs, truth, sanity, spoof, path: Path) -> None:
    prof = _profile(recs, spec.pre_epochs, spec.attack_epochs)
    creds = [r["credential_status"] for r in recs]
    t0, t1 = recs[0]["timestamp"], recs[-1]["timestamp"]
    active = truth[truth["stage"] != CLEAN]
    md = f"""# Stream provenance — out/{spec.name}.jsonl (Track F mission stream)

Generated by `python -m backend.missions --mission {spec.name}`. Same field
classification as docs/stream_provenance.md (features measured/injected,
position solved by differential WLS, `_truth` the clean fix, credential
SCRIPTED, `_attack` the injector's truth log). Differences for this stream:

- **Window:** {len(recs)} epochs, {t0} → {t1}: {spec.pre_epochs} clean lead-in,
  {spec.attack_epochs} attack, {spec.post_epochs} post. Scored after a
  {spec.warmup_epochs}-epoch warm-up (dropped) so the trailing baselines are real.
- **Attack:** {summarise(truth)}. Scenario `{spoof.name}`, power {spoof.power_db} dB,
  walk {spoof.walk_off_mps} m/s ({spoof.walk_mode}), step {spoof.step_displacement_m} m,
  common bias {spoof.common_bias_m} m, carrier_rate_error {spoof.carrier_rate_error} m/s,
  bearing {spoof.bearing_deg}. {spec.notes}
- **Exclusion rule:** `{spec.exclusion}`{" — a whole-constellation disagreement on the cross-constellation clock/position channels (at the feature saturation) excludes exactly that constellation; otherwise the k=1 per-SV residual rule" if spec.exclusion == "constellation_first" else " — per-SV post-fit residual |z| ≥ calibrated saturation (demo.py)"}.
- **Correction gate alert limit:** {spec.alert_limit_m} m.
- **Credential schedule:** `{spec.credential}` — VALID {creds.count('VALID')} · PENDING {creds.count('PENDING')} · EXPIRED {creds.count('EXPIRED')} epochs (scripted; T_int 60 × d 2).
- **Terrain channel (Track E):** {"ON — SIMULATED sensor, confusion diagonal " + str(spec.terrain_diag) + " (stated, swept parameter), window " + str(spec.terrain_window) + "; signed OSM pre-map; calibrated on this slice's clean fixes" if spec.terrain else "off"}.
- **Solver sanity on the slice:** clean fix vs survey horizontal p50 {sanity['horizontal_p50']:.2f} m, max {sanity['horizontal_max']:.2f} m, unsolvable {sanity['unsolvable']}.

## Measured on this stream

| quantity | value |
|---|---|
| lead-in max displacement | {prof['lead_in_max_m']:.4f} m |
| first attack epoch with displacement > 1 m | {prof['attack_first_over_1m']} (slice index; onset {spec.pre_epochs}) |
| peak attack displacement | {prof['attack_peak_m']} m |
| displacement at attack end | {prof['attack_end_m']} m |
| post-attack max displacement | {prof['post_max_m']} m |
| confidence lead-in min | {prof['conf_lead_in_min']} |
| confidence attack p50 / p95 | {prof['conf_attack_p50']:.3f} / {prof['conf_attack_p95']:.3f} |
| excluded satellites during attack (median / max) | {prof['excluded_attack_median']} / {prof['excluded_attack_max']} |
| correction_ok during attack / after | {prof['correction_ok_attack']} / {prof['correction_ok_post']} |

Attack epochs by stage: {active['stage'].value_counts().to_dict()}.
State timeline: `python -m console.replay out/{spec.name}.jsonl`.
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(md)


# --------------------------------------------------------------------------- the four

def _logistics(onset, duration_s):
    return CARRY_OFF(onset=onset, carrier_rate_error=CARRIER_RATE_ERROR_MPS, bearing_deg=90.0,
                     walk_off_mps=0.2, target_svs=top_n_by_elevation(6), duration_s=duration_s)

def _recon(onset, duration_s):
    return REPEATER_OFFSET(onset=onset, bearing_deg=90.0, standoff_m=300.0, duration_s=duration_s)

def _casevac(onset, duration_s):
    return CARRY_OFF(onset=onset, carrier_rate_error=0.0, bearing_deg=135.0, walk_off_mps=1.0,
                     target_svs=top_n_by_elevation(6), duration_s=duration_s)

def _combat(onset, duration_s):
    return SIMPLISTIC_POSITION(onset=onset, bearing_deg=90.0, target_svs="all_gps",
                               step_m=250.0, duration_s=duration_s)


REGISTRY: dict[str, MissionSpec] = {
    "logistics": MissionSpec(
        "logistics", _logistics, pre_epochs=60, attack_epochs=90, post_epochs=360,
        credential="lapse", exclusion="k1", alert_limit_m=15.0,
        notes="0.2 m/s walk (venue-tuned inside §7's 'tune at the venue') so the corridor "
              "crossing and the corrected-fix window are legible at 30 s epochs; the 1 m/s "
              "reference is the original demo (TRACK_F_LOGISTICS.md)."),
    "recon": MissionSpec(
        "recon", _recon, pre_epochs=60, attack_epochs=30, post_epochs=420,
        credential="renewal", exclusion="constellation_first", alert_limit_m=100.0,
        notes="Repeater at a 300 m standoff east (stated, MEACONING's figure); 30 attack epochs "
              "because a 90-epoch all-GPS step polluted feature 4 for the whole tail "
              "(TRACK_F_RECON.md); the scout's stationary deduction is added by augment()."),
    "casevac": MissionSpec(
        "casevac", _casevac, pre_epochs=60, attack_epochs=60, post_epochs=180,
        credential="valid", exclusion="k1", alert_limit_m=15.0,
        terrain=True, terrain_diag=0.85, terrain_window=1,
        notes="Carrier-coherent along-track walk toward the CCP; Track E terrain channel on "
              "with a SIMULATED sensor (diag 0.85, stated); the route-constrained terrain fix "
              "is added by augment() (TRACK_F_CASEVAC.md)."),
    "combat": MissionSpec(
        "combat", _combat, pre_epochs=60, attack_epochs=20, post_epochs=430,
        credential="renewal", exclusion="constellation_first", alert_limit_m=15.0,
        notes="Crude 15 dB step on all GPS, 250 m commanded (stated); 20 attack epochs so the "
              "baselines recover within the slice (TRACK_F_COMBAT.md)."),
}


def register_augment(name: str, fn: Callable[[list, dict], list]) -> None:
    """Mission owners attach their post-pass (see the module docstring)."""
    from dataclasses import replace
    REGISTRY[name] = replace(REGISTRY[name], augment=fn)


def _load_augments() -> None:
    """Import optional per-mission modules that call register_augment()."""
    import importlib
    for mod in ("backend.missions_recon", "backend.missions_casevac"):
        try:
            importlib.import_module(mod)
        except ModuleNotFoundError as e:
            if e.name != mod:
                raise


# --------------------------------------------------------------------------- cli

def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mission", required=True, help="one of %s or all" % sorted(REGISTRY))
    ap.add_argument("--obs", default=OBS)
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args(argv)
    _load_augments()
    names = sorted(REGISTRY) if args.mission == "all" else [args.mission]
    for n in names:
        if n not in REGISTRY:
            raise SystemExit(f"unknown mission {n!r}; one of {sorted(REGISTRY)}")
    sh = load_shared(args.obs, Path(args.out))
    for n in names:
        run_mission(REGISTRY[n], sh, Path(args.out))
    # Report the arbitrated timeline the console will show (console.replay is
    # the reference implementation; imported here for the report only).
    from console.replay import arbitrate, transitions
    for n in names:
        eps = [json.loads(l) for l in open(Path(args.out) / f"{n}.jsonl") if l.strip()]
        print(f"\n{n}: state timeline")
        for i, prev, new, reason in transitions(arbitrate(eps)):
            print(f"  epoch {i:4d}  {prev:11s} -> {new:11s}  ({reason})")


if __name__ == "__main__":
    main()
