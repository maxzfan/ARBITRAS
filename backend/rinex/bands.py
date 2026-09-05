"""Canonical two-band observable selection, one mapping per constellation.

RINEX 3 observable codes differ per system (design.md §4). Every downstream
feature wants the same three things per satellite per band — code, carrier,
C/N0 — so the loader flattens the system-specific codes onto a fixed pair of
slots, band 1 (the L1-class signal the receiver tracks first) and band 2.

Frequencies are the ones the wavelengths come from; carrier phase is reported
in cycles and code-minus-carrier needs it in metres.
"""

C_LIGHT = 299_792_458.0

# system -> (band-1 (code, phase, snr), band-2 (code, phase, snr))
CODES = {
    "G": (("C1C", "L1C", "S1C"), ("C2W", "L2W", "S2W")),   # GPS  L1 C/A , L2 W
    "E": (("C1C", "L1C", "S1C"), ("C5Q", "L5Q", "S5Q")),   # Galileo E1 , E5a
    "R": (("C1C", "L1C", "S1C"), ("C2C", "L2C", "S2C")),   # GLONASS G1 , G2
    "C": (("C2I", "L2I", "S2I"), ("C6I", "L6I", "S6I")),   # BeiDou B1I , B3I
    "S": (("C1C", "L1C", "S1C"), ("C5I", "L5I", "S5I")),   # SBAS L1 , L5
}

# system -> (f1, f2) in Hz. GLONASS is FDMA and handled separately below.
FREQ = {
    "G": (1575.42e6, 1227.60e6),
    "E": (1575.42e6, 1176.45e6),
    "R": (1602.00e6, 1246.00e6),   # channel k=0; see glonass_freq()
    "C": (1561.098e6, 1268.52e6),
    "S": (1575.42e6, 1176.45e6),
}

# GLONASS FDMA channel spacing
R_CHAN = (562.5e3, 437.5e3)


def glonass_freq(k: int = 0):
    """GLONASS G1/G2 for frequency channel k (-7..6). k=0 is the nominal centre.

    Channel numbers live in the nav file, not the observation file. Band-1
    wavelength varies +/-0.23% across the channel range, so k=0 is a fine
    default for code-minus-carrier *divergence* (a rate) and a poor one for its
    absolute level. Callers with a nav file should pass the real k.
    """
    return (FREQ["R"][0] + k * R_CHAN[0], FREQ["R"][1] + k * R_CHAN[1])


def wavelengths(system: str, k: int = 0):
    """(lambda_1, lambda_2) in metres for a constellation."""
    f1, f2 = glonass_freq(k) if system == "R" else FREQ[system]
    return (C_LIGHT / f1, C_LIGHT / f2)


def observable_codes(systems) -> list:
    """Flat list of RINEX codes to request from georinex for these systems."""
    out = []
    for s in systems:
        for band in CODES[s]:
            out.extend(band)
    return sorted(set(out))
