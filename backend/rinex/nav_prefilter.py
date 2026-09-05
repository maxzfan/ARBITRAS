"""Strip IRNSS (system I) records from a RINEX 3 nav file.

georinex 1.16.1 chokes on IRNSS records in BRDC00IGS (field-count mismatch).
IRNSS is not used by this project; G/E/R/C/S are preserved.

RINEX 3 nav: a record is a header line 'X##  YYYY MM DD ...' followed by
continuation lines. GLONASS (R) and SBAS (S) have 3; everything else has 7.
"""
import gzip
import re
from pathlib import Path

CONT = {"R": 3, "S": 3}          # continuation-line count by system
DEFAULT_CONT = 7
REC = re.compile(r"^([GERCIJS])(\d{2}) ")


def prefilter(src: Path, dst: Path, drop=("I",)) -> dict:
    op = gzip.open if src.suffix == ".gz" else open
    with op(src, "rt", errors="replace") as fh:
        lines = fh.readlines()

    out, counts, i, in_header = [], {}, 0, True
    while i < len(lines):
        line = lines[i]
        if in_header:
            out.append(line)
            if "END OF HEADER" in line:
                in_header = False
            i += 1
            continue
        m = REC.match(line)
        if not m:
            out.append(line)
            i += 1
            continue
        sysc = m.group(1)
        n = 1 + CONT.get(sysc, DEFAULT_CONT)
        counts[sysc] = counts.get(sysc, 0) + 1
        if sysc not in drop:
            out.extend(lines[i:i + n])
        i += n

    dst.write_text("".join(out))
    return counts


if __name__ == "__main__":
    import sys
    src = Path(sys.argv[1] if len(sys.argv) > 1
               else "data/BRDC00IGS_R_20262320000_01D_MN.rnx.gz")
    dst = Path(sys.argv[2] if len(sys.argv) > 2 else "data/brdc_filtered.rnx")
    print("record counts by system:", prefilter(src, dst))
    print("wrote", dst, dst.stat().st_size, "bytes")
