"""
ms_semantics.py — Semantic Meaning Engine

The gap the document identifies: syntax vs semantics.

  Syntax:    "(a * b)" — a formula involving multiplication
  Semantics: "scaling" — what multiplication MEANS

Real mathematical understanding requires BOTH.
Humans understand multiplication as:
  - repeated addition (operational)
  - scaling (geometric)
  - combination counting (combinatorial)
  - area (visual)

This module derives computable semantic roles from concept behavior,
and maintains a proof strategy library so agents can reason about
WHAT TO TRY before brute-forcing.

TWO SYSTEMS:

1. SEMANTIC TAGGER
   Analyzes concept behavior and assigns meaning tags:
     - algebraic role (identity, zero, inverse, closure)
     - growth character (linear, quadratic, exponential)
     - symmetry properties (commutative, associative)
     - structural role (building block, derived, meta-operation)
     - geometric intuition (area-like, distance-like, scaling)

2. PROOF STRATEGY LIBRARY
   Named heuristics agents can apply before brute-force exploration:
     - "expand and simplify" (try distributive)
     - "find the symmetry" (try commutativity)
     - "factor out common terms"
     - "look for telescoping"
     - "square both sides"
     - "use the known identity for this family"

   When an agent is stuck on a proof, it asks:
   "Which strategy does this expression suggest?"
   Then applies that strategy's lemma pattern.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

from ms_core import *


# ── Semantic Tag ──────────────────────────────────────────────────────────

@dataclass
class SemanticTag:
    """
    A semantic meaning attached to a concept.
    Not just the formula — what it MEANS mathematically.
    """
    concept_name: str
    canonical:    str

    # Algebraic properties (derived from behavior)
    commutative:    bool = False
    zero_at_zero:   bool = False   # f(a,0) = 0
    identity_at_1:  bool = False   # f(a,1) = a
    always_positive: bool = False

    # Growth character
    growth_type:  str = "unknown"  # 'constant'|'linear'|'quadratic'|'cubic'|'exponential'|'complex'

    # Structural role
    structural_role: str = "unknown"  # 'primitive'|'composition'|'power'|'sum'|'meta'

    # Human-readable semantic roles
    roles:        list[str] = field(default_factory=list)

    # Geometric intuition (derived from growth and symmetry)
    geometric_intuition: str = ""  # 'area-like'|'distance-like'|'scaling'|'counting'

    # Proof strategy hints
    suggested_strategies: list[str] = field(default_factory=list)


# ── Semantic Tagger ───────────────────────────────────────────────────────

class SemanticTagger:
    """
    Analyzes concepts and assigns semantic meaning tags.
    These are derived computationally from behavioral analysis —
    not hand-coded lookups.
    """

    def __init__(self):
        self._tags: dict[str, SemanticTag] = {}

    def tag_concept(self, concept: "Concept") -> Optional[SemanticTag]:
        """
        Analyze a concept's behavior and assign semantic meaning tags.
        Returns the SemanticTag, or None if analysis fails.
        """
        if concept.name in self._tags:
            return self._tags[concept.name]
        if concept.program_node is None:
            return None

        node = concept.program_node
        tag  = SemanticTag(concept_name=concept.name, canonical=concept.canonical)

        # ── Behavioral sampling ───────────────────────────────────────
        try:
            b_vals = [node.eval({"a": 1, "b": b}, [0]) for b in range(1, 9)]
            tag.always_positive = all(v > 0 for v in b_vals)
        except Exception:
            b_vals = []

        # ── Algebraic properties ──────────────────────────────────────
        try:
            # Commutative: f(2,3) == f(3,2)?
            v23 = node.eval({"a": 2, "b": 3}, [0])
            v32 = node.eval({"a": 3, "b": 2}, [0])
            tag.commutative = (v23 == v32)
        except Exception:
            pass

        try:
            # Zero-preserving: f(a,0) == 0?
            v0 = node.eval({"a": 5, "b": 0}, [0])
            tag.zero_at_zero = (v0 == 0)
        except Exception:
            pass

        try:
            # Identity at 1: f(a,1) == a?
            v1 = node.eval({"a": 5, "b": 1}, [0])
            tag.identity_at_1 = (v1 == 5)
        except Exception:
            pass

        # ── Growth character ──────────────────────────────────────────
        if len(b_vals) >= 6:
            tag.growth_type = self._classify_growth(b_vals)

        # ── Structural role ───────────────────────────────────────────
        canon = concept.canonical
        if canon in ("(a + b)", "(a * b)", "(a ** b)"):
            tag.structural_role = "primitive"
        elif "** 2" in canon or "** 3" in canon:
            tag.structural_role = "power"
        elif "//" in canon and "+" in canon:
            tag.structural_role = "sum"
        elif "loop" in canon:
            tag.structural_role = "iterative"
        else:
            tag.structural_role = "composition"

        # ── Human-readable roles ──────────────────────────────────────
        roles = []
        if tag.commutative:
            roles.append("commutative")
        if tag.zero_at_zero:
            roles.append("zero-annihilating")
        if tag.identity_at_1:
            roles.append("has-multiplicative-identity")
        if tag.growth_type == "linear":
            roles.append("linear-scaling")
        elif tag.growth_type == "quadratic":
            roles.append("quadratic-growth")
        elif tag.growth_type == "exponential":
            roles.append("exponential-growth")
        if tag.always_positive:
            roles.append("positive-definite")
        tag.roles = roles

        # ── Geometric intuition ───────────────────────────────────────
        if tag.growth_type == "quadratic" and tag.always_positive:
            tag.geometric_intuition = "area-like (scales as square of dimension)"
        elif tag.growth_type == "linear" and tag.commutative:
            tag.geometric_intuition = "scaling (uniform growth in any direction)"
        elif tag.growth_type == "exponential":
            tag.geometric_intuition = "compound growth (self-multiplying)"
        elif tag.growth_type == "quadratic":
            tag.geometric_intuition = "quadratic growth"
        elif tag.structural_role == "sum":
            tag.geometric_intuition = "accumulation (summing a sequence)"

        # ── Proof strategy hints ──────────────────────────────────────
        strats = []
        if tag.commutative:
            strats.append("try_commutativity")
        if tag.zero_at_zero:
            strats.append("use_zero_annihilation")
        if tag.identity_at_1:
            strats.append("use_multiplicative_identity")
        if tag.structural_role == "power":
            strats.append("expand_power")
        if tag.structural_role == "sum":
            strats.append("try_telescoping")
        if tag.growth_type == "quadratic":
            strats.append("factor_quadratic")
        tag.suggested_strategies = strats

        self._tags[concept.name] = tag
        return tag

    def _classify_growth(self, vals: list[int]) -> str:
        """Classify a sequence into growth type via finite differences."""
        diffs = [vals]
        for _ in range(4):
            prev = diffs[-1]
            if len(prev) < 2:
                break
            diffs.append([prev[i+1]-prev[i] for i in range(len(prev)-1)])

        for degree, diff_seq in enumerate(diffs):
            if len(diff_seq) >= 2 and len(set(diff_seq)) == 1:
                labels = ["constant", "linear", "quadratic", "cubic", "quartic"]
                return labels[degree] if degree < len(labels) else f"poly{degree}"

        nonzero = [v for v in vals if v != 0]
        if len(nonzero) >= 4:
            try:
                ratios = [nonzero[i+1]/nonzero[i] for i in range(min(4,len(nonzero)-1))]
                if max(ratios)-min(ratios) < 0.1 and ratios[0] > 1.1:
                    return "exponential"
            except Exception:
                pass
        return "complex"

    def tag_all(self, concepts: list["Concept"]) -> dict[str, SemanticTag]:
        """Tag all concepts in a registry."""
        for c in concepts:
            if c.program_node is not None:
                self.tag_concept(c)
        return self._tags

    def get_tag(self, name: str) -> Optional[SemanticTag]:
        return self._tags.get(name)

    def concepts_with_role(self, role: str) -> list[str]:
        """Return names of all concepts that have a given semantic role."""
        return [name for name, tag in self._tags.items()
                if role in tag.roles]

    def summary(self) -> str:
        if not self._tags:
            return "No semantic tags yet"
        commutative = sum(1 for t in self._tags.values() if t.commutative)
        quadratic   = sum(1 for t in self._tags.values() if t.growth_type == "quadratic")
        exponential = sum(1 for t in self._tags.values() if t.growth_type == "exponential")
        return (f"Semantics: {len(self._tags)} tagged  "
                f"({commutative} commutative, {quadratic} area-like, "
                f"{exponential} exponential-growth)")


# ── Proof Strategy Library ────────────────────────────────────────────────

@dataclass
class ProofStrategy:
    """
    A named proof heuristic: when to use it and what to try.
    This is how mathematicians guide proofs before brute-force.
    """
    name:        str
    description: str
    trigger:     str   # when to apply (semantic condition)
    action:      str   # what to do (which lemmas/rules to try first)
    priority:    float = 0.5


class ProofStrategyLibrary:
    """
    A collection of named proof strategies.
    When the proof engine is about to explore, it first asks:
    "Which strategy fits this expression?"
    Then applies that strategy's recommended actions.

    This is the difference between:
      Brute-force: "try all rules until one works"
      Strategic:   "this looks like a distributive problem → try distributive first"
    """

    def __init__(self):
        self._strategies = self._build_strategies()

    def _build_strategies(self) -> dict[str, ProofStrategy]:
        return {
            "expand_and_simplify": ProofStrategy(
                name        = "expand_and_simplify",
                description = "Expand products and collect like terms",
                trigger     = "expression contains products of sums",
                action      = "apply distributive law, then collect constants",
                priority    = 0.9,
            ),
            "use_symmetry": ProofStrategy(
                name        = "use_symmetry",
                description = "Reorder commutative operations to canonical form",
                trigger     = "expression has commutative operations in non-canonical order",
                action      = "apply commutativity rules to sort terms",
                priority    = 0.8,
            ),
            "identity_reduction": ProofStrategy(
                name        = "identity_reduction",
                description = "Simplify using identity elements (×1, +0, ^1)",
                trigger     = "expression contains multiplication by 1, addition of 0, or power of 1",
                action      = "apply identity rules to simplify",
                priority    = 0.95,
            ),
            "power_laws": ProofStrategy(
                name        = "power_laws",
                description = "Use power product and power-of-power laws",
                trigger     = "expression contains multiple powers of the same base",
                action      = "apply power_product or power_of_power rule",
                priority    = 0.85,
            ),
            "factor_common": ProofStrategy(
                name        = "factor_common",
                description = "Factor out common subexpressions",
                trigger     = "same subexpression appears in multiple terms",
                action      = "reverse-distributive: a*x + b*x → (a+b)*x",
                priority    = 0.7,
            ),
            "telescoping": ProofStrategy(
                name        = "telescoping",
                description = "Recognize sum-of-differences pattern",
                trigger     = "expression is a sum where consecutive terms cancel",
                action      = "apply telescope: f(b) - f(a-1)",
                priority    = 0.75,
            ),
            "square_expansion": ProofStrategy(
                name        = "square_expansion",
                description = "Expand (a+b)² = a² + 2ab + b²",
                trigger     = "expression is a square of a sum",
                action      = "apply distributive twice to expand square",
                priority    = 0.8,
            ),
        }

    def suggest_for_expression(self, expr_str: str,
                                tag: Optional[SemanticTag] = None) -> list[ProofStrategy]:
        """
        Given an expression string (and optional semantic tag),
        suggest which proof strategies to try first.
        """
        suggestions = []

        # Rule-based suggestions from expression structure
        if " * (" in expr_str or "* (" in expr_str:
            suggestions.append(self._strategies["expand_and_simplify"])
        if " + 0" in expr_str or "* 1)" in expr_str or "** 1)" in expr_str:
            suggestions.append(self._strategies["identity_reduction"])
        if " ** 2)" in expr_str and "+" in expr_str:
            suggestions.append(self._strategies["square_expansion"])
        if "** " in expr_str and expr_str.count("**") >= 2:
            suggestions.append(self._strategies["power_laws"])
        if "// 2" in expr_str and "-" in expr_str:
            suggestions.append(self._strategies["telescoping"])

        # Semantic-based suggestions
        if tag:
            if tag.commutative:
                suggestions.append(self._strategies["use_symmetry"])
            if tag.identity_at_1 or tag.zero_at_zero:
                suggestions.append(self._strategies["identity_reduction"])
            for strat_name in tag.suggested_strategies:
                if strat_name in self._strategies:
                    suggestions.append(self._strategies[strat_name])

        # Sort by priority, deduplicate
        seen = set()
        unique = []
        for s in sorted(suggestions, key=lambda s: -s.priority):
            if s.name not in seen:
                seen.add(s.name)
                unique.append(s)

        return unique[:4]

    def all_strategies(self) -> list[ProofStrategy]:
        return list(self._strategies.values())

    def describe_approach(self, expr_str: str,
                           tag: Optional[SemanticTag] = None) -> str:
        """
        Return a human-readable description of the proof approach.
        This is "mathematical intuition" — knowing what to try.
        """
        strats = self.suggest_for_expression(expr_str, tag)
        if not strats:
            return "explore broadly (no specific strategy suggested)"
        top = strats[0]
        return f"{top.name}: {top.description}"
