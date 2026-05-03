"""
ms_symmetry.py — Theory Symmetry Scanner

The upgrade from curiosity-by-proximity to curiosity-by-purpose.

Current system asks: "if f² exists, does f³?"  (pattern extension)
This system asks:    "addition is commutative — why isn't T(n)?" (structural tension)

The difference is the SOURCE of the question:
  Pattern extension: what's the next case in a known sequence?
  Symmetry pressure: what SHOULD exist based on structural analogies?

FOUR SYMMETRY SCANNERS:

1. PROPERTY INHERITANCE SCANNER
   If concept B is derived from concept A, and A has property P,
   then B SHOULD also have P (or there should be a reason it doesn't).
   When B lacks a property its parents have → "inheritance gap conjecture"

   Example: addition is commutative. multiplication is commutative.
   Their derived formula T(n) is NOT commutative.
   → Conjecture: "Does T(n) have a commutative analog T(a,b) = T(b,a)?"

2. FAMILY COMPLETENESS SCANNER  
   If a concept family has k members with property P and 0 members with Q,
   but P and Q are structurally analogous → "missing dual conjecture"

   Example: Quadratic family has squaring (f²) but no cubing family entry.
   → Conjecture: "Should the quadratic family have a cubic counterpart?"

3. GROWTH LADDER SCANNER
   When a sequence of concepts has growth rates [linear, quadratic],
   there's structural pressure to complete the ladder.
   → Conjecture: "Is there a cubic-growth formula in this family?"

4. DEGREE JUMP SCANNER
   When a parent concept has degree D and a child has degree D+2,
   there should be an intermediate concept with degree D+1.
   → Conjecture: "What bridges linear and quadratic in this family?"
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


# ── Structural Conjecture ─────────────────────────────────────────────────

@dataclass
class StructuralConjecture:
    """
    A conjecture generated from structural imbalance detection.
    More specific and mathematically motivated than curiosity predictions.
    """
    conjecture_id: str
    kind:          str    # 'inheritance_gap'|'missing_dual'|'ladder_gap'|'degree_jump'
    claim:         str    # human-readable conjecture
    formal:        str    # formal statement
    tension_score: float  # how structurally significant this gap is (0-1)
    source_concepts: list[str]  # which concepts triggered this
    predicted_expr: Optional[str] = None
    status:        str = "open"   # 'open'|'resolved'|'refuted'


# ── Theory Symmetry Scanner ───────────────────────────────────────────────

class TheorySymmetryScanner:
    """
    Scans the concept graph for structural imbalances and generates
    mathematically motivated conjectures.

    Unlike curiosity-by-proximity, these conjectures arise from noticing
    that the theory is structurally *incomplete* or *asymmetric*.
    """

    def __init__(self):
        self._conjectures: dict[str, StructuralConjecture] = {}
        self._announced:   set[str] = set()

    def scan(self, concepts: list,
             semantic_tags: dict,   # concept_name → SemanticTag
             laws: list[dict],
             round_num: int) -> list[StructuralConjecture]:
        """
        Run all four scanners and return newly detected structural conjectures.
        """
        new_conjectures: list[StructuralConjecture] = []

        new_conjectures.extend(
            self._scan_property_inheritance(concepts, semantic_tags, round_num))
        new_conjectures.extend(
            self._scan_growth_ladder(concepts, semantic_tags, round_num))
        new_conjectures.extend(
            self._scan_family_completeness(concepts, semantic_tags, laws, round_num))
        new_conjectures.extend(
            self._scan_degree_jump(concepts, semantic_tags, round_num))

        return new_conjectures

    def _scan_property_inheritance(self, concepts: list,
                                    tags: dict,
                                    round_num: int) -> list[StructuralConjecture]:
        """
        Scanner 1: If B is derived from A, and A has property P but B doesn't,
        generate a conjecture about why B lacks P.
        """
        results = []
        concept_map = {c.name: c for c in concepts}

        for c in concepts:
            if not c.derived_from:
                continue
            c_tag = tags.get(c.name)
            if c_tag is None:
                continue

            for parent_name in c.derived_from:
                p_tag = tags.get(parent_name)
                if p_tag is None:
                    continue

                # Check: parent is commutative but child is not
                if p_tag.commutative and not c_tag.commutative:
                    cid = f"inherit_comm_{c.name[:12]}"
                    if cid not in self._conjectures:
                        conj = StructuralConjecture(
                            conjecture_id  = cid,
                            kind           = "inheritance_gap",
                            claim          = (f"{parent_name} is commutative but "
                                              f"{c.name} is not — "
                                              f"is there a symmetrized version?"),
                            formal         = (f"∃ f: f(a,b) = f(b,a) and "
                                              f"f captures the essence of {c.name}"),
                            tension_score  = 0.7,
                            source_concepts= [parent_name, c.name],

                        )
                        self._conjectures[cid] = conj
                        results.append(conj)

                # Check: parent has identity at 1 but child has identity at 0 only
                if p_tag.identity_at_1 and not c_tag.identity_at_1 and c_tag.zero_at_zero:
                    cid = f"inherit_ident_{c.name[:12]}"
                    if cid not in self._conjectures:
                        conj = StructuralConjecture(
                            conjecture_id  = cid,
                            kind           = "inheritance_gap",
                            claim          = (f"{parent_name} has multiplicative identity "
                                              f"but {c.name} only has zero identity — "
                                              f"what normalizes {c.name}?"),
                            formal         = (f"∃ n₀: {c.name}(a, n₀) = a for all a"),
                            tension_score  = 0.6,
                            source_concepts= [parent_name, c.name],
                        )
                        self._conjectures[cid] = conj
                        results.append(conj)

        return results

    def _scan_growth_ladder(self, concepts: list,
                             tags: dict,
                             round_num: int) -> list[StructuralConjecture]:
        """
        Scanner 3: When growth rates form an incomplete ladder,
        conjecture that the missing rungs exist.
        """
        results = []

        # Group concepts by growth type
        by_growth: dict[str, list[str]] = {}
        for c in concepts:
            t = tags.get(c.name)
            if t and t.growth_type not in ("unknown", "complex"):
                by_growth.setdefault(t.growth_type, []).append(c.name)

        # Check for ladder gaps
        growth_order = ["linear", "quadratic", "cubic", "quartic"]
        present = {g for g in growth_order if g in by_growth}

        # If linear and quadratic exist but cubic doesn't → conjecture cubic
        if "linear" in present and "quadratic" in present and "cubic" not in present:
            cid = "ladder_cubic_missing"
            if cid not in self._conjectures:
                # Find a quadratic concept to base the conjecture on
                quad_examples = by_growth.get("quadratic", [])[:2]
                conj = StructuralConjecture(
                    conjecture_id  = cid,
                    kind           = "ladder_gap",
                    claim          = (f"Growth ladder has linear and quadratic concepts "
                                      f"but no cubic — is there a cubic summation formula?"),
                    formal         = (f"∃ f: f grows as n³ and relates to known "
                                      f"quadratic concepts ({', '.join(quad_examples)})"),
                    tension_score  = 0.85,   # high — ladder gaps are strong structural signals
                    source_concepts= quad_examples,
                    predicted_expr = "((((1 + b) * b) // 2) ** 3)",  # T(n)^3 is the natural cubic completion
                )
                self._conjectures[cid] = conj
                results.append(conj)

        # If quadratic exists but no exponential → different kind of gap
        if "quadratic" in present and "exponential" not in present:
            cid = "ladder_exponential_missing"
            if cid not in self._conjectures:
                conj = StructuralConjecture(
                    conjecture_id  = cid,
                    kind           = "ladder_gap",
                    claim          = ("Quadratic growth exists but no exponential — "
                                      "is there a combinatorial explosion formula?"),
                    formal         = "∃ f: f(n) ~ aⁿ derived from known quadratic concepts",
                    tension_score  = 0.6,
                    source_concepts= list(present),
                )
                self._conjectures[cid] = conj
                results.append(conj)

        return results

    def _scan_family_completeness(self, concepts: list,
                                   tags: dict,
                                   laws: list[dict],
                                   round_num: int) -> list[StructuralConjecture]:
        """
        Scanner 2: If a concept family has members with property P
        but no members with the 'dual' of P, conjecture the dual exists.
        """
        results = []
        law_kinds = {l.get("kind", "") for l in laws}

        # Squaring laws exist but no factoring laws
        has_squaring = "squaring_identity" in law_kinds
        has_factoring = any("factor" in k for k in law_kinds)

        if has_squaring and not has_factoring:
            cid = "missing_factoring_law"
            if cid not in self._conjectures:
                conj = StructuralConjecture(
                    conjecture_id  = cid,
                    kind           = "missing_dual",
                    claim          = ("Squaring laws exist (f → f²) but no factoring laws — "
                                      "squaring and factoring are duals, factoring should exist"),
                    formal         = "∃ law: f² = g implies g = f·f (dual of squaring)",
                    tension_score  = 0.75,
                    source_concepts= [],
                )
                self._conjectures[cid] = conj
                results.append(conj)

        # Scaling laws exist but no division/ratio laws
        has_scaling = "scaling" in law_kinds
        has_division = any("divis" in k or "ratio" in k for k in law_kinds)

        if has_scaling and not has_division:
            cid = "missing_division_law"
            if cid not in self._conjectures:
                conj = StructuralConjecture(
                    conjecture_id  = cid,
                    kind           = "missing_dual",
                    claim          = ("Scaling laws (k·f) exist but no ratio laws — "
                                      "if scaling by k is a law, dividing by k should be too"),
                    formal         = "∃ law: (k·f)/k = f (inverse of scaling)",
                    tension_score  = 0.65,
                    source_concepts= [],
                )
                self._conjectures[cid] = conj
                results.append(conj)

        return results

    def _scan_degree_jump(self, concepts: list,
                           tags: dict,
                           round_num: int) -> list[StructuralConjecture]:
        """
        Scanner 4: When a parent has degree D and child has degree D+2,
        conjecture that an intermediate concept with degree D+1 exists.
        """
        results = []
        concept_map = {c.name: c for c in concepts}
        growth_degree = {"linear": 1, "quadratic": 2, "cubic": 3,
                         "exponential": 4, "constant": 0}

        for c in concepts:
            c_tag = tags.get(c.name)
            if c_tag is None:
                continue
            c_degree = growth_degree.get(c_tag.growth_type, -1)
            if c_degree < 0:
                continue

            for parent_name in c.derived_from:
                p_tag = tags.get(parent_name)
                if p_tag is None:
                    continue
                p_degree = growth_degree.get(p_tag.growth_type, -1)
                if p_degree < 0:
                    continue

                # Jump of 2+ degrees: there should be an intermediate
                if c_degree - p_degree >= 2:
                    cid = f"degree_jump_{parent_name[:8]}_{c.name[:8]}"
                    if cid not in self._conjectures:
                        intermediate_degree = growth_degree.get(
                            list(growth_degree.keys())[p_degree], "intermediate")
                        conj = StructuralConjecture(
                            conjecture_id  = cid,
                            kind           = "degree_jump",
                            claim          = (f"{parent_name} grows {p_tag.growth_type} "
                                              f"but {c.name} grows {c_tag.growth_type} — "
                                              f"a degree jump of {c_degree-p_degree}. "
                                              f"Is there an intermediate formula?"),
                            formal         = (f"∃ f: f has degree {p_degree+1} "
                                              f"bridging {parent_name} and {c.name}"),
                            tension_score  = 0.8,
                            source_concepts= [parent_name, c.name],
                        )
                        self._conjectures[cid] = conj
                        results.append(conj)

        return results

    def announce_new(self, conjectures: list[StructuralConjecture]) -> None:
        """Print newly detected structural conjectures."""
        for conj in conjectures:
            if conj.conjecture_id in self._announced:
                continue
            self._announced.add(conj.conjecture_id)
            sources = " + ".join(conj.source_concepts[:2])
            print(f"  {cyan('⚖️  STRUCTURAL TENSION')}  "
                  f"{bold(conj.kind.replace('_', ' ').upper())}")
            print(f"     {dim(conj.claim)}")
            print(f"     {dim(f'tension={conj.tension_score:.1f}  from: {sources}')}")

    def open_conjectures(self) -> list[StructuralConjecture]:
        return [c for c in self._conjectures.values() if c.status == "open"]

    def top_conjectures(self, n: int = 3) -> list[StructuralConjecture]:
        return sorted(self.open_conjectures(),
                      key=lambda c: -c.tension_score)[:n]

    def all_conjectures(self) -> list[StructuralConjecture]:
        return list(self._conjectures.values())

    def summary(self) -> str:
        total = len(self._conjectures)
        open_ = len(self.open_conjectures())
        by_kind = {}
        for c in self._conjectures.values():
            by_kind[c.kind] = by_kind.get(c.kind, 0) + 1
        kinds_str = "  ".join(f"{k}:{v}" for k, v in by_kind.items())
        return (f"Symmetry scanner: {total} structural conjectures "
                f"({open_} open)  |  {kinds_str}")


# ── Helper ────────────────────────────────────────────────────────────────

def cyan(s: str) -> str:
    return f"\x1b[96m{s}\x1b[0m"

def bold(s: str) -> str:
    return f"\x1b[1m{s}\x1b[0m"

def dim(s: str) -> str:
    return f"\x1b[2m{s}\x1b[0m"