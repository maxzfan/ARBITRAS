"""Trust states and the confidence thresholds that map onto them.

THIS FILE IS THE SINGLE SWAP POINT FOR THRESHOLDS (design.md §8).
At 21:00 Saturday, tracks A and C replace the placeholder values with measured
separation points. Nothing else in the codebase should contain a threshold.
"""
from enum import IntEnum


class TrustState(IntEnum):
    """Ordered so that authority comparisons are integer comparisons.

    Higher = more authority. 'Monotone non-increasing' (invariant 1) is
    `min()`, and 'cap at DEGRADED' (credential override) is also `min()`.
    """
    SURRENDERED = 0
    RESTRICTED = 1
    DEGRADED = 2
    NOMINAL = 3


# Lower bound of confidence required to be IN each state.
# design.md §8: "These thresholds are placeholders. Replace with measured
# separation points Saturday evening. Round numbers without a reason are the
# most attackable thing in the project."
THRESHOLDS = {
    TrustState.NOMINAL: 0.75,
    TrustState.DEGRADED: 0.50,
    TrustState.RESTRICTED: 0.25,
    # SURRENDERED is everything below RESTRICTED's bound.
}

# Surfaced in the console and stamped into every emitted decision, so a
# placeholder threshold cannot reach the submission video unnoticed.
# Set to "measured" (with the procedure + separation) once §10 step 4 is done.
THRESHOLD_PROVENANCE = "PLACEHOLDER"

# Hysteresis (invariant 2). Also placeholders, but of a different kind: these
# are policy, not fitted to data, and are defensible as stated in §8.
RECOVERY_EPOCHS = 10   # consecutive epochs above the higher threshold
MIN_DWELL_EPOCHS = 5   # minimum epochs in a state before any upgrade

# Stale-epoch policy. design.md §5: "Silence is not consent."
STALE_GRACE_TICKS = 3  # consecutive missing/malformed epochs before stepping down


def state_from_confidence(confidence: float) -> TrustState:
    """Confidence -> the state it implies, before any credential override."""
    if confidence >= THRESHOLDS[TrustState.NOMINAL]:
        return TrustState.NOMINAL
    if confidence >= THRESHOLDS[TrustState.DEGRADED]:
        return TrustState.DEGRADED
    if confidence >= THRESHOLDS[TrustState.RESTRICTED]:
        return TrustState.RESTRICTED
    return TrustState.SURRENDERED


# Credential override (design.md §8). Two mechanisms, deliberately distinct:
# FORCE pins the state regardless of confidence; CAP bounds it from above.
CREDENTIAL_FORCE = {
    "REVOKED": TrustState.SURRENDERED,
    "EXPIRED": TrustState.SURRENDERED,
}
CREDENTIAL_CAP = {
    "UNVERIFIED": TrustState.DEGRADED,
}
# PENDING: no override — the previously verified credential is still inside its
# window, and PENDING lasts exactly the TESLA disclosure lag (§9).
# VALID: no override.
CREDENTIAL_STATUSES = frozenset(
    {"VALID", "PENDING", "EXPIRED", "UNVERIFIED", "REVOKED"}
)
