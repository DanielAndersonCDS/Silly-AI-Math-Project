"""
ms_abstraction.py — Abstraction Promotion Engine

The final gap between "formal proof system" and "autonomous theory builder":

  Pattern recognition:  "I see (a+b)², (a*b)², T(b)² repeatedly"
  Abstraction:          "These are all instances of SQUARING — a new operation"
  New object born:      Squaring(f) = f² — applicable to ANY expression f

This is how mathematics actually advances:
  - Negative numbers: "subtraction always produces something — name it"
  - Complex numbers:  "√(-1) keeps appearing — name it i"  
  - Functions:        "these rules all map inputs to outputs — name that"
  - Groups:           "many structures share these 4 axioms — name the pattern"

Each abstraction creates a NEW MATHEMATICAL OBJECT that can itself
be studied, composed, and used to prove things.

WHAT THIS MODULE DOES:

1. AbstractionDetector:
   Scans deduced consequences looking for parametric patterns.
   When many expressions share a structural template like f(x)^n,
   it identifies the template as an abstraction candidate.

2. AbstractionPromoter:
   Takes a detected template and promotes it to a named concept
   in the registry — a new mathematical object with its own:
     - name
     - formal definition (the template)
     - instances (the expressions that fit it)
     - derivation proof (showing how instances follow from it)

3. AbstractionAnnouncer:
   Prints the birth of new mathematical objects clearly:
     "🌟 NEW OBJECT BORN: Squaring"
     "   Template: f² — applies to any expression f"
     "   Instances: (a+b)², (a*b)², T(b)²"
     "   This is a new kind of mathematical structure"

ABSTRACTION TEMPLATES DETECTED:

  Power family:    f^n  for n=2,3,...  (squaring, cubing, etc.)
  Double:          f + f = 2f           (doubling operator)
  Self-product:    f * f = f²           (squaring via multiplication)
  Composition:     f(g(x))              (function composition)
  Shift family:    f + k for k=1,2,...  (translation)
  Scale family:    k * f for k=2,3,...  (scaling)
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import re

from ms_core import *
from ms_planner import DiscoveredTheorem


# ── Abstract Object ───────────────────────────────────────────────────────

@dataclass
class AbstractObject:
    """
    A new mathematical object born from abstraction.

    Not a specific formula — a TEMPLATE that generates a family of formulas.
    Example: Squaring is not (a+b)² — it's the operation f → f² for any f.
    """
    name:        str          # e.g. "Squaring", "Doubling", "PowerFamily"
    template:    str          # e.g. "f² for any expression f"
    formal:      str          # e.g. "∀f: Sq(f) = f * f"
    instances:   list[str]    # e.g. ["(a+b)²", "(a*b)²", "T(b)²"]
    origin_concepts: list[str]  # which concepts it was derived from
    round_born:  int = 0
    depth:       int = 1      # how abstract (1=concrete, 2=meta, 3=meta-meta)
    proof_count: int = 0      # how many proofs involve this abstraction


# ── Abstraction Detector ──────────────────────────────────────────────────

class AbstractionDetector:
    """
    Scans collections of deduced theorems looking for parametric patterns.
    A pattern qualifies as an abstraction when:
      - 3+ expressions share the same structural template
      - The template is more general than any individual expression
      - The template hasn't been named yet
    """

    def __init__(self):
        self._templates = [
            ("Squaring",     _detect_squaring,     "∀f: Sq(f) = f²",         1),
            ("Cubing",       _detect_cubing,       "∀f: Cube(f) = f³",       1),
            ("Doubling",     _detect_doubling,     "∀f: Double(f) = f + f",  1),
            ("PowerFamily",  _detect_power_family, "∀f,n: Power(f,n) = fⁿ", 2),
            ("Shifting",     _detect_shifting,     "∀f,k: Shift(f,k) = f+k", 1),
            ("Scaling",      _detect_scaling,      "∀f,k: Scale(f,k) = k*f", 1),
            ("SelfProduct",  _detect_self_product, "∀f: SelfProd(f) = f*f",  1),
        ]
        self._detected:  dict[str, AbstractObject] = {}
        self._announced: set[str] = set()

    def scan(self, theorems: list[DiscoveredTheorem],
             round_num: int) -> list[AbstractObject]:
        """
        Scan a list of deduced theorems for abstraction opportunities.
        Returns newly detected abstract objects.
        """
        new_objects: list[AbstractObject] = []

        for template_name, detect_fn, formal, depth in self._templates:
            if template_name in self._detected:
                # Already found — update instance count
                obj = self._detected[template_name]
                matches = detect_fn(theorems)
                for expr in matches:
                    if expr not in obj.instances:
                        obj.instances.append(expr)
                continue

            matches = detect_fn(theorems)
            if len(matches) >= 3:
                origins = list(set(t.origin for t in theorems
                                   if t.expression in matches))
                obj = AbstractObject(
                    name             = template_name,
                    template         = _template_str(template_name),
                    formal           = formal,
                    instances        = matches,
                    origin_concepts  = origins,
                    round_born       = round_num,
                    depth            = depth,
                    proof_count      = 0,
                )
                self._detected[template_name] = obj
                new_objects.append(obj)

        return new_objects

    def announce(self, obj: AbstractObject) -> None:
        """Print the birth of a new abstract mathematical object."""
        if obj.name in self._announced:
            return
        self._announced.add(obj.name)

        depth_label = {1: "concrete operation", 2: "meta-operation",
                       3: "structural axiom"}.get(obj.depth, "abstract object")

        print(f"\n  {'━'*64}")
        print(f"  🌟 {bold(cyan('ABSTRACTION BORN'))}  "
              f"{bold(obj.name)}  {dim(f'({depth_label})')}")
        print(f"     {dim(obj.formal)}")
        print(f"     Template: {dim(obj.template)}")
        print(f"     {len(obj.instances)} known instances:  "
              f"{dim('  '.join(i[:30] for i in obj.instances[:3]))}")
        if len(obj.instances) > 3:
            print(f"     {dim(f'... and {len(obj.instances)-3} more')}")
        print(f"  {'━'*64}\n")

    def all_objects(self) -> list[AbstractObject]:
        return list(self._detected.values())

    def summary(self) -> str:
        if not self._detected:
            return "No abstract objects detected yet"
        names = ", ".join(self._detected.keys())
        return (f"Abstract objects: {len(self._detected)}  ({names})")


# ── Detection functions ───────────────────────────────────────────────────

def _detect_squaring(theorems: list[DiscoveredTheorem]) -> list[str]:
    """Detect expressions of the form f² (squared something)."""
    matches = []
    for t in theorems:
        expr = t.expression
        # Match: (X ** 2) pattern
        if re.search(r'\*\* 2\b', expr) or expr.endswith('** 2)'):
            matches.append(expr)
        # Match: (X * X) where X appears twice identically
        mul_match = re.match(r'^\((.+) \* \1\)$', expr)
        if mul_match:
            matches.append(expr)
    return matches


def _detect_cubing(theorems: list[DiscoveredTheorem]) -> list[str]:
    """Detect expressions of the form f³."""
    matches = []
    for t in theorems:
        expr = t.expression
        if re.search(r'\*\* 3\b', expr) or expr.endswith('** 3)'):
            matches.append(expr)
    return matches


def _detect_doubling(theorems: list[DiscoveredTheorem]) -> list[str]:
    """Detect expressions of the form f + f (doubling)."""
    matches = []
    for t in theorems:
        expr = t.expression
        # Match: (X + X) where X appears twice identically
        add_match = re.match(r'^\((.+) \+ \1\)$', expr)
        if add_match:
            matches.append(expr)
    return matches


def _detect_power_family(theorems: list[DiscoveredTheorem]) -> list[str]:
    """Detect when the same base appears raised to multiple powers."""
    # Group by base expression
    bases: dict[str, list[str]] = {}
    for t in theorems:
        expr = t.expression
        pow_match = re.match(r'^\((.+) \*\* (\d+)\)$', expr)
        if pow_match:
            base, power = pow_match.groups()
            bases.setdefault(base, []).append(expr)

    # Base with 2+ different powers = power family
    matches = []
    for base, exprs in bases.items():
        if len(exprs) >= 2:
            matches.extend(exprs)
    return matches


def _detect_shifting(theorems: list[DiscoveredTheorem]) -> list[str]:
    """Detect expressions of the form f + k (constant shifts)."""
    matches = []
    for t in theorems:
        expr = t.expression
        # Match: (X + N) where N is a small integer
        shift_match = re.match(r'^\((.+) \+ (\d+)\)$', expr)
        if shift_match and int(shift_match.group(2)) <= 5:
            matches.append(expr)
    return matches


def _detect_scaling(theorems: list[DiscoveredTheorem]) -> list[str]:
    """Detect expressions of the form k * f (constant scaling)."""
    matches = []
    for t in theorems:
        expr = t.expression
        # Match: (N * X) or (X + X) style doubling via multiplication
        scale_match = re.match(r'^\((\d+) \* (.+)\)$', expr)
        if scale_match and int(scale_match.group(1)) <= 8:
            matches.append(expr)
    return matches


def _detect_self_product(theorems: list[DiscoveredTheorem]) -> list[str]:
    """Detect expressions of the form f * f."""
    matches = []
    for t in theorems:
        expr = t.expression
        mul_match = re.match(r'^\((.+) \* \1\)$', expr)
        if mul_match:
            matches.append(expr)
    return matches


def _template_str(name: str) -> str:
    """Human-readable template description."""
    return {
        "Squaring":    "f → f²  (any expression can be squared)",
        "Cubing":      "f → f³  (any expression can be cubed)",
        "Doubling":    "f → f+f (any expression can be doubled)",
        "PowerFamily": "f → {f², f³, f⁴...}  (a full tower of powers)",
        "Shifting":    "f → f+k (any expression can be shifted by constant k)",
        "Scaling":     "f → k·f (any expression can be scaled by constant k)",
        "SelfProduct": "f → f·f (identical to squaring, via multiplication)",
    }.get(name, f"{name}(f) — parametric operation")