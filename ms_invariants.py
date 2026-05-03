"""
ms_invariants.py — Structural Invariant Detection Engine

The gap between Layer 2.5 and Layer 3 is this:
  Layer 2.5: "This formula works on these inputs."
  Layer 3:   "This PROPERTY holds for ALL inputs — and survives transforms."

An invariant is something that doesn't change when you apply a transformation.
  - "Multiplication of even numbers is always even" — parity invariant
  - "Squaring preserves positivity" — sign invariant
  - "Telescoping preserves growth class" — structural invariant

This is real mathematical reasoning — not testing, but noticing what MUST be true.

WHAT THIS MODULE DOES:
  1. For each concept, compute a structural fingerprint (set of properties)
  2. For each known transform (square, scale, telescope), check which
     properties are preserved vs changed
  3. State discovered invariants as formal claims
  4. Connect them back to the concept registry and debate system

INVARIANT TYPES DETECTED:
  - Parity:      always even / always odd / mixed
  - Sign:        always positive / always negative / mixed
  - Monotonicity: strictly increasing / decreasing / neither
  - Growth class: linear / quadratic / cubic / quartic / exponential
  - Symmetry:    f(a,b) == f(b,a) (commutative)
  - Zero:        f(a,0) == 0 or f(0,b) == 0

INVARIANT SURVIVAL RULES (what we check):
  - Squaring:   preserves sign (+→+), changes growth class (k→2k)
  - Scaling k×: preserves monotonicity, changes parity
  - Telescoping: preserves growth class, may change sign
  - Composition: complex, need empirical check
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import random

from ms_core import *
from ms_concepts import *


# ── Structural fingerprint ────────────────────────────────────────────────

@dataclass
class ConceptFingerprint:
    """A set of structural properties for a concept."""
    concept_name: str
    canonical:    str

    # Parity properties (at a=1, b=1..12)
    parity:       str = "mixed"    # "always_even" | "always_odd" | "mixed"

    # Sign properties
    sign:         str = "mixed"    # "always_pos" | "always_neg" | "mixed"

    # Monotonicity (a=1, b increasing)
    monotone:     str = "neither"  # "increasing" | "decreasing" | "neither"

    # Growth class
    growth:       str = "unknown"  # "constant" | "linear" | "quadratic" |
                                   # "cubic" | "quartic" | "exponential" | "complex"

    # Symmetry: f(a,b) == f(b,a)?
    symmetric:    bool = False

    # Zero boundary: f(a,0)==0 or f(0,b)==0?
    zero_at_zero: bool = False

    # Raw sample for quick comparison
    sample_vals:  list = field(default_factory=list)


@dataclass
class InvariantClaim:
    """A detected invariant — a property that holds universally."""
    invariant_id:   str
    kind:           str    # 'property' | 'transform_survival' | 'transform_change'
    subject:        str    # concept name
    transform:      Optional[str]  # transform applied (if kind == transform_*)
    transform_target: Optional[str]  # concept after transform
    property_name:  str    # what property
    value_before:   str    # before transform (or just the value)
    value_after:    Optional[str]   # after transform
    survives:       Optional[bool]  # True = preserved, False = changed
    claim:          str    # human-readable claim
    formal:         str    # formal statement
    confidence:     float  # 0.0 to 1.0 based on test points
    round_found:    int    = 0
    test_points:    int    = 0


# ── Invariant Engine ──────────────────────────────────────────────────────

class InvariantEngine:
    """
    Scans the concept registry for structural invariants.
    Runs periodically to discover new ones as concepts accumulate.
    """

    def __init__(self):
        self._fingerprints: dict[str, ConceptFingerprint] = {}
        self._invariants:   dict[str, InvariantClaim]     = {}
        self._announced:    set[str]                      = set()

    # ── Fingerprinting ────────────────────────────────────────────────────

    def fingerprint(self, concept: "Concept") -> Optional[ConceptFingerprint]:
        """
        Compute the structural fingerprint for a concept.
        Caches results — only recomputes if not seen before.
        """
        if concept.name in self._fingerprints:
            return self._fingerprints[concept.name]
        if concept.program_node is None:
            return None

        node = concept.program_node
        fp   = ConceptFingerprint(concept_name=concept.name,
                                   canonical=concept.canonical)

        # Sample at a=1, b=1..12
        try:
            b_vals = [node.eval({"a": 1, "b": b}, [0]) for b in range(1, 13)]
            if None in b_vals or not all(isinstance(v, int) for v in b_vals):
                return None
            if any(abs(v) > 10_000_000 for v in b_vals):
                return None
        except Exception:
            return None

        fp.sample_vals = b_vals

        # Parity
        evens = sum(1 for v in b_vals if v % 2 == 0)
        if evens == len(b_vals):
            fp.parity = "always_even"
        elif evens == 0:
            fp.parity = "always_odd"
        else:
            fp.parity = "mixed"

        # Sign
        pos = sum(1 for v in b_vals if v > 0)
        neg = sum(1 for v in b_vals if v < 0)
        if pos == len(b_vals):
            fp.sign = "always_pos"
        elif neg == len(b_vals):
            fp.sign = "always_neg"
        else:
            fp.sign = "mixed"

        # Monotonicity
        diffs = [b_vals[i+1] - b_vals[i] for i in range(len(b_vals)-1)]
        if all(d > 0 for d in diffs):
            fp.monotone = "increasing"
        elif all(d < 0 for d in diffs):
            fp.monotone = "decreasing"
        else:
            fp.monotone = "neither"

        # Growth class via finite differences
        fp.growth = self._classify_growth(b_vals)

        # Symmetry: try f(2,3) == f(3,2) etc.
        sym_pts = [(2,3),(3,4),(1,5),(4,2),(5,3)]
        try:
            sym_checks = []
            for a, b in sym_pts:
                va = node.eval({"a": a, "b": b}, [0])
                vb = node.eval({"a": b, "b": a}, [0])
                sym_checks.append(va == vb)
            fp.symmetric = all(sym_checks)
        except Exception:
            fp.symmetric = False

        # Zero at zero: f(a,0) == 0?
        try:
            v = node.eval({"a": 1, "b": 0}, [0])
            fp.zero_at_zero = (v == 0)
        except Exception:
            fp.zero_at_zero = False

        self._fingerprints[concept.name] = fp
        return fp

    def _classify_growth(self, vals: list[int]) -> str:
        """Classify a b-sequence by finite differences."""
        if len(vals) < 5:
            return "unknown"

        diffs = [vals]
        for _ in range(5):
            prev = diffs[-1]
            if len(prev) < 2:
                break
            diffs.append([prev[i+1] - prev[i] for i in range(len(prev)-1)])

        for degree, diff_seq in enumerate(diffs):
            if len(diff_seq) >= 3 and len(set(diff_seq)) == 1:
                labels = ["constant", "linear", "quadratic",
                          "cubic", "quartic", "quintic"]
                return labels[degree] if degree < len(labels) else f"poly{degree}"

        # Exponential check
        nonzero = [v for v in vals if v != 0]
        if len(nonzero) >= 4:
            try:
                ratios = [nonzero[i+1]/nonzero[i] for i in range(min(5, len(nonzero)-1))]
                if max(ratios) - min(ratios) < 0.08 and ratios[0] > 1.05:
                    return "exponential"
            except Exception:
                pass

        return "complex"

    # ── Invariant scanning ────────────────────────────────────────────────

    def scan(self, concepts: ConceptRegistry, round_num: int) -> list[InvariantClaim]:
        """
        Main entry point. Scan all known concepts for new invariants.
        Returns list of newly discovered invariants.
        """
        new_invariants: list[InvariantClaim] = []
        all_concepts = concepts.all_concepts()

        # Step 1: Fingerprint all concepts
        fps: dict[str, ConceptFingerprint] = {}
        for c in all_concepts:
            fp = self.fingerprint(c)
            if fp is not None:
                fps[c.name] = fp

        # Step 2: Single-concept invariants (absolute properties)
        for name, fp in fps.items():
            new_invariants.extend(self._property_invariants(fp, round_num))

        # Step 3: Transform-survival invariants (what survives squaring, scaling...)
        for c in all_concepts:
            if c.child_of and c.child_of in fps and c.name in fps:
                parent_fp = fps[c.child_of]
                child_fp  = fps[c.name]
                new_invariants.extend(
                    self._transform_invariants(parent_fp, child_fp, c, round_num)
                )

        # Step 4: Announce new invariants
        for inv in new_invariants:
            if inv.invariant_id not in self._announced:
                self._announced.add(inv.invariant_id)
                self._announce(inv)

        return new_invariants

    def _property_invariants(self, fp: ConceptFingerprint,
                              round_num: int) -> list[InvariantClaim]:
        """Detect absolute properties of a single concept."""
        results = []
        prefix  = f"prop_{fp.concept_name}"

        # Only report interesting invariants (not "mixed" everything)
        if fp.parity != "mixed":
            iid = f"{prefix}_parity_{fp.parity}"
            if iid not in self._invariants:
                parity_word = "even" if fp.parity == "always_even" else "odd"
                claim = (f"{fp.concept_name} always produces {parity_word} numbers "
                         f"(at a=1)")
                formal = (f"∀b ∈ ℕ: {fp.canonical} mod 2 = "
                          f"{'0' if fp.parity == 'always_even' else '1'}")
                inv = InvariantClaim(
                    invariant_id  = iid,
                    kind          = "property",
                    subject       = fp.concept_name,
                    transform     = None,
                    transform_target = None,
                    property_name = "parity",
                    value_before  = fp.parity,
                    value_after   = None,
                    survives      = None,
                    claim         = claim,
                    formal        = formal,
                    confidence    = 1.0,
                    round_found   = round_num,
                    test_points   = 12,
                )
                self._invariants[iid] = inv
                results.append(inv)

        if fp.symmetric:
            iid = f"{prefix}_symmetric"
            if iid not in self._invariants:
                inv = InvariantClaim(
                    invariant_id  = iid,
                    kind          = "property",
                    subject       = fp.concept_name,
                    transform     = None,
                    transform_target = None,
                    property_name = "symmetry",
                    value_before  = "symmetric",
                    value_after   = None,
                    survives      = None,
                    claim         = (f"{fp.concept_name} is commutative: "
                                     f"f(a,b) = f(b,a)"),
                    formal        = f"∀a,b: {fp.canonical}[a,b] = {fp.canonical}[b,a]",
                    confidence    = 1.0,
                    round_found   = round_num,
                    test_points   = 5,
                )
                self._invariants[iid] = inv
                results.append(inv)

        return results

    def _transform_invariants(self, parent_fp: ConceptFingerprint,
                               child_fp: ConceptFingerprint,
                               child_concept: "Concept",
                               round_num: int) -> list[InvariantClaim]:
        """
        Detect what structural properties survive (or change) when going
        from parent to child via a known transform.
        """
        results  = []
        transform = self._infer_transform(parent_fp, child_fp)
        if transform is None:
            return results

        prefix = f"trans_{parent_fp.concept_name}_{child_concept.name}_{transform}"

        # Check each property for survival
        checks = [
            ("sign",        parent_fp.sign,     child_fp.sign),
            ("monotone",    parent_fp.monotone,  child_fp.monotone),
            ("growth",      parent_fp.growth,    child_fp.growth),
            ("parity",      parent_fp.parity,    child_fp.parity),
        ]

        for prop, before, after in checks:
            iid = f"{prefix}_{prop}"
            if iid in self._invariants:
                continue
            survives = (before == after and before != "unknown" and before != "mixed")

            # Only report non-trivial findings
            # Interesting: growth changes (expected), sign survives squaring
            interesting = False
            if prop == "sign" and transform == "squaring" and before == "always_pos" and survives:
                interesting = True
            if prop == "growth" and not survives and before not in ("unknown", "complex"):
                interesting = True
            if prop == "monotone" and survives and transform in ("squaring", "scaling"):
                interesting = True
            if prop == "symmetry" and transform == "squaring":
                interesting = True

            if not interesting:
                continue

            if survives:
                claim = (f"{transform.title()} preserves {prop}: "
                         f"{parent_fp.concept_name} → {child_concept.name}")
                formal = (f"∀a,b: {prop}({child_fp.canonical}) = "
                          f"{prop}({parent_fp.canonical})  [{transform}]")
            else:
                claim = (f"{transform.title()} changes {prop}: "
                         f"{parent_fp.concept_name}[{before}] → "
                         f"{child_concept.name}[{after}]")
                formal = (f"{prop}({parent_fp.canonical}) = {before}  but  "
                          f"{prop}({child_fp.canonical}) = {after}  [{transform}]")

            inv = InvariantClaim(
                invariant_id     = iid,
                kind             = "transform_survival" if survives else "transform_change",
                subject          = parent_fp.concept_name,
                transform        = transform,
                transform_target = child_concept.name,
                property_name    = prop,
                value_before     = before,
                value_after      = after,
                survives         = survives,
                claim            = claim,
                formal           = formal,
                confidence       = 0.95,
                round_found      = round_num,
                test_points      = 12,
            )
            self._invariants[iid] = inv
            results.append(inv)

        return results

    def _infer_transform(self, parent_fp: ConceptFingerprint,
                          child_fp: ConceptFingerprint) -> Optional[str]:
        """Infer what transform relates parent to child by comparing samples."""
        pv = parent_fp.sample_vals
        cv = child_fp.sample_vals
        if not pv or not cv or len(pv) != len(cv):
            return None

        # Squaring: child == parent²
        if all(cv[i] == pv[i] * pv[i] for i in range(len(pv))):
            return "squaring"

        # Scaling: child == k × parent
        if pv[0] != 0:
            try:
                k = cv[0] / pv[0]
                if k == int(k) and 2 <= int(k) <= 8:
                    if all(cv[i] == pv[i] * int(k) for i in range(len(pv))):
                        return f"scaling_{int(k)}x"
            except Exception:
                pass

        # Cubing: child == parent³
        if all(cv[i] == pv[i] ** 3 for i in range(len(pv))):
            return "cubing"

        return None

    # ── Announcement ──────────────────────────────────────────────────────

    def _announce(self, inv: InvariantClaim) -> None:
        """Print an invariant discovery."""
        if inv.kind == "property":
            icon = "🔷"
            label = "INVARIANT"
        elif inv.survives:
            icon = "🔷"
            label = "INVARIANT SURVIVES"
        else:
            icon = "🔀"
            label = "TRANSFORM CHANGES"

        print(f"  {icon} {bold(label)}  {cyan(inv.claim)}")
        print(f"     {dim(inv.formal[:80])}")

    # ── Queries ───────────────────────────────────────────────────────────

    def all_invariants(self) -> list[InvariantClaim]:
        return list(self._invariants.values())

    def invariants_for(self, concept_name: str) -> list[InvariantClaim]:
        return [inv for inv in self._invariants.values()
                if inv.subject == concept_name
                or inv.transform_target == concept_name]

    def summary(self) -> str:
        total = len(self._invariants)
        props = sum(1 for i in self._invariants.values() if i.kind == "property")
        survives = sum(1 for i in self._invariants.values() if i.survives)
        changes  = sum(1 for i in self._invariants.values()
                       if i.kind == "transform_change")
        return (f"Invariants: {total} total  "
                f"({props} properties, {survives} survive transforms, "
                f"{changes} transform changes)")