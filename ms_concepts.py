"""
math_society_v6.py  —  Anti-Stagnation + Hard Constraints
───────────────────────────────────────────
Agents no longer have hardcoded discoveries. Instead they:

  1. Represent solutions as executable PROGRAM TREES
  2. Mutate those trees to generate new candidate programs
  3. Score candidates on correctness + compression + novelty + efficiency
  4. Keep the best programs in their personal library

No agent is ever told what to invent. Multiplication, exponentiation, and
the Gauss formula can only emerge if the mutation engine happens upon them
and selection pressure keeps them alive.

Program Tree Nodes
──────────────────
  Const(n)           → literal integer
  Var("a") / Var("b")→ task inputs
  Add(l, r)          → l + r
  Sub(l, r)          → l - r
  Mul(l, r)          → l * r  (costs 2 energy — "expensive primitive")
  Loop(body, count)  → repeat body `count` times, accumulating
  Fn("name", a, b)   → call a named abstraction from the agent's library

Mutation Operators
──────────────────
  GROW       — replace a Const/Var leaf with a small sub-expression
  SHRINK     — replace a sub-tree with a simpler node
  SWAP       — swap two sibling nodes
  LOOP_WRAP  — wrap a repeated addition pattern into a Loop node
  ABSTRACT   — if a program works well, name it and add to library
  COMBINE    — splice sub-trees from two different programs

Fitness
───────
  fitness = correctness_score
          + compression_bonus   (shorter tree = better)
          + novelty_bonus       (never-seen structure = +)
          - energy_cost

Usage
─────
  python math_society_v4.py                    # 5 agents, 30 rounds
  python math_society_v4.py --agents 6 --rounds 40
  python math_society_v4.py --budget 20        # tighter energy
  python math_society_v4.py --pop 12           # bigger mutation population
  python math_society_v4.py --fresh            # wipe db
  python math_society_v4.py --query agents
  python math_society_v4.py --query market
  python math_society_v4.py --query programs   # show best discovered programs
"""

import argparse
import copy
import json
import math
import random
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional


# ══════════════════════════════════════════════════════════════════
# TERMINAL COLOURS
# ══════════════════════════════════════════════════════════════════

class C:
    R="\033[0m"; B="\033[1m"; D="\033[2m"
    RED="\033[91m"; GRN="\033[92m"; YLW="\033[93m"
    BLU="\033[94m"; MAG="\033[95m"; CYN="\033[96m"

def bold(s):    return f"{C.B}{s}{C.R}"
def dim(s):     return f"{C.D}{s}{C.R}"
def red(s):     return f"{C.RED}{s}{C.R}"
def green(s):   return f"{C.GRN}{s}{C.R}"
def yellow(s):  return f"{C.YLW}{s}{C.R}"
def cyan(s):    return f"{C.CYN}{s}{C.R}"
def magenta(s): return f"{C.MAG}{s}{C.R}"
def blue(s):    return f"{C.BLU}{s}{C.R}"


# ══════════════════════════════════════════════════════════════════
# PROGRAM TREE
# ══════════════════════════════════════════════════════════════════

MAX_DEPTH   = 6     # tree depth cap — prevents bloat
MAX_ENERGY  = 500   # execution energy cap — prevents infinite loops

from ms_core import *

@dataclass
class Concept:
    """
    A discovered concept: a group of programs that share the same
    canonical form and pass the same generalisation tests.
    """
    name:        str
    canonical:   str          # canonical expression
    members:     list         # list of (agent_name, prog_name) pairs
    first_seen:  int          # round
    strength:    float = 0.0  # average fitness of members
    domain_tags: list = field(default_factory=list)
    derived_from: list = field(default_factory=list)  # parent concept names
    child_of:    str = ""     # primary parent (for display hierarchy)
    # Store the actual program node for algebra operations
    program_node: Any = field(default=None, compare=False, repr=False)
    # Discovery value components (updated by score_importance())
    importance_score:   float = 0.0   # composite importance
    unlocks_count:      int   = 0     # how many task kinds this unlocked
    compression_ratio:  float = 0.0   # strength / formula_complexity
    surprise_bonus:     float = 0.0   # set when it resolves a curiosity prediction

    def __str__(self):
        parent = f" ← {self.child_of}" if self.child_of else ""
        return (f"Concept({self.name!r}{parent}  canonical={self.canonical!r}  "
                f"members={len(self.members)}  strength={self.strength:.1f})")



def _task_sigma(task_kind: str) -> str:
    """Human-readable LHS for a task kind."""
    return {
        "sum_cubes":   "Σi³(1..n)",
        "sum_squares": "Σi²(1..n)",
        "sum_range":   "Σi(1..n)",
        "partial_sum": "Σi(a..n)",
    }.get(task_kind, task_kind)


def _law_name(task_kind: str, transform: str) -> str:
    names = {
        ("sum_cubes",   "square"):   "Nicomachus' Theorem",
        ("sum_squares", "weighted"): "Sum of Squares Law",
        ("sum_range",   "square"):   "Triangular Number Identity",
    }
    return names.get((task_kind, transform), f"{task_kind}_{transform}_law")



class ConceptRegistry:
    """
    Global registry of discovered concepts.
    Agents register programs here; the registry detects when multiple
    agents have independently found the same canonical form.
    """

    def __init__(self):
        # canonical_sig → Concept
        self._concepts: dict[str, Concept] = {}
        self._concept_counter = 0
        self.events: list[str] = []   # log of emergence events

    def register(self, agent_name: str, prog: Program, round_num: int) -> Optional[Concept]:
        """
        Register a program. If its canonical form matches an existing
        concept, add to that concept. If it's new, create a concept entry.
        Returns the Concept if a NEW concept just emerged or strengthened.
        """
        sig = canonical_signature(prog)
        if sig in self._concepts:
            c = self._concepts[sig]
            entry = (agent_name, prog.name)
            if entry not in c.members:
                c.members.append(entry)
                c.strength = max(c.strength, prog.fitness)
            # Keep the simpler (smaller) program_node — prefer b-only over two-var
            if c.program_node is None or prog.root.size() < c.program_node.size():
                c.program_node = prog.root.clone()
            return None   # not new — just grew

        # New concept
        self._concept_counter += 1
        name = self._name_concept(sig, prog)
        self._pending_relationships = {}
        derived = self._infer_parents(sig)
        rel_notes = dict(self._pending_relationships)
        parent  = derived[0] if derived else ""
        c = Concept(
            name=name,
            canonical=sig,
            members=[(agent_name, prog.name)],
            first_seen=round_num,
            strength=prog.fitness,
            domain_tags=list(prog.concept_tags),
            derived_from=derived,
            child_of=parent,
            program_node=prog.root.clone(),
        )
        self._concepts[sig] = c

        # Build emergence message — include relationship if detected
        rel_str = ""
        if parent and parent in rel_notes:
            rel_map = {
                "squared":    f"= {parent}²",
                "telescope":  f"= {parent}(b) − {parent}(a−1)",
                "contains":   f"extends {parent}",
                "*2": f"= 2·{parent}", "*3": f"= 3·{parent}",
                "*4": f"= 4·{parent}", "*6": f"= 6·{parent}",
            }
            rel_str = "  " + cyan(rel_map.get(rel_notes[parent],
                                               f"← {parent}"))

        msg = (f"  {magenta('🧠 CONCEPT')}  {bold(name)} emerged  "
               f"canonical={cyan(sig[:45])}  "
               f"by {agent_name}{rel_str}")
        self.events.append(msg)
        print(msg)
        return c

    # ── Open-Ended Relationship Discovery Engine ─────────────────────────
    #
    # This is the CRDE upgrade from "test known transform types" to
    # "search for any transform T such that T(A) ≈ B".
    #
    # Method: treat finding T as a small program synthesis problem.
    # We have a single-variable input (A's output value at each test point)
    # and target (B's output value). We search for a minimal program T(x)
    # that maps A's values to B's values. If found, the relationship is
    # "B = T(A)" — discovered purely from behavior, not from syntax.
    #
    # This is what lets agents discover relationships nobody anticipated.

    def discover_relationships(self, round_num: int,
                               max_pairs: int = 6) -> None:
        """
        Search for novel relationships between known concepts by trying to
        find a transform T such that T(A(b)) ≈ B(b) for a pair (A, B).

        Unlike _infer_parents (which tests a fixed list of transforms),
        this generates *arbitrary* small programs and tests them — so it
        can discover unexpected relationships.

        Runs every few rounds to avoid slowing the simulation.
        """
        concepts = [c for c in self._concepts.values()
                    if c.program_node is not None and c.strength >= 100]
        if len(concepts) < 2:
            return

        # Test points: b=1..8 with a=1 (b-only evaluation)
        test_bs = list(range(1, 9))

        def fingerprint(c: Concept) -> list[int] | None:
            vals = []
            for b in test_bs:
                try:
                    v = c.program_node.eval({"a": 1, "b": b}, [0])
                    if not isinstance(v, int) or abs(v) > 1_000_000:
                        return None
                    vals.append(v)
                except Exception:
                    return None
            return vals if len(set(vals)) >= 3 else None

        # Pre-compute fingerprints
        fps: dict[str, list[int]] = {}
        for c in concepts:
            fp = fingerprint(c)
            if fp:
                fps[c.canonical] = fp

        # Already-known relationships (don't re-announce)
        if not hasattr(self, "_known_relationships"):
            self._known_relationships: set[tuple[str, str]] = set()

        # Pick random pairs to examine this round
        strong = [c for c in concepts if c.canonical in fps]
        if len(strong) < 2:
            return
        pairs_tried = 0
        random.shuffle(strong)

        for i, ca in enumerate(strong):
            if pairs_tried >= max_pairs:
                break
            fp_a = fps[ca.canonical]

            for cb in strong[i+1:]:
                if pairs_tried >= max_pairs:
                    break
                fp_b = fps[cb.canonical]
                pair_key = tuple(sorted([ca.canonical, cb.canonical]))
                if pair_key in self._known_relationships:
                    continue
                pairs_tried += 1

                # Try to find T such that T(A(b)) = B(b) for all b
                transform = self._search_transform(fp_a, fp_b, ca, cb)
                if transform:
                    t_expr, t_name, confidence = transform
                    self._known_relationships.add(pair_key)
                    msg = (f"  {cyan('🔗 RELATION')}  "
                           f"{bold(ca.name)} {dim('→')} {cyan(t_name)} {dim('→')} "
                           f"{bold(cb.name)}  "
                           f"{dim(f'T(x)={t_expr[:35]}')}  "
                           f"{green(f'conf={confidence:.0%}')}")
                    self.events.append(msg)
                    print(msg)
                    # Store on the concept so proof narrator can use it
                    if not cb.derived_from or ca.name not in cb.derived_from:
                        cb.derived_from.append(ca.name)
                    if not cb.child_of:
                        cb.child_of = ca.name

    def _search_transform(
            self,
            fp_a: list[int],
            fp_b: list[int],
            ca: "Concept",
            cb: "Concept",
    ) -> tuple[str, str, float] | None:
        """
        Search for a small program T(x) such that T(fp_a[i]) ≈ fp_b[i].

        Uses a structured search over transform templates before falling
        back to mutation — ordered from simplest to most complex.

        Returns (expr_string, human_name, confidence) or None.
        """
        # ── Tier 1: Closed-form transforms ──────────────────────────────
        # These are fast exact tests for the most common relationships.
        # Any hit here is 100% confident.
        tests: list[tuple[str, str, callable]] = [
            # Arithmetic transforms on A
            ("x**2",        "square",        lambda x: x*x),
            ("x**3",        "cube",          lambda x: x*x*x),
            ("2*x",         "double",        lambda x: 2*x),
            ("3*x",         "triple",        lambda x: 3*x),
            ("x//2",        "halve",         lambda x: x//2),
            ("x//3",        "third",         lambda x: x//3),
            ("x+1",         "increment",     lambda x: x+1),
            ("x-1",         "decrement",     lambda x: x-1),
            ("x*(x+1)//2",  "triangularise", lambda x: x*(x+1)//2),
            ("x*(x-1)//2",  "choose2",       lambda x: x*(x-1)//2),
            # Relationships involving both A and index (b)
        ]

        for expr, name, fn in tests:
            try:
                predicted = [fn(x) for x in fp_a]
                matches = sum(p == t for p, t in zip(predicted, fp_b))
                if matches == len(fp_b):
                    return (expr, name, 1.0)
            except Exception:
                continue

        # ── Tier 2: Parameterised transforms ─────────────────────────────
        # Try T(x) = x*k + c for small k, c
        for k in range(-4, 8):
            for c in range(-6, 7):
                if k == 0 and c == 0:
                    continue
                try:
                    predicted = [x * k + c for x in fp_a]
                    if predicted == fp_b:
                        expr = (f"{k}*x+{c}" if c != 0 else f"{k}*x")
                        name = (f"scale×{k}+{c}" if c != 0 else f"scale×{k}")
                        return (expr, name, 1.0)
                except Exception:
                    continue

        # ── Tier 3: Mutual transforms ─────────────────────────────────────
        # Maybe A = T(B) instead of B = T(A)
        for expr, name, fn in tests[:6]:
            try:
                predicted = [fn(x) for x in fp_b]
                matches = sum(p == t for p, t in zip(predicted, fp_a))
                if matches == len(fp_a):
                    return (f"inv({expr})", f"inverse-{name}", 1.0)
            except Exception:
                continue

        # ── Tier 4: Fuzzy match — approximate transform ───────────────────
        # If nothing exact, check if there's a *nearly* consistent mapping.
        # Useful for catching near-misses that hint at a relationship.
        for expr, name, fn in tests:
            try:
                predicted = [fn(x) for x in fp_a]
                matches = sum(p == t for p, t in zip(predicted, fp_b))
                confidence = matches / len(fp_b)
                if confidence >= 0.75 and len(fp_b) >= 6:
                    return (f"~{expr}", f"approx-{name}", confidence)
            except Exception:
                continue

        return None

    def unify_check(self, round_num: int):
        """
        After each round, check if any two concepts are behaviourally
        equivalent by sampling test inputs and comparing outputs.
        Merge equivalent concepts and announce the unification.
        """
        concepts = list(self._concepts.values())
        if len(concepts) < 2:
            return
        # Test inputs must discriminate b-only formulas from two-variable ones.
        # Crucially: include cases where a ≠ 1 so that f(b)=b*(b+1)/2 and
        # g(a,b)=a*b*(a+b)/2 are NOT seen as equal (they only agree when a=1).
        test_envs = [{"a": a, "b": b}
                     for a, b in [(1,5),(1,10),(2,3),(3,4),(2,7)]]
        sigs = list(self._concepts.keys())
        merged: set[str] = set()
        for i in range(len(sigs)):
            if sigs[i] in merged: continue
            ci = self._concepts[sigs[i]]
            for j in range(i+1, len(sigs)):
                if sigs[j] in merged: continue
                cj = self._concepts[sigs[j]]
                if self._behaviourally_equal(sigs[i], sigs[j], test_envs):
                    # Merge j into i
                    ci.members.extend(cj.members)
                    ci.strength = max(ci.strength, cj.strength)
                    # Keep shorter canonical
                    if len(sigs[j]) < len(sigs[i]):
                        ci.canonical = sigs[j]
                    merged.add(sigs[j])
                    msg = (f"  {magenta('🔗 UNIFIED')}  "
                           f"{cyan(ci.name)} ← {cyan(cj.name)}  "
                           f"(same behaviour, different form)")
                    self.events.append(msg)
                    print(msg)
        for sig in merged:
            del self._concepts[sig]

    # ── Concept Family Clustering ────────────────────────────────────────
    #
    # Periodically groups known concepts by growth signature into families.
    # A "family" is a cluster of concepts that share the same mathematical
    # character — all exponentials, all quadratics, all linear, etc.
    #
    # When an agent is stuck and runs _classify_sequence, the result now
    # includes "known siblings" — other concepts in the same family. This
    # gives the agent concrete exemplars to mutate from, not just a label.

    def cluster_families(self, round_num: int) -> dict[str, list[str]]:
        """
        Group all known concepts by behavioral growth family.
        Returns { family_name: [concept_name, ...] }.
        Announces new family formations.
        """
        if not hasattr(self, "_families"):
            self._families: dict[str, list[str]] = {}
            self._family_announced: set[str] = set()

        test_bs = list(range(1, 9))
        families: dict[str, list[str]] = {}

        for c in self._concepts.values():
            if c.program_node is None:
                continue
            # Compute growth signature
            try:
                vals = [c.program_node.eval({"a": 1, "b": b}, [0])
                        for b in test_bs]
                if None in vals or not all(isinstance(v, int) for v in vals):
                    continue
                vals = [v for v in vals if abs(v) < 10_000_000]
                if len(vals) < 5:
                    continue
            except Exception:
                continue

            family = self._growth_family(vals)
            families.setdefault(family, []).append(c.name)

        # Announce newly formed families (3+ members)
        for fam, members in families.items():
            if len(members) >= 3:
                key = f"{fam}:{len(members)}"
                if key not in self._family_announced and len(members) > len(
                        self._families.get(fam, [])):
                    self._family_announced.add(key)
                    print(f"  {cyan('🏛  FAMILY')}  {bold(fam.upper())} "
                          f"— {len(members)} concepts share this structure: "
                          f"{dim(', '.join(members[:4]))}"
                          f"{dim(f' +{len(members)-4}' if len(members)>4 else '')}")

        self._families = families
        return families

    def _growth_family(self, vals: list[int]) -> str:
        """Classify a sequence into a growth family using finite differences."""
        if len(vals) < 4:
            return "unknown"
        diffs = [vals]
        for _ in range(4):
            prev = diffs[-1]
            if len(prev) < 2:
                break
            diffs.append([prev[i+1] - prev[i] for i in range(len(prev)-1)])

        for degree, diff_seq in enumerate(diffs):
            if len(diff_seq) >= 2 and len(set(diff_seq)) == 1:
                labels = ["constant", "linear", "quadratic", "cubic", "quartic"]
                return labels[degree] if degree < len(labels) else f"poly{degree}"

        # Exponential check
        nonzero = [v for v in vals if v != 0]
        if len(nonzero) >= 4:
            try:
                ratios = [nonzero[i+1]/nonzero[i] for i in range(len(nonzero)-1)]
                if max(ratios) - min(ratios) < 0.1 and ratios[0] > 1.05:
                    r = round(sum(ratios)/len(ratios), 1)
                    return f"exponential_r{r}"
            except Exception:
                pass
        return "complex"


    # ── Law Crystallisation ───────────────────────────────────────────────
    #
    # When the system has enough evidence that two concepts are related
    # by a verified mathematical identity (not just structural similarity),
    # it crystallises a NAMED LAW — a statement of the form
    # "For all n, [concept A] = [transform of concept B]".
    #
    # This is the transition from "I know a formula that works" to
    # "I know WHY it works and can state that as a universal rule."

    def crystallise_laws(self, task_solvers: dict[str, str],
                         round_num: int) -> list[dict]:
        """
        Scan the concept registry for relationships that qualify as laws.
        task_solvers: {task_kind: concept_name} — which concept currently
                      solves each task kind.

        Returns list of newly crystallised laws.
        """
        if not hasattr(self, "_known_laws"):
            self._known_laws: dict[str, dict] = {}   # law_id → law dict

        new_laws: list[dict] = []
        concepts = list(self._concepts.values())

        for child_c in concepts:
            if not child_c.child_of:
                continue
            parent_name = child_c.child_of
            # _concepts is keyed by canonical signature, not name.
            # Look up by name explicitly.
            parent_c = next(
                (c for c in concepts if c.name == parent_name), None
            )
            if parent_c is None or parent_c.program_node is None:
                continue
            if child_c.program_node is None:
                continue

            # ── Test 1: Is child = parent² (Nicomachus candidate)? ───────
            test_pts = [(1,b) for b in range(2, 9)]
            sq_verified = True
            for a, b in test_pts:
                try:
                    pv = parent_c.program_node.eval({"a": a, "b": b}, [0])
                    cv = child_c.program_node.eval({"a": a, "b": b}, [0])
                    if cv != pv * pv:
                        sq_verified = False
                        break
                except Exception:
                    sq_verified = False
                    break

            if not sq_verified:
                continue

            # ── Test 2: Does parent solve sum_range and child solve sum_cubes?
            parent_solves = task_solvers.get("sum_range")
            child_solves  = task_solvers.get("sum_cubes")

            # Check if parent concept IS the sum_range solver
            parent_is_triangular = (
                parent_name == parent_solves or
                parent_name in ("sum_formula_1", "triangular") or
                any(m.name == parent_name or
                    (hasattr(m, 'concept') and m.concept == parent_name)
                    for m in [parent_c])
            )

            # Also check by behavioural test: does parent(1,b) = b*(b+1)/2?
            tri_check = all(
                parent_c.program_node.eval({"a": 1, "b": b}, [0])
                == b * (b + 1) // 2
                for b in range(1, 9)
            )

            law_id = f"nicomachus_{parent_name}_{child_c.name}"
            if law_id in self._known_laws:
                continue

            if sq_verified and tri_check:
                # Nicomachus' theorem verified!
                law = {
                    "id":        law_id,
                    "name":      "Nicomachus' Theorem",
                    "statement": (f"For all n ≥ 1:  "
                                  f"1³+2³+…+n³ = (1+2+…+n)²"),
                    "formal":    (f"{child_c.canonical}  =  "
                                  f"({parent_c.canonical})²"),
                    "parent":    parent_name,
                    "child":     child_c.name,
                    "verified_pts": len(test_pts),
                    "round":     round_num,
                    "kind":      "squaring_identity",
                }
                self._known_laws[law_id] = law
                new_laws.append(law)
                self._announce_law(law)

            # ── General squaring law ──────────────────────────────────────
            elif sq_verified:
                law_id2 = f"square_law_{parent_name}_{child_c.name}"
                if law_id2 not in self._known_laws:
                    law = {
                        "id":        law_id2,
                        "name":      f"Squaring Law: {parent_name}",
                        "statement": (f"For all inputs:  "
                                      f"{child_c.name}(n) = {parent_name}(n)²"),
                        "formal":    (f"{child_c.canonical}  =  "
                                      f"({parent_c.canonical})²"),
                        "parent":    parent_name,
                        "child":     child_c.name,
                        "verified_pts": len(test_pts),
                        "round":     round_num,
                        "kind":      "squaring_identity",
                    }
                    self._known_laws[law_id2] = law
                    new_laws.append(law)
                    self._announce_law(law)

        # ── Scan for iteration laws ───────────────────────────────────────
        # If concept B = loop(A, k) for some k, then B = k·A (multiplication law)
        for c in concepts:
            if c.program_node is None:
                continue
            canon = c.canonical
            if not canon.startswith("loop("):
                continue
            # Extract inner expression
            for parent_c in concepts:
                if parent_c.name == c.name or parent_c.program_node is None:
                    continue
                # Test: c(a,b) == parent(a,b) * k for some small k
                for k in range(2, 7):
                    law_id = f"iter_law_{parent_c.name}_{c.name}_k{k}"
                    if law_id in self._known_laws:
                        continue
                    iter_ok = True
                    for a, b in [(1,3),(1,5),(2,4),(3,3)]:
                        try:
                            pv = parent_c.program_node.eval({"a":a,"b":b},[0])
                            cv = c.program_node.eval({"a":a,"b":b},[0])
                            if cv != pv * k:
                                iter_ok = False; break
                        except Exception:
                            iter_ok = False; break
                    if iter_ok:
                        law = {
                            "id":        law_id,
                            "name":      f"Scaling Law: {c.name} = {k}·{parent_c.name}",
                            "statement": (f"For all inputs:  "
                                          f"{c.name}(a,b) = {k} × {parent_c.name}(a,b)"),
                            "formal":    f"{c.canonical}  =  {k} × ({parent_c.canonical})",
                            "parent":    parent_c.name,
                            "child":     c.name,
                            "k":         k,
                            "verified_pts": 4,
                            "round":     round_num,
                            "kind":      "scaling",
                        }
                        self._known_laws[law_id] = law
                        new_laws.append(law)
                        self._announce_law(law)
                        break  # only one k per pair

        return new_laws

    def _announce_law(self, law: dict) -> None:
        """Print a law crystallisation announcement."""
        print(f"  {bold(yellow('📜 LAW CRYSTALLISED'))}  "
              f"{bold(law['name'])}")
        print(f"  {dim('  Statement:')}  {law['statement']}")
        print(f"  {dim('  Formal:   ')}  {dim(law['formal'][:70])}")
        print(f"  {dim('  Verified at')} {law['verified_pts']} test points — "
              f"stored as permanent knowledge")

    def all_laws(self) -> list[dict]:
        """Return all crystallised laws."""
        if not hasattr(self, "_known_laws"):
            return []
        return list(self._known_laws.values())

    def siblings_in_family(self, family: str) -> list[str]:
        """Return concept names in the same family as the given family label."""
        if not hasattr(self, "_families"):
            return []
        return self._families.get(family, [])

    def _behaviourally_equal(self, sig_a: str, sig_b: str,
                              test_envs: list[dict]) -> bool:
        node_a = _parse_expr_stub(sig_a)
        node_b = _parse_expr_stub(sig_b)
        results_a, results_b = [], []
        for env in test_envs:
            try:
                results_a.append(node_a.eval(dict(env), [0]))
                results_b.append(node_b.eval(dict(env), [0]))
            except Exception:
                return False
        # If both parsed to degenerate Const(0), don't merge
        if all(v == 0 for v in results_a) and all(v == 0 for v in results_b):
            return False
        # Must agree on ALL inputs AND produce at least 2 distinct values
        # (avoids merging constant-output programs)
        if results_a == results_b:
            return len(set(results_a)) >= 2
        return False

    def _infer_parents(self, sig: str) -> list[str]:
        """
        Detect structural relationships between a new concept and known ones.
        Goes beyond string containment to find behavioral relationships:
        - structural containment (known formula appears inside new one)
        - squaring (new = known²)
        - telescope (new = known(b) - known(a-1))
        - scaling (new = known * constant)
        - composition (new uses known as a sub-expression non-trivially)
        Returns a list of parent concept names, most specific first.
        """
        parents = []
        relationship_notes: dict[str, str] = {}  # name → how it's related

        try:
            new_node = _parse_expr_stub(sig)
        except Exception:
            new_node = None

        # Sample points for behavioral checks
        test_pts = [(1, 3), (1, 5), (1, 8), (2, 4), (2, 6)]

        def eval_node(node, a, b):
            try:
                return node.eval({"a": a, "b": b}, [0])
            except Exception:
                return None

        new_vals = None
        if new_node:
            new_vals = [eval_node(new_node, a, b) for a, b in test_pts]
            if None in new_vals:
                new_vals = None

        for existing_sig, c in self._concepts.items():
            if existing_sig == sig or len(existing_sig) <= 1:
                continue

            # ── Check 1: String containment (structural) ─────────────────
            if len(existing_sig) > 3 and existing_sig in sig:
                parents.append(c.name)
                relationship_notes[c.name] = "contains"
                continue

            if new_vals is None or c.program_node is None:
                continue

            # ── Check 2: Squaring ─────────────────────────────────────────
            # new(a,b) == known(a,b)²  for all test points
            known_vals = [eval_node(c.program_node, a, b) for a, b in test_pts]
            if None not in known_vals:
                if all(nv == kv * kv for nv, kv in zip(new_vals, known_vals)
                       if nv is not None and kv is not None):
                    parents.insert(0, c.name)   # most specific — put first
                    relationship_notes[c.name] = "squared"
                    continue

            # ── Check 3: Telescope ────────────────────────────────────────
            # new(a,b) == known(b) - known(a-1)
            # Only check b-only known formulas
            if not _uses_var_a(c.program_node):
                tele_vals = []
                ok = True
                for a, b in test_pts:
                    kb = eval_node(c.program_node, 1, b)
                    ka1 = eval_node(c.program_node, 1, a - 1)
                    if kb is None or ka1 is None:
                        ok = False; break
                    tele_vals.append(kb - ka1)
                if ok and tele_vals == new_vals:
                    parents.insert(0, c.name)
                    relationship_notes[c.name] = "telescope"
                    continue

            # ── Check 4: Constant scaling ─────────────────────────────────
            # new == known * k for some integer k (2..6)
            for k in (2, 3, 4, 6):
                if all(nv == kv * k for nv, kv in zip(new_vals, known_vals)
                       if nv is not None and kv is not None):
                    parents.append(c.name)
                    relationship_notes[c.name] = f"*{k}"
                    break

        # Store relationship notes on the concept so proof narrator can use them
        self._pending_relationships = relationship_notes
        return parents

    # Scratch space for relationship notes set by _infer_parents
    _pending_relationships: dict = {}

    # ── Concept Algebra ───────────────────────────────────────────────────
    #
    # These methods let the registry SUGGEST new programs by symbolically
    # transforming known ones. This is how agents generalise:
    #
    #   Known:   f(b)    — e.g. Gauss formula (((1+b)*b)//2)
    #   Derive:  f(a,b)  = f(b) - f(a-1)   (partial sum)
    #   Derive:  f(b+k)  — shifted version
    #   Derive:  f(b)**2 — square of formula (sum_cubes identity)
    #   Derive:  f(b)*g(b) — product of two formulas
    #
    # The algebra engine gives agents a "library of transformations"
    # rather than requiring them to find the same formula from scratch.

    def algebra_suggestions(self, task_kind: str) -> list[Program]:
        """
        Given the current task kind, look at known concepts and suggest
        algebraically derived programs that might solve the new task.

        This is the core of symbolic generalisation — 'I know f(b),
        can I build what I need from it?'
        """
        suggestions = []
        known = list(self._concepts.values())
        if not known: return suggestions

        for c in known:
            # Use stored program node (reliable) rather than re-parsing canonical string
            if c.program_node is not None:
                node = c.program_node.clone()
            else:
                node = _parse_expr_stub(c.canonical)
                if isinstance(node, Const): continue   # degenerate parse

            # ── Transform 1: Telescope difference ─────────────────────────
            # f(a,b) = f(b) - f(a-1)
            # Only valid when the known formula is purely a function of b —
            # i.e. it doesn't use Var("a"). Applying the telescope to a
            # two-variable formula like (a*b*(a+b)//2) breaks because 'a'
            # then appears with conflicting roles in f(b) vs f(a-1).
            if task_kind in ("partial_sum", "sum_range", "sum_cubes"):
                if not _uses_var_a(node):
                    f_b   = node.clone()
                    f_am1 = _sub_b_with(node.clone(), Sub(Var("a"), Const(1)))
                    derived = Sub(f_b, f_am1)
                    suggestions.append(Program(
                        name=f"alg_tele_{c.name[:6]}_{random.randint(0,99):02d}",
                        root=derived,
                        created_by="concept_algebra",
                        concept_tags=[task_kind]
                    ))

            # ── Transform 2: Square of known formula ─────────────────────
            # f(b)^2
            # Recognises that sum_cubes = gauss(b)^2
            if task_kind in ("sum_cubes", "power"):
                f_sq = Pow(node.clone(), Const(2))
                suggestions.append(Program(
                    name=f"alg_sq_{c.name[:6]}_{random.randint(0,99):02d}",
                    root=f_sq,
                    created_by="concept_algebra",
                    concept_tags=[task_kind]
                ))

            # ── Transform 3: Shift input ──────────────────────────────────
            # f(b+k) or f(b-k)
            # Allows formulas that were calibrated for 1-indexed to adapt
            for k in [1, -1, 2]:
                if k > 0:
                    shifted_b = Add(Var("b"), Const(k))
                else:
                    shifted_b = Sub(Var("b"), Const(-k))
                f_shifted = _sub_b_with(node.clone(), shifted_b)
                suggestions.append(Program(
                    name=f"alg_sh{k:+d}_{c.name[:5]}_{random.randint(0,99):02d}",
                    root=f_shifted,
                    created_by="concept_algebra",
                    concept_tags=[task_kind]
                ))

            # ── Transform 4: Compose two formulas ────────────────────────
            # f(b) * g(b) or f(b) + g(b) where g is also known
            for c2 in known:
                if c2.name == c.name: continue
                if c2.program_node is not None:
                    node2 = c2.program_node.clone()
                else:
                    node2 = _parse_expr_stub(c2.canonical)
                    if isinstance(node2, Const): continue
                for op_name, op_cls in [("mul", Mul), ("add", Add)]:
                    composed = op_cls(node.clone(), node2.clone())
                    suggestions.append(Program(
                        name=f"alg_{op_name}_{c.name[:4]}_{c2.name[:4]}",
                        root=composed,
                        created_by="concept_algebra",
                        concept_tags=[task_kind]
                    ))

            # ── Transform 5: Sum-of-squares from triangular number ────────
            # Σi² = b*(b+1)*(2b+1)//6 = gauss(b) * (2b+1) // 3
            # Only valid for b-only (single-variable) formulas — same guard
            # as the telescope transform.
            if task_kind == "sum_squares" and not _uses_var_a(node):
                two_b_plus_1 = Add(Mul(Const(2), Var("b")), Const(1))
                sq_formula = IDiv(Mul(node.clone(), two_b_plus_1), Const(3))
                suggestions.append(Program(
                    name=f"alg_sumsq_{c.name[:6]}_{random.randint(0,99):02d}",
                    root=sq_formula,
                    created_by="concept_algebra",
                    concept_tags=[task_kind]
                ))

        return suggestions[:16]   # raised cap slightly for new transform

    def _name_concept(self, sig: str, prog: Program) -> str:
        """Heuristic human-readable concept name from structure."""
        if sig == "(a + b)" or sig == "(b + a)":  return "addition"
        if sig == "(a * b)" or sig == "(b * a)":  return "multiplication"
        if sig == "(a ** b)":                      return "exponentiation"
        if "** b" in sig or "** 2" in sig:         return f"power_{self._concept_counter}"
        # Identify formula complexity by signature features
        if "// 6" in sig:                          return f"sum_squares_{self._concept_counter}"
        if "// 2" in sig and "** 2" in sig:        return f"sum_cubes_{self._concept_counter}"
        if "// 2" in sig and "*" in sig:           return f"sum_formula_{self._concept_counter}"
        if "loop" in sig and "loop" in sig[5:]:   return f"nested_loop_{self._concept_counter}"
        if sig.startswith("loop("):               return f"loop_concept_{self._concept_counter}"
        if "(a - b)" in sig:                      return "subtraction"
        tags = prog.concept_tags
        base = tags[0] if tags else "concept"
        return f"{base}_{self._concept_counter}"

    def summary(self) -> str:
        if not self._concepts:
            return dim("  (no concepts yet)")
        lines = [f"\n  {bold('CONCEPT REGISTRY')} ({len(self._concepts)} concepts):"]
        for sig, c in sorted(self._concepts.items(), key=lambda x: -x[1].strength):
            members_str = ", ".join(f"{a}/{p}" for a, p in c.members[:4])
            if len(c.members) > 4:
                members_str += f" +{len(c.members)-4}"
            lines.append(
                f"  {cyan(c.name):<22} canonical={dim(c.canonical[:35]):<37} "
                f"str={c.strength:5.1f}  members={members_str}"
            )
        return "\n".join(lines)

    def all_concepts(self) -> list[Concept]:
        return list(self._concepts.values())

    def score_importance(self, unlocked_by: dict[str, str] = None,
                          surprise_resolutions: dict[str, float] = None) -> None:
        """
        Compute and update importance scores for all concepts.
        Runs the three document-specified metrics:

        1. Consequence count: how many task kinds did this concept unlock?
           (importance ∝ unlocks_count)
        2. Compression ratio: fitness / formula_complexity
           (importance ∝ strength / node_size)
        3. Surprise bonus: set externally when concept resolves a curiosity prediction

        Also prints top-3 most important discoveries when called.
        """
        if unlocked_by is None:
            unlocked_by = {}
        if surprise_resolutions is None:
            surprise_resolutions = {}

        # Invert unlocked_by: {task_kind: concept_name} → count per concept
        unlock_counts: dict[str, int] = {}
        for task_kind, concept_name in unlocked_by.items():
            unlock_counts[concept_name] = unlock_counts.get(concept_name, 0) + 1

        for c in self._concepts.values():
            # Score 1: consequence count (unlocks)
            c.unlocks_count = unlock_counts.get(c.name, 0)

            # Score 2: compression ratio
            size = c.program_node.size() if c.program_node else 10
            c.compression_ratio = c.strength / max(size, 1)

            # Score 3: surprise bonus
            c.surprise_bonus = surprise_resolutions.get(c.name, 0.0)

            # Composite: weighted sum
            c.importance_score = (
                c.unlocks_count    * 40.0   +   # unlocking = big deal
                c.compression_ratio * 0.5   +   # elegance matters
                c.surprise_bonus    * 30.0       # confirmed prediction = big deal
            )


# ══════════════════════════════════════════════════════════════════
# GENERALISATION TESTER
# ══════════════════════════════════════════════════════════════════

# Held-out test inputs per task kind — never used during training
_GENERALISATION_TESTS: dict[str, list[tuple[int,int,int]]] = {
    "add":          [(11,7,18),(3,13,16),(9,9,18),(100,1,101)],
    "repeated_add": [(3,11,33),(7,13,91),(4,25,100),(8,9,72)],
    "power":        [(2,9,512),(3,4,81),(2,10,1024),(4,3,64)],
    "sum_range":    [(1,10,55),(1,20,210),(1,5,15),(1,15,120)],
    # NEW level-6 tasks — requires entirely new formula discovery
    # Σ i²  = n(n+1)(2n+1)/6
    "sum_squares":  [(1,5,55),(1,8,204),(1,10,385),(1,6,91)],
    # Σ i³  = [n(n+1)/2]²  (square of triangular number!)
    "sum_cubes":    [(1,4,100),(1,6,441),(1,5,225),(1,3,36)],
    # Alternating: 1-2+3-4+... = ceil(n/2) for odd, -n/2 for even
    "alt_sum":      [(1,4,-2),(1,5,3),(1,6,-3),(1,7,4)],
    # Partial sum starting from a: Σ from a..b = (b-a+1)*(a+b)/2
    "partial_sum":  [(3,8,33),(2,7,27),(4,9,39),(5,10,45)],
}

def _sum_squares(n: int) -> int:
    return n * (n+1) * (2*n+1) // 6

def _sum_cubes(n: int) -> int:
    t = n * (n+1) // 2
    return t * t

def _alt_sum(n: int) -> int:
    # 1-2+3-4+... to n terms
    return (n + 1) // 2 if n % 2 == 1 else -(n // 2)

def _partial_sum(a: int, b: int) -> int:
    return (b - a + 1) * (a + b) // 2

def _uses_var_a(node: Node) -> bool:
    """Return True if the tree contains any Var('a') leaf."""
    if isinstance(node, Var):
        return node.name == "a"
    if isinstance(node, Const):
        return False
    if isinstance(node, (Add, Sub, Mul, IDiv)):
        return _uses_var_a(node.left) or _uses_var_a(node.right)
    if isinstance(node, Pow):
        return _uses_var_a(node.base) or _uses_var_a(node.exp)
    if isinstance(node, Loop):
        return _uses_var_a(node.body) or _uses_var_a(node.count)
    if isinstance(node, IfNode):
        return any(_uses_var_a(c) for c in
                   (node.cond_left, node.cond_right, node.yes, node.no))
    if isinstance(node, CallFn):
        return _uses_var_a(node.arg_a) or _uses_var_a(node.arg_b)
    return False


def _sub_a_with_const(node: Node, val: int) -> Node:
    """Replace all Var('a') leaves with Const(val)."""
    if isinstance(node, Var):
        return Const(val) if node.name == "a" else node.clone()
    if isinstance(node, Const):
        return node.clone()
    if isinstance(node, (Add, Sub, Mul, IDiv)):
        return type(node)(_sub_a_with_const(node.left, val),
                          _sub_a_with_const(node.right, val))
    if isinstance(node, Pow):
        return Pow(_sub_a_with_const(node.base, val),
                   _sub_a_with_const(node.exp, val))
    if isinstance(node, Loop):
        return Loop(_sub_a_with_const(node.body, val),
                    _sub_a_with_const(node.count, val))
    if isinstance(node, IfNode):
        return IfNode(_sub_a_with_const(node.cond_left, val), node.cond_op,
                      _sub_a_with_const(node.cond_right, val),
                      _sub_a_with_const(node.yes, val),
                      _sub_a_with_const(node.no, val))
    return node.clone()


def _sub_b_with(node: Node, replacement: Node) -> Node:
    """
    Recursively replace all Var("b") leaves in `node` with `replacement`.
    Used by the Concept Algebra telescope transform:
        f(b) → f(a-1) by substituting Var("b") with Sub(Var("a"), Const(1))
    This is symbolic differentiation — it lets the system derive range formulas
    from sum-from-1 formulas without re-discovering them from scratch.
    """
    if isinstance(node, Var) and node.name == "b":
        return replacement.clone()
    elif isinstance(node, Const):
        return node.clone()
    elif isinstance(node, Var):
        return node.clone()
    elif isinstance(node, Add):
        return Add(_sub_b_with(node.left, replacement),
                   _sub_b_with(node.right, replacement))
    elif isinstance(node, Sub):
        return Sub(_sub_b_with(node.left, replacement),
                   _sub_b_with(node.right, replacement))
    elif isinstance(node, Mul):
        return Mul(_sub_b_with(node.left, replacement),
                   _sub_b_with(node.right, replacement))
    elif isinstance(node, IDiv):
        return IDiv(_sub_b_with(node.left, replacement),
                    _sub_b_with(node.right, replacement))
    elif isinstance(node, Pow):
        return Pow(_sub_b_with(node.base, replacement),
                   _sub_b_with(node.exp, replacement))
    elif isinstance(node, Loop):
        return Loop(_sub_b_with(node.body, replacement),
                    _sub_b_with(node.count, replacement))
    elif isinstance(node, IfNode):
        return IfNode(
            _sub_b_with(node.cond_left,  replacement),
            node.cond_op,
            _sub_b_with(node.cond_right, replacement),
            _sub_b_with(node.yes,        replacement),
            _sub_b_with(node.no,         replacement),
        )
    return node.clone()


def generalisation_score(prog: Program, task_kind: str) -> float:
    """
    Test on held-out inputs the program has never seen during evolution.
    Returns 0.0–1.0.
    """
    tests = _GENERALISATION_TESTS.get(task_kind, [])
    if not tests:
        return 0.5   # unknown kind — neutral
    passed = 0
    for a, b, expected in tests:
        b_list = [0]
        try:
            result = prog.run({"a": a, "b": b}, b_list)
            if result == expected:
                passed += 1
        except Exception:
            pass
    return passed / len(tests)


# ══════════════════════════════════════════════════════════════════
# TASKS
# ══════════════════════════════════════════════════════════════════