"""Plain-language explanation of an arbitration decision.

Templated, per design.md §14 ("Explanation layer, templated first").

Two rules from the design document govern every string here:
  - Operator language, not builder language. "The vehicle stops accepting
    waypoints," never "the state machine transitions to RESTRICTED."
  - Never a quantitative claim that is not checkable. Every number carries the
    contract path it came from, so `verify()` can confirm it against the epoch
    record before the console displays it (design.md §14, 22:30 block).
"""
from typing import Any, Optional

from .machine import Decision
from .states import RECOVERY_EPOCHS, TrustState

BEHAVIOUR = {
    TrustState.NOMINAL: "Full autonomy. Navigating on GNSS and accepting new waypoints.",
    TrustState.DEGRADED: "Continuing the mission, coasting position on inertial. Discrepancy flagged.",
    TrustState.RESTRICTED: "Completing the current leg only. New waypoints refused.",
    TrustState.SURRENDERED: "Holding position. Control is with the operator.",
}

# Which signal diverged, in the operator's terms.
FEATURE_PHRASE = {
    "cn0_anomaly": "signal strength has moved off its own baseline",
    "pseudorange_residual": "range measurements are drifting from their smoothed track",
    "code_carrier_divergence": "code and carrier measurements have separated",
    "cross_constellation": "the constellations no longer agree on position",
    # Track E (tracks/TRACK_E.md): the one non-RF channel.
    "terrain_mismatch": "the ground under the vehicle does not match the map at the reported position",
}

CREDENTIAL_PHRASE = {
    "REVOKED": "Mission authorisation was revoked.",
    "EXPIRED": "Mission authorisation lapsed. Renewal stopped.",
    "UNVERIFIED": "Mission authorisation could not be verified.",
    "PENDING": "Mission authorisation is awaiting key disclosure.",
}

# A feature below this is not worth naming as the cause.
SALIENCE_FLOOR = 0.30


def _claim(text: str, value: Any, path: str) -> dict:
    return {"text": text, "value": value, "path": path}


def explain(d: Decision) -> dict:
    """-> {headline, detail, claims}. Claims are verifiable against the epoch."""
    claims: list[dict] = []
    parts: list[str] = []

    # --- why authority is where it is -------------------------------------
    if d.reason == "credential_force":
        headline = CREDENTIAL_PHRASE.get(
            d.credential_status or "", "Mission authorisation failed."
        )
        parts.append("Signal quality did not enter into this.")
    elif d.reason == "credential_cap":
        headline = CREDENTIAL_PHRASE.get(
            d.credential_status or "", "Mission authorisation is unconfirmed."
        )
        parts.append("Authority is capped until it verifies.")
    elif d.reason == "stale":
        headline = "The position feed went silent."
        parts.append("Silence is treated as degradation, not as consent.")
    else:
        worst = None
        # Only scalar features can be "the signal that diverged": the
        # contract's `features` also carries the nested per-satellite map
        # `by_sv` (TRACK_D.md contract extension 1), which is not a feature
        # score and must not be compared against one.
        numeric = {k: v for k, v in (d.features or {}).items()
                   if isinstance(v, (int, float))}
        if numeric:
            worst = max(numeric, key=lambda k: numeric.get(k) or 0)
            if (numeric.get(worst) or 0) < SALIENCE_FLOOR:
                worst = None
        if worst:
            headline = FEATURE_PHRASE.get(worst, worst).capitalize() + "."
            claims.append(
                _claim(f"{numeric[worst]:.2f}", numeric[worst],
                       f"features.{worst}")
            )
        elif d.state is TrustState.NOMINAL:
            headline = "All monitored signals are within their normal range."
        elif d.implied_state is not None and d.implied_state > d.state:
            # Recovering: confidence already implies more authority than is held.
            # Invariant 2 holds it down deliberately; say so, rather than the
            # false "confidence is low" line that used to appear at 0.95.
            headline = ("Signals are back within their normal range. "
                        "Authority is restored one step at a time.")
            parts.append(
                f"Confidence must hold above the next threshold for "
                f"{RECOVERY_EPOCHS} consecutive epochs before each step up."
            )
        else:
            headline = "Position confidence is below the trusted range."
        if d.confidence is not None:
            claims.append(
                _claim(f"{d.confidence:.2f}", d.confidence, "confidence")
            )

    # --- what the vehicle is doing about it -------------------------------
    parts.append(BEHAVIOUR[d.state])

    # --- geometry, in metres, which is what an operator can hold onto -----
    g = d.geometry or {}
    bound = g.get("displacement_bound_m")
    if bound is not None:
        parts.append(
            f"An attacker could move us up to {bound:.0f} m and stay consistent "
            f"with the satellites we still trust."
        )
        claims.append(_claim(f"{bound:.0f} m", bound, "geometry.displacement_bound_m"))
    excluded = g.get("excluded_sv") or []
    if excluded:
        parts.append(
            f"{len(excluded)} satellite{'s' if len(excluded) != 1 else ''} "
            f"dropped out of the trusted set ({', '.join(excluded)})."
        )
        claims.append(_claim(str(len(excluded)), len(excluded), "geometry.excluded_sv"))

    # --- terrain (Track E): the most operator-legible sentence available ---
    t = d.terrain or {}
    L = t.get("match_likelihood")
    sensed = (t.get("sensed") or {}).get("class")
    mapped = (t.get("map_at_position") or {}).get("class")
    if L is not None and sensed and mapped and sensed != mapped:
        parts.append(f"The terrain sensor reads {sensed}; the map has {mapped} "
                     f"at the reported position.")
        claims.append(_claim(f"{L:.2f}", L, "terrain.match_likelihood"))

    # --- invariant 5: DEGRADED is an active state -------------------------
    if d.advisory:
        parts.append(d.advisory)

    # --- invariant 4: the clock coupling, said out loud -------------------
    if not d.clock_discipline:
        parts.append(
            "The clock is free-running and no new authorisation will be accepted "
            "while confidence is below the trusted range."
        )

    # --- the freeze rule, when it has something to say --------------------
    div = d.geometry_divergence
    if div and div.get("newly_excluded_sv"):
        parts.append(
            "Trusted geometry has drifted from the last fully-trusted epoch: "
            f"{', '.join(div['newly_excluded_sv'])} newly excluded."
        )

    return {"headline": headline, "detail": " ".join(parts), "claims": claims}


def _dig(obj: Any, path: str) -> Any:
    cur = obj
    for key in path.split("."):
        if not isinstance(cur, dict) or key not in cur:
            return None
        cur = cur[key]
    return cur


def verify(explanation: dict, epoch: Optional[dict]) -> tuple[bool, list[str]]:
    """Check every quantitative claim against the epoch record.

    design.md §14: the console must not display a number it cannot source.
    Returns (ok, failures). On failure the console shows the state and the
    headline but suppresses the numeric detail.
    """
    failures = []
    for c in explanation.get("claims", []):
        actual = _dig(epoch or {}, c["path"])
        if actual is None:
            failures.append(f"{c['path']} absent from epoch")
            continue
        if isinstance(actual, list):
            actual = len(actual)
        if isinstance(actual, (int, float)) and isinstance(c["value"], (int, float)):
            if abs(float(actual) - float(c["value"])) > 1e-9:
                failures.append(
                    f"{c['path']}: claimed {c['value']}, epoch says {actual}"
                )
        elif actual != c["value"]:
            failures.append(f"{c['path']}: claimed {c['value']}, epoch says {actual}")
    return (not failures), failures
