"""Compose and write the design.md §5 interface contract.

    {"timestamp", "confidence", "credential_status", "position",
     "features", "geometry", "satellites_tracked"}

**This is the architectural boundary.** Track A fills `confidence`, `features`
and `satellites_tracked`. `geometry` is Track C's block and `credential_status`
is the credential layer's; both arrive as arguments and are passed through
untouched. Nothing here computes them, and nothing here knows a state name.

`position` is what the receiver *believes* (§5) — under attack, the spoofed
one. Deriving it needs a position solution, which needs line-of-sight vectors,
which is Track C. Until that lands, the emitter reports the station's surveyed
position with `position_source: "surveyed"` so the record is well-formed and
visibly not a solution. Anything reading a displacement off a surveyed position
would be reading zero, which is why the flag is there rather than a bare
placeholder.

Transport is JSON Lines appended to a file, tailed by the console (§5).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

# design.md §4 — USN8 surveyed ECEF, metres
USN8_ECEF = (1112161.8802, -4842854.4026, 3985497.3830)

# WGS-84
_A, _F = 6378137.0, 1.0 / 298.257223563
_E2 = _F * (2 - _F)


def ecef_to_lla(x: float, y: float, z: float) -> dict:
    """WGS-84 geodetic latitude/longitude/height, Bowring's method."""
    lon = np.arctan2(y, x)
    p = np.hypot(x, y)
    lat = np.arctan2(z, p * (1 - _E2))
    for _ in range(6):                      # converges in three
        n = _A / np.sqrt(1 - _E2 * np.sin(lat) ** 2)
        alt = p / np.cos(lat) - n
        lat = np.arctan2(z, p * (1 - _E2 * n / (n + alt)))
    n = _A / np.sqrt(1 - _E2 * np.sin(lat) ** 2)
    return {"lat": float(np.degrees(lat)), "lon": float(np.degrees(lon)),
            "alt": float(p / np.cos(lat) - n)}


SURVEYED = ecef_to_lla(*USN8_ECEF)


def record(time: datetime, features: dict, scored: dict, n_sv: int,
           geometry: dict | None = None, credential_status: str = "VALID",
           position: dict | None = None, by_sv: dict | None = None,
           terrain: dict | None = None) -> dict:
    """One §5 contract object.

    `by_sv` (TRACK_D.md contract extension 1) nests into `features` as the
    per-satellite anomaly map — detection.by_sv_scores computes it; this
    emitter only rounds and passes it through, like every other block.

    `terrain` (tracks/TRACK_E.md) is Track E's block, passed through untouched
    and present ONLY when the channel ran — a sensorless record carries no
    `terrain` key, so it is byte-identical to a record from before Track E.
    """
    ts = time if time.tzinfo else time.replace(tzinfo=timezone.utc)
    feats = {k: round(float(v), 4) for k, v in features.items()}
    if by_sv is not None:
        feats["by_sv"] = {sv: round(float(v), 4)
                          for sv, v in sorted(by_sv.items())}
    rec = {
        "timestamp": ts.isoformat().replace("+00:00", "Z"),
        "confidence": round(scored["confidence"], 4),
        "credential_status": credential_status,
        "position": position or dict(SURVEYED),
        "position_source": "solution" if position else "surveyed",
        "features": feats,
        "geometry": geometry,
        "satellites_tracked": int(n_sv),
        # Not in §5, and additive rather than a change to it: the composite is
        # never to be quoted without saying what made it (CLAUDE.md). That
        # includes `combine_mode` -- weighted_sum and max produce different
        # numbers from the same features, so a record that did not name its
        # rule would be ambiguous.
        "score_detail": {k: scored[k] for k in
                         ("feature_score", "geometry_deficit", "beta",
                          "geometry_available", "weights_tuned", "weights",
                          "weight_sensitive_fraction", "features_scored", "combine_mode")},
    }
    if terrain is not None:
        rec["terrain"] = terrain
    return rec


def write_jsonl(records, path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")
    return path
