"""Line-of-sight matrix H over tracked satellites.

H is n x (3+k): three position columns plus one clock-bias column per
constellation PRESENT in the satellite set (design.md correction to §6b).
The clock-column drop is automatic — building H over a trusted subset that
contains no SVs of a constellation simply never creates that column, so
the matrix is never rank-deficient by construction.
"""
from __future__ import annotations

import numpy as np


def unit_los(sat_ecef: np.ndarray, rx_ecef: np.ndarray) -> np.ndarray:
    """Unit line-of-sight vector, receiver -> satellite."""
    d = sat_ecef - rx_ecef
    return d / np.linalg.norm(d)


def elevation_deg(sat_ecef: np.ndarray, rx_ecef: np.ndarray) -> float:
    """Elevation above local horizontal, degrees.

    Uses the geocentric up-vector (rx/|rx|); differs from geodetic by
    <0.2 deg at USN8 — irrelevant against a 10 deg mask.
    """
    up = rx_ecef / np.linalg.norm(rx_ecef)
    return float(np.degrees(np.arcsin(np.clip(unit_los(sat_ecef, rx_ecef) @ up,
                                              -1.0, 1.0))))


def azimuth_deg(sat_ecef: np.ndarray, rx_ecef: np.ndarray) -> float:
    """Azimuth clockwise from north, degrees in [0, 360).

    Standard ENU azimuth atan2(E, N) with the same geocentric up-vector
    as elevation_deg, so the pair is a consistent local frame.
    """
    up = rx_ecef / np.linalg.norm(rx_ecef)
    east = np.cross([0.0, 0.0, 1.0], up)
    east = east / np.linalg.norm(east)
    north = np.cross(up, east)
    u = unit_los(sat_ecef, rx_ecef)
    return float(np.degrees(np.arctan2(u @ east, u @ north)) % 360.0)


def build_H(sat_pos: dict[str, np.ndarray],
            rx_ecef: np.ndarray) -> tuple[np.ndarray, list[str], list[str]]:
    """Build H from {sv_id: ecef_position}.

    Returns (H, sv_ids row order, constellation column order).
    Row_i = [u_x, u_y, u_z, one-hot clock column for its constellation].
    """
    sv_ids = sorted(sat_pos)
    consts = sorted({sv[0] for sv in sv_ids})
    col = {c: 3 + j for j, c in enumerate(consts)}

    h = np.zeros((len(sv_ids), 3 + len(consts)))
    for i, sv in enumerate(sv_ids):
        h[i, :3] = unit_los(sat_pos[sv], rx_ecef)
        h[i, col[sv[0]]] = 1.0
    return h, sv_ids, consts
