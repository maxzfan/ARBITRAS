"""Track D — course correction (TRACK_D.md).

Trusted-subset weighted fix, RAIM-style protection level, and the gate that
says whether the vehicle may drive on it. Consumes Track A observables and
Track C's H matrix via `GeometryEngine.solve_context()`; emits the
`geometry.correction` sub-block of the design.md §5 contract. Never decides
state — the console owns that.
"""
