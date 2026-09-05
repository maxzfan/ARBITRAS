"""Generate fixtures/epoch_obs.csv — the hand-written Track D fixture.

TRACK_D.md: "fixtures/epoch.json plus a hand-written fixtures/epoch_obs.parquet
(one epoch of pseudoranges and LOS vectors, ~12 rows) is enough for D1 through
D3." CSV instead of parquet: the pinned env (bootstrap.sh) has no pyarrow and a
12-row fixture does not justify adding a wheel to every machine. Every number
below is chosen by hand and stated here; the CSV is just this table serialised,
so D1-D3 never depend on data/ or georinex.

Construction: 8 GPS + 4 Galileo satellites at plausible az/el for USN8, ranges
~22,000 km. Pseudoranges are exact: geometric range from the surveyed antenna
plus a per-constellation clock bias (range-equivalent metres). Zero noise, so
solver acceptance tests can assert to 1e-9. Tests inject their own faults.

    python fixtures/make_epoch_obs.py     # rewrites fixtures/epoch_obs.csv
"""
import numpy as np
import pandas as pd

# USN8 surveyed ECEF (design.md §4) — the linearisation point x_lin.
RX_ECEF = np.array([1112161.8802, -4842854.4026, 3985497.3830])
LAT, LON = np.deg2rad(38.9207), np.deg2rad(-77.0669)

# Per-constellation receiver clock bias, range-equivalent metres. Hand-picked,
# different from each other so a solver that mixes the clock columns up fails.
CLOCK_M = {"G": 3000.0, "E": 2000.0}

# (sv, az deg, el deg) — a plausible open-sky USN8 constellation, by hand.
SKY = [
    ("G04", 45.0, 70.0), ("G07", 120.0, 45.0), ("G09", 200.0, 30.0),
    ("G16", 310.0, 55.0), ("G18", 85.0, 15.0), ("G26", 250.0, 20.0),
    ("G27", 160.0, 60.0), ("G31", 20.0, 35.0),
    ("E03", 300.0, 25.0), ("E11", 60.0, 50.0), ("E15", 190.0, 12.0),
    ("E24", 270.0, 65.0),
]


def enu_to_ecef_unit(az_deg: float, el_deg: float) -> np.ndarray:
    az, el = np.deg2rad(az_deg), np.deg2rad(el_deg)
    enu = np.array([np.cos(el) * np.sin(az), np.cos(el) * np.cos(az),
                    np.sin(el)])
    sl, cl = np.sin(LAT), np.cos(LAT)
    so, co = np.sin(LON), np.cos(LON)
    rot = np.array([[-so, -sl * co, cl * co],
                    [co, -sl * so, cl * so],
                    [0.0, cl, sl]])
    return rot @ enu


def main() -> None:
    rows = []
    for sv, az, el in SKY:
        u = enu_to_ecef_unit(az, el)          # unit LOS, receiver -> satellite
        rng = 22_000_000.0 + (90.0 - el) * 20_000.0   # plausible, hand-picked
        sat = RX_ECEF + rng * u
        rows.append({"sv": sv, "sat_x": sat[0], "sat_y": sat[1],
                     "sat_z": sat[2], "pr_m": rng + CLOCK_M[sv[0]],
                     "az_deg": az, "el_deg": el})
    df = pd.DataFrame(rows)
    df.to_csv("fixtures/epoch_obs.csv", index=False, float_format="%.6f")
    print(df.to_string(index=False))
    print("\nrx_ecef:", RX_ECEF.tolist(), "\nclock_m:", CLOCK_M)


if __name__ == "__main__":
    main()
