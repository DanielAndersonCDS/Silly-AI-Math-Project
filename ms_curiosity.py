"""
ms_curiosity.py — Expectation-Based Curiosity Engine

Real curiosity isn't random exploration — it's the response to
a violated expectation. When a system's internal model predicts X
and observes Y, the gap |X - Y| creates the drive to explain.

This module adds that mechanism:

1. EXPECTATION ENGINE
   Given what the system knows (laws, abstractions, theory tree),
   generates predictions about what SHOULD be true:
     "T^2 = sum_cubes [proven]  →  T^3 SHOULD also have a law"
     "Squaring preserves positivity  →  T^3 should be positive"
     "PowerFamily covers n=2,3  →  n=4 should exist"

2. SURPRISE DETECTOR
   Compares predictions against what's actually known.
   Scores each gap:
     - High score: predicted but not yet found → strong curiosity
     - Zero score: predicted and confirmed → no curiosity
     - Negative: confirmed false → anomaly, revise theory

3. CURIOSITY PRIORITIZER
   Ranks open questions by:
     - How surprising the gap is
     - How fundamental the predicted relationship is
     - How close the system is to resolving it

4. RESEARCH AGENDA
   The top-ranked open questions become the system's research agenda —
   agents get directed toward these gaps rather than exploring randomly.
   This is "curiosity-driven discovery" vs "task-driven discovery."

CONCRETE PREDICTIONS GENERATED:
  From PowerFamily: T^n should exist for all n — T^3, T^4 are open
  From Nicomachus (T^2 = sum_cubes): what does T^3 = ?
  From Squaring abstraction: squaring any concept should produce a new law
  From Scaling laws: if 3·f is a law, is 4·f, 5·f also?
  From family clustering: if LINEAR family has 13 members, CUBIC should too
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import random

from ms_core import *
from ms_abstraction import AbstractObject, AbstractionDetector


# ── Prediction ────────────────────────────────────────────────────────────

@dataclass
class Prediction:
    """A prediction generated from known theories."""
    prediction_id: str
    source:        str          # which law/abstraction generated this
    claim:         str          # what we predict should be true
    formal:        str          # formal version of the claim
    predicted_expr: Optional[str]   # the expression we expect to find/prove
    confidence:    float        # how confident based on source strength
    priority:      float        # urgency (higher = more worth exploring)
    status:        str = "open" # 'open' | 'confirmed' | 'refuted' | 'irrelevant'
    surprise_score: float = 0.0  # |prediction - observation| when checked
    round_generated: int = 0


# ── Expectation Engine ────────────────────────────────────────────────────

class ExpectationEngine:
    """
    Generates predictions from known laws, abstractions, and families.
    Each prediction is something the system EXPECTS to find based on
    what it already knows.
    """

    def __init__(self):
        self._predictions: dict[str, Prediction] = {}
        self._counter = 0

    def _new_id(self) -> str:
        self._counter += 1
        return f"P{self._counter:04d}"

    def generate_from_abstraction(self, obj: AbstractObject,
                                   round_num: int) -> list[Prediction]:
        """
        From abstract objects like PowerFamily, generate predictions:
          'If f^2 and f^3 exist, f^4 should too'
          'If Squaring applies to addition and multiplication, it applies to T(b)'
        """
        predictions = []

        if obj.name == "PowerFamily":
            # PowerFamily: if we have n=2,3, predict n=4
            # Extract base expressions
            import re
            bases = set()
            for inst in obj.instances:
                m = re.match(r'^\((.+) \*\* (\d+)\)$', inst)
                if m:
                    bases.add(m.group(1))

            for base in list(bases)[:3]:
                pred_expr = f"({base} ** 4)"
                pid = f"power4_{hash(base) % 10000:04d}"
                if pid not in self._predictions:
                    p = Prediction(
                        prediction_id  = pid,
                        source         = f"PowerFamily({base})",
                        claim          = f"If {base}^2 and {base}^3 exist, {base}^4 should too",
                        formal         = f"PowerFamily predicts: ({base} ** 4) is a meaningful expression",
                        predicted_expr = pred_expr,
                        confidence     = 0.8,
                        priority       = 0.6,
                        round_generated = round_num,
                    )
                    self._predictions[pid] = p
                    predictions.append(p)

        elif obj.name == "Squaring":
            # Squaring: for each concept not yet squared, predict its square matters
            for inst in obj.instances[:5]:
                pid = f"sq_law_{hash(inst) % 10000:04d}"
                if pid not in self._predictions:
                    p = Prediction(
                        prediction_id  = pid,
                        source         = "Squaring abstraction",
                        claim          = f"({inst})^2 should satisfy some mathematical law",
                        formal         = f"∃ task kind k: ({inst} ** 2) solves k",
                        predicted_expr = f"({inst} ** 2)",
                        confidence     = 0.6,
                        priority       = 0.5,
                        round_generated = round_num,
                    )
                    self._predictions[pid] = p
                    predictions.append(p)

        return predictions

    def generate_from_law(self, law: dict, round_num: int) -> list[Prediction]:
        """
        From a crystallised law, generate predictions about related laws.
        Example: Nicomachus says T^2 = sum_cubes → what does T^3 = ?
        """
        predictions = []
        name     = law.get("name", "")
        parent   = law.get("parent", "")
        child    = law.get("child", "")

        # From squaring law: predict cubing law exists
        if "Squaring" in name and parent:
            pid = f"cube_{hash(parent) % 10000:04d}"
            if pid not in self._predictions:
                p = Prediction(
                    prediction_id  = pid,
                    source         = f"Law: {name}",
                    claim          = f"If {parent}^2 is meaningful, {parent}^3 should be too",
                    formal         = f"By analogy with {name}: ({parent} ** 3) has a mathematical role",
                    predicted_expr = f"({parent} ** 3)",
                    confidence     = 0.7,
                    priority       = 0.75,
                    round_generated = round_num,
                )
                self._predictions[pid] = p
                predictions.append(p)

        # From Nicomachus: predict the cube law
        if "Nicomachus" in name:
            pid = "nicomachus_cube"
            if pid not in self._predictions:
                p = Prediction(
                    prediction_id  = "nicomachus_cube",
                    source         = "Nicomachus' Theorem",
                    claim          = "T^2 = sum_cubes — does T^3 also equal a known sum?",
                    formal         = "By analogy: ∃ sum_kind k: T(n)^3 = k(n)",
                    predicted_expr = "((((1 + b) * b) // 2) ** 3)",
                    confidence     = 0.6,
                    priority       = 0.9,   # high priority — direct follow-up to key theorem
                    round_generated = round_num,
                )
                self._predictions[pid] = p
                predictions.append(p)

        return predictions

    def generate_from_family(self, family_name: str, members: list[str],
                              round_num: int) -> list[Prediction]:
        """
        From concept families, predict that underrepresented families
        will grow to match well-represented ones.
        """
        predictions = []

        if family_name == "quadratic" and len(members) >= 3:
            pid = "cubic_family_growth"
            if pid not in self._predictions:
                p = Prediction(
                    prediction_id  = pid,
                    source         = f"Family: {family_name} ({len(members)} members)",
                    claim          = "Quadratic family is large — cubic family should grow too",
                    formal         = "By family symmetry: cubic sequences deserve as many concepts as quadratic",
                    predicted_expr = None,
                    confidence     = 0.5,
                    priority       = 0.4,
                    round_generated = round_num,
                )
                self._predictions[pid] = p
                predictions.append(p)

        return predictions

    def all_predictions(self) -> list[Prediction]:
        return list(self._predictions.values())

    def open_predictions(self) -> list[Prediction]:
        return [p for p in self._predictions.values() if p.status == "open"]


# ── Surprise Detector ─────────────────────────────────────────────────────

class SurpriseDetector:
    """
    Measures the gap between predictions and observations.
    High surprise = anomaly = worth investigating.
    """

    def check_predictions(self, predictions: list[Prediction],
                           known_concepts: list[str],
                           known_laws: list[dict],
                           round_num: int) -> list[Prediction]:
        """
        For each open prediction, check if it has been confirmed or refuted.
        Returns predictions whose status changed (surprises).
        """
        surprises = []
        known_set = set(known_concepts)
        law_names = {l.get("name","") for l in known_laws}
        law_stmts = {l.get("statement","") for l in known_laws}

        for pred in predictions:
            if pred.status != "open":
                continue
            if pred.predicted_expr is None:
                continue

            # Check if predicted expression IS a known concept (exact match)
            if pred.predicted_expr in known_set:
                pred.status = "confirmed"
                pred.surprise_score = 0.0
                surprises.append(pred)
                continue

            # Check substring: predicted expr is a sub-form of a known concept
            # (e.g. T^3 would appear inside T^3 + a or similar)
            for known in known_set:
                if pred.predicted_expr in known or known in pred.predicted_expr:
                    if len(known) > 10 and len(pred.predicted_expr) > 10:
                        pred.status = "confirmed"
                        pred.surprise_score = 0.0
                        surprises.append(pred)
                        break

        return surprises


# ── Curiosity Engine ──────────────────────────────────────────────────────

class CuriosityEngine:
    """
    The main curiosity loop:
      1. Generate predictions from what we know
      2. Check predictions against what we've found
      3. Rank open predictions by priority
      4. Direct agents toward the highest-priority gaps

    This transforms agents from task-solvers into researchers who
    have their own agenda of open questions to pursue.
    """

    def __init__(self):
        self._expectations = ExpectationEngine()
        self._surprise     = SurpriseDetector()
        self._agenda:    list[Prediction] = []
        self._resolved:  list[Prediction] = []
        self._events:    list[str]        = []

    def generate_theory_gap_questions(self, branches: list,
                                       laws: list[dict],
                                       round_num: int) -> list[Prediction]:
        """
        Layer 5 upgrade 1: discovery-driven problem selection.

        Instead of asking 'if f² exists, does f³?' (pattern extension),
        ask 'what CONNECTS these theory branches?' (conceptual leap).

        Detects gaps between branches and formulates research questions
        that would close those gaps — the same way Euler connected
        different areas of mathematics by asking structural questions.
        """
        predictions = []

        branch_names = [b.name if hasattr(b, 'name') else str(b)
                        for b in branches]

        # Gap 1: Summation ↔ Power Sum bridge
        has_summation = any("ummation" in bn or "sum" in bn.lower()
                            for bn in branch_names)
        has_power_sum = any("ower" in bn or "power" in bn.lower()
                            for bn in branch_names)

        if has_summation and has_power_sum:
            pid = "cross_branch_power_sum_unification"
            if pid not in self._expectations._predictions:
                # Check if we have T^2 = sum_cubes already
                has_nicomachus = any("icomachus" in l.get("name","")
                                     for l in laws)
                if has_nicomachus:
                    p = Prediction(
                        prediction_id   = pid,
                        source          = "Theory gap: Summation ↔ Power Sum",
                        claim           = "T(n)^2 = Σi³ is proven — what is the UNIFIED law for T(n)^k?",
                        formal          = "∃ formula F(k): T(n)^k = F(k, n) for all k ≥ 2",
                        predicted_expr  = "((((1 + b) * b) // 2) ** 3)",
                        confidence      = 0.8,
                        priority        = 0.95,   # highest — this is a deep structural question
                        round_generated = round_num,
                    )
                    self._expectations._predictions[pid] = p
                    predictions.append(p)
                    print(f"  {cyan('🔭 THEORY GAP')}  {bold('Cross-branch question generated:')}")
                    print(f"    {dim(p.claim)}")

        # Gap 2: Power sum series continuity
        law_names = {l.get("name","") for l in laws}
        has_sum_squares = any("square" in ln.lower() for ln in law_names)
        has_sum_cubes   = any("cube" in ln.lower() or "icomachus" in ln.lower()
                              for ln in law_names)

        if has_sum_squares and has_sum_cubes:
            pid = "power_sum_series_continuation"
            if pid not in self._expectations._predictions:
                p = Prediction(
                    prediction_id   = pid,
                    source          = "Theory gap: Power Sum series",
                    claim           = "Σi² and Σi³ both known — is there a pattern for Σi^k (all k)?",
                    formal          = "The Bernoulli number formula: Σi^k = B_k(n+1) - B_k(0) / (k+1)",
                    predicted_expr  = None,   # open-ended — no single expression
                    confidence      = 0.7,
                    priority        = 0.85,
                    round_generated = round_num,
                )
                self._expectations._predictions[pid] = p
                predictions.append(p)

        return predictions

    def update(self, laws: list[dict],
               abstractions: list[AbstractObject],
               families: dict[str, list[str]],
               known_concept_exprs: list[str],
               round_num: int,
               theory_branches: list = None) -> list[Prediction]:
        """
        Full curiosity update cycle:
          1. Generate new predictions from latest knowledge
          2. Check existing predictions for confirmation/refutation
          3. Update research agenda
        Returns newly opened high-priority predictions.
        """
        new_predictions: list[Prediction] = []

        # Generate predictions from abstractions
        for obj in abstractions:
            preds = self._expectations.generate_from_abstraction(obj, round_num)
            new_predictions.extend(preds)

        # Generate predictions from laws
        for law in laws:
            preds = self._expectations.generate_from_law(law, round_num)
            new_predictions.extend(preds)

        # Generate predictions from families
        for fam_name, members in families.items():
            preds = self._expectations.generate_from_family(
                fam_name, members, round_num)
            new_predictions.extend(preds)

        # Layer 5: theory-gap questions (concept bridges between branches)
        if theory_branches:
            gap_preds = self.generate_theory_gap_questions(
                theory_branches, laws, round_num)
            new_predictions.extend(gap_preds)

        # Check all open predictions
        surprises = self._surprise.check_predictions(
            self._expectations.open_predictions(),
            known_concept_exprs,
            laws,
            round_num,
        )

        # Update agenda: open predictions sorted by priority
        self._agenda = sorted(
            self._expectations.open_predictions(),
            key=lambda p: -(p.priority * p.confidence),
        )

        # Announce new high-priority predictions
        announced = []
        for pred in new_predictions:
            if pred.priority >= 0.7:
                print(f"  {cyan('🤔 OPEN QUESTION')}  {bold(pred.claim)}")
                print(f"     {dim(pred.formal)}")
                print(f"     {dim(f'Priority={pred.priority:.1f}  Confidence={pred.confidence:.1f}  source={pred.source}')}")
                self._events.append(f"OPEN_QUESTION: {pred.claim}")
                announced.append(pred)

        return announced

    def top_questions(self, n: int = 3) -> list[Prediction]:
        """Return the top n open research questions by priority."""
        return self._agenda[:n]

    def inject_into_agents(self, agents: list,
                            round_num: int) -> None:
        """
        Give agents the current research agenda.
        High-priority open questions become targets for the curiosity
        exploration (contemplate) phase.
        """
        top = self.top_questions(2)
        for pred in top:
            if pred.predicted_expr is None:
                continue
            # Inject as a curiosity target for agents
            for agent in agents:
                if not hasattr(agent, "_curiosity_targets"):
                    agent._curiosity_targets = []
                if pred.predicted_expr not in agent._curiosity_targets:
                    agent._curiosity_targets.append(pred.predicted_expr)

    def summary(self) -> str:
        open_qs  = len(self._expectations.open_predictions())
        total    = len(self._expectations.all_predictions())
        resolved = total - open_qs
        top      = self.top_questions(1)
        top_str  = top[0].claim[:60] if top else "none"
        return (f"Curiosity: {total} predictions  "
                f"({open_qs} open, {resolved} resolved)  |  "
                f"Top question: {top_str}")