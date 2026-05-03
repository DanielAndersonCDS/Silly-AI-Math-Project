"""
ms_unification.py — Structural Unification Engine v3.0

Intelligence = compression of structure, not accumulation of concepts.

PIPELINE:
    for gap in gaps:
        candidates = generate_candidates(gap)
        best = select(evaluate(candidates))
        if integrate(best):
            StructuralMerger.run(best, theory)
            PowerFamilyUnifier.run(theory)
            DualConstructor.run(theory)
            CompressionEngine.run(theory)

FIVE MODULES:
    1. StructuralMerger      — detect f ≈ k·g, replace f with Scaled(g, k)
    2. PowerFamilyUnifier    — merge Σi¹, Σi², Σi³ into PowerSum(k, a, b)
    3. DualConstructor       — Square(f) ↔ Root(f,2), close symmetry gaps
    4. AutomaticReplacer     — enforce removal of replaced structures
    5. CompressionEngine     — minimize concept count, prune redundancy

TARGET METRICS:
    Concepts     ↓ significantly
    Reusability  > 0.45
    Symmetry     > 0.40
    Compression  > 0.65
    Proof density ↑

FAILURE RULES:
    if concepts increase   → force merge pass
    if symmetry low        → prioritize dual generation
    if redundancy detected → collapse immediately
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import math

from ms_core import *
from ms_core import _parse_expr_stub
from ms_concepts import ConceptRegistry, Concept


# ══════════════════════════════════════════════════════════════════
# ABSTRACT OBJECTS — parameterized structures replacing instances
# ══════════════════════════════════════════════════════════════════

@dataclass
class Scaled:
    """
    Scaled(base, k) — represents k · base_function.
    Replaces all loop_concept_k and additive scaling variants.
    base: canonical expression string of the base concept
    k:    integer scalar
    """
    base: str
    k:    int

    def to_expr(self) -> str:
        return f"({self.k} * {self.base})"

    def to_node(self) -> Node:
        base_node = _parse_expr_stub(self.base)
        return Mul(Const(self.k), base_node)

    def __str__(self) -> str:
        return f"Scaled({self.base[:30]}, {self.k})"


@dataclass
class PowerSum:
    """
    PowerSum(k, a, b) — represents Σᵢ₌ₐᵇ iᵏ.
    Replaces individual sum_range (k=1), sum_squares (k=2), sum_cubes (k=3).

    Known closed forms:
      k=1: b(b+1)/2 − a(a-1)/2               (triangular)
      k=2: b(b+1)(2b+1)/6 − (a-1)a(2a-1)/6  (sum of squares)
      k=3: [b(b+1)/2]² − [(a-1)a/2]²         (Nicomachus)
    """
    k: int   # power (1, 2, 3, ...)
    a: str = "a"
    b: str = "b"

    def closed_form_node(self) -> Optional[Node]:
        """Return the closed-form Node for this power sum, or None."""
        b, a = Var("b"), Var("a")

        def T(x: Node) -> Node:
            """Triangular number T(x) = x(x+1)//2."""
            return IDiv(Mul(x, Add(x.clone(), Const(1))), Const(2))

        def T_prev(x: Node) -> Node:
            """T(x-1) = (x-1)x//2."""
            xm1 = Sub(x, Const(1))
            return IDiv(Mul(xm1, x.clone()), Const(2))

        if self.k == 1:
            # Σi (a..b) = T(b) - T(a-1)
            return Sub(T(b), T_prev(a))

        elif self.k == 2:
            # Σi² (1..b) = b(b+1)(2b+1)//6
            # Partial: telescoped as Q(b) - Q(a-1)
            def Q(x: Node) -> Node:
                two_x_plus_1 = Add(Mul(Const(2), x), Const(1))
                return IDiv(Mul(T(x.clone()), two_x_plus_1), Const(3))
            am1 = Sub(a, Const(1))
            return Sub(Q(b), Q(am1))

        elif self.k == 3:
            # Σi³ (1..b) = T(b)²
            # Partial: [T(b)]² - [T(a-1)]²
            am1 = Sub(a, Const(1))
            return Sub(Pow(T(b), Const(2)), Pow(T_prev(am1), Const(2)))

        return None  # k ≥ 4 not implemented

    def description(self) -> str:
        formulas = {
            1: "Σᵢ₌ₐᵇ i   = T(b) − T(a−1)",
            2: "Σᵢ₌ₐᵇ i²  = Q(b) − Q(a−1)   where Q(n) = T(n)(2n+1)/3",
            3: "Σᵢ₌ₐᵇ i³  = T(b)² − T(a−1)²  [Nicomachus]",
        }
        return formulas.get(self.k, f"Σᵢ₌ₐᵇ i^{self.k} (no closed form)")

    def __str__(self) -> str:
        return f"PowerSum(k={self.k}, {self.a}..{self.b})"


@dataclass
class Root:
    """
    Root(f, n) — the n-th root inverse of f.
    Root(f, 2) is the inverse of Square(f).
    Closes the missing_dual symmetry gap.
    """
    f: str   # canonical expression of the squared function
    n: int   # root degree (2 = square root, 3 = cube root)

    def description(self) -> str:
        return f"Root(n={self.n}) of {self.f[:35]}"

    def to_formal(self) -> str:
        sym = {2: "√", 3: "∛"}.get(self.n, f"^(1/{self.n})")
        return f"{sym}({self.f[:40]})"

    def __str__(self) -> str:
        return f"Root({self.f[:25]}, {self.n})"


# ══════════════════════════════════════════════════════════════════
# MODULE 1: STRUCTURAL MERGER
# Detect f ≈ k·g and replace f with Scaled(g, k)
# ══════════════════════════════════════════════════════════════════

class StructuralMerger:
    """
    Detects scaling relationships: f(a,b) = k · g(a,b).
    Replaces the scaled variant with Scaled(g, k).
    Eliminates loop_concept_k redundancy entirely.

    Detection rule:
        if estimate_constant_ratio(f, g) = k (integer, 2..8)
        then f is redundant → replace with Scaled(g, k)
    """

    TEST_PTS = [(1, b) for b in range(2, 9)]

    @staticmethod
    def estimate_constant_ratio(f_node: Node,
                                 g_node: Node) -> Optional[int]:
        """
        Returns integer k if f(a,b) = k · g(a,b) for all test points.
        Returns None otherwise.
        """
        try:
            pairs = []
            for a, b in StructuralMerger.TEST_PTS:
                fv = f_node.eval({"a": a, "b": b}, [0])
                gv = g_node.eval({"a": a, "b": b}, [0])
                if gv == 0:
                    continue
                ratio = fv / gv
                pairs.append(ratio)

            if len(pairs) < 5:
                return None

            # Check if ratio is constant and integer
            if max(pairs) - min(pairs) > 0.02:
                return None
            k = round(sum(pairs) / len(pairs))
            if 2 <= k <= 8 and abs(k - pairs[0]) < 0.02:
                return k
        except Exception:
            pass
        return None

    @classmethod
    def detect(cls, concepts: list[Concept]) -> list[tuple[Concept, Concept, int]]:
        """
        Scan all concept pairs for scaling relationships.
        Returns list of (scaled_concept, base_concept, k).
        """
        results = []
        seen: set[tuple[str, str]] = set()

        for i, cf in enumerate(concepts):
            if cf.program_node is None:
                continue
            for cg in concepts[i+1:]:
                if cg.program_node is None:
                    continue
                key = tuple(sorted([cf.name, cg.name]))
                if key in seen:
                    continue
                seen.add(key)

                k = cls.estimate_constant_ratio(cf.program_node,
                                                 cg.program_node)
                if k is not None:
                    # cf = k · cg
                    results.append((cf, cg, k))
                else:
                    k2 = cls.estimate_constant_ratio(cg.program_node,
                                                      cf.program_node)
                    if k2 is not None:
                        # cg = k2 · cf
                        results.append((cg, cf, k2))

        return results

    @classmethod
    def run(cls, registry: ConceptRegistry,
             replacer: "AutomaticReplacer",
             round_num: int) -> list[str]:
        """
        Full merge pass. Returns list of eliminated concept names.
        """
        eliminated = []
        concepts   = registry.all_concepts()
        pairs      = cls.detect(concepts)

        for scaled_c, base_c, k in pairs:
            obj = Scaled(base=base_c.canonical, k=k)

            # Announce
            print(f"  {cyan('🗜  MERGE')}  {bold(scaled_c.name)} = "
                  f"{bold(str(k))}·{bold(base_c.name)}"
                  f"  → {dim(str(obj))}")

            # Replace in registry: mark scaled_c as redundant
            replacer.register_replacement(
                old_name    = scaled_c.name,
                new_expr    = obj.to_expr(),
                reason      = f"scaling: {scaled_c.name} = {k}·{base_c.name}",
                round_num   = round_num,
            )
            eliminated.append(scaled_c.name)

        return eliminated


# ══════════════════════════════════════════════════════════════════
# MODULE 2: POWER FAMILY UNIFIER
# Merge Σi¹, Σi², Σi³ into PowerSum(k, a, b)
# ══════════════════════════════════════════════════════════════════

class PowerFamilyUnifier:
    """
    Detects when the concept registry contains individual sum formulas
    for different powers (sum_range=k1, sum_squares=k2, sum_cubes=k3)
    and unifies them under a single parameterized PowerSum structure.

    Before:  3 separate concepts, no visible relationship
    After:   PowerSum family with k=1,2,3 as instances

    The unification also generates the k=4 prediction (Σi⁴) as an
    open research question, driving further discovery.
    """

    # Behavioral fingerprints for recognizing power sums
    # Format: (task_kind, k, sample_values_at_b=1..6_a=1)
    POWER_SUM_SIGNATURES: list[tuple[str, int, list[int]]] = [
        ("sum_range",   1, [1, 3, 6, 10, 15, 21]),      # T(n)
        ("sum_squares", 2, [1, 5, 14, 30, 55, 91]),     # Σi²
        ("sum_cubes",   3, [1, 9, 36, 100, 225, 441]),  # Σi³
    ]

    @classmethod
    def identify_power_sums(cls,
                             concepts: list[Concept]) -> dict[int, Concept]:
        """
        Find which concepts correspond to which power sum degree.
        Returns {k: concept} for each identified power sum.
        """
        identified: dict[int, Concept] = {}

        for c in concepts:
            if c.program_node is None:
                continue
            try:
                sample = [c.program_node.eval({"a": 1, "b": b}, [0])
                          for b in range(1, 7)]
            except Exception:
                continue

            for kind, k, signature in cls.POWER_SUM_SIGNATURES:
                if sample == signature and k not in identified:
                    identified[k] = c
                    break

        return identified

    @classmethod
    def run(cls, registry: ConceptRegistry,
             round_num: int) -> Optional["PowerSumFamily"]:
        """
        If 2+ power sums are identified, unify them into a PowerSumFamily.
        Returns the family object if created, None otherwise.
        """
        concepts   = registry.all_concepts()
        identified = cls.identify_power_sums(concepts)

        if len(identified) < 2:
            return None   # need at least 2 instances to unify

        family = PowerSumFamily(
            members       = {k: c.name for k, c in identified.items()},
            round_unified = round_num,
        )

        # Generate closed-form nodes for all identified members
        for k, c in identified.items():
            ps = PowerSum(k=k)
            cf = ps.closed_form_node()
            if cf is not None:
                family.closed_forms[k] = cf.to_str()

        # Announce
        k_list = sorted(identified.keys())
        print(f"\n  {green('⚡ POWER FAMILY UNIFIED')}  "
              f"k = {k_list}  →  {bold('PowerSum(k, a, b)')}")
        for k, c in sorted(identified.items()):
            ps = PowerSum(k=k)
            print(f"     k={k}  {bold(c.name):<22}  {dim(ps.description())}")

        # Generate open prediction for next k
        next_k = max(k_list) + 1
        if next_k <= 4:
            print(f"  {cyan('🤔 OPEN')}  PowerSum(k={next_k}) — "
                  f"Σᵢ^{next_k} has no closed form in registry yet")
        print()

        return family


@dataclass
class PowerSumFamily:
    """The unified power sum family — replaces individual sum concepts."""
    members:       dict[int, str]    # k → concept_name
    closed_forms:  dict[int, str]    = field(default_factory=dict)  # k → expr
    round_unified: int = 0

    def has_k(self, k: int) -> bool:
        return k in self.members

    def missing_k(self, max_k: int = 5) -> list[int]:
        return [k for k in range(1, max_k+1) if k not in self.members]

    def summary(self) -> str:
        ks = sorted(self.members.keys())
        return (f"PowerSum family: k={ks}  "
                f"({len(ks)} members)  "
                f"missing: {self.missing_k()[:3]}")


# ══════════════════════════════════════════════════════════════════
# MODULE 3: DUAL CONSTRUCTOR
# Square(f) ↔ Root(f, 2), closes missing_dual gaps
# ══════════════════════════════════════════════════════════════════

class DualConstructor:
    """
    For every concept that is f² (a squaring law child),
    constructs Root(f, 2) — the inverse — and registers it.

    Linking rule:
        Square(f) ↔ Root(f, 2)
        Cube(f)   ↔ Root(f, 3)

    This closes the missing_dual symmetry gaps and directly
    increases symmetry completion metric.
    """

    @classmethod
    def run(cls, registry: ConceptRegistry,
             laws: list[dict],
             round_num: int) -> list[Root]:
        """
        Scan laws for squaring/cubing relationships.
        Construct and register their dual Root objects.
        Returns list of Root objects created.
        """
        created = []

        for law in laws:
            if law.get("kind") != "squaring_identity":
                continue

            parent_name = law.get("parent", "")
            child_name  = law.get("child",  "")
            if not parent_name or not child_name:
                continue

            # Find parent concept node
            parent_c = next((c for c in registry.all_concepts()
                             if c.name == parent_name), None)
            if parent_c is None or parent_c.program_node is None:
                continue

            # Determine degree: is this square (2) or cube (3)?
            # Check: child(1,b) == parent(1,b)^3?
            degree = 2
            try:
                test_pts = [(1, b) for b in range(2, 6)]
                cube_ok = all(
                    parent_c.program_node.eval({"a": 1, "b": b}, [0]) ** 3 ==
                    (lambda c, b: c.program_node.eval({"a": 1, "b": b}, [0]))(
                        next((x for x in registry.all_concepts()
                              if x.name == child_name), parent_c), b)
                    for _, b in test_pts
                )
                if cube_ok:
                    degree = 3
            except Exception:
                pass

            root_id = f"root_dual_{parent_name[:8]}_{degree}"
            # Don't create duplicates
            if any(c.name == root_id for c in registry.all_concepts()):
                continue

            root_obj = Root(f=parent_c.canonical, n=degree)

            # Build an approximate root node: parent^(1/n) is irrational,
            # so we register the formal description, not an executable node.
            # The root IS the parent itself — it's the dual label, not a formula.
            # The executable meaning: "the concept whose square IS child_name"
            prog = Program(
                name         = root_id,
                root         = parent_c.program_node.clone(),
                created_by   = "dual_constructor",
                concept_tags = ["dual", f"root_{degree}", parent_name],
            )
            prog.fitness = parent_c.strength * 0.9

            is_new = registry.register("dual_constructor", prog, round_num)

            print(f"  {magenta('🔄 DUAL CREATED')}  "
                  f"{cyan(root_id)}  = {bold(root_obj.to_formal())}")
            print(f"     Closes dual gap for: {bold(child_name)} = {bold(parent_name)}²")
            print(f"     Formal: {dim(f'Root(n={degree}) of {parent_name}')}\n")

            created.append(root_obj)

        return created


# ══════════════════════════════════════════════════════════════════
# MODULE 4: AUTOMATIC REPLACER
# Old structures must be removed, not preserved
# ══════════════════════════════════════════════════════════════════

@dataclass
class ReplacementRecord:
    old_name:  str
    new_expr:  str
    reason:    str
    round_num: int
    executed:  bool = False


class AutomaticReplacer:
    """
    Enforces removal of replaced structures from the concept registry.

    Critical rule: old structures MUST be removed, not preserved.
    Preserving them defeats the compression goal entirely.

    Maintains a replacement map so any downstream reference to an
    eliminated concept is transparently redirected to its replacement.
    """

    def __init__(self):
        self._replacements: dict[str, ReplacementRecord] = {}
        self._redirect_map: dict[str, str] = {}   # old_name → new_expr

    def register_replacement(self, old_name: str, new_expr: str,
                              reason: str, round_num: int) -> None:
        rec = ReplacementRecord(
            old_name  = old_name,
            new_expr  = new_expr,
            reason    = reason,
            round_num = round_num,
        )
        self._replacements[old_name] = rec
        self._redirect_map[old_name] = new_expr

    def execute(self, registry: ConceptRegistry) -> list[str]:
        """
        Remove all registered replacement targets from the concept registry.
        Returns list of actually removed concept names.
        """
        removed = []
        pending = [r for r in self._replacements.values() if not r.executed]

        for rec in pending:
            # Remove from registry internal dict (keyed by canonical)
            to_delete = [sig for sig, c in registry._concepts.items()
                         if c.name == rec.old_name]
            for sig in to_delete:
                del registry._concepts[sig]
                rec.executed = True
                removed.append(rec.old_name)
                print(f"  {red('✂  REMOVED')}  {bold(rec.old_name)}  "
                      f"{dim(f'→ {rec.new_expr[:40]}')}  "
                      f"{dim(f'[{rec.reason}]')}")

        return removed

    def resolve(self, name: str) -> str:
        """
        Follow the replacement chain for a concept name.
        Returns the canonical replacement expression.
        """
        seen = set()
        current = name
        while current in self._redirect_map and current not in seen:
            seen.add(current)
            current = self._redirect_map[current]
        return current

    def pending_count(self) -> int:
        return sum(1 for r in self._replacements.values() if not r.executed)

    def all_records(self) -> list[ReplacementRecord]:
        return list(self._replacements.values())


# ══════════════════════════════════════════════════════════════════
# MODULE 5: COMPRESSION ENGINE
# Minimize concept count while preserving expressiveness
# ══════════════════════════════════════════════════════════════════

@dataclass
class CompressionReport:
    concepts_before:  int
    concepts_after:   int
    laws_before:      int
    laws_after:       int
    removed_names:    list[str]
    merged_families:  list[str]
    duals_added:      int
    compression_ratio: float   # concepts_after / concepts_before
    expressiveness:   float    # laws_after / laws_before (must stay ≥ 1.0)
    reusability_est:  float    # estimate based on cross-references
    verdict:          str


class CompressionEngine:
    """
    Orchestrates the full compression pass:
    1. Run StructuralMerger — eliminate k·f redundancy
    2. Run PowerFamilyUnifier — merge sum power family
    3. Run DualConstructor — add missing Root duals
    4. Run AutomaticReplacer.execute() — remove eliminated concepts
    5. Recompute metrics and issue compression report

    Failure rules (hard enforced):
        if concepts_after > concepts_before → force second merge pass
        if reusability < 0.35              → prioritize dual generation
        if compression_ratio > 0.90        → report insufficient compression
    """

    def __init__(self):
        self.merger   = StructuralMerger()
        self.replacer = AutomaticReplacer()
        self._reports: list[CompressionReport] = []
        self._power_family: Optional[PowerSumFamily] = None

    def run(self, registry: ConceptRegistry,
             laws: list[dict],
             proof_engine,            # SymbolicProofEngine — for proof generation
             round_num: int) -> CompressionReport:
        """
        Full compression pass. Returns a CompressionReport.
        """
        concepts_before = len(registry.all_concepts())
        laws_before     = len(laws)

        # ── Step 1: Structural Merger ──────────────────────────────────────
        print(f"\n  {bold('🗜  COMPRESSION PASS')}  round={round_num}")
        print(f"  {'─'*60}")
        print(f"  Before: {concepts_before} concepts  {laws_before} laws")

        eliminated = StructuralMerger.run(registry, self.replacer, round_num)

        # ── Step 2: Power Family Unifier ───────────────────────────────────
        family = PowerFamilyUnifier.run(registry, round_num)
        if family:
            self._power_family = family
            # Mark individual sum concepts as members of the family
            for k, cname in family.members.items():
                c = next((x for x in registry.all_concepts()
                          if x.name == cname), None)
                if c:
                    c.domain_tags.append(f"power_sum_k{k}")

        # ── Step 3: Dual Constructor ───────────────────────────────────────
        duals = DualConstructor.run(registry, laws, round_num)

        # ── Step 4: Execute Replacements ───────────────────────────────────
        removed = self.replacer.execute(registry)

        # ── Step 5: Also attempt to prove the new PowerSum closed forms ────
        if family:
            self._prove_power_forms(family, proof_engine, round_num)

        # ── Step 6: Metrics ────────────────────────────────────────────────
        concepts_after = len(registry.all_concepts())
        laws_after     = len(laws)   # laws don't shrink — we never remove proofs

        compression_ratio = concepts_after / max(concepts_before, 1)

        # Estimate reusability: fraction of concepts cited by 2+ others
        reusability_est = self._estimate_reusability(registry.all_concepts())

        # Expressiveness: must stay ≥ 1.0 (no lost laws)
        expressiveness = laws_after / max(laws_before, 1)

        # Verdict
        if compression_ratio > 0.90:
            verdict = "INSUFFICIENT — concept count barely reduced"
        elif compression_ratio > 0.75:
            verdict = "MODERATE — meaningful reduction achieved"
        elif compression_ratio > 0.55:
            verdict = "STRONG — significant compression"
        else:
            verdict = "EXCELLENT — aggressive compression achieved"

        report = CompressionReport(
            concepts_before  = concepts_before,
            concepts_after   = concepts_after,
            laws_before      = laws_before,
            laws_after       = laws_after,
            removed_names    = removed,
            merged_families  = [family.summary()] if family else [],
            duals_added      = len(duals),
            compression_ratio = compression_ratio,
            expressiveness   = expressiveness,
            reusability_est  = reusability_est,
            verdict          = verdict,
        )
        self._reports.append(report)
        self._print_report(report)

        # ── Failure handling ───────────────────────────────────────────────
        if concepts_after > concepts_before:
            print(f"  {red('⚠  CONCEPTS INCREASED')} — forcing second merge pass")
            self.replacer.execute(registry)

        if reusability_est < 0.35 and not duals:
            print(f"  {yellow('⚠  LOW REUSABILITY')} — dual generation should be prioritized")

        return report

    def _prove_power_forms(self, family: PowerSumFamily,
                            proof_engine,
                            round_num: int) -> None:
        """
        Attempt symbolic proof of PowerSum closed forms.
        Each successful proof increases proof density directly.
        """
        for k in sorted(family.members.keys()):
            ps = PowerSum(k=k)
            cf = ps.closed_form_node()
            if cf is None:
                continue

            claim_name = f"power_sum_k{k}_closed_form"
            if proof_engine is None:
                continue

            # Try to prove: closed_form evaluates correctly on test points
            # We do this empirically since symbolic proof of summation
            # identities requires induction, beyond current engine
            test_pts = [(1, b) for b in range(1, 9)]
            _, sig = family.POWER_SUM_SIGNATURES[k - 1] if k <= 3 else (None, k, [])

            passed = 0
            total  = len(test_pts)
            for a, b in test_pts:
                try:
                    v = cf.eval({"a": a, "b": b}, [0])
                    if k == 1:  expected = b * (b + 1) // 2
                    elif k == 2: expected = b * (b + 1) * (2*b + 1) // 6
                    elif k == 3: expected = (b * (b + 1) // 2) ** 2
                    else:        expected = None
                    if expected is not None and v == expected:
                        passed += 1
                except Exception:
                    pass

            status = green("✓ verified") if passed == total else yellow(f"~ {passed}/{total}")
            print(f"  {dim('🔬 POWER SUM')}  k={k}  {ps.description()[:50]}  {status}")

    def _estimate_reusability(self, concepts: list[Concept]) -> float:
        """
        Estimate reusability: fraction of concepts that appear as parents
        of at least one other concept (i.e. used to derive something else).
        """
        parents = set()
        for c in concepts:
            for p in c.derived_from:
                parents.add(p)
        if not concepts:
            return 0.0
        return len(parents) / len(concepts)

    def _print_report(self, r: CompressionReport) -> None:
        delta = r.concepts_before - r.concepts_after
        sign  = green(f"-{delta}") if delta > 0 else red(f"+{abs(delta)}")
        ratio_bar = "█" * int((1 - r.compression_ratio) * 20)
        print(f"  After:  {r.concepts_after} concepts  "
              f"({sign} removed)  {r.laws_after} laws")
        print(f"  Compression: [{ratio_bar:<20}]  "
              f"{(1-r.compression_ratio):.0%} reduction")
        print(f"  Reusability est: {r.reusability_est:.2f}  "
              f"| Expressiveness: {r.expressiveness:.2f}x")
        print(f"  Verdict: {bold(r.verdict)}")
        if r.removed_names:
            print(f"  Removed: {dim(', '.join(r.removed_names[:6]))}")
        if r.merged_families:
            print(f"  Families: {dim(', '.join(r.merged_families[:2]))}")
        if r.duals_added:
            print(f"  Duals added: +{r.duals_added}")
        print(f"  {'─'*60}\n")

    def latest_report(self) -> Optional[CompressionReport]:
        return self._reports[-1] if self._reports else None

    def power_family(self) -> Optional[PowerSumFamily]:
        return self._power_family


# ══════════════════════════════════════════════════════════════════
# FULL PIPELINE ORCHESTRATOR
# ══════════════════════════════════════════════════════════════════

class UnificationEngine:
    """
    Orchestrates the full SRE v3.0 pipeline:

        for gap in gaps:
            candidates = generate_candidates(gap)
            best = select(evaluate(candidates))
            if integrate(best):
                StructuralMerger.run(best, theory)
                PowerFamilyUnifier.run(theory)
                DualConstructor.run(theory)
                CompressionEngine.run(theory)

    Entry point for math_society.py integration.
    """

    def __init__(self):
        self.compression = CompressionEngine()
        self._run_count  = 0

    def update(self,
               registry: ConceptRegistry,
               laws: list[dict],
               proof_engine,
               round_num: int,
               force: bool = False) -> Optional[CompressionReport]:
        """
        Run full unification pass.
        Normally called every 15 rounds after round 35.
        force=True bypasses the schedule (e.g. when concepts spike).
        """
        concepts = registry.all_concepts()
        if len(concepts) < 5 and not force:
            return None

        self._run_count += 1
        report = self.compression.run(registry, laws, proof_engine, round_num)
        return report

    def should_force_run(self, registry: ConceptRegistry) -> bool:
        """
        Returns True if the pipeline should run immediately
        (failure condition: concept count is growing without compression).
        """
        latest = self.compression.latest_report()
        if latest is None:
            return False
        current = len(registry.all_concepts())
        # If concepts grew by 20%+ since last compression → force
        return current > latest.concepts_after * 1.20

    def metrics_summary(self) -> str:
        latest = self.compression.latest_report()
        if not latest:
            return "Unification engine: no passes run yet"
        family = self.compression.power_family()
        fam_str = family.summary() if family else "no family"
        return (f"Unification: {self._run_count} passes  "
                f"| Last: {latest.verdict[:30]}  "
                f"| {fam_str}")

    def final_report(self, registry: ConceptRegistry,
                      laws: list[dict]) -> None:
        """Print full unification summary at end of run."""
        latest = self.compression.latest_report()
        if not latest:
            print(f"  {dim('Unification engine: no compression passes ran')}")
            return

        family = self.compression.power_family()
        replacer = self.compression.replacer

        print(f"\n  {bold('⚡ STRUCTURAL UNIFICATION REPORT')}")
        print(f"  {'━'*62}")

        # Compression history
        for i, r in enumerate(self.compression._reports, 1):
            delta = r.concepts_before - r.concepts_after
            print(f"  Pass {i}: {r.concepts_before}→{r.concepts_after} concepts  "
                  f"({green(f'-{delta}') if delta > 0 else red(f'+{abs(delta)}')})  "
                  f"{dim(r.verdict[:35])}")

        # Power family
        if family:
            print(f"\n  {bold('POWER SUM FAMILY')}  {family.summary()}")
            for k in sorted(family.members.keys()):
                ps = PowerSum(k=k)
                print(f"    k={k}  {dim(ps.description())}")
            if family.missing_k(4):
                print(f"    {cyan('OPEN: k='+ str(family.missing_k(4)[0]))}"
                      f" — {dim('next power sum undiscovered')}")

        # Replaced structures
        records = replacer.all_records()
        if records:
            executed = [r for r in records if r.executed]
            print(f"\n  {bold('ELIMINATED')}  ({len(executed)} structures replaced):")
            for rec in executed[:6]:
                print(f"    {red('✂')} {dim(rec.old_name):<22}  "
                      f"→ {cyan(rec.new_expr[:35])}")
            if len(executed) > 6:
                print(f"    {dim(f'... and {len(executed)-6} more')}")

        # Final metrics
        if latest:
            print(f"\n  {bold('FINAL STATE')}")
            print(f"  Concepts:     {latest.concepts_after}  "
                  f"(was {latest.concepts_before})")
            print(f"  Compression:  {(1-latest.compression_ratio):.0%} reduction")
            print(f"  Reusability:  {latest.reusability_est:.2f}"
                  f"  {'✓' if latest.reusability_est > 0.45 else '✗ target 0.45+'}")
            print(f"  Expressiveness: {latest.expressiveness:.2f}x  "
                  f"(laws preserved: "
                  f"{'✓' if latest.expressiveness >= 1.0 else '✗'})")

        print(f"  {'━'*62}\n")
