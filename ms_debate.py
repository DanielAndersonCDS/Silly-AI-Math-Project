"""
ms_debate.py — Mathematical Debate System

Ancient Greek mathematicians didn't just discover theorems alone.
They debated them publicly. Challenges forced proofs to become rigorous.
Defenders had to explain WHY, not just that something works.

This module adds that pressure to the agent civilization.

DEBATE PROTOCOL:
  1. A Proposer agent announces a conjecture (concept + claim)
  2. Challenger agents have N rounds to find a counterexample
  3. If no counterexample found → Proposal ACCEPTED, proposer gains prestige
  4. If counterexample found → Proposal REFUTED, challenger gains prestige
  5. Accepted laws enter the permanent registry with higher confidence

DEBATE TYPES:
  - Equivalence debate: "A behaves like B"    → challenger finds A(x) ≠ B(x)
  - Law debate: "∀n: P(n)"                   → challenger finds P(n) = False
  - Superiority debate: "My formula beats yours" → judged on test suite

PRESTIGE ECONOMY:
  - Win a debate: +50 prestige
  - Lose a debate: -10 prestige  (small, losing is still brave)
  - Propose a law that survives all challenges: +100 prestige
  - Survive 3+ challenges: law gets "🏆 Battle-Tested" status
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import random

from ms_core import *
from ms_concepts import *


# ── Debate record ─────────────────────────────────────────────────────────

@dataclass
class DebateRecord:
    debate_id:    str
    kind:         str          # 'law' | 'equivalence' | 'superiority'
    proposer:     str          # agent name
    claim:        str          # human-readable claim
    formal:       str          # symbolic statement
    concept_a:    str          # primary concept involved
    concept_b:    Optional[str] = None   # secondary (for equivalence/superiority)
    round_opened: int = 0
    round_closed: int = 0
    status:       str = 'open'  # 'open' | 'accepted' | 'refuted' | 'expired'
    challenger:   Optional[str] = None
    counterexample: Optional[str] = None  # e.g. "n=7: P(7)=False"
    challenges_survived: int = 0
    prestige_awarded: dict = field(default_factory=dict)  # agent→amount


@dataclass
class DebateChallenge:
    debate_id:  str
    challenger: str
    attempt:    str   # what they tried
    result:     str   # 'refuted' | 'failed'
    evidence:   str   # numeric evidence


# ── Debate Arena ───────────────────────────────────────────────────────────

class DebateArena:
    """
    Manages all debates in the civilization.
    Agents can open debates, challenge claims, and earn prestige.
    """

    CHALLENGE_WINDOW = 8    # rounds challengers have to find counterexample
    PRESTIGE_WIN     = 50
    PRESTIGE_LOSE    = -10
    PRESTIGE_SURVIVE = 20   # per challenge survived
    PRESTIGE_LAW     = 100  # law survives all challenges and gets accepted

    def __init__(self):
        self.debates:    dict[str, DebateRecord] = {}
        self.challenges: list[DebateChallenge]   = []
        self._counter = 0
        self.events:  list[str] = []

    # ── Opening a debate ──────────────────────────────────────────────────

    def propose_law(self, proposer: str, law: dict, round_num: int) -> DebateRecord:
        """
        An agent proposes a crystallised law for public debate.
        Other agents now have CHALLENGE_WINDOW rounds to refute it.
        """
        self._counter += 1
        debate_id = f"debate_{self._counter:04d}"

        rec = DebateRecord(
            debate_id    = debate_id,
            kind         = 'law',
            proposer     = proposer,
            claim        = law.get('name', 'Unknown law'),
            formal       = law.get('statement', law.get('formal', '')),
            concept_a    = law.get('parent', ''),
            concept_b    = law.get('child', ''),
            round_opened = round_num,
        )
        self.debates[debate_id] = rec

        msg = (f"\n  {'━'*62}\n"
               f"  ⚔️  {bold('DEBATE OPENED')}  {bold(cyan(rec.claim))}\n"
               f"     Proposer: {bold(proposer)}  |  "
               f"Challenge window: {self.CHALLENGE_WINDOW} rounds\n"
               f"     Claim: {dim(rec.formal[:70])}\n"
               f"  {'━'*62}")
        print(msg)
        self.events.append(f"DEBATE_OPEN: {rec.claim} by {proposer}")
        return rec

    def propose_equivalence(self, proposer: str,
                            concept_a: str, canon_a: str,
                            concept_b: str, canon_b: str,
                            round_num: int) -> DebateRecord:
        """Propose that two concepts are behaviourally equivalent."""
        self._counter += 1
        debate_id = f"debate_{self._counter:04d}"
        claim = f"{concept_a} ≡ {concept_b}"
        formal = f"{canon_a}  ≡  {canon_b}"

        rec = DebateRecord(
            debate_id    = debate_id,
            kind         = 'equivalence',
            proposer     = proposer,
            claim        = claim,
            formal       = formal,
            concept_a    = concept_a,
            concept_b    = concept_b,
            round_opened = round_num,
        )
        self.debates[debate_id] = rec
        msg = (f"  ⚔️  {bold('EQUIVALENCE DEBATE')}  {cyan(claim)}  "
               f"proposed by {bold(proposer)}")
        print(msg)
        self.events.append(f"DEBATE_EQUIV: {claim} by {proposer}")
        return rec

    # ── Challenging a debate ──────────────────────────────────────────────

    def challenge(self, debate_id: str, challenger: str,
                  concepts: ConceptRegistry,
                  round_num: int) -> Optional[DebateChallenge]:
        """
        An agent attempts to refute an open debate.
        Returns a challenge record, or None if nothing was found.

        Strategy: try random inputs outside the verified range and
        check if the claim fails. This is empirical refutation.
        """
        rec = self.debates.get(debate_id)
        if rec is None or rec.status != 'open':
            return None
        if challenger == rec.proposer:
            return None   # can't challenge your own claim

        refuted = False
        evidence = ""

        if rec.kind == 'law':
            # Try to find input where the law fails
            refuted, evidence = self._attack_law(rec, concepts)

        elif rec.kind == 'equivalence':
            # Try to find input where A(x) ≠ B(x)
            refuted, evidence = self._attack_equivalence(rec, concepts)

        result = 'refuted' if refuted else 'failed'
        ch = DebateChallenge(
            debate_id  = debate_id,
            challenger = challenger,
            attempt    = f"tested {rec.formal[:40]}",
            result     = result,
            evidence   = evidence,
        )
        self.challenges.append(ch)

        if refuted:
            rec.status     = 'refuted'
            rec.challenger = challenger
            rec.counterexample = evidence
            rec.round_closed   = round_num

            proposer_prestige = self.PRESTIGE_LOSE
            challenger_prestige = self.PRESTIGE_WIN
            rec.prestige_awarded = {
                rec.proposer: proposer_prestige,
                challenger:   challenger_prestige,
            }

            print(f"  ⚔️  {red('DEBATE REFUTED')}  {bold(rec.claim)}")
            print(f"     {bold(challenger)} found counterexample: {dim(evidence)}")
            print(f"     {bold(rec.proposer)} loses {abs(proposer_prestige)}cr  |  "
                  f"{bold(challenger)} gains {challenger_prestige}cr\n")
            self.events.append(f"DEBATE_REFUTED: {rec.claim} by {challenger}")
        else:
            rec.challenges_survived += 1

        return ch

    def _attack_law(self, rec: DebateRecord,
                    concepts: ConceptRegistry) -> tuple[bool, str]:
        """Try to find input where a law claim fails."""
        all_c = {c.name: c for c in concepts.all_concepts()}
        parent_c = all_c.get(rec.concept_a)
        child_c  = all_c.get(rec.concept_b)

        if parent_c is None or child_c is None:
            return False, "concepts not found"
        if parent_c.program_node is None or child_c.program_node is None:
            return False, "no program nodes"

        # Determine what relationship the law claims
        # 'Squaring Law' → child = parent²
        # 'Scaling Law'  → child = k × parent  (extract k from name)
        # 'Nicomachus'   → squaring of triangular = cubes
        law_name = rec.claim
        is_squaring = 'Squaring' in law_name or 'Nicomachus' in law_name
        scale_k = None
        if 'Scaling' in law_name:
            import re
            m = re.search(r'= (\d+)·', law_name)
            if m:
                scale_k = int(m.group(1))

        # Test on larger inputs than the original verification range
        attack_pts = (
            [(1, b) for b in range(9, 26)] +
            [(a, b) for a in range(2, 5) for b in range(3, 10)]
        )
        for a, b in attack_pts:
            try:
                pv = parent_c.program_node.eval({"a": a, "b": b}, [0])
                cv = child_c.program_node.eval({"a": a, "b": b}, [0])
                if is_squaring and cv != pv * pv:
                    return True, f"f({a},{b}): child={cv}  parent²={pv*pv}"
                elif scale_k is not None and cv != pv * scale_k:
                    return True, f"f({a},{b}): child={cv}  {scale_k}×parent={pv*scale_k}"
            except Exception:
                pass

        return False, "all tested inputs satisfy the law"

    def _attack_equivalence(self, rec: DebateRecord,
                             concepts: ConceptRegistry) -> tuple[bool, str]:
        """Try to find input where A(x) ≠ B(x)."""
        all_c = {c.name: c for c in concepts.all_concepts()}
        ca = all_c.get(rec.concept_a)
        cb = all_c.get(rec.concept_b)

        if ca is None or cb is None:
            return False, "concepts not found"
        if ca.program_node is None or cb.program_node is None:
            return False, "no program nodes"

        attack_pts = [(a, b) for a in range(1, 8) for b in range(1, 10)]
        for a, b in attack_pts:
            try:
                va = ca.program_node.eval({"a": a, "b": b}, [0])
                vb = cb.program_node.eval({"a": a, "b": b}, [0])
                if va != vb:
                    return True, f"f({a},{b}): A={va}  B={vb}"
            except Exception:
                pass

        return False, "behaviourally equivalent on all tested inputs"

    # ── Closing expired debates ───────────────────────────────────────────

    def close_expired(self, round_num: int,
                      agents_by_name: dict) -> list[DebateRecord]:
        """
        Close debates that have exceeded their challenge window.
        Surviving claims get accepted and earn prestige for the proposer.
        """
        closed = []
        for rec in self.debates.values():
            if rec.status != 'open':
                continue
            if round_num - rec.round_opened < self.CHALLENGE_WINDOW:
                continue

            rec.status     = 'accepted'
            rec.round_closed = round_num

            # Prestige for surviving all challenges
            base = self.PRESTIGE_LAW if rec.challenges_survived == 0 else \
                   self.PRESTIGE_LAW + rec.challenges_survived * self.PRESTIGE_SURVIVE
            rec.prestige_awarded = {rec.proposer: base}

            battle_tested = rec.challenges_survived >= 3
            badge = "  🏆 Battle-Tested!" if battle_tested else ""

            print(f"  ✅  {bold(green('DEBATE ACCEPTED'))}  {bold(rec.claim)}{badge}")
            print(f"     {bold(rec.proposer)} earns {base}cr prestige  |  "
                  f"survived {rec.challenges_survived} challenge(s)\n")
            self.events.append(f"DEBATE_ACCEPTED: {rec.claim} by {rec.proposer}")
            closed.append(rec)

        return closed

    # ── Statistics ────────────────────────────────────────────────────────

    def leaderboard(self) -> list[tuple[str, int]]:
        """Return (agent_name, total_prestige) sorted descending."""
        totals: dict[str, int] = {}
        for rec in self.debates.values():
            for agent, amount in rec.prestige_awarded.items():
                totals[agent] = totals.get(agent, 0) + amount
        return sorted(totals.items(), key=lambda x: -x[1])

    def summary(self) -> str:
        total    = len(self.debates)
        accepted = sum(1 for r in self.debates.values() if r.status == 'accepted')
        refuted  = sum(1 for r in self.debates.values() if r.status == 'refuted')
        open_    = sum(1 for r in self.debates.values() if r.status == 'open')
        lb = self.leaderboard()[:3]
        lb_str = "  ".join(f"{a}:{p}cr" for a,p in lb)
        return (f"Debates: {total} total  "
                f"({accepted} accepted, {refuted} refuted, {open_} open)\n"
                f"  Top debaters: {lb_str}")