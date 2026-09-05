# TRACK C — Geometry + Measurement

You own `backend/geometry/` and `backend/measurement/`. You produce the
`geometry` block of the §5 contract. This is the weight-independent half of
the confidence score and it is NOT on the cut list.

## Setup (~10 min, unattended)
    git clone <repo> && cd dnhacks-pnt
    bash bootstrap.sh
    source .venv/bin/activate

## Already done — do not redo
The nav file is resolved and VERIFIED (design.md §16 called this blocking):

    python backend/rinex/nav_prefilter.py    # already run, output committed-ignored
    # -> data/brdc_filtered.rnx

Stripped 164 IRNSS records. Verified loading in georinex:
G 32, E 120, R 26, C 37 SVs, 4,166 epochs, Keplerian params all present
(M0, Eccentricity, sqrtA, Omega0, Io).

## GOTCHA 1 — Galileo SVs are duplicated
georinex returns `E02`, `E02_1`, `E02_2`, `E02_3` — it splits Galileo by nav
message type (I/NAV vs F/NAV), not by satellite. That is why E shows 120 for a
~26-satellite constellation. **Dedupe to the base PRN before building H** or
you will double-count rows and the information matrix will be wrong in a way
that looks plausible.

## GOTCHA 2 — the §6b open question is answered, and the naive form fails
design.md §6b asks: does the determinant ratio stay meaningful as the trusted
subset shrinks? Worked on paper: **no, not as written.** Two reasons, both fixable.

**(a) H is n x (3+k), not n x 4.** k = number of constellations in the trusted
set; each contributes its own clock bias. Exclude every SV of one constellation
and the matrix goes rank-deficient in that constellation's clock state:
det -> 0 discontinuously, for a reason that has nothing to do with position
information. This fires exactly under **meaconing (§7 scenario 3)**, where the
correct detector response is to distrust one whole constellation. The score
would fail hardest on the attack it should handle best.

  Fix: when a constellation's trusted SV count reaches zero, DROP its clock
  column. Reduce the state vector; never carry a rank-deficient matrix.

**(b) The raw ratio decays far too fast to read.** det(HtH) scales with the
(3+k)th power of the geometry. Dropping 2 of 11 SVs can move it an order of
magnitude, and it is exactly 0 the moment trusted SVs < unknowns. On screen in
video beat 3 it reads "0.00" almost immediately.

  Fix: report the **normalised** ratio `(det ratio)^(1/(3+k))`. This is the
  D-optimality form — the ratio of GDOP volumes, the geometric mean of the
  information eigenvalues. Legible across the whole exclusion range, and
  *easier* to defend on a whiteboard, which satisfies the §13 convention that
  the score be derivable in two minutes.

**Confirm this numerically once H exists — ~15 min, not a rebuild.** Sweep
exclusion count 0..7 on a real epoch, plot raw vs normalised. If the normalised
curve is monotone and spans a useful range, the geometry track is unblocked and
Claim 2a survives. That plot is also worth putting in the README.

## 1. Nav -> satellite positions -> line-of-sight matrix H
`gnss-lib-py` (Stanford NavLab) makes this tractable. Rows = tracked SVs,
columns = [3 position, k clock biases].

## 2. Information ratio, normalised, plotted over the clean day
`det(HtH)` on the trusted subset over the full-constellation solution, then the
(1/(3+k)) power. Scaled to [0,1]. No free parameter — that is the whole point.

## 3. Analytic displacement bound + next-best-observation
Next-best falls out of the rank-one update:
`det(G + hh^T) = det(G)(1 + h^T G^-1 h)` — ranking candidates is one quadratic
form each. Borrowed from active view selection (CONVERGE, Stanford); cite it
by name in the README.

## 4. Freeze-at-last-NOMINAL (evening)
Line-of-sight vectors derive from the receiver's own position estimate, which
under attack is the spoofed one. Below NOMINAL, evaluate geometry against the
LOS set held at the last NOMINAL epoch. **Divergence between frozen and live
geometry is itself evidence.** This is the strongest finding in the project —
same structure as the TESLA clock dependency. It belongs in the README.

## 5. Measurement (§10) — sweep harness
FSR as a **distribution over a Dirichlet sample of detector weight vectors**,
not a point estimate. ~40 lines. The Dirichlet varies the four feature weights
and the feature/geometry blend; the geometry term itself stays fixed. Report
**what fraction of the score is weight-sensitive** — that is a better answer to
arXiv 2607.05415 than a wide distribution alone.

Then: empirical swept displacement vs the analytic bound, plotted on one axis.
If the empirical number ever exceeds the bound, the bound is wrong. Run that
check explicitly and say that you ran it.

## Deliverable by 18:30
`geometry` block flowing in the contract: information_ratio, excluded_sv,
displacement_bound_m, next_best_observation.
