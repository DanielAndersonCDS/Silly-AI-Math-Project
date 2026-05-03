"""
ms_control.py — Semantic Control Layer

The missing piece: semantic tags changing agent behavior.

Current (open loop):
    discover → prove → tag → store

Needed (closed loop):
    discover → prove → tag → strategy weights
    → agent exploration bias → guided discovery
    → reinforcement → stronger strategies

This module implements the control policy that converts
semantic meaning into action bias. Three components:

1. STRATEGY WEIGHT TABLE
   Maps semantic roles → strategy bonuses.
   Updated by reinforcement when strategies succeed or fail.

2. AGENT PRIOR UPDATER
   Given an agent's known concepts and their semantic tags,
   computes a weighted "research style" vector that biases
   which expressions the agent prioritizes exploring.

3. SEMANTIC INTEREST SCORER
   Augments the existing novelty × compression score with
   a strategy alignment bonus:

     score = novelty × compression × (1 + strategy_bonus)

   Where strategy_bonus comes from: how well does this
   candidate expression match the strategies suggested by
   the agent's current semantic knowledge?

WHAT EMERGES:
   Agents develop "research styles" — not programmed,
   but arising from which strategies reinforce for them.
   One agent becomes an "algebraic expander", another
   a "symmetry exploiter", another a "power-family tracer."
   These styles are exactly what human mathematicians develop
   from experience.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import random

from ms_core import *
from ms_semantics import SemanticTag, SemanticTagger, ProofStrategyLibrary


# ── Strategy weight table ─────────────────────────────────────────────────

# Maps (semantic_role, strategy_name) → affinity weight
# Higher weight = stronger recommendation to try this strategy
# when this role is present.
_BASE_AFFINITIES: dict[tuple[str, str], float] = {
    ("commutative",               "use_symmetry"):         0.9,
    ("commutative",               "try_commutativity"):    0.8,
    ("zero-annihilating",         "use_zero_annihilation"):0.9,
    ("has-multiplicative-identity","identity_reduction"):  0.9,
    ("quadratic-growth",          "factor_quadratic"):     0.8,
    ("quadratic-growth",          "square_expansion"):     0.7,
    ("exponential-growth",        "power_laws"):           0.9,
    ("linear-scaling",            "expand_and_simplify"):  0.6,
    ("positive-definite",         "use_symmetry"):         0.4,
    # Structural roles
    ("power",                     "power_laws"):           0.9,
    ("power",                     "expand_power"):         0.8,
    ("sum",                       "try_telescoping"):      0.85,
    ("sum",                       "factor_common"):        0.7,
    ("iterative",                 "expand_and_simplify"):  0.7,
}


@dataclass
class AgentStyle:
    """
    An agent's learned research style — which strategies it
    tends to succeed with, updated by reinforcement.
    """
    agent_name:      str
    strategy_weights: dict[str, float] = field(default_factory=dict)
    successes:       dict[str, int]    = field(default_factory=dict)
    failures:        dict[str, int]    = field(default_factory=dict)
    dominant_role:   str = ""   # the semantic role this agent explores most

    def reinforce(self, strategy: str, reward: float) -> None:
        """Increase weight for a strategy that succeeded."""
        self.strategy_weights[strategy] = (
            self.strategy_weights.get(strategy, 0.5) + 0.1 * reward
        )
        self.successes[strategy] = self.successes.get(strategy, 0) + 1

    def penalize(self, strategy: str) -> None:
        """Decrease weight for a strategy that failed."""
        self.strategy_weights[strategy] = max(
            0.1,
            self.strategy_weights.get(strategy, 0.5) - 0.05
        )
        self.failures[strategy] = self.failures.get(strategy, 0) + 1

    def top_strategy(self) -> str:
        if not self.strategy_weights:
            return ""
        return max(self.strategy_weights, key=lambda s: self.strategy_weights[s])

    def style_description(self) -> str:
        top = self.top_strategy()
        style_map = {
            "expand_and_simplify": "algebraic expander",
            "use_symmetry":        "symmetry seeker",
            "power_laws":          "power-family tracer",
            "try_telescoping":     "summation specialist",
            "factor_quadratic":    "quadratic analyst",
            "square_expansion":    "binomial explorer",
            "identity_reduction":  "identity simplifier",
        }
        return style_map.get(top, f"{top} specialist") if top else "generalist"


# ── Semantic Control Engine ───────────────────────────────────────────────

class SemanticControlEngine:
    """
    Closes the loop: semantic meaning → agent action bias.

    This is the "librarian" that transforms stored knowledge
    into active research guidance.
    """

    def __init__(self, tagger: SemanticTagger,
                  strategy_lib: ProofStrategyLibrary):
        self._tagger      = tagger
        self._strategies  = strategy_lib
        self._styles:     dict[str, AgentStyle] = {}
        self._affinities  = dict(_BASE_AFFINITIES)   # mutable copy

    # Innate tendencies by mathematician name — reflects their historical specialisations.
    # These seed the initial strategy weights before any reinforcement occurs,
    # creating immediate differentiation that reinforcement then amplifies.
    _INNATE_BIAS: dict[str, str] = {
        "Gauss":     "try_telescoping",       # Gauss = summation / triangular numbers
        "Euclid":    "expand_and_simplify",   # Euclid = systematic expansion
        "Noether":   "use_symmetry",          # Noether = symmetry / invariants
        "Fermat":    "power_laws",            # Fermat = powers / number theory
        "Lovelace":  "factor_quadratic",      # Lovelace = quadratic analysis
        "Euler":     "power_laws",            # Euler = series and powers
        "Turing":    "identity_reduction",    # Turing = simplification / reduction
        "Ramanujan": "use_symmetry",          # Ramanujan = deep pattern symmetry
        "Riemann":   "try_telescoping",       # Riemann = summation / series
        "Hilbert":   "expand_and_simplify",   # Hilbert = formal expansion
        "Leibniz":   "factor_common",         # Leibniz = factoring / calculus
        "Pascal":    "factor_quadratic",      # Pascal = combinatorial / quadratic
    }

    def _get_style(self, agent_name: str) -> AgentStyle:
        if agent_name not in self._styles:
            style = AgentStyle(agent_name=agent_name)
            # Seed with innate bias for this mathematician
            innate = self._INNATE_BIAS.get(agent_name, "")
            if innate:
                style.strategy_weights[innate] = 1.5   # strong initial bias
                style.dominant_role = innate
            self._styles[agent_name] = style
        return self._styles[agent_name]

    # ── Core function: semantic interest score ────────────────────────

    def semantic_interest_bonus(self, agent_name: str,
                                 candidate_expr: str,
                                 candidate_node: "Optional[Node]" = None,
                                 agent_concepts: "list[str]" = None) -> float:
        """
        Given a candidate expression and an agent's known concepts,
        return a semantic bonus score (0.0 to 1.0) based on how well
        this candidate aligns with the agent's semantic strengths.

        This is added to the novelty×compression score:
            final_score = base_score × (1 + semantic_bonus)

        So an agent with strong symmetry skills gets a bonus when
        exploring commutative expressions, even if they're not the
        most novel.
        """
        style = self._get_style(agent_name)
        bonus = 0.0

        # Check strategy alignment: does this expression trigger
        # strategies the agent is good at?
        strats = self._strategies.suggest_for_expression(candidate_expr)
        for strat in strats:
            w = style.strategy_weights.get(strat.name, 0.5)
            bonus += w * strat.priority * 0.6  # was 0.3 — needs to be strong enough to matter

        # Check curiosity target alignment: is this what the curiosity
        # engine predicted we should look at?
        if hasattr(self, "_curiosity_targets") and self._curiosity_targets:
            for target in self._curiosity_targets:
                if target in candidate_expr or candidate_expr in target:
                    bonus += 0.4   # strong bonus for pursuing open questions

        return min(bonus, 1.0)   # cap at 1.0 (doubles the base score at most)

    def compute_agent_priors(self, agent_name: str,
                              known_concepts: list) -> dict[str, float]:
        """
        Given an agent's known concepts and their semantic tags,
        compute a strategy weight vector reflecting what this agent
        knows how to do.

        This is the "research style" computation.
        Called when an agent's knowledge base changes significantly.
        """
        style = self._get_style(agent_name)
        strategy_votes: dict[str, float] = {}

        for concept in known_concepts:
            tag = self._tagger.get_tag(concept.name) if hasattr(concept, 'name') else None
            if tag is None:
                continue

            # For each role this concept has, look up recommended strategies
            for role in tag.roles:
                for (r, s), affinity in self._affinities.items():
                    if r == role:
                        strategy_votes[s] = strategy_votes.get(s, 0.0) + affinity

            # Direct strategy hints from semantic tag
            for strat_name in tag.suggested_strategies:
                strategy_votes[strat_name] = strategy_votes.get(strat_name, 0.0) + 0.5

        # Blend with existing weights (don't fully override)
        for strat, vote in strategy_votes.items():
            existing = style.strategy_weights.get(strat, 0.5)
            style.strategy_weights[strat] = 0.7 * existing + 0.3 * (vote / 3.0)

        # Set dominant role
        if strategy_votes:
            style.dominant_role = max(strategy_votes, key=strategy_votes.get)

        return style.strategy_weights

    def reinforce(self, agent_name: str, strategy_used: str,
                   succeeded: bool, magnitude: float = 1.0) -> None:
        """
        Reinforcement update: a strategy that was used just succeeded or failed.
        Updates the agent's style weights and the global affinity table.
        """
        style = self._get_style(agent_name)
        if succeeded:
            style.reinforce(strategy_used, magnitude)
            # Slightly increase global affinity too
            for key in self._affinities:
                if key[1] == strategy_used:
                    self._affinities[key] = min(1.0, self._affinities[key] + 0.02)
        else:
            style.penalize(strategy_used)

    def get_biased_imagination_seeds(self, agent_name: str,
                                      own_programs: list,
                                      strategy_weights: dict[str, float],
                                      n: int = 3) -> list[tuple]:
        """
        Given an agent's programs and their current strategy weights,
        return a prioritized list of (transform_name, priority_multiplier)
        for the imagination phase.

        Currently imagination applies transforms in fixed order.
        This makes it strategy-weighted: an agent strong in power_laws
        will prioritize squared/cubed transforms; one strong in symmetry
        will prioritize commuted/expanded transforms.
        """
        base_transforms = [
            ("squared",  "power_laws",          1.0),
            ("cubed",    "power_laws",           1.0),
            ("looped",   "expand_and_simplify",  0.8),
            ("shifted",  "identity_reduction",   0.7),
            ("doubled",  "use_symmetry",         0.7),
            ("expanded", "expand_and_simplify",  0.8),
        ]
        scored = []
        for t_name, strategy, base_priority in base_transforms:
            w = strategy_weights.get(strategy, 0.5)
            scored.append((t_name, base_priority * w))

        scored.sort(key=lambda x: -x[1])
        return scored[:n]

    def announce_style(self, agent_name: str) -> str:
        """Return a human-readable description of this agent's research style."""
        style = self._get_style(agent_name)
        if not style.strategy_weights:
            return f"{agent_name}: generalist (no style formed yet)"
        desc = style.style_description()
        top  = style.top_strategy()
        w    = style.strategy_weights.get(top, 0.0)
        return (f"{agent_name}: {desc}  "
                f"(top={top}  weight={w:.2f}  "
                f"successes={sum(style.successes.values())})")

    def all_styles(self) -> dict[str, AgentStyle]:
        return self._styles

    def summary(self) -> str:
        if not self._styles:
            return "Control layer: no styles formed yet"
        styles = [s.style_description() for s in self._styles.values()]
        unique = list(dict.fromkeys(styles))
        return (f"Control layer: {len(self._styles)} agent styles  "
                f"| Specialisations: {', '.join(unique[:4])}")