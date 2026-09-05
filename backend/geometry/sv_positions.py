"""Keplerian broadcast-ephemeris propagator (IS-GPS-200 20.3.3.4.3).

Valid for GPS, Galileo and BeiDou MEO/IGSO — all broadcast Keplerian
elements. GLONASS broadcasts ECEF state vectors requiring numerical
integration and is out of scope (README limitations).

Precision note: the geometry block needs line-of-sight *directions* only
(~0.1 deg tolerance, i.e. tens of km of SV position error is acceptable).
No transmit-time correction, no Sagnac rotation, no SV clock bias.
Common-mode LOS error largely cancels in the determinant *ratio*.
"""
from __future__ import annotations

from datetime import datetime
from math import atan2, cos, sin, sqrt

import numpy as np

MU = 3.986005e14           # WGS-84 gravitational parameter, m^3/s^2 (GPS ICD)
OMEGA_E = 7.2921151467e-5  # earth rotation rate, rad/s
HALF_WEEK = 302400.0
WEEK = 604800.0

# Keplerian fields as named by georinex.
FIELDS = ("sqrtA", "Eccentricity", "M0", "omega", "Io", "Omega0",
          "DeltaN", "OmegaDot", "IDOT", "Cuc", "Cus", "Crc", "Crs",
          "Cic", "Cis", "Toe")


def kepler_to_ecef(eph: dict, t_gpst: datetime) -> np.ndarray:
    """SV ECEF position (m) at GPS time t from one broadcast record.

    `eph` holds the georinex Keplerian fields plus `toc_gpst`, the record
    epoch already converted to GPS time (BDT+14s handled upstream).
    """
    tk = (t_gpst - eph["toc_gpst"]).total_seconds()
    if tk > HALF_WEEK:
        tk -= WEEK
    elif tk < -HALF_WEEK:
        tk += WEEK

    a = eph["sqrtA"] ** 2
    e = eph["Eccentricity"]
    n = sqrt(MU / a**3) + eph["DeltaN"]
    m_k = eph["M0"] + n * tk

    # Kepler's equation, Newton's method.
    e_k = m_k
    for _ in range(10):
        e_k -= (e_k - e * sin(e_k) - m_k) / (1.0 - e * cos(e_k))

    nu = atan2(sqrt(1.0 - e * e) * sin(e_k), cos(e_k) - e)
    phi = nu + eph["omega"]

    s2p, c2p = sin(2 * phi), cos(2 * phi)
    u = phi + eph["Cus"] * s2p + eph["Cuc"] * c2p
    r = a * (1.0 - e * cos(e_k)) + eph["Crs"] * s2p + eph["Crc"] * c2p
    i = eph["Io"] + eph["IDOT"] * tk + eph["Cis"] * s2p + eph["Cic"] * c2p

    x_orb, y_orb = r * cos(u), r * sin(u)
    node = (eph["Omega0"] + (eph["OmegaDot"] - OMEGA_E) * tk
            - OMEGA_E * eph["Toe"])

    return np.array([
        x_orb * cos(node) - y_orb * cos(i) * sin(node),
        x_orb * sin(node) + y_orb * cos(i) * cos(node),
        y_orb * sin(i),
    ])
