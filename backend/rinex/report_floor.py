"""Print the measured noise floor. Source of the table in docs/measured.md.

    python -m backend.rinex.report_floor [obs_file]
"""
import sys

import numpy as np

from .loader import load_obs
from .noise import measure, panel

DEFAULT = "data/USN800USA_R_20262320000_01D_30S_MO.crx.gz"


def main(path: str = DEFAULT, systems: str = "GERCS") -> None:
    eps = load_obs(path, systems=systems)
    fl = measure(eps)
    print(fl)

    cn0 = panel(eps, "cn0_1").values
    step = np.diff(np.unique(cn0[np.isfinite(cn0)]))
    step = float(step[step > 0].min())
    quant = 1.4826 * step / np.sqrt(2)
    print(f"\nC/N0 quantisation step in file: {step:.3f} dB "
          f"-> differenced-MAD floor {quant:.3f} dB-Hz")
    if abs(fl.cn0_sigma - quant) < 1e-3:
        print("  measured sigma IS the quantisation floor: this is a ceiling on "
              "the noise, not a measurement of it")

    for label, s in (("C/N0 sigma (dB-Hz)", fl.cn0_sigma_by_sv),
                     ("code-minus-carrier sigma (m)", fl.cmc_sigma_by_sv)):
        print(f"\n{label}: median {s.median():.3f}  p90 {s.quantile(.9):.3f}")
        for sysc in systems:
            ss = s[[i.startswith(sysc) for i in s.index]]
            if len(ss):
                print(f"   {sysc} n={len(ss):3d}  median {ss.median():.3f}")

    lv = fl.cn0_level_by_sv
    print(f"\nC/N0 level (dB-Hz): min {lv.min():.2f}  median {lv.median():.2f}  "
          f"max {lv.max():.2f}")


if __name__ == "__main__":
    main(*sys.argv[1:])
