"""
ms_planner.py — Goal-Directed Proof Planner

This is the upgrade from "verifier" to "navigator."

CURRENT SYSTEM (verifier):
    Agent discovers pattern empirically
    → Proof engine checks if it can justify the claim
    → If yes: "proven"

THIS MODULE (navigator):
    Starts from axioms + known concepts
    → Systematically applies rules to derive new expressions
    → Records every reachable form as a theorem
    → Agents can then SOLVE tasks using planned theorems

The difference:
    Verifier: "Can we justify this?"       (reactive)
    Navigator: "What can we derive?"       (generative)

This is how formal proof assistants like Lean/Coq work:
    you don't just check proofs — you search for them.

WHAT THE PLANNER DOES:
    1. ConsequenceExplorer:
       Given a set of known expressions, apply all rewrite rules
       exhaustively to enumerate reachable forms.
       Each form becomes a "discovered theorem" the agent didn't
       need to find empirically.

    2. GoalDirectedProver:
       Given a TARGET expression, work backwards:
       "What do I need to prove first to reach this goal?"
       Decomposes hard goals into subgoals, then proves each.
       This is backward chaining.

    3. LemmaInventor:
       Notices when the same subexpression appears in many proofs
       and promotes it to a named lemma automatically.
       Agents don't just USE lemmas — the system CREATES them.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import copy
import random

from ms_core import *
from ms_core import _parse_expr_stub   # private but needed for expression parsing
from ms_proof import SymbolicProofEngine, ProofResult, ProofStep, normalize, RULES


# ── Consequence Explorer ──────────────────────────────────────────────────

@dataclass
class DiscoveredTheorem:
    """A theorem discovered by deductive exploration, not empirical observation."""
    expression:  str        # the canonical form discovered
    origin:      str        # which known concept it was derived from
    steps:       list[ProofStep] = field(default_factory=list)
    depth:       int = 0    # how many rule applications from origin
    novelty:     float = 0.0  # how different from origin


class ConsequenceExplorer:
    """
    Explores the space of theorems reachable from known facts by
    systematically applying algebraic rules.

    This implements "discovers by proving" — the engine generates
    new mathematical facts purely by rule application, without
    needing agents to find them empirically first.
    """

    def __init__(self, proof_engine: SymbolicProofEngine):
        self._engine     = proof_engine
        self._discovered: dict[str, DiscoveredTheorem] = {}  # canon → theorem
        self._explored:   set[str] = set()  # expressions already expanded

    def explore_from(self, concept_name: str, node: Node,
                     max_depth: int = 3,
                     max_new: int = 8) -> list[DiscoveredTheorem]:
        """
        Starting from a known concept expression, discover reachable forms
        by applying algebraic rules via the normalize engine.

        The key insight: normalize() already handles cross-module type
        checking correctly. We exploit it by applying single-rule
        transformations to subexpressions, re-parsing, and normalizing.
        """
        new_theorems: list[DiscoveredTheorem] = []
        try:
            origin_str = canonicalize(node).to_str()
        except Exception:
            origin_str = node.to_str()

        # Use structural string mutations + normalize as our exploration strategy.
        # Generate candidate expressions by substituting known sub-patterns.
        candidates = _generate_variants(node, origin_str)

        for expr_str, derivation_hint in candidates[:max_new * 2]:
            if len(new_theorems) >= max_new:
                break
            try:
                candidate_node = _parse_expr_stub(expr_str)
                normalized, steps = normalize(candidate_node)
                canon = canonicalize(normalized).to_str()
            except Exception:
                continue

            if canon == origin_str or canon in self._discovered:
                continue
            if len(set(canon)) < 3:  # degenerate
                continue

            novelty = _expression_novelty(canon, origin_str)
            if novelty < 0.05:  # too similar to origin
                continue

            step = ProofStep(
                step_num  = 1,
                rule_name = derivation_hint,
                before    = origin_str,
                after     = canon,
                location  = "exploration",
            )
            dt = DiscoveredTheorem(
                expression = canon,
                origin     = concept_name,
                steps      = steps[:3] or [step],
                depth      = 1,
                novelty    = novelty,
            )
            self._discovered[canon] = dt
            new_theorems.append(dt)

        return new_theorems

    def scan_concepts(self, concepts: "ConceptRegistry",
                      round_num: int) -> list[DiscoveredTheorem]:
        """
        Scan all known concepts and explore consequences from each.
        Returns newly discovered theorems across the whole registry.
        """
        all_new: list[DiscoveredTheorem] = []
        for c in concepts.all_concepts():
            if c.program_node is None:
                continue
            # Only explore from foundational concepts (small trees)
            if len(c.canonical) > 30:
                continue
            new = self.explore_from(c.name, c.program_node,
                                    max_depth=2, max_new=4)
            all_new.extend(new)
        return all_new

    def all_discovered(self) -> list[DiscoveredTheorem]:
        return list(self._discovered.values())


# ── Goal-Directed Prover (backward chaining) ─────────────────────────────

@dataclass
class ProofPlan:
    """A multi-step proof plan from subgoals to main goal."""
    main_goal: str
    subgoals:  list[str]         # subgoals to prove first
    strategy:  str               # description of approach
    complete:  bool = False
    steps:     list[ProofStep] = field(default_factory=list)


class GoalDirectedProver:
    """
    Works backward from a proof goal to find a proof path.

    Instead of: "normalize LHS and RHS and compare"
    This does:  "what do I need to prove to reach this goal?"

    Algorithm (backward chaining):
        1. Target: prove LHS = RHS
        2. Can we transform RHS one step back toward LHS?
           → If yes: that transformation becomes a subgoal
        3. Recurse until we reach the same form as LHS
        4. Chain the subgoal proofs together

    This is how human mathematicians plan proofs:
        "To prove X, it suffices to prove Y and Z."
    """

    def __init__(self, proof_engine: SymbolicProofEngine):
        self._engine = proof_engine
        self._plans:  list[ProofPlan] = []

    def plan_and_prove(self, lhs_node: Node, rhs_node: Node,
                       claim: str = "",
                       max_depth: int = 5) -> Optional[ProofResult]:
        """
        Attempt to prove LHS = RHS using backward chaining.

        Strategy:
          1. Try forward proof first (fast path)
          2. If that fails, try backward from RHS
          3. If backward finds a path, assemble the full proof
        """
        # Fast path: try direct forward proof
        result = self._engine.prove(lhs_node, rhs_node, claim)
        if result.status == "proven":
            return result

        # Backward path: try to reduce RHS toward LHS
        lhs_str = lhs_node.to_str()
        rhs_str = rhs_node.to_str()

        # Try each rule on RHS — can any rule applied to RHS produce
        # something closer to LHS?
        rhs_copy = copy.deepcopy(rhs_node)
        backward_steps: list[ProofStep] = []

        for depth in range(max_depth):
            # Try all rules on current RHS
            for rule in RULES:
                res = rule.apply(rhs_copy)
                if res is None:
                    continue
                new_rhs, _ = res
                new_rhs_str = new_rhs.to_str()

                if new_rhs_str == lhs_str:
                    # Found it! Backward step closes the proof
                    backward_steps.append(ProofStep(
                        step_num  = len(backward_steps) + 1,
                        rule_name = f"backward:{rule.name}",
                        before    = rhs_str,
                        after     = new_rhs_str,
                        location  = "root",
                    ))
                    plan = ProofPlan(
                        main_goal = claim or f"{lhs_str} = {rhs_str}",
                        subgoals  = [],
                        strategy  = f"backward_chain_depth_{depth+1}",
                        complete  = True,
                        steps     = backward_steps,
                    )
                    self._plans.append(plan)
                    return ProofResult(
                        claim  = claim,
                        lhs    = lhs_str,
                        rhs    = rhs_str,
                        status = "proven",
                        steps  = backward_steps,
                        method = "backward_chaining",
                    )

                # Not closed yet — take this step and continue
                step = ProofStep(
                    step_num  = len(backward_steps) + 1,
                    rule_name = f"backward:{rule.name}",
                    before    = rhs_copy.to_str(),
                    after     = new_rhs_str,
                    location  = "root",
                )
                backward_steps.append(step)
                rhs_copy = new_rhs
                break  # take first applicable rule, then re-check

        return None  # couldn't find backward proof

    def suggest_subgoals(self, lhs_node: Node,
                          rhs_node: Node) -> list[str]:
        """
        If direct proof fails, suggest intermediate expressions
        that might bridge LHS to RHS.

        This is lemma invention — identifying useful stepping stones.
        """
        lhs_str  = lhs_node.to_str()
        rhs_str  = rhs_node.to_str()
        subgoals = []

        # Idea: expressions reachable from LHS in 1-2 steps that contain
        # structural elements of RHS
        rhs_tokens = set(rhs_str.replace("(","").replace(")","").split())

        frontier = [copy.deepcopy(lhs_node)]
        seen     = {lhs_str}
        for _ in range(20):
            if not frontier:
                break
            current = frontier.pop(0)
            for rule in RULES[:8]:   # just the simpler rules
                res = rule.apply(current)
                if res is None:
                    continue
                new_node, _ = res
                new_str = new_node.to_str()
                if new_str in seen:
                    continue
                seen.add(new_str)

                # Does this intermediate expression share tokens with RHS?
                new_tokens = set(new_str.replace("(","").replace(")","").split())
                overlap    = len(new_tokens & rhs_tokens) / max(len(rhs_tokens), 1)
                if overlap > 0.5 and new_str != rhs_str:
                    subgoals.append(new_str)
                frontier.append(new_node)
                if len(subgoals) >= 3:
                    break

        return subgoals[:3]


# ── Lemma Inventor ────────────────────────────────────────────────────────

class LemmaInventor:
    """
    Analyzes proof traces to identify recurring subexpressions
    and automatically promotes them to named lemmas.

    When the same transformation appears in 3+ different proofs,
    it's a candidate for promotion to a named lemma — agents
    don't need to rediscover it every time.
    """

    def __init__(self, proof_engine: SymbolicProofEngine):
        self._engine       = proof_engine
        self._step_counts: dict[str, int] = {}   # rule_name → frequency
        self._promoted:    set[str]       = set()

    def observe_proof(self, result: ProofResult) -> None:
        """Record which rules/lemmas were used in a proof."""
        for step in result.steps:
            key = step.rule_name
            self._step_counts[key] = self._step_counts.get(key, 0) + 1

    def scan_and_promote(self, threshold: int = 3,
                          round_num: int = 0) -> list[str]:
        """
        Scan rule usage frequencies. If any rule has been applied
        in threshold+ proofs and isn't yet a named lemma, create one.
        Returns names of newly promoted lemmas.
        """
        promoted = []
        # Map step_name → (lemma_name, lhs_str, rhs_str)
        # Steps are stored as 'by_lemma:X' after the lemma_map renaming,
        # or as raw rule names for steps that haven't been mapped yet.
        rule_to_lemma = {
            # Raw rule names (pre-lemma-map)
            "additive_identity":       ("add_zero",      "(a + 0)",  "a"),
            "multiplicative_identity": ("mul_one",       "(a * 1)",  "a"),
            "multiplicative_zero":     ("mul_zero",      "(a * 0)",  "0"),
            "power_identity":          ("pow_one",       "(a ** 1)", "a"),
            "commute_add":             ("commute_add",   "(b + a)",  "(a + b)"),
            "commute_mul":             ("commute_mul",   "(b * a)",  "(a * b)"),
            "distribute_left":         ("distributive",  "(a * (b + a))", "((a * b) + (a * a))"),
            # by_lemma: prefixed names (post-lemma-map)
            "by_lemma:add_zero":       ("add_zero",      "(a + 0)",  "a"),
            "by_lemma:mul_one":        ("mul_one",       "(a * 1)",  "a"),
            "by_lemma:mul_zero":       ("mul_zero",      "(a * 0)",  "0"),
            "by_lemma:pow_one":        ("pow_one",       "(a ** 1)", "a"),
            "by_lemma:commutativity_add": ("commute_add","(b + a)",  "(a + b)"),
            "by_lemma:commutativity_mul": ("commute_mul","(b * a)",  "(a * b)"),
            "by_lemma:distributive":   ("distributive",  "(a * (b + a))", "((a * b) + (a * a))"),
            "by_lemma:add_zero":       ("add_zero",      "(a + 0)",  "a"),
        }

        for rule_name, count in self._step_counts.items():
            if count < threshold:
                continue
            if rule_name not in rule_to_lemma:
                continue
            lemma_name, lhs_s, rhs_s = rule_to_lemma[rule_name]
            if lemma_name in self._promoted:
                continue
            if lemma_name in self._engine._lemmas:
                continue

            try:
                lhs_node = _parse_expr_stub(lhs_s)
                rhs_node = _parse_expr_stub(rhs_s)
                result   = self._engine.prove(lhs_node, rhs_node, lemma_name)
                if result.status in ("proven", "trivial"):
                    self._engine.register_lemma(lemma_name, result)
                    self._promoted.add(lemma_name)
                    promoted.append(lemma_name)
                    print(f"  {cyan('🧩 LEMMA INVENTED')}  {bold(lemma_name)}  "
                          f"{dim(f'(used {count}x, auto-promoted at round {round_num})')}")
            except Exception:
                pass

        return promoted

    def invent_strategies(self, proof_engine: "SymbolicProofEngine",
                           strategy_lib: "ProofStrategyLibrary",
                           round_num: int) -> list[str]:
        """
        Layer 5 upgrade 3: strategy invention.

        When a proof uses a distinctive combination of steps that appears
        in multiple proofs, name that combination as a new strategy.

        Example: if commute_add → mul_one → distributive appears 3+ times,
        name it 'sum_squaring' and add it to the strategy library.

        This is how mathematicians develop proof techniques — not by being
        told 'use symmetry', but by noticing 'I keep doing this same sequence'.
        """
        invented = []

        # Extract step sequences from all proofs
        sequences: dict[str, int] = {}   # frozen sequence → count
        for result in proof_engine._proven.values():
            if result.status != "proven" or len(result.steps) < 2:
                continue
            # Get sequence of rule names (strip by_lemma: prefix for matching)
            seq = tuple(
                s.rule_name.replace("by_lemma:", "").split("[")[0]
                for s in result.steps[:4]
            )
            if len(seq) >= 2:
                sequences[seq] = sequences.get(seq, 0) + 1

        # Also track 2-tuples (more likely to recur with limited proof volume)
        for result in proof_engine._proven.values():
            if result.status != "proven" or len(result.steps) < 2:
                continue
            steps_clean = tuple(
                s.rule_name.replace("by_lemma:", "").split("[")[0]
                for s in result.steps[:3]
            )
            # Add all 2-prefixes too
            if len(steps_clean) >= 2:
                pair = steps_clean[:2]
                sequences[pair] = sequences.get(pair, 0) + 1

        # Name sequences that appear 2+ times
        strategy_names = {
            ("commute_add", "commutativity_mul", "distributive"): "symmetric_expansion",
            ("commute_add", "mul_one"):                            "commute_then_simplify",
            ("commutativity_mul", "distributive", "commutativity_mul"): "double_commute_expand",
            ("additive_identity", "multiplicative_identity"):     "identity_cleanup",
            ("distributive", "commute_add"):                      "expand_then_sort",
            ("commute_add", "commutativity_mul"):                  "cross_commute",
            ("commutativity_mul", "distributive"):                "mul_expand",
            ("add_zero", "mul_one"):                              "dual_identity",
        }

        for seq, count in sequences.items():
            if count < 2:
                continue
            if seq in strategy_names:
                name = strategy_names[seq]
            else:
                name = f"strategy_{seq[0][:6]}_{seq[-1][:6]}"

            # Only invent if not already in library
            existing_names = {s.name for s in strategy_lib.all_strategies()}
            if name in existing_names or name in self._promoted:
                continue

            # Add to strategy library
            from ms_semantics import ProofStrategy
            new_strat = ProofStrategy(
                name        = name,
                description = f"Auto-invented: {' → '.join(seq[:3])}",
                trigger     = f"expression matches pattern for {seq[0]} application",
                action      = f"apply sequence: {', '.join(seq)}",
                priority    = 0.7,
            )
            strategy_lib._strategies[name] = new_strat
            self._promoted.add(name)
            invented.append(name)
            print(f"  {green('🔧 STRATEGY INVENTED')}  {bold(name)}  "
                  f"{dim(f'(pattern used {count}x, auto-named at round {round_num})')}")
            print(f"    {dim(' → '.join(seq[:4]))}")

        return invented
        return (f"Lemma inventor: {len(self._step_counts)} rules tracked  |  "
                f"{len(self._promoted)} auto-promoted")


# ── Helpers ───────────────────────────────────────────────────────────────

def _expression_novelty(new_expr: str, origin_expr: str) -> float:
    """Measure how structurally different new_expr is from origin_expr."""
    new_tokens    = set(new_expr.replace("(","").replace(")","").split())
    origin_tokens = set(origin_expr.replace("(","").replace(")","").split())
    if not new_tokens and not origin_tokens:
        return 0.0
    jaccard = len(new_tokens & origin_tokens) / len(new_tokens | origin_tokens)
    return 1.0 - jaccard


def _generate_variants(node: Node, origin_str: str) -> list[tuple[str, str]]:
    """
    Generate candidate expression strings to explore as consequences.
    Uses structural composition — wrapping the expression in algebraic
    contexts that produce genuinely new canonical forms.
    """
    variants = []
    s = origin_str.strip()

    # Power variants — squaring and cubing
    variants.append((f"({s} ** 2)",        "square"))
    variants.append((f"({s} ** 3)",        "cube"))

    # Self-composition — distributive expansion
    variants.append((f"({s} * {s})",       "self_multiply"))
    variants.append((f"({s} + {s})",       "self_add"))

    # Composition with variables
    variants.append((f"({s} * a)",         "scale_by_a"))
    variants.append((f"({s} + a)",         "shift_by_a"))
    variants.append((f"({s} * b)",         "scale_by_b"))
    variants.append((f"(a * {s})",         "a_times"))

    # Structural composition
    if " + " in s:
        # (a+b)*a expands to a²+ab via distributive
        variants.append((f"({s} * a)",     "distribute_over"))
        variants.append((f"({s} * b)",     "distribute_b"))

    if " * " in s:
        # a*b squared
        variants.append((f"(({s}) ** 2)",  "square_product"))

    return variants