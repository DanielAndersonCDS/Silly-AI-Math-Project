"""
ms_theory.py — Axiom Engine + Theory Tree

This module bridges the gap between "agents that find patterns" and
"agents that build formal theory."

TWO SYSTEMS:

1. AXIOM ENGINE
   Maintains a set of foundational truths that require no proof.
   Everything else is derived FROM axioms.
   Agents can cite axioms in proofs, making derivations traceable
   back to first principles.

   Core axioms (automatically registered):
     A1  a + 0 = a              (additive identity)
     A2  a + b = b + a          (commutativity of addition)
     A3  a * 1 = a              (multiplicative identity)
     A4  a * b = b * a          (commutativity of multiplication)
     A5  a * (b + c) = a*b + a*c  (distributive law)
     A6  a ** 1 = a             (power identity)
     A7  a ** 0 = 1             (zero power)

2. THEORY TREE
   Organizes mathematical knowledge hierarchically.
   Theorems build on top of axioms and prior theorems.
   When enough theorems cluster around a concept family,
   a new "theory branch" is automatically named and announced.

   Example tree that emerges:
     ARITHMETIC FOUNDATION
     ├── A1..A7 (axioms)
     ├── multiplication = repeated addition  (derived)
     └── SUMMATION THEORY
         ├── triangular number formula
         ├── partial sum formula (from triangular)
         └── POWER SUM THEORY
             ├── sum of squares
             └── Nicomachus' theorem (from triangular)

THEORY BRANCHES ANNOUNCED WHEN:
  - 3+ theorems share a common parent concept
  - The branch has a recognisable mathematical character
  - At least one theorem in the branch is symbolically proven
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import random

from ms_core import *


# ── Axiom ─────────────────────────────────────────────────────────────────

@dataclass
class Axiom:
    id:       str    # e.g. 'A1'
    name:     str    # e.g. 'additive_identity'
    statement: str   # human-readable
    formal:   str    # symbolic form
    lhs:      str    # canonical LHS expression string
    rhs:      str    # canonical RHS expression string


# ── Theorem node in the theory tree ───────────────────────────────────────

@dataclass
class TheoremNode:
    theorem_id:   str
    name:         str
    statement:    str
    formal:       str
    proof_status: str          # 'axiom' | 'proven' | 'trivial' | 'empirical'
    proof_steps:  int = 0
    depends_on:   list[str] = field(default_factory=list)  # theorem/axiom ids
    round_added:  int = 0
    concept_tags: list[str] = field(default_factory=list)  # which concepts involved
    discoverer:   str = ""


# ── Theory branch ─────────────────────────────────────────────────────────

@dataclass
class TheoryBranch:
    branch_id:   str
    name:        str           # e.g. "Summation Theory"
    description: str
    root_axioms: list[str]     # axiom ids this branch builds on
    theorems:    list[str]     # theorem ids in this branch
    round_formed: int = 0
    parent_branch: Optional[str] = None


# ── Axiom Engine ──────────────────────────────────────────────────────────

class AxiomEngine:
    """
    Maintains the foundational axioms of the mathematical civilization.
    These are truths that need no proof — everything else derives from them.
    """

    def __init__(self):
        self._axioms: dict[str, Axiom] = {}
        self._register_core_axioms()

    def _register_core_axioms(self):
        """Register the core arithmetic axioms automatically."""
        core = [
            Axiom("A1", "additive_identity",
                  "Adding zero leaves a number unchanged",
                  "∀a: a + 0 = a",
                  "(a + 0)", "a"),
            Axiom("A2", "additive_commutativity",
                  "Addition order doesn't matter",
                  "∀a,b: a + b = b + a",
                  "(a + b)", "(b + a)"),
            Axiom("A3", "multiplicative_identity",
                  "Multiplying by one leaves a number unchanged",
                  "∀a: a × 1 = a",
                  "(a * 1)", "a"),
            Axiom("A4", "multiplicative_commutativity",
                  "Multiplication order doesn't matter",
                  "∀a,b: a × b = b × a",
                  "(a * b)", "(b * a)"),
            Axiom("A5", "distributive_law",
                  "Multiplication distributes over addition",
                  "∀a,b,c: a × (b + c) = a×b + a×c",
                  "(a * (b + c))", "((a * b) + (a * c))"),
            Axiom("A6", "power_identity",
                  "Any number to the power of 1 is itself",
                  "∀a: a¹ = a",
                  "(a ** 1)", "a"),
            Axiom("A7", "zero_power",
                  "Any nonzero number to the power of 0 is 1",
                  "∀a≠0: a⁰ = 1",
                  "(a ** 0)", "1"),
            Axiom("A8", "multiplicative_zero",
                  "Multiplying by zero gives zero",
                  "∀a: a × 0 = 0",
                  "(a * 0)", "0"),
        ]
        for ax in core:
            self._axioms[ax.id] = ax

    def all_axioms(self) -> list[Axiom]:
        return list(self._axioms.values())

    def get(self, axiom_id: str) -> Optional[Axiom]:
        return self._axioms.get(axiom_id)

    def which_axioms_apply(self, expression: str) -> list[str]:
        """Return list of axiom ids whose LHS appears in the expression."""
        applicable = []
        for ax in self._axioms.values():
            if ax.lhs in expression or ax.name.replace('_', ' ') in expression:
                applicable.append(ax.id)
        return applicable

    def summary(self) -> str:
        return (f"Axiom system: {len(self._axioms)} foundational axioms  "
                f"({', '.join(ax.id for ax in self._axioms.values()[:4])}...)")


# ── Theory Tree ────────────────────────────────────────────────────────────

class TheoryTree:
    """
    Organizes mathematical knowledge hierarchically.
    Theorems build on axioms and other theorems.
    Theory branches form when enough related theorems cluster.
    """

    # Minimum theorems in a branch before announcing it
    BRANCH_THRESHOLD = 1

    def __init__(self, axiom_engine: AxiomEngine):
        self._axioms  = axiom_engine
        self._theorems: dict[str, TheoremNode] = {}
        self._branches: dict[str, TheoryBranch] = {}
        self._announced_branches: set[str] = set()

        # Seed the tree with axioms as root theorems
        for ax in axiom_engine.all_axioms():
            node = TheoremNode(
                theorem_id   = ax.id,
                name         = ax.name,
                statement    = ax.statement,
                formal       = ax.formal,
                proof_status = "axiom",
                concept_tags = ["arithmetic"],
            )
            self._theorems[ax.id] = node

        # Create the root branch
        self._branches["arithmetic_foundation"] = TheoryBranch(
            branch_id    = "arithmetic_foundation",
            name         = "Arithmetic Foundation",
            description  = "The axioms of basic arithmetic",
            root_axioms  = [ax.id for ax in axiom_engine.all_axioms()],
            theorems     = [ax.id for ax in axiom_engine.all_axioms()],
            round_formed = 0,
        )

    # ── Adding theorems ───────────────────────────────────────────────────

    def add_theorem(self, law: dict, proof_status: str,
                    proof_steps: int, round_num: int,
                    discoverer: str = "",
                    proof_result: "Optional[ProofResult]" = None) -> TheoremNode:
        """
        Add a crystallised law as a theorem node in the tree.
        Automatically infers dependencies from the law's parent concept.
        If proof_result is provided, extracts lemma citations from it.
        """
        tid = f"T_{law['id'].replace(' ', '_')[:20]}"
        if tid in self._theorems:
            return self._theorems[tid]

        # Find which axioms this theorem uses
        depends = self._infer_dependencies(law)

        # If we have the full proof, extract lemma citations as additional deps
        if proof_result is not None:
            for step in proof_result.steps:
                if step.rule_name.startswith("by_lemma:"):
                    lemma_name = step.rule_name.split(":", 1)[1]
                    # Find the theorem node with this name
                    for existing_tid, existing_t in self._theorems.items():
                        if lemma_name in existing_t.name or \
                                existing_t.name.replace(" ", "_") in lemma_name:
                            if existing_tid not in depends:
                                depends.append(existing_tid)

        node = TheoremNode(
            theorem_id   = tid,
            name         = law.get("name", "Unknown"),
            statement    = law.get("statement", ""),
            formal       = law.get("formal",    law.get("statement", "")),
            proof_status = proof_status,
            proof_steps  = proof_steps,
            depends_on   = depends,
            round_added  = round_num,
            concept_tags = self._infer_tags(law),
            discoverer   = discoverer,
        )
        self._theorems[tid] = node
        return node

    def _infer_dependencies(self, law: dict) -> list[str]:
        """Infer which axioms/theorems a law depends on."""
        deps = []
        kind = law.get("kind", "")
        stmt = law.get("statement", "").lower()

        # Squaring laws use multiplicative commutativity and power rules
        if "squaring" in kind or "nicomachus" in law.get("name","").lower():
            deps.extend(["A3", "A4", "A6"])   # mul_identity, commutativity, power
        # Scaling laws use distributive
        if "scaling" in kind:
            deps.extend(["A3", "A5"])          # mul_identity, distributive
        # Sum-related theorems build on triangular if parent is triangular
        parent = law.get("parent", "")
        if "sum_formula" in parent or "triangular" in parent:
            # Find the triangular theorem in our tree
            for tid, t in self._theorems.items():
                if "triangular" in t.name or "sum_formula" in t.name:
                    deps.append(tid)
                    break

        return list(dict.fromkeys(deps))   # deduplicate preserving order

    def _infer_tags(self, law: dict) -> list[str]:
        """Assign concept tags based on law content."""
        tags = []
        name = law.get("name", "").lower()
        if "nicomachus" in name or "cube" in name:
            tags.extend(["summation", "power_sums", "cubes"])
        if "triangular" in name or "sum_range" in law.get("lhs_task",""):
            tags.extend(["summation", "triangular"])
        if "scaling" in name or "scaling" in law.get("kind", ""):
            tags.append("scaling")
        if "squaring" in name:
            tags.extend(["squaring", "quadratic"])
        if not tags:
            tags.append("arithmetic")
        return tags

    # ── Branch formation ──────────────────────────────────────────────────

    def detect_branches(self, round_num: int) -> list[TheoryBranch]:
        """
        Scan for new theory branches — groups of related theorems
        that deserve a named mathematical theory.
        """
        new_branches = []

        # Group non-axiom theorems by tag
        tag_groups: dict[str, list[TheoremNode]] = {}
        for t in self._theorems.values():
            if t.proof_status == "axiom":
                continue
            for tag in t.concept_tags:
                tag_groups.setdefault(tag, []).append(t)

        branch_templates = {
            "summation": (
                "summation_theory",
                "Summation Theory",
                "Theorems about summing sequences of numbers",
            ),
            "power_sums": (
                "power_sum_theory",
                "Power Sum Theory",
                "Relationships between sums of powers (Nicomachus, etc.)",
            ),
            "squaring": (
                "quadratic_theory",
                "Quadratic Structure Theory",
                "Properties of squaring operations and quadratic growth",
            ),
            "scaling": (
                "scaling_theory",
                "Scaling & Proportionality Theory",
                "How multiplication creates proportional relationships",
            ),
        }

        for tag, theorems in tag_groups.items():
            if len(theorems) < self.BRANCH_THRESHOLD:
                continue
            if tag not in branch_templates:
                continue

            bid, bname, bdesc = branch_templates[tag]
            if bid in self._announced_branches:
                continue

            # Require at least one proven theorem (not just empirical)
            has_proof = any(
                t.proof_status in ("proven", "trivial")
                for t in theorems
            )
            if not has_proof:
                continue

            branch = TheoryBranch(
                branch_id     = bid,
                name          = bname,
                description   = bdesc,
                root_axioms   = ["A1", "A2", "A3", "A4", "A5"],
                theorems      = [t.theorem_id for t in theorems],
                round_formed  = round_num,
                parent_branch = "arithmetic_foundation",
            )
            self._branches[bid] = branch
            self._announced_branches.add(bid)
            new_branches.append(branch)
            self._announce_branch(branch, theorems)

        return new_branches

    def _announce_branch(self, branch: TheoryBranch,
                          theorems: list[TheoremNode]) -> None:
        proven = [t for t in theorems if t.proof_status in ("proven", "trivial")]
        empirical = [t for t in theorems if t.proof_status == "empirical"]

        print(f"\n  {'━'*64}")
        print(f"  🌳 {bold('THEORY BRANCH FORMED')}  "
              f"{bold(cyan(branch.name))}")
        print(f"     {dim(branch.description)}")
        print(f"     Theorems: {len(theorems)} total  "
              f"({len(proven)} proven, {len(empirical)} empirical)")
        print(f"     {dim('Builds on: ' + ', '.join(branch.root_axioms[:4]))}")
        print(f"  {'━'*64}\n")

    # ── Queries ───────────────────────────────────────────────────────────

    def path_to_axioms(self, theorem_id: str,
                        depth: int = 0) -> list[str]:
        """
        Trace a theorem's dependency chain back to axioms.
        Returns a list of (theorem_id, depth) pairs.
        """
        if depth > 10:
            return []
        node = self._theorems.get(theorem_id)
        if node is None:
            return []

        result = [(theorem_id, depth)]
        for dep in node.depends_on:
            result.extend(self.path_to_axioms(dep, depth + 1))
        return result

    def format_chain(self, theorem_id: str) -> str:
        """Format the full derivation chain from axioms to this theorem."""
        chain = self.path_to_axioms(theorem_id)
        if not chain:
            return "  (not in theory tree)"

        lines = ["  Derivation chain:"]
        seen = set()
        for tid, depth in chain:
            if tid in seen:
                continue
            seen.add(tid)
            node = self._theorems.get(tid)
            if node is None:
                continue
            indent = "  " + "  " * depth
            badge = {
                "axiom":    "⬡ AXIOM",
                "proven":   "✓ DERIVED",
                "trivial":  "≡ IDENTITY",
                "empirical":"~ EMPIRICAL",
            }.get(node.proof_status, "?")
            lines.append(f"{indent}{badge}  {node.name}")
            if node.proof_status == "axiom":
                lines.append(f"{indent}  {dim(node.formal)}")

        return "\n".join(lines)

    def summary(self) -> str:
        non_axiom = [t for t in self._theorems.values()
                     if t.proof_status != "axiom"]
        proven    = [t for t in non_axiom if t.proof_status in ("proven","trivial")]
        branches  = [b for b in self._branches.values()
                     if b.branch_id != "arithmetic_foundation"]

        return (f"Theory tree: {len(self._theorems)} nodes  "
                f"({len(self._axioms.all_axioms())} axioms, "
                f"{len(non_axiom)} theorems, "
                f"{len(proven)} proven)  |  "
                f"{len(branches)} named branches")

    @property
    def _axioms(self) -> AxiomEngine:
        return self._axiom_engine

    @_axioms.setter
    def _axioms(self, value: AxiomEngine):
        self._axiom_engine = value