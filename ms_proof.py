"""
ms_proof.py — Symbolic Proof Engine

The gap between Layer 2.5 and Layer 3:
  Empirical:  "Works for 7 test points"  →  status: VERIFIED
  Symbolic:   "Must hold for all inputs"  →  status: PROVEN

This module attempts to prove algebraic identities symbolically by
applying rewrite rules step-by-step to transform one expression into
another. If both sides reduce to the same canonical form, the identity
is proven — not just observed.

REWRITE RULES (applied bottom-up, repeatedly until stable):
  Level 1 — Arithmetic identities:
    x + 0  → x              x * 1  → x
    x * 0  → 0              x - 0  → x
    x ** 1 → x              x ** 0 → 1
    0 + x  → x              1 * x  → x

  Level 2 — Algebraic structure:
    (x + y) + z → x + (y + z)   [associativity, for flattening]
    x * (y + z) → x*y + x*z     [distributive — expand]
    x*y + x*z   → x*(y+z)       [distributive — factor]

  Level 3 — Power laws:
    x**m * x**n  → x**(m+n)     [when m,n are Const]
    (x**m)**n    → x**(m*n)     [when m,n are Const]

PROOF STRATEGY:
  1. Apply all rules exhaustively to LHS until stable (normal form)
  2. Apply all rules exhaustively to RHS until stable (normal form)
  3. If canonical(LHS_normal) == canonical(RHS_normal) → PROVEN
  4. Record each rewrite step as a proof step

PROOF STATUS:
  'proven'    — symbolic proof found
  'empirical' — tested on N points, no counterexample found
  'refuted'   — counterexample found
  'unknown'   — neither proven nor refuted

KNOWN PROVABLE IDENTITIES (in this system):
  - a*b = loop(a,b)          [multiplication as loop]
  - (a+b)**2 = a**2 + 2ab + b**2  [binomial square]
  - x*1 = x                  [multiplicative identity]
  - x+0 = x                  [additive identity]
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import copy

from ms_core import *


# ── Proof record ──────────────────────────────────────────────────────────

@dataclass
class ProofStep:
    step_num:    int
    rule_name:   str
    before:      str   # expression before this step
    after:       str   # expression after this step
    location:    str   # where in tree rule applied (e.g. "root.left")


@dataclass
class ProofResult:
    claim:        str           # "LHS = RHS"
    lhs:          str           # original LHS
    rhs:          str           # original RHS
    status:       str           # 'proven' | 'empirical' | 'refuted' | 'unknown'
    steps:        list[ProofStep] = field(default_factory=list)
    test_points:  int = 0
    counterexample: Optional[str] = None
    method:       str = ""      # what proved/refuted it

    def is_proven(self) -> bool:
        return self.status in ('proven', 'trivial')

    def summary(self) -> str:
        if self.status == 'proven':
            return (f"✓ PROVEN in {len(self.steps)} steps — "
                    f"holds for ALL inputs by symbolic derivation")
        elif self.status == 'trivial':
            return "≡ TRIVIALLY TRUE — both sides are canonically identical (by definition)"
        elif self.status == 'empirical':
            return (f"~ EMPIRICAL — verified at {self.test_points} points, "
                    f"not symbolically derived")
        elif self.status == 'refuted':
            return f"✗ REFUTED — counterexample: {self.counterexample}"
        return "? UNKNOWN"


# ── Rewrite rules ──────────────────────────────────────────────────────────

class RewriteRule:
    """A symbolic rewrite rule: pattern → replacement."""

    def __init__(self, name: str, fn):
        self.name = name
        self._fn  = fn

    def apply(self, node: Node) -> Optional[tuple[Node, str]]:
        """Try to apply this rule at the root. Returns (new_node, location) or None."""
        result = self._fn(node)
        if result is not None:
            return result, "root"
        return None



def _is(node, cls_name: str) -> bool:
    """Check node type by name — works across module import boundaries."""
    return type(node).__name__ == cls_name

def _make_rules() -> list[RewriteRule]:
    """Create the standard rule library."""
    rules = []

    # ── Identity rules ────────────────────────────────────────────────────

    def additive_identity(n):
        if _is(n, 'Add'):
            if _is(n.right, 'Const') and n.right.value == 0:
                return n.left
            if _is(n.left, 'Const') and n.left.value == 0:
                return n.right
        return None

    def multiplicative_identity(n):
        if _is(n, 'Mul'):
            if _is(n.right, 'Const') and n.right.value == 1:
                return n.left
            if _is(n.left, 'Const') and n.left.value == 1:
                return n.right
        return None

    def multiplicative_zero(n):
        if _is(n, 'Mul'):
            if _is(n.right, 'Const') and n.right.value == 0:
                return Const(0)
            if _is(n.left, 'Const') and n.left.value == 0:
                return Const(0)
        return None

    def power_identity(n):
        if _is(n, 'Pow'):
            if _is(n.exp, 'Const') and n.exp.value == 1:
                return n.base
            if _is(n.exp, 'Const') and n.exp.value == 0:
                return Const(1)
        return None

    def subtractive_zero(n):
        if _is(n, 'Sub'):
            if _is(n.right, 'Const') and n.right.value == 0:
                return n.left
        return None

    def const_fold_add(n):
        if _is(n, 'Add'):
            if _is(n.left, 'Const') and _is(n.right, 'Const'):
                return Const(n.left.value + n.right.value)
        return None

    def const_fold_mul(n):
        if _is(n, 'Mul'):
            if _is(n.left, 'Const') and _is(n.right, 'Const'):
                return Const(n.left.value * n.right.value)
        return None

    def const_fold_sub(n):
        if _is(n, 'Sub'):
            if _is(n.left, 'Const') and _is(n.right, 'Const'):
                return Const(n.left.value - n.right.value)
        return None

    def const_fold_pow(n):
        if _is(n, 'Pow'):
            if _is(n.base, 'Const') and _is(n.exp, 'Const'):
                if 0 <= n.exp.value <= 12:
                    return Const(n.base.value ** n.exp.value)
        return None

    # ── Power laws ────────────────────────────────────────────────────────

    def power_product(n):
        """x**m * x**n → x**(m+n)"""
        if _is(n, 'Mul'):
            if (_is(n.left, 'Pow') and _is(n.right, 'Pow')
                    and n.left.base.to_str() == n.right.base.to_str()
                    and isinstance(n.left.exp, Const)
                    and isinstance(n.right.exp, Const)):
                new_exp = Const(n.left.exp.value + n.right.exp.value)
                return Pow(n.left.base.clone(), new_exp)
        return None

    def power_of_power(n):
        """(x**m)**n → x**(m*n)"""
        if _is(n, 'Pow'):
            if (_is(n.base, 'Pow')
                    and isinstance(n.base.exp, Const)
                    and _is(n.exp, 'Const')):
                new_exp = Const(n.base.exp.value * n.exp.value)
                return Pow(n.base.base.clone(), new_exp)
        return None

    # ── Distributive (expand only — safer than factoring) ─────────────────

    def distribute_left(n):
        """a * (b + c) → a*b + a*c"""
        if _is(n, 'Mul'):
            if _is(n.right, 'Add'):
                a = n.left.clone()
                b = n.right.left.clone()
                c = n.right.right.clone()
                return Add(Mul(a, b), Mul(a.clone(), c))
        return None

    def distribute_right(n):
        """(a + b) * c → a*c + b*c"""
        if _is(n, 'Mul'):
            if _is(n.left, 'Add'):
                a = n.left.left.clone()
                b = n.left.right.clone()
                c = n.right.clone()
                return Add(Mul(a, c), Mul(b, c.clone()))
        return None

    # ── Commutativity (for canonical ordering) ────────────────────────────

    def commute_add(n):
        """Sort Add children alphabetically."""
        if _is(n, 'Add'):
            ls, rs = n.left.to_str(), n.right.to_str()
            if ls > rs:
                return Add(n.right.clone(), n.left.clone())
        return None

    def commute_mul(n):
        """Sort Mul children alphabetically."""
        if _is(n, 'Mul'):
            ls, rs = n.left.to_str(), n.right.to_str()
            if ls > rs:
                return Mul(n.right.clone(), n.left.clone())
        return None

    rule_fns = [
        ("additive_identity",      additive_identity),
        ("multiplicative_identity", multiplicative_identity),
        ("multiplicative_zero",    multiplicative_zero),
        ("power_identity",         power_identity),
        ("subtractive_zero",       subtractive_zero),
        ("const_fold_add",         const_fold_add),
        ("const_fold_mul",         const_fold_mul),
        ("const_fold_sub",         const_fold_sub),
        ("const_fold_pow",         const_fold_pow),
        ("power_product",          power_product),
        ("power_of_power",         power_of_power),
        ("commute_add",            commute_add),
        ("commute_mul",            commute_mul),
        # Distributive last — expands tree, use carefully
        ("distribute_left",        distribute_left),
        ("distribute_right",       distribute_right),
    ]

    return [RewriteRule(name, fn) for name, fn in rule_fns]


# ── Rewrite engine ────────────────────────────────────────────────────────

RULES = _make_rules()


def _apply_rules_once(node: Node,
                      steps: list[ProofStep],
                      depth: int = 0,
                      max_depth: int = 8,
                      lemma_map: Optional[dict] = None) -> tuple[Node, bool]:
    """
    Apply rules bottom-up once to the tree.
    Returns (new_node, changed).
    lemma_map: {rule_name -> lemma_name} so rule steps cite lemmas by name.
    """
    if depth > max_depth:
        return node, False

    changed = False
    kwargs = dict(depth=depth+1, max_depth=max_depth, lemma_map=lemma_map)

    # Recurse into children first (bottom-up)
    if _is(node, 'Add'):
        l, lc = _apply_rules_once(node.left,  steps, **kwargs)
        r, rc = _apply_rules_once(node.right, steps, **kwargs)
        if lc or rc:
            node = type(node)(l, r)
            changed = True

    elif _is(node, 'Sub'):
        l, lc = _apply_rules_once(node.left,  steps, **kwargs)
        r, rc = _apply_rules_once(node.right, steps, **kwargs)
        if lc or rc:
            node = type(node)(l, r)
            changed = True

    elif _is(node, 'Mul'):
        l, lc = _apply_rules_once(node.left,  steps, **kwargs)
        r, rc = _apply_rules_once(node.right, steps, **kwargs)
        if lc or rc:
            node = type(node)(l, r)
            changed = True

    elif _is(node, 'IDiv'):
        l, lc = _apply_rules_once(node.left,  steps, **kwargs)
        r, rc = _apply_rules_once(node.right, steps, **kwargs)
        if lc or rc:
            node = type(node)(l, r)
            changed = True

    elif _is(node, 'Pow'):
        b, bc = _apply_rules_once(node.base, steps, **kwargs)
        e, ec = _apply_rules_once(node.exp,  steps, **kwargs)
        if bc or ec:
            node = type(node)(b, e)
            changed = True

    # Try rules at this node
    for rule in RULES:
        before_str = node.to_str()
        result = rule.apply(node)
        if result is not None:
            new_node, loc = result
            after_str = new_node.to_str()
            if after_str != before_str:
                step_name = rule.name
                if lemma_map and rule.name in lemma_map:
                    step_name = f"by_lemma:{lemma_map[rule.name]}"
                steps.append(ProofStep(
                    step_num  = len(steps) + 1,
                    rule_name = step_name,
                    before    = before_str,
                    after     = after_str,
                    location  = loc,
                ))
                return new_node, True

    return node, changed


def normalize(node: Node,
              max_iterations: int = 30,
              lemma_map: Optional[dict] = None) -> tuple[Node, list[ProofStep]]:
    """
    Apply rewrite rules repeatedly until stable (normal form).
    Returns (normalized_node, steps_taken).
    lemma_map: {rule_name → lemma_name} for citation renaming.
    """
    steps: list[ProofStep] = []
    current = copy.deepcopy(node)

    for _ in range(max_iterations):
        new_node, changed = _apply_rules_once(current, steps, lemma_map=lemma_map)
        if not changed:
            break
        current = new_node
        if len(current.to_str()) > 500:
            break

    return current, steps


# ── Main proof engine ──────────────────────────────────────────────────────

class SymbolicProofEngine:
    """
    Attempts to prove algebraic identities symbolically.

    Strategy:
      1. Normalize both sides using rewrite rules
      2. Canonicalize both normal forms
      3. Compare — if equal, PROVEN
      4. If not, fall back to empirical verification

    Lemma system:
      Proven results are stored as named lemmas. Future proofs can
      cite them by name — "by lemma X" — rather than re-deriving.
      This is how real mathematics accumulates: each proof builds
      on the library of previously established results.
    """

    def __init__(self):
        self._proven:   dict[str, ProofResult] = {}   # cache_key → result
        self._lemmas:   dict[str, ProofResult] = {}   # lemma_name → result
        self._attempts: int = 0

    def register_lemma(self, name: str, result: ProofResult) -> None:
        """Store a proven result as a named lemma for future proofs."""
        if result.status in ("proven", "trivial"):
            self._lemmas[name] = result

    def cite_lemma(self, name: str) -> Optional[ProofResult]:
        """Look up a lemma by name."""
        return self._lemmas.get(name)

    def _try_lemma_shortcut(self, lhs_str: str, rhs_str: str) -> Optional[ProofResult]:
        """
        Check if any stored lemma proves this identity directly.
        This is what 'citing prior work' looks like in a proof:
        instead of re-deriving, we point to an already-proven result.
        """
        # Direct match: someone already proved exactly this
        for lemma_name, lemma in self._lemmas.items():
            if lemma.lhs == lhs_str and lemma.rhs == rhs_str:
                cited = ProofResult(
                    claim  = f"{lhs_str} = {rhs_str}",
                    lhs    = lhs_str,
                    rhs    = rhs_str,
                    status = "proven",
                    steps  = [ProofStep(1, f"by_lemma:{lemma_name}",
                                         lhs_str, rhs_str, "lemma_citation")],
                    method = f"lemma:{lemma_name}",
                )
                return cited
            # Symmetric match: a=b already proven, now proving b=a
            if lemma.lhs == rhs_str and lemma.rhs == lhs_str:
                cited = ProofResult(
                    claim  = f"{lhs_str} = {rhs_str}",
                    lhs    = lhs_str,
                    rhs    = rhs_str,
                    status = "proven",
                    steps  = [ProofStep(1, f"by_lemma:{lemma_name}[symmetric]",
                                         lhs_str, rhs_str, "lemma_citation")],
                    method = f"lemma:{lemma_name}[symmetric]",
                )
                return cited
        return None

    def prove(self, lhs_node: Node, rhs_node: Node,
              claim: str = "") -> ProofResult:
        """
        Attempt to prove lhs_node == rhs_node symbolically.
        Returns a ProofResult with status and steps.
        """
        self._attempts += 1
        lhs_str = lhs_node.to_str()
        rhs_str = rhs_node.to_str()

        if not claim:
            claim = f"{lhs_str} = {rhs_str}"

        # Check cache
        cache_key = f"{lhs_str}=={rhs_str}"
        if cache_key in self._proven:
            return self._proven[cache_key]

        # Check lemma library — cite prior work instead of re-deriving
        lemma_result = self._try_lemma_shortcut(lhs_str, rhs_str)
        if lemma_result is not None:
            self._proven[cache_key] = lemma_result
            return lemma_result

        result = ProofResult(claim=claim, lhs=lhs_str, rhs=rhs_str,
                              status="unknown")

        # ── Build lemma map: rule_name → lemma_name ───────────────────────
        # When a rewrite rule fires that corresponds to a named lemma,
        # the step gets cited as "by_lemma:X" instead of the raw rule name.
        # This makes the proof trace show knowledge accumulation.
        lemma_map: dict[str, str] = {
            "additive_identity":      "add_zero",
            "multiplicative_identity": "mul_one",
            "multiplicative_zero":    "mul_zero",
            "power_identity":         "power_one",
            "commute_add":            "commutativity_add",
            "commute_mul":            "commutativity_mul",
            "distribute_left":        "distributive",
            "distribute_right":       "distributive",
            "power_product":          "power_product_law",
            "power_of_power":         "power_of_power_law",
        }
        # Augment with any registered lemmas that match rule names
        for lname in self._lemmas:
            safe = lname.replace(" ", "_").replace("/", "_")
            for rule_name in lemma_map:
                if safe in rule_name or rule_name in safe:
                    lemma_map[rule_name] = lname

        # ── Step 1: Normalize both sides ──────────────────────────────────
        lhs_normal, lhs_steps = normalize(lhs_node, lemma_map=lemma_map)
        rhs_normal, rhs_steps = normalize(rhs_node, lemma_map=lemma_map)

        all_steps = (
            [ProofStep(i+1, s.rule_name, s.before, s.after, f"LHS.{s.location}")
             for i, s in enumerate(lhs_steps)] +
            [ProofStep(i+1, s.rule_name, s.before, s.after, f"RHS.{s.location}")
             for i, s in enumerate(rhs_steps)]
        )

        # ── Step 2: Canonicalize and compare ─────────────────────────────
        try:
            lhs_canon = canonicalize(lhs_normal).to_str()
            rhs_canon = canonicalize(rhs_normal).to_str()
        except Exception:
            lhs_canon = lhs_normal.to_str()
            rhs_canon = rhs_normal.to_str()

        if lhs_canon == rhs_canon:
            if all_steps:
                result.status = "proven"
                result.steps  = all_steps
                result.method = "symbolic_normalization"
            else:
                # Both sides are already identical — trivially true by definition,
                # not derived. This is recognition, not proof.
                result.status = "trivial"
                result.steps  = []
                result.method = "canonical_identity"
            self._proven[cache_key] = result
            return result

        # ── Step 3: Also try expanding LHS distributively ─────────────────
        # Some identities only prove after expansion then canonicalization
        try:
            lhs_expanded, expand_steps = normalize(lhs_node)
            # One more round with distribute rules first
            for _ in range(10):
                changed = False
                for rule in RULES:
                    res = rule.apply(lhs_expanded)
                    if res:
                        lhs_expanded, _ = res
                        changed = True
                        break
                if not changed:
                    break

            lhs_exp_canon = canonicalize(lhs_expanded).to_str()
            if lhs_exp_canon == rhs_canon:
                result.status = "proven"
                result.steps  = all_steps + expand_steps
                result.method = "symbolic_expansion"
                self._proven[cache_key] = result
                return result
        except Exception:
            pass

        # ── Step 4: Empirical verification (fallback) ─────────────────────
        test_pts = [(1,3),(1,5),(1,8),(2,4),(2,6),(3,5),(3,7),(4,6),(1,10),(2,9)]
        passed = 0
        for a, b in test_pts:
            try:
                lv = lhs_node.eval({"a": a, "b": b}, [0])
                rv = rhs_node.eval({"a": a, "b": b}, [0])
                if lv == rv:
                    passed += 1
                else:
                    result.status       = "refuted"
                    result.counterexample = f"f({a},{b}): LHS={lv}  RHS={rv}"
                    result.method       = "empirical"
                    result.test_points  = passed
                    self._proven[cache_key] = result
                    return result
            except Exception:
                pass

        if passed == len(test_pts):
            result.status      = "empirical"
            result.test_points = passed
            result.method      = "empirical"
        else:
            result.status = "unknown"

        self._proven[cache_key] = result
        # Auto-register proven results as lemmas for future citation
        if result.status in ("proven", "trivial") and claim:
            safe_name = claim.replace(" ", "_").replace("/", "_")[:40]
            self._lemmas[safe_name] = result
        return result

    def prove_law(self, law: dict) -> ProofResult:
        """
        Attempt to prove a crystallised law symbolically.
        Expects law to have 'parent' and 'child' concept names
        and program_nodes.
        """
        parent_node = law.get("_parent_node")
        child_node  = law.get("_child_node")

        if parent_node is None or child_node is None:
            # Can't prove without nodes
            result = ProofResult(
                claim  = law.get("name", "?"),
                lhs    = law.get("statement", ""),
                rhs    = "",
                status = "unknown",
                method = "no_nodes",
            )
            return result

        # For squaring laws: prove child == parent²
        if law.get("kind") == "squaring_identity":
            rhs = Pow(copy.deepcopy(parent_node), Const(2))
            return self.prove(child_node, rhs, law.get("name", ""))

        # For scaling laws: prove child == k * parent
        if law.get("kind") == "scaling":
            k = law.get("k", 2)
            rhs = Mul(Const(k), copy.deepcopy(parent_node))
            return self.prove(child_node, rhs, law.get("name", ""))

        # Generic: empirical only
        return ProofResult(
            claim  = law.get("name", "?"),
            lhs    = law.get("statement", ""),
            rhs    = "",
            status = "empirical",
            test_points = law.get("verified_pts", 0),
            method = "empirical",
        )

    # ── Well-known identity checks ────────────────────────────────────────

    def prove_nicomachus(self, triangular_node: Node,
                          cubes_node: Node) -> ProofResult:
        """
        Prove Nicomachus' theorem:
          sum_cubes(n) = triangular(n)²

        This proves it symbolically if the canonical forms match.
        """
        rhs = Pow(copy.deepcopy(triangular_node), Const(2))
        return self.prove(cubes_node, rhs,
                          "Nicomachus: Σi³ = (Σi)²")

    # ── Reporting ─────────────────────────────────────────────────────────

    def format_proof(self, result: ProofResult,
                     verbose: bool = False) -> str:
        """Format a proof result for display."""
        lines = []
        lines.append(f"  {'━'*60}")
        lines.append(f"  🔬 PROOF ATTEMPT  {bold(result.claim)}")

        if result.status == "proven":
            # Check if any step is a lemma citation
            lemma_steps = [s for s in result.steps
                           if s.rule_name.startswith("by_lemma:")]
            if lemma_steps:
                lemma_name = lemma_steps[0].rule_name.split(":", 1)[1]
                lines.append(f"  {green('✓ PROVEN')} by lemma:  {dim(lemma_name)}")
            else:
                lines.append(f"  {green('✓ PROVEN')}  "
                             f"holds for ALL inputs by algebraic derivation")
            if verbose and result.steps:
                lines.append(f"  Derivation ({len(result.steps)} steps):")
                for step in result.steps[:8]:
                    if step.rule_name.startswith("by_lemma:"):
                        lines.append(f"    ∴ {green('cites')} {dim(step.rule_name[9:])}")
                    else:
                        lines.append(f"    Step {step.step_num}: "
                                     f"{dim(step.rule_name)} → {dim(step.after[:50])}")
        elif result.status == "trivial":
            lines.append(f"  {cyan('≡ IDENTITY')}  "
                         f"both sides are canonically identical (by definition)")
        elif result.status == "empirical":
            lines.append(f"  {yellow('~ EMPIRICAL')}  "
                         f"verified at {result.test_points} points — "
                         f"not symbolically derived")
        elif result.status == "refuted":
            lines.append(f"  {red('✗ REFUTED')}  "
                         f"counterexample: {result.counterexample}")
        else:
            lines.append(f"  {dim('? UNKNOWN')}  symbolic and empirical checks inconclusive")

        lines.append(f"  {'━'*60}")
        return "\n".join(lines)

    def stats(self) -> str:
        proven    = sum(1 for r in self._proven.values() if r.status == "proven")
        trivial   = sum(1 for r in self._proven.values() if r.status == "trivial")
        empirical = sum(1 for r in self._proven.values() if r.status == "empirical")
        refuted   = sum(1 for r in self._proven.values() if r.status == "refuted")
        # Count proofs that contain at least one lemma citation step
        cited = sum(
            1 for r in self._proven.values()
            if r.status == "proven" and any(
                s.rule_name.startswith("by_lemma:") for s in r.steps
            )
        )
        return (f"Proof engine: {self._attempts} attempts  "
                f"({proven} derived [{cited} cite lemmas], {trivial} trivial, "
                f"{empirical} empirical, {refuted} refuted)  |  "
                f"{len(self._lemmas)} lemmas in library")