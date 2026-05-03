"""
LAYER 6 INTEGRATION PATCH for math_society.py
==============================================

This file shows the exact lines to add/change in math_society.py
to wire in ms_layer6.py. Three locations:

  PATCH 1 — import (near top of run() alongside other ms_ imports)
  PATCH 2 — instantiation (inside run(), alongside debate/concepts/etc.)
  PATCH 3 — round loop hook (every 10 rounds, after round 30)
  PATCH 4 — final report (at the very end of run(), before db.close())

────────────────────────────────────────────────────────────────────
PATCH 1 — Add this import line next to the other ms_ imports
(around line: from ms_symmetry import TheorySymmetryScanner)
────────────────────────────────────────────────────────────────────
"""

# ADD THIS LINE alongside the other ms_ imports:
# from ms_layer6 import Layer6Engine

"""
────────────────────────────────────────────────────────────────────
PATCH 2 — Instantiate Layer6Engine inside run()
(right after: sym_scanner = TheorySymmetryScanner())
────────────────────────────────────────────────────────────────────

    sym_scanner = TheorySymmetryScanner()
    layer6      = Layer6Engine()          # ← ADD THIS LINE

────────────────────────────────────────────────────────────────────
PATCH 3 — Round loop hook
Add this block inside the for rnd in range(1, rounds+1) loop,
right after the existing "if rnd >= 30 and rnd % 15 == 0:" block.

Location: after the conjecture-upgrade pipeline block.
────────────────────────────────────────────────────────────────────

        # ── Layer 6: Paradigm Formation Engine ───────────────────────────
        # Runs every 10 rounds once enough concepts exist.
        # Performs: framework rewriting, concept importance Hall of Fame,
        # autonomous unification hunting, cross-domain transfer,
        # elegance auditing. All six Layer-6 components in one call.
        if rnd >= 30 and rnd % 10 == 0 and len(concepts.all_concepts()) >= 4:
            layer6.update(
                concepts_obj       = concepts,
                laws               = concepts.all_laws(),
                agents             = agents,
                unlock_credit      = _unlock_credit,
                surprise_resolutions = _surprise_resolutions,
                round_num          = rnd,
            )

────────────────────────────────────────────────────────────────────
PATCH 4 — Final report
Add this block at the very end of run(), just before export_run().

Location: after the "Concept summary" print and before export_run(db, run_id)
────────────────────────────────────────────────────────────────────

    # ── Layer 6 Final Report ──────────────────────────────────────────────
    print(f"\\n  {bold('LAYER 6 — PARADIGM FORMATION REPORT')}")
    layer6.final_report(
        concepts_obj = concepts,
        laws         = concepts.all_laws(),
        round_num    = rounds,
    )

────────────────────────────────────────────────────────────────────
COMPLETE DIFF (for reference)
────────────────────────────────────────────────────────────────────

Line ~1830 (imports block at top of run()):
  BEFORE:
    from ms_symmetry import TheorySymmetryScanner
  AFTER:
    from ms_symmetry import TheorySymmetryScanner
    from ms_layer6   import Layer6Engine

Line ~1860 (instantiation block):
  BEFORE:
    sym_scanner = TheorySymmetryScanner()
  AFTER:
    sym_scanner = TheorySymmetryScanner()
    layer6      = Layer6Engine()

Line ~2100 (inside round loop, after conjecture pipeline):
  ADD:
    if rnd >= 30 and rnd % 10 == 0 and len(concepts.all_concepts()) >= 4:
        layer6.update(concepts, concepts.all_laws(), agents,
                      _unlock_credit, _surprise_resolutions, rnd)

Line ~2320 (end of run(), before export_run):
  ADD:
    layer6.final_report(concepts, concepts.all_laws(), rounds)

────────────────────────────────────────────────────────────────────
WHY THESE LOCATIONS?
────────────────────────────────────────────────────────────────────

• Every 10 rounds (not every round) — Layer-6 analysis is expensive;
  running it every round would dominate runtime.

• After round 30 — need enough concepts (4+) and laws to be meaningful.
  Before round 30, the registry is too sparse for unification/transfer.

• The update() call fires AFTER the existing concept/law/debate updates,
  so it sees the freshest state of the concept registry and law set.

• final_report() fires after all the existing reports (concepts summary,
  discovered programs) so it appears as the capstone of the run output.

────────────────────────────────────────────────────────────────────
WHAT YOU'LL SEE IN THE OUTPUT
────────────────────────────────────────────────────────────────────

At round 30, 40, 50...:
  🔧 FRAMEWORK PROPOSAL  T_number  (new_primitive)
     T(n) = n(n+1)//2 — first-class primitive
     Motivation: appears in sum_squares, sum_cubes, partial_sum...

  🔗 UNIFICATION  sum_formula_1 ≃ gauss_b_seed
     sum_formula_1(n) = 1 × gauss_b_seed(n)
     Meta-law: ∀n: sum_formula_1(n) = gauss_b_seed(n) [constant 1-fold]

  🔀 TRANSFER  sum_range → sum_cubes  via squaring
     f(b) ** 2  ✓ verified on held-out inputs

At end of run:
  🏆 CONCEPT HALL OF FAME
  1. 🏛 sum_formula_1     [████████████░░░░░░░░]  0.72
       FOUNDATIONAL  appears in laws; spawns derived concepts; compact

  💎 ELEGANCE REPORT
     Elegance ratio: 2.50  (5 laws / 8 axioms... wait, 2 laws / 8 → 0.25)
     Redundancy: 12%  (2 pruning candidates)
     Most elegant: sum_formula_1(420)  gauss_b_seed(380)  ...

  🧠 PARADIGM FORMATION INDEX  —  Layer 6-
     A. Framework invention    [████░░░░░░░░░░░░░░░░]  0.20
     B. Unification depth      [██████░░░░░░░░░░░░░░]  0.30
     C. Elegance ratio         [████████░░░░░░░░░░░░]  0.40
     D. Cross-domain transfer  [████████████░░░░░░░░]  0.60
     E. Concept fertility      [████░░░░░░░░░░░░░░░░]  0.20
     F. Theory compression     [██████░░░░░░░░░░░░░░]  0.30
     ──────────────────────────────────────────────────────────────
     COMPOSITE                 [████████░░░░░░░░░░░░]  0.37/1.00

     Paradigm formation is beginning. The system proposes new symbolic
     vocabulary, detects cross-domain transfers, and ranks concept
     importance with genuine taste. This is the early edge of Layer 6.
"""

# This file is documentation only — nothing to run.
# Copy ms_layer6.py to your project directory and apply the patches above.
print("Apply the patches in this file to math_society.py to enable Layer 6.")
print("See the comments above for exact locations.")
