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
from ms_concepts import *
from ms_environment import *

@dataclass
class MarketListing:
    prog:         Program
    listed_by:    str
    listed_at_run: int
    listed_at_round: int


class KnowledgeMarket:
    def __init__(self):
        self.listings: dict[str, MarketListing] = {}
        self.trades: list[dict] = []

    def publish(self, prog: Program, agent_name: str,
                agent_credits: int, run_id: int, round_num: int) -> tuple[bool, int]:
        if prog.name in self.listings:
            return False, agent_credits
        self.listings[prog.name] = MarketListing(prog, agent_name, run_id, round_num)
        msg = (f"  {magenta('📡 MARKET')} {bold(agent_name)} published "
               f"{cyan(prog.name)} [{prog.to_str()[:40]}] "
               f"[cost {PUBLISH_COST}cr]")
        print(msg)
        return True, agent_credits - PUBLISH_COST

    def available_for(self, known: list[str]) -> list[MarketListing]:
        return [l for n, l in self.listings.items() if n not in known]

    def acquire(self, name: str, buyer: str, buyer_credits: int,
                agents: dict, round_num: int) -> tuple[bool, int]:
        if name not in self.listings or buyer_credits < ACQUIRE_COST:
            return False, buyer_credits
        l = self.listings[name]
        buyer_credits -= ACQUIRE_COST
        if l.listed_by in agents:
            agents[l.listed_by].credits += ROYALTY_FEE
        self.trades.append({"round": round_num, "buyer": buyer,
                             "name": name, "seller": l.listed_by})
        print(f"  {magenta('💰 TRADE')}  {bold(buyer)} bought "
              f"{cyan(name)} from {l.listed_by}")
        return True, buyer_credits


# ══════════════════════════════════════════════════════════════════
# AGENT
# ══════════════════════════════════════════════════════════════════

AGENT_NAMES = ["Euclid","Gauss","Ramanujan","Euler","Noether",
               "Turing","Lovelace","Hilbert","Cantor","Fermat"]


@dataclass
class SolutionRecord:
    task_kind: str; energy_used: int; method: str
    correct: bool;  round_number: int


class Agent:
    def __init__(self, name: str, energy_budget: int, pop_size: int,
                 starting_credits: int = 20):
        self.name          = name
        self.energy_budget = energy_budget
        self.credits       = starting_credits
        self.library       = ProgramLibrary()
        self.history: list[SolutionRecord] = []
        self.total_tasks   = 0
        self.total_correct = 0
        self.kaizen_rounds = 0
        self.engine        = MutationEngine(population_size=pop_size,
                                            energy_budget=energy_budget)
        self._best_prog_cache: dict[str, Program] = {}  # kind → best prog
        self.concepts: "ConceptRegistry | None" = None  # wired by run()
        self.current_round: int = 0  # updated each round by run()
        # Anti-stagnation
        self._stagnant_rounds: dict[str, int] = {}   # task_kind → rounds stuck
        self._last_correct: dict[str, int]    = {}   # task_kind → last correct round
        self._exploration_boost: float = 0.0         # extra mutation pressure when stuck
        # Survival pressure
        self._wrong_streak: dict[str, int] = {}      # task_kind → consecutive wrong count
        # Mismatch detector: tracks when a *known* abstraction gives wrong answers
        # When this fires, agent should prefer algebra suggestions over random mutation
        self._concept_mismatch: dict[str, int] = {}  # task_kind → rounds concept failed
        self._conj_candidates: dict[str, list[tuple[Program, int, int]]] = {}
        self._sequence_analysis: dict[str, dict] = {}  # kind → meta-pattern analysis
        self._observed_tasks: list[tuple] = []          # (a, b, expected, kind) log
        # ── Mortality & Legacy ────────────────────────────────────────────
        self.generation:   int   = 1          # which generation of this lineage
        self.lifespan:     int   = random.randint(40, 55)  # rounds until death
        self.born_round:   int   = 0          # set by run() at spawn time
        self.parent_name:  str   = ""         # who spawned this agent
        self.legacy_score: float = 0.0        # credits earned through concept reuse
        self.discoveries:  int   = 0          # count of original concepts registered

    # ── Mortality & Lineage ───────────────────────────────────────────────

    @property
    def age(self) -> int:
        return max(0, self.current_round - self.born_round)

    @property
    def is_dying(self) -> bool:
        return self.age >= self.lifespan

    def spawn_offspring(self, child_name: str, round_num: int) -> "Agent":
        """
        Create a new agent that inherits the best of this agent's knowledge.
        The child starts with:
        - A random subset of the parent's top programs (knowledge inheritance)
        - A fraction of the parent's credits (inheritance tax applied)
        - A fresh lifespan (new generation)
        - The parent's curiosity bias baked in via library seeding
        """
        child = Agent(child_name, self.energy_budget, self.engine.pop_size,
                      starting_credits=max(20, self.credits // 3))
        child.generation  = self.generation + 1
        child.parent_name = self.name
        child.born_round  = round_num
        child.lifespan    = random.randint(40, 55)
        child.concepts    = self.concepts

        # Inherit best programs — sorted by fitness, keep top 60%
        own = sorted([p for p in self.library.all() if p.created_by == self.name],
                     key=lambda p: -p.fitness)
        inherit_n = max(2, int(len(own) * 0.6))
        for prog in own[:inherit_n]:
            inherited = prog.clone()
            # Keep original created_by so legacy credits flow back to the
            # actual discoverer — don't reassign ownership on inheritance
            child.library.add(inherited)
            for tag in inherited.concept_tags:
                if tag not in child._best_prog_cache:
                    child._best_prog_cache[tag] = inherited

        return child

    def _classify_sequence(self, task: "Task") -> dict:
        """
        Analyse the target sequence to determine its mathematical family.
        Uses finite differences on the EXPECTED values from task history —
        what we've been asked to compute, not what we currently know.
        """
        kind = task.kind

        # ── Build sequence from task history ─────────────────────────────
        # Collect (b, expected) pairs from our observation log.
        # We accept any a value — for b-only formulas a doesn't matter,
        # and we can still detect the polynomial degree from the b-sequence.
        pts: dict[int, int] = {}
        if hasattr(self, '_observed_tasks'):
            for (a_, b_, exp_, k_) in self._observed_tasks:
                if k_ == kind and b_ not in pts:
                    pts[b_] = exp_  # first observed value for this b

        # Include the current task
        if task.b not in pts:
            pts[task.b] = task.expected

        # Sort by b to get a proper sequence
        samples = [pts[b] for b in sorted(pts.keys())]

        # ── Fallback: evaluate cached program ────────────────────────────
        if len(samples) < 4:
            cached = self._best_prog_cache.get(kind)
            if cached:
                for b in range(1, 11):
                    try:
                        v = cached.root.eval({"a": task.a, "b": b}, [0])
                        if isinstance(v, int) and abs(v) < 10_000_000:
                            if b not in pts:
                                samples.append(v)
                    except Exception:
                        pass

        if len(samples) < 3:
            return {"family": "unknown", "degree": -1, "delta_k": None, "ratio": None,
                    "insight": "Need more observations — keep exploring"}

        # ── Only use consecutive b values for difference analysis ─────────
        # Sort by b and filter to the longest consecutive run
        b_vals = sorted(pts.keys())
        # Find consecutive runs
        best_run = []
        cur_run  = [b_vals[0]]
        for i in range(1, len(b_vals)):
            if b_vals[i] == b_vals[i-1] + 1:
                cur_run.append(b_vals[i])
            else:
                if len(cur_run) > len(best_run):
                    best_run = cur_run
                cur_run = [b_vals[i]]
        if len(cur_run) > len(best_run):
            best_run = cur_run

        if len(best_run) >= 3:
            samples = [pts[b] for b in best_run[:8]]
        else:
            # No consecutive run — fall back to ratio test on whatever we have
            samples = [pts[b] for b in b_vals[:8]]

        # ── Early exponential check for sparse sequences ─────────────────
        # Even with only 3 points, constant ratio is strong evidence.
        # Do this before difference analysis which needs consecutive values.
        all_vals = [pts[b] for b in b_vals if pts[b] != 0]
        if len(all_vals) >= 3:
            try:
                ratios = [all_vals[i+1]/all_vals[i] for i in range(len(all_vals)-1)]
                if ratios and max(ratios) - min(ratios) < 0.08:
                    r = sum(ratios)/len(ratios)
                    if r > 1.1:  # actual growth, not near-constant
                        return {"family": "exponential", "degree": -1,
                                "delta_k": None, "ratio": r,
                                "insight": f"Exponential (ratio≈{r:.2f}) — look for k**b forms"}
            except Exception:
                pass

        # ── Finite difference analysis ────────────────────────────────────
        samples = sorted(samples)[:8]
        diffs = [samples]
        for _ in range(4):
            prev = diffs[-1]
            if len(prev) < 2:
                break
            diffs.append([prev[i+1] - prev[i] for i in range(len(prev)-1)])

        def is_constant(lst):
            return len(lst) >= 2 and max(abs(x - lst[0]) for x in lst) == 0

        for degree, diff_seq in enumerate(diffs):
            if is_constant(diff_seq):
                k = diff_seq[0]
                labels = ["constant", "linear", "quadratic", "cubic", "quartic"]
                insights = [
                    f"Constant — output is always {k}",
                    f"Linear (Δ={k}) — look for a*b + c forms",
                    f"Quadratic (Δ²={k}) — closed form involves b*(b+1)/2",
                    f"Cubic (Δ³={k}) — closed form involves b*(b+1)*(2b+1)/6",
                    f"Quartic (Δ⁴={k}) — closed form involves [b*(b+1)/2]²",
                ]
                return {"family": labels[degree], "degree": degree,
                        "delta_k": k, "ratio": None,
                        "insight": insights[degree] if degree < len(insights) else f"degree-{degree} polynomial"}

        # ── Exponential check ─────────────────────────────────────────────
        try:
            nonzero = [s for s in samples if s != 0]
            if len(nonzero) >= 4:
                ratios = [nonzero[i+1]/nonzero[i] for i in range(len(nonzero)-1)]
                if max(ratios) - min(ratios) < 0.05:
                    r = sum(ratios)/len(ratios)
                    return {"family": "exponential", "degree": -1, "delta_k": None, "ratio": r,
                            "insight": f"Exponential (ratio≈{r:.2f}) — look for k**b forms"}
        except Exception:
            pass

        return {"family": "unknown", "degree": -1, "delta_k": None, "ratio": None,
                "insight": "Complex pattern — no simple family detected"}
        """
        Analyse the target sequence to determine its mathematical family.
        Uses finite differences — the same technique mathematicians use to
        identify polynomial degree.

        Builds the sequence by evaluating any known program across b=1..10
        at a=1. If no program is known, uses the single observed (a,b,expected)
        triple to make a limited inference.
        """
        samples = []

        # First try: use any cached program for this kind across b values
        cached = self._best_prog_cache.get(task.kind)
        if cached:
            for b in range(1, 11):
                try:
                    v = cached.root.eval({"a": 1, "b": b}, [0])
                    if isinstance(v, int) and abs(v) < 10_000_000:
                        samples.append(v)
                except Exception:
                    pass

        # Second try: reconstruct from task history (a, b, expected pairs)
        if len(samples) < 5:
            kind = task.kind
            # Gather all (b, expected) pairs seen for this kind at a=1
            history_pts = {}
            for rec in self.history:
                if rec.task_kind == kind and hasattr(rec, '_task_ref'):
                    pass  # SolutionRecord doesn't store full task
            # We don't store full task data in SolutionRecord, so we can't
            # reconstruct. Fall back to third try below.
            pass

        # Third try: use the single known (a, b, expected) value to infer pattern
        # We can compute nearby b-values from the task's correct answer using
        # the relationship between task kinds and their known formulas
        if len(samples) < 5:
            # Use the one ground-truth point we do have — task.expected at (task.a, task.b)
            # Can't build a full sequence, but we can signal "unknown" gracefully
            return {"family": "unknown", "degree": -1, "delta_k": None, "ratio": None,
                    "insight": "Only one data point available — keep exploring"}

        # Compute finite differences up to degree 4
        diffs = [samples[:8]]  # use first 8 points
        for _ in range(4):
            prev = diffs[-1]
            if len(prev) < 2:
                break
            d = [prev[i+1] - prev[i] for i in range(len(prev)-1)]
            diffs.append(d)

        def is_constant(lst, tol=0):
            return len(lst) >= 3 and max(abs(x - lst[0]) for x in lst) <= tol

        for degree, diff_seq in enumerate(diffs):
            if is_constant(diff_seq):
                k = diff_seq[0]
                if degree == 0:
                    return {"family": "constant", "degree": 0, "delta_k": k, "ratio": None,
                            "insight": f"Constant sequence — output is always {k}"}
                if degree == 1:
                    return {"family": "linear", "degree": 1, "delta_k": k, "ratio": None,
                            "insight": f"Linear (arithmetic) sequence — Δ={k}. Look for a*b + c forms."}
                if degree == 2:
                    return {"family": "quadratic", "degree": 2, "delta_k": k, "ratio": None,
                            "insight": f"Quadratic sequence (Δ²={k}). Closed form involves b*(b+1)/2."}
                if degree == 3:
                    return {"family": "cubic", "degree": 3, "delta_k": k, "ratio": None,
                            "insight": f"Cubic sequence (Δ³={k}). Closed form involves b*(b+1)*(2b+1)/6."}
                if degree == 4:
                    return {"family": "quartic", "degree": 4, "delta_k": k, "ratio": None,
                            "insight": f"Quartic sequence (Δ⁴={k}). Look for [b*(b+1)/2]² structure."}

        # Check exponential
        if len(samples) >= 4 and samples[0] not in (0, None):
            try:
                ratios = [samples[i+1] / samples[i] for i in range(4) if samples[i] != 0]
                if ratios and max(ratios) - min(ratios) < 0.05:
                    r = sum(ratios) / len(ratios)
                    return {"family": "exponential", "degree": -1, "delta_k": None, "ratio": r,
                            "insight": f"Exponential sequence (ratio≈{r:.2f}). Look for k**b forms."}
            except Exception:
                pass

        return {"family": "unknown", "degree": -1, "delta_k": None, "ratio": None,
                "insight": "Complex sequence — no simple polynomial or exponential pattern."}

    def _announce_insight(self, task: "Task", analysis: dict,
                          rounds_stuck: int) -> None:
        """Print a meta-pattern insight when stuck — agents reason about WHY,
        and see known examples from the same mathematical family."""
        if analysis["family"] == "unknown":
            return

        family = analysis["family"]
        insight = analysis["insight"]

        # Find known siblings — other concepts in the same growth family
        siblings: list[str] = []
        if self.concepts is not None and hasattr(self.concepts, "_families"):
            # Map our family label to the registry's family label
            registry_family = family
            if family == "exponential":
                # Look for any exponential_r* family
                for fam_key in self.concepts._families:
                    if fam_key.startswith("exponential"):
                        siblings = self.concepts.siblings_in_family(fam_key)[:4]
                        break
            else:
                siblings = self.concepts.siblings_in_family(family)[:4]

        sibling_str = ""
        if siblings:
            sibling_str = f"  known: {dim(', '.join(siblings[:3]))}"

        print(f"  {cyan('🔭 INSIGHT')}  {bold(self.name)} analysed "
              f"{cyan(task.kind)} after {rounds_stuck}r stuck:  "
              f"{bold(family.upper())} sequence  — "
              f"{dim(insight)}{sibling_str}")

    def solve(self, task: Task) -> tuple[int, int, str]:
        """
        1. Check if we have a trusted program for this task kind.
        2. If not (or it fails), run the mutation engine.
        3. Execute best candidate and return result.
        """
        # Log task for sequence analysis — lets us reconstruct the target
        # sequence even before we have a working program for it
        self._observed_tasks.append((task.a, task.b, task.expected, task.kind))
        if len(self._observed_tasks) > 300:
            self._observed_tasks = self._observed_tasks[-300:]
        # Try cached best program for this task kind
        if task.kind in self._best_prog_cache:
            prog = self._best_prog_cache[task.kind]
            b = [0]
            try:
                result = prog.run(task.env(), b)
                prog.usage_count += 1
                # HARD GATE: reject cache if it busts the energy budget
                if b[0] <= self.energy_budget:
                    return result, b[0], prog.name
                # Cache is over-budget — evict it and search again
                del self._best_prog_cache[task.kind]
                # Mismatch: cached abstraction gave wrong answer
                self._concept_mismatch[task.kind] =                     self._concept_mismatch.get(task.kind, 0) + 1
            except (EnergyExceeded, ExecError, RecursionError, ZeroDivisionError):
                del self._best_prog_cache[task.kind]
                self._concept_mismatch[task.kind] =                     self._concept_mismatch.get(task.kind, 0) + 1

        # ── Stagnation detection ─────────────────────────────────────────────
        # Only track stagnation for task kinds that are actively being served.
        # Extinct task kinds (e.g. 'add' after round 6) should not accumulate
        # stagnation — the agent solved them, they just don't appear anymore.
        kind = task.kind
        last_ok = self._last_correct.get(kind, self.current_round)
        rounds_stuck = self.current_round - last_ok
        self._stagnant_rounds[kind] = rounds_stuck

        if rounds_stuck >= 3:
            self._exploration_boost = min(3.0, rounds_stuck * 0.4)
            if rounds_stuck % 4 == 0:
                print(f"  {red('🔄 STUCK')}   {bold(self.name)} "
                      f"on {cyan(kind)} for {rounds_stuck} rounds — "
                      f"forcing exploration (boost={self._exploration_boost:.1f})")
                # On first STUCK announcement: classify the target sequence
                # to give the agent a strategic hint about what family of
                # formula to search for. This is the Layer 3 insight —
                # "what kind of thing am I looking for?" before "how do I find it?"
                if kind not in self._sequence_analysis:
                    analysis = self._classify_sequence(task)
                    self._sequence_analysis[kind] = analysis
                    self._announce_insight(task, analysis, rounds_stuck)
            self._best_prog_cache.pop(kind, None)
        else:
            self._exploration_boost = 0.0

        # Mismatch detector: concept failed on this task kind
        mismatch = self._concept_mismatch.get(kind, 0)
        if mismatch == 3:   # announce on third failure
            print(f"  {magenta('⚡ MISMATCH')}  {bold(self.name)} "
                  f"concept for {cyan(kind)} failed {mismatch}x — "
                  f"activating concept algebra")

        # ── Mutation search ───────────────────────────────────────────────────
        seq_analysis = self._sequence_analysis.get(kind)
        candidates = self.engine.evolve(
            task, self.library, self.energy_budget,
            exploration_boost=self._exploration_boost,
            concepts=self.concepts,
            seq_analysis=seq_analysis,
        )
        if not candidates:
            return 0, self.energy_budget + 1, "failed"

        best = candidates[0]
        b = [0]
        try:
            result = best.run(task.env(), b)
        except (EnergyExceeded, ExecError, RecursionError, ZeroDivisionError):
            return 0, b[0], "exec_error"

        # Abstract: if this program is correct and efficient, name and store it
        if result == task.expected and b[0] <= self.energy_budget:
            if task.kind.startswith("free_"):
                # For conjecture tasks, require the same program to succeed on
                # 3 distinct input pairs before we trust it as a real abstraction.
                kind = task.kind
                buf = self._conj_candidates.setdefault(kind, [])
                # Check if this program (by canonical form) has been seen before
                try:
                    sig = canonicalize(best.root).to_str()
                except Exception:
                    sig = best.root.to_str()
                matching = [(p, a_, b_) for p, a_, b_ in buf
                            if (canonicalize(p.root).to_str()
                                if True else p.to_str()) == sig
                            or p.to_str() == best.to_str()]
                buf.append((best, task.a, task.b))
                # Keep buffer small — only last 20 entries
                if len(buf) > 20:
                    self._conj_candidates[kind] = buf[-20:]
                # Count distinct (a,b) pairs where this canonical form succeeded
                try:
                    pairs = {(task.a, task.b)} | {(a_, b_) for p, a_, b_ in matching}
                except Exception:
                    pairs = {(task.a, task.b)}
                if len(pairs) >= 3:
                    self._maybe_abstract(best, task, b[0], self.concepts)
                    self._conj_candidates.pop(kind, None)  # reset after promotion
            else:
                self._maybe_abstract(best, task, b[0], self.concepts)

        return result, b[0], best.name

    def _maybe_abstract(self, prog: Program, task: Task, energy: int,
                        concepts: "ConceptRegistry | None" = None):
        """
        Promote a good program to a named abstraction.
        Uses canonical form to avoid storing duplicates of the same idea.
        """
        kind = task.kind
        existing = self._best_prog_cache.get(kind)

        # For agent-generated conjecture tasks (free_*), use the proposer's
        # formula fingerprint as the generalisation test rather than held-out
        # inputs. Any program that consistently produces the right answers
        # across the random (a,b) pairs the environment generates is good enough.
        is_conjecture = kind.startswith("free_")

        # Generalisation gate — threshold scales with task difficulty
        gen = generalisation_score(prog, kind)
        gen_threshold = {
            "add":          0.5,
            "repeated_add": 0.5,
            "power":        0.5,   # raised — (a+b)² passes 0/4 tests, must fail
            "sum_range":    0.75,
            "partial_sum":  0.75,
            "sum_squares":  0.75,
            "sum_cubes":    0.75,
            "alt_sum":      0.5,
        }.get(kind, 0.1 if is_conjecture else 0.25)

        # Always enforce the threshold — even on first discovery.
        # Without this, a program with gen=0% can sneak in as the "first"
        # for a kind and then get cached and used incorrectly for many rounds.
        # Exception: conjecture tasks get a free pass since we can't test them.
        if gen < gen_threshold and not is_conjecture:
            return

        # Replace if:
        # 1. No existing program for this kind, OR
        # 2. New program generalises better than existing, OR
        # 3. Same/better generalisation and smaller size, OR
        # 4. Completely different canonical form
        existing_gen = generalisation_score(existing, kind) if existing else 0.0
        better_gen    = gen > existing_gen + 0.1   # meaningfully better generalisation
        same_gen_smaller = gen >= existing_gen - 0.05 and \
                           existing is not None and prog.size() < existing.size()
        try:
            new_canon = canonicalize(prog.root).to_str()
            old_canon = canonicalize(existing.root).to_str() if existing else None
        except Exception:
            new_canon = prog.to_str()
            old_canon = existing.to_str() if existing else None
        different_canon = (new_canon != old_canon)

        if existing is None or better_gen or same_gen_smaller or different_canon:
            named = prog.clone()
            if is_conjecture:
                # Conjecture solutions get a name including the hash so each
                # conjecture gets its own slot — doesn't stomp on prior work
                named.name = f"sol_{kind[-6:]}_{self.name[:3].lower()}"
            else:
                named.name = f"{kind[:4]}_{self.name[:3].lower()}"
            named.created_by = self.name
            named.concept_tags = [kind]
            is_new = self.library.add(named)
            self._best_prog_cache[kind] = named
            self._last_correct[kind] = self.current_round
            self._stagnant_rounds[kind] = 0
            if is_new:
                canon_disp = new_canon[:40]
                gen_str = f"gen={gen:.0%}"
                print(f"  {yellow('🔬 ABSTRACT')} {bold(self.name)} "
                      f"stored {cyan(named.name)} = {dim(canon_disp)}  {green(gen_str)}")
                self.discoveries += 1
                # Print proof for significant discoveries (not trivial variants)
                # Only for core math tasks and curiosity finds with real depth
                if (kind not in ("add", "repeated_add", "power") and
                        named.size() >= 7 and
                        self.concepts is not None and
                        random.random() < 0.4):   # sample 40% to avoid flooding
                    proof = generate_proof(named, self.name,
                                           self.concepts, self.current_round)
                    print(proof)
            # Register with global concept registry
            if concepts is not None:
                concepts.register(self.name, named, self.current_round)
                # Tag the concept with which task kind it solves —
                # crystallise_law needs this to find the right concept pairs
                canon_key = new_canon
                for c in concepts.all_concepts():
                    if c.program_node is not None:
                        try:
                            if canonicalize(c.program_node).to_str() == canon_key:
                                if not hasattr(c, "solves_kinds"):
                                    c.solves_kinds = set()
                                c.solves_kinds.add(kind)
                        except Exception:
                            pass
                # If we just registered a two-variable sum_range formula,
                # also register the b-only specialization (a=1 fixed).
                # This ensures the concept algebra's telescope transform
                # always has a b-only parent to work from for partial_sum.
                if kind == "sum_range" and _uses_var_a(named.root):
                    b_only_root = _sub_a_with_const(named.root.clone(), 1)
                    b_only = Program(
                        name=f"sum_b_{self.name[:3].lower()}",
                        root=b_only_root,
                        created_by=self.name,
                        concept_tags=["sum_range"]
                    )
                    b_only.fitness = named.fitness
                    concepts.register(self.name, b_only, self.current_round)

    @property
    def is_comfortable(self) -> bool:
        """
        True when the agent has no failures in the last 6 tasks and
        has mastered at least 4 distinct task kinds overall.
        Only considers task kinds seen recently — extinct kinds don't count.
        """
        if len(self.history) < 6:
            return False
        recent = self.history[-6:]
        all_correct = all(r.correct for r in recent)
        enough_kinds = len(self._last_correct) >= 4
        # Only check stagnation on kinds seen in the last 8 attempts
        recent_kinds = {r.task_kind for r in self.history[-24:]}
        active_stagnation = {k: v for k, v in self._stagnant_rounds.items()
                             if k in recent_kinds}
        no_stagnation = max(active_stagnation.values(), default=0) == 0
        return all_correct and enough_kinds and no_stagnation

    def contemplate(self, env: "Environment", concepts: "ConceptRegistry",
                    round_num: int,
                    control_engine: "Optional[Any]" = None) -> None:
        """
        Genuine curiosity: run the mutation engine with no assigned task.
        The agent experiments freely, keeping any program whose output
        pattern is structurally unlike anything in the concept registry.

        'Surprising' means: the output sequence f(1), f(2), ..., f(8)
        has a growth profile the agent hasn't seen before — not just a
        different formula for a known relationship.

        This is curiosity as emergent behaviour: the agent isn't told what
        to look for. It discovers by running, evaluating, and noticing.
        """
        if not self.library.all():
            return

        own_progs = [p for p in self.library.all() if p.created_by == self.name]

        # ── Step 1a: Pre-simulate known transforms (imagination phase) ───
        # Before running expensive mutations, mentally apply known transforms
        # from the CRDE to own programs and predict which would be novel.
        # This is the "Tesla step" — imagine before building.
        # Only run mutations in directions predicted to produce novel outputs.
        imagination_seeds: list[Program] = []
        if concepts and own_progs:
            known_fps = set()
            for c in concepts.all_concepts():
                if c.program_node:
                    try:
                        fp = tuple(c.program_node.eval({"a":1,"b":b},[0])
                                   for b in range(1,7))
                        known_fps.add(fp)
                    except Exception:
                        pass

            # Apply each known transform to each own program and evaluate novelty
            # Order transforms by agent's strategy weights — specialists try
            # their preferred transforms first
            strategy_weights = getattr(self, "_strategy_weights", {})

            def _transform_priority(t_name):
                mapping = {
                    "squared":  strategy_weights.get("power_laws", 0.5) + strategy_weights.get("factor_quadratic", 0.5),
                    "cubed":    strategy_weights.get("power_laws", 0.5),
                    "looped":   strategy_weights.get("expand_and_simplify", 0.5),
                    "shifted":  strategy_weights.get("try_telescoping", 0.5),
                    "doubled":  strategy_weights.get("use_symmetry", 0.5),
                }
                return mapping.get(t_name, 0.5)

            for prog in own_progs[:3]:
                node = prog.root.clone()
                all_imagined = [
                    ("squared",   Pow(node.clone(), Const(2))),
                    ("cubed",     Pow(node.clone(), Const(3))),
                    ("looped",    Loop(node.clone(), Const(random.randint(2,5)))),
                    ("shifted",   _sub_b_with(node.clone(), Add(Var("b"), Const(1)))),
                    ("doubled",   Mul(Const(2), node.clone())),
                ]
                # Sort by agent's strategy preference
                imagined = sorted(all_imagined, key=lambda x: -_transform_priority(x[0]))
                for t_name, t_root in imagined:
                    try:
                        fp = tuple(t_root.eval({"a":1,"b":b},[0])
                                   for b in range(1,7))
                        if fp not in known_fps and len(set(fp)) >= 3:
                            # This predicted transform looks novel — seed it
                            p = Program(f"imag_{t_name[:4]}_{random.randint(0,99):02d}",
                                        t_root, created_by=self.name)
                            imagination_seeds.append(p)
                            known_fps.add(fp)  # don't propose same direction twice
                    except Exception:
                        pass

        # ── Step 1: Generate a diverse population via free mutation ──────
        # Seed from the agent's existing programs + random trees
        candidates: list[Program] = []

        for prog in own_progs[:4]:
            for _ in range(3):
                c = self.engine.mutate(prog, self.library)
                c.name = f"exp_{random.randint(0,9999):04d}"
                candidates.append(c)

        # Also generate fully random programs — these escape the known territory
        for _ in range(8):
            root = self.engine._random_tree(depth=random.randint(2, 4))
            candidates.append(Program(f"rnd_{random.randint(0,9999):04d}", root,
                                       created_by=self.name))

        # Imagination seeds go first — highest priority candidates
        candidates = imagination_seeds + candidates

        # ── Curiosity target seeds ────────────────────────────────────────
        # If the curiosity engine has flagged open questions, add them as
        # explicit candidate programs. This is "directed research" — the agent
        # doesn't just stumble on T³, it actively tries to construct it.
        curiosity_targets = getattr(self, "_curiosity_targets", [])
        for target_expr in curiosity_targets[:2]:
            try:
                target_node = _parse_expr_stub(target_expr)
                curiosity_prog = Program(
                    f"cur_{self.name[:3].lower()}_{random.randint(0,99):02d}",
                    target_node,
                    created_by=self.name,
                )
                candidates.insert(0, curiosity_prog)  # highest priority
            except Exception:
                pass

        # ── Step 2: Build fingerprints of all known concepts ─────────────
        # A fingerprint is the output sequence f(b) for b=1..10 with a=1.
        # We use this to measure whether a new program is structurally novel.
        known_prints: list[list[int]] = []
        if concepts:
            for c in concepts.all_concepts():
                node = c.program_node if c.program_node else \
                       _parse_expr_stub(c.canonical)
                fp = []
                for b in range(1, 11):
                    try:
                        fp.append(node.eval({"a": 1, "b": b}, [0]))
                    except Exception:
                        fp = []
                        break
                if len(fp) == 10 and len(set(fp)) >= 3:
                    known_prints.append(fp)

        def fingerprint(node: "Node") -> list[int] | None:
            fp = []
            for b in range(1, 11):
                try:
                    v = node.eval({"a": 1, "b": b}, [0])
                    if not isinstance(v, int) or abs(v) > 10_000_000:
                        return None
                    fp.append(v)
                except Exception:
                    return None
            return fp if len(set(fp)) >= 3 else None  # reject constants

        def novelty_score(fp: list[int]) -> float:
            """
            How different is this fingerprint from all known ones?
            Uses normalised Euclidean distance to the nearest known concept.
            Higher = more surprising.
            """
            if not known_prints:
                return 1.0
            best = 0.0
            for kp in known_prints:
                mx_fp = max(abs(x) for x in fp) or 1
                mx_kp = max(abs(x) for x in kp) or 1
                dist = sum((a/mx_fp - b/mx_kp)**2
                           for a, b in zip(fp, kp)) ** 0.5
                best = max(best, dist)
            return best

        def compression_score(fp: list[int], prog: Program) -> float:
            """
            How much mathematical structure per unit of program size?
            A small program generating a rich (fast-growing, varied) sequence
            is more interesting than a large program generating near-constants.

            Score = log(output_range) / program_size
            Capped so huge outputs don't dominate.
            """
            vals = [abs(v) for v in fp if v != 0]
            if not vals:
                return 0.0
            output_range = max(vals) - min(vals) + 1
            # How much does the sequence grow / vary?
            richness = math.log(max(output_range, 2))
            size = max(prog.size(), 1)
            return richness / size

        def interest_score(fp: list[int], prog: Program) -> float:
            """Combined interestingness: novel shape AND compact encoding,
            boosted by semantic strategy alignment."""
            nov = novelty_score(fp)
            comp = compression_score(fp, prog)
            comp_norm = min(comp / 0.5, 2.0)
            base = nov * (0.6 + 0.4 * comp_norm)

            # Semantic control bonus: if this expression aligns with
            # strategies the agent has learned work well, boost its score
            if control_engine is not None:
                try:
                    sig = canonicalize(prog.root).to_str()
                    sem_bonus = control_engine.semantic_interest_bonus(
                        self.name, sig,
                        agent_concepts=getattr(self, '_strategy_weights', None)
                    )
                    base = base * (1.0 + sem_bonus)
                except Exception:
                    pass
            return base

        # ── Step 3: Score candidates by interest, keep the most remarkable ─
        surprising: list[tuple[float, Program, list[int]]] = []
        seen_sigs: set[str] = set()

        for cand in candidates:
            fp = fingerprint(cand.root)
            if fp is None:
                continue
            try:
                sig = canonicalize(cand.root).to_str()
            except Exception:
                sig = cand.root.to_str()
            if sig in seen_sigs:
                continue
            seen_sigs.add(sig)

            # Skip trivial single-node programs — Var('a'), Var('b'), constants.
            # These can win conjecture tasks by accident and pollute the registry.
            if isinstance(cand.root, (Const, Var)):
                continue

            # Skip if already a known concept
            if concepts and any(c.canonical == sig for c in concepts.all_concepts()):
                continue

            score = interest_score(fp, cand)
            if score > 0.25:
                surprising.append((score, cand, fp))

        if not surprising:
            return

        # Pick the most interesting candidate
        surprising.sort(key=lambda x: -x[0])
        int_score, best_cand, best_fp = surprising[0]
        nov_score = novelty_score(best_fp)

        # ── Step 4: Characterise what was found ──────────────────────────
        # Try to give it a meaningful description based on its growth profile
        diffs = [best_fp[i+1] - best_fp[i] for i in range(len(best_fp)-1)]
        diff2 = [diffs[i+1] - diffs[i] for i in range(len(diffs)-1)]

        if all(d == diffs[0] for d in diffs):
            shape = "linear"
        elif all(d == diff2[0] for d in diff2):
            shape = "quadratic"
        elif (best_fp[0] != 0 and
              all(best_fp[i] != 0 and best_fp[i+1] != 0 and
                  abs(best_fp[i+1] / best_fp[i] - best_fp[1] / best_fp[0]) < 0.15
                  for i in range(1, min(5, len(best_fp)-1)))):
            shape = "exponential-like"
        else:
            shape = "unknown"

        try:
            sig = canonicalize(best_cand.root).to_str()
        except Exception:
            sig = best_cand.root.to_str()

        # Use a hash of the formula so same formula from different agents
        # doesn't spawn two separate conjecture task kinds
        import hashlib
        sig_hash = hashlib.md5(sig.encode()).hexdigest()[:6]
        conjecture_kind = f"free_{sig_hash}"

        proposed = env.propose_conjecture(
            agent_name=self.name,
            kind=conjecture_kind,
            formula_node=best_cand.root,
            description=sig[:40],
            round_num=round_num
        )

        if proposed:
            seq_str = ", ".join(str(x) for x in best_fp[:6]) + "..."
            comp = compression_score(best_fp, best_cand)
            origin = "💡 imagined" if best_cand.name.startswith("imag_") else "🔀 mutated"
            print(f"  {cyan('💭 CURIOUS')}  {bold(self.name)} found something "
                  f"{shape} and new ({dim(origin)}): {dim(sig[:48])}")
            print(f"  {dim(f'           sequence: {seq_str}  (novelty={nov_score:.2f}  compression={comp:.2f})')}")

            # ── Absorb the discovery into the agent's own library ─────────
            # This is the critical step: the curiosity finding becomes a
            # named concept the agent can build on, trade, and compose —
            # exactly like a task-driven discovery. Without this the agent
            # notices the pattern but never crystallises it into knowledge.
            disc = best_cand.clone()
            disc.name        = f"free_{self.name[:3].lower()}_{round_num}"
            disc.created_by  = self.name
            disc.concept_tags = [conjecture_kind, shape]   # shape tag lets algebra find it
            disc.fitness     = 80.0 + nov_score * 20       # novelty earns fitness

            is_new = self.library.add(disc)
            if is_new:
                print(f"  {yellow('🔬 ABSTRACT')} {bold(self.name)} "
                      f"crystallised {cyan(disc.name)} = {dim(sig[:40])}  "
                      f"{green(f'[{shape}]')}")
                # Generate proof for the curiosity discovery
                if (self.concepts is not None and disc.size() >= 5 and
                        random.random() < 0.35):
                    proof = generate_proof(disc, self.name,
                                           self.concepts, round_num)
                    print(proof)

            # Register with the global concept registry so other agents'
            # algebra engines can see it and build on it.
            if concepts is not None:
                concepts.register(self.name, disc, round_num)
                self.discoveries += 1

            # If the shape is b-only and linear/quadratic, also try registering
            # it as a telescopeable parent for any future partial-sum-like task.
            if not _uses_var_a(disc.root) and shape in ("linear", "quadratic"):
                concepts.register(self.name, disc, round_num)

    def kaizen(self, market: KnowledgeMarket, round_num: int,
               agents: dict, trading_enabled: bool, run_id: int):
        if len(self.history) < 3:
            return
        recent = self.history[-6:]
        pain = sum(r.energy_used for r in recent
                   if r.energy_used > self.energy_budget * 0.7)
        if pain == 0:
            return
        self.kaizen_rounds += 1

        # Publish best programs to market
        if trading_enabled:
            for prog in self.library.all():
                if prog.fitness > 80 and prog.name not in market.listings:
                    ok, self.credits = market.publish(
                        prog, self.name, self.credits, run_id, round_num
                    )
                    break   # publish one per kaizen

            # Buy if we're struggling
            for listing in market.available_for(self.library.names()):
                if self.credits >= ACQUIRE_COST:
                    ok, self.credits = market.acquire(
                        listing.prog.name, self.name,
                        self.credits, agents, round_num
                    )
                    if ok:
                        acquired = listing.prog.clone()
                        self.library.add(acquired)
                        kind = (acquired.concept_tags[0]
                                if acquired.concept_tags else "unknown")
                        self._best_prog_cache[kind] = acquired
                        break


# ══════════════════════════════════════════════════════════════════
# DATABASE
# ══════════════════════════════════════════════════════════════════

DB_PATH    = Path("math_society.db")
EXPORT_DIR = Path("exports")
EXPORT_DIR.mkdir(exist_ok=True)

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT, finished_at TEXT,
    num_agents INTEGER, rounds INTEGER, budget INTEGER,
    pop_size INTEGER, trading INTEGER,
    total_tasks INTEGER DEFAULT 0, correct_tasks INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS agents (
    name TEXT PRIMARY KEY,
    credits INTEGER DEFAULT 20,
    total_tasks INTEGER DEFAULT 0,
    total_correct INTEGER DEFAULT 0,
    kaizen_rounds INTEGER DEFAULT 0,
    library_json TEXT DEFAULT '{}',
    first_run INTEGER, last_run INTEGER
);
CREATE TABLE IF NOT EXISTS discovered_programs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER, round_num INTEGER,
    agent TEXT, prog_name TEXT,
    expr TEXT, fitness REAL,
    task_kind TEXT, size INTEGER,
    concept_tags TEXT
);
CREATE TABLE IF NOT EXISTS market_listings (
    prog_name TEXT PRIMARY KEY,
    listed_by TEXT, expr TEXT,
    listed_at_run INTEGER, listed_at_round INTEGER
);
CREATE TABLE IF NOT EXISTS market_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER, round_num INTEGER,
    buyer TEXT, prog_name TEXT, seller TEXT
);
CREATE TABLE IF NOT EXISTS task_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER, round_num INTEGER,
    agent TEXT, task_desc TEXT, task_kind TEXT,
    answer INTEGER, expected INTEGER,
    correct INTEGER, energy_used INTEGER,
    budget INTEGER, over_budget INTEGER,
    method TEXT, reward INTEGER
);
CREATE TABLE IF NOT EXISTS leaderboard_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER, round_num INTEGER,
    agent TEXT, rank_pos INTEGER,
    credits INTEGER, correct INTEGER,
    tasks INTEGER, kaizen INTEGER,
    programs TEXT
);
"""


class DB:
    def __init__(self, path: Path):
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        self._migrate()
        self.run_id: int = 0

    def _migrate(self):
        """
        Safe schema migrations — adds columns that exist in the current
        schema but are missing from an older database (e.g. v3 -> v4).
        Runs automatically on every startup; safe to re-run.
        """

        needed = self._all_needed_columns()
        for table, col, defn in needed:
            # Always re-read PRAGMA so earlier ALTERs in the same loop are visible
            current_cols = {r[1] for r in
                            self.conn.execute(f"PRAGMA table_info({table})").fetchall()}
            if col not in current_cols:
                try:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {defn}")
                    self.conn.commit()
                    print(f"\033[2m  ↑  db migrated: added {table}.{col}\033[0m")
                except Exception:
                    pass  # already exists or immutable — safe to ignore

    def _all_needed_columns(self) -> list:
        """
        Master list of every column every table needs in v4.
        Safe to re-run — skipped if column already exists.
        Add new entries here whenever the schema gains a column.
        """
        return [
            # runs
            ("runs", "pop_size",           "INTEGER DEFAULT 0"),
            ("runs", "finished_at",         "TEXT"),
            ("runs", "rounds",              "INTEGER DEFAULT 0"),
            ("runs", "budget",              "INTEGER DEFAULT 0"),
            ("runs", "num_agents",          "INTEGER DEFAULT 0"),
            ("runs", "trading",             "INTEGER DEFAULT 0"),
            ("runs", "total_tasks",         "INTEGER DEFAULT 0"),
            ("runs", "correct_tasks",       "INTEGER DEFAULT 0"),
            # agents
            ("agents", "library_json",      "TEXT DEFAULT '{}'"),
            ("agents", "first_run",         "INTEGER DEFAULT 0"),
            ("agents", "last_run",          "INTEGER DEFAULT 0"),
            ("agents", "total_tasks",       "INTEGER DEFAULT 0"),
            ("agents", "total_correct",     "INTEGER DEFAULT 0"),
            ("agents", "kaizen_rounds",     "INTEGER DEFAULT 0"),
            ("agents", "credits",           "INTEGER DEFAULT 20"),
            # leaderboard_snapshots
            ("leaderboard_snapshots", "run_id",    "INTEGER"),
            ("leaderboard_snapshots", "round_num", "INTEGER"),
            ("leaderboard_snapshots", "agent",     "TEXT"),
            ("leaderboard_snapshots", "rank_pos",  "INTEGER"),
            ("leaderboard_snapshots", "credits",   "INTEGER"),
            ("leaderboard_snapshots", "correct",   "INTEGER"),
            ("leaderboard_snapshots", "tasks",     "INTEGER"),
            ("leaderboard_snapshots", "kaizen",    "INTEGER"),
            ("leaderboard_snapshots", "programs",  "TEXT"),
            # task_attempts
            ("task_attempts", "run_id",       "INTEGER"),
            ("task_attempts", "round_num",    "INTEGER"),
            ("task_attempts", "agent",        "TEXT"),
            ("task_attempts", "task_desc",    "TEXT"),
            ("task_attempts", "task_kind",    "TEXT"),
            ("task_attempts", "answer",       "INTEGER"),
            ("task_attempts", "expected",     "INTEGER"),
            ("task_attempts", "correct",      "INTEGER"),
            ("task_attempts", "energy_used",  "INTEGER"),
            ("task_attempts", "budget",       "INTEGER"),
            ("task_attempts", "over_budget",  "INTEGER"),
            ("task_attempts", "method",       "TEXT"),
            ("task_attempts", "reward",       "INTEGER"),
            # market
            ("market_listings", "prog_name",       "TEXT"),
            ("market_listings", "listed_by",       "TEXT"),
            ("market_listings", "expr",             "TEXT"),
            ("market_listings", "listed_at_run",   "INTEGER"),
            ("market_listings", "listed_at_round", "INTEGER"),
            ("market_trades",   "run_id",           "INTEGER"),
            ("market_trades",   "round_num",        "INTEGER"),
            ("market_trades",   "buyer",            "TEXT"),
            ("market_trades",   "prog_name",        "TEXT"),
            ("market_trades",   "seller",           "TEXT"),
            # discovered_programs
            ("discovered_programs", "run_id",       "INTEGER"),
            ("discovered_programs", "round_num",    "INTEGER"),
            ("discovered_programs", "agent",        "TEXT"),
            ("discovered_programs", "prog_name",    "TEXT"),
            ("discovered_programs", "expr",         "TEXT"),
            ("discovered_programs", "fitness",      "REAL"),
            ("discovered_programs", "task_kind",    "TEXT"),
            ("discovered_programs", "size",         "INTEGER"),
            ("discovered_programs", "concept_tags", "TEXT"),
        ]

    def start_run(self, num_agents, rounds, budget, pop_size, trading) -> int:
        cur = self.conn.execute(
            "INSERT INTO runs (started_at,num_agents,rounds,budget,pop_size,trading)"
            " VALUES (?,?,?,?,?,?)",
            (datetime.now().isoformat(), num_agents, rounds, budget, pop_size, int(trading))
        )
        self.conn.commit()
        self.run_id = cur.lastrowid
        return self.run_id

    def finish_run(self, total, correct):
        self.conn.execute(
            "UPDATE runs SET finished_at=?,total_tasks=?,correct_tasks=? WHERE id=?",
            (datetime.now().isoformat(), total, correct, self.run_id)
        )
        self.conn.commit()

    def load_agents(self, names: list[str]) -> dict:
        out = {}
        for n in names:
            r = self.conn.execute("SELECT * FROM agents WHERE name=?", (n,)).fetchone()
            if r: out[n] = dict(r)
        return out

    def upsert_agent(self, agent: Agent):
        self.conn.execute("""
            INSERT INTO agents (name,credits,total_tasks,total_correct,
                                kaizen_rounds,library_json,first_run,last_run)
            VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(name) DO UPDATE SET
                credits=excluded.credits, total_tasks=excluded.total_tasks,
                total_correct=excluded.total_correct,
                kaizen_rounds=excluded.kaizen_rounds,
                library_json=excluded.library_json,
                last_run=excluded.last_run
        """, (agent.name, agent.credits, agent.total_tasks, agent.total_correct,
              agent.kaizen_rounds, agent.library.to_json(),
              self.run_id, self.run_id))
        self.conn.commit()

    def log_program(self, run_id, round_num, agent: Agent, prog: Program, kind: str):
        self.conn.execute(
            "INSERT INTO discovered_programs"
            " (run_id,round_num,agent,prog_name,expr,fitness,task_kind,size,concept_tags)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (run_id, round_num, agent.name, prog.name, prog.to_str(),
             prog.fitness, kind, prog.size(), json.dumps(prog.concept_tags))
        )
        self.conn.commit()

    def log_task(self, run_id, round_num, agent_name, task: Task,
                 answer, correct, energy, budget, method, reward):
        self.conn.execute("""
            INSERT INTO task_attempts
            (run_id,round_num,agent,task_desc,task_kind,answer,expected,
             correct,energy_used,budget,over_budget,method,reward)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (run_id, round_num, agent_name, task.description, task.kind,
              answer, task.expected, int(correct), energy, budget,
              int(energy > budget), method, reward))
        if self.conn.total_changes % 40 == 0:
            self.conn.commit()

    def log_market_listing(self, prog: Program, agent: str, run_id, round_num):
        self.conn.execute("""
            INSERT OR IGNORE INTO market_listings
            (prog_name,listed_by,expr,listed_at_run,listed_at_round)
            VALUES (?,?,?,?,?)
        """, (prog.name, agent, prog.to_str(), run_id, round_num))
        self.conn.commit()

    def log_trade(self, run_id, round_num, buyer, name, seller):
        self.conn.execute(
            "INSERT INTO market_trades (run_id,round_num,buyer,prog_name,seller)"
            " VALUES (?,?,?,?,?)",
            (run_id, round_num, buyer, name, seller)
        )
        self.conn.commit()

    def snapshot_leaderboard(self, agents: list[Agent], run_id, round_num):
        sa = sorted(agents, key=lambda a: a.credits, reverse=True)
        for rank, agent in enumerate(sa, 1):
            self.conn.execute("""
                INSERT INTO leaderboard_snapshots
                (run_id,round_num,agent,rank_pos,credits,correct,tasks,kaizen,programs)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, (run_id, round_num, agent.name, rank, agent.credits,
                  agent.total_correct, agent.total_tasks, agent.kaizen_rounds,
                  json.dumps(agent.library.names())))
        self.conn.commit()

    def flush(self): self.conn.commit()
    def close(self): self.conn.commit(); self.conn.close()

    def query(self, sql, params=()):
        return self.conn.execute(sql, params).fetchall()


def export_run(db: DB, run_id: int):
    prefix = EXPORT_DIR / f"run_{run_id:04d}"
    tables = {
        "agents":    "SELECT * FROM agents WHERE last_run=?",
        "programs":  "SELECT * FROM discovered_programs WHERE run_id=? ORDER BY id",
        "tasks":     "SELECT * FROM task_attempts WHERE run_id=? ORDER BY id",
        "market":    "SELECT * FROM market_listings",
        "trades":    "SELECT * FROM market_trades WHERE run_id=?",
        "leaderboard": "SELECT * FROM leaderboard_snapshots WHERE run_id=? ORDER BY round_num,rank_pos",
    }
    for key, sql in tables.items():
        rows = [dict(r) for r in db.query(sql, (run_id,) if "?" in sql else ())]
        Path(f"{prefix}_{key}.json").write_text(json.dumps(rows, indent=2))

    run_info = dict(db.query("SELECT * FROM runs WHERE id=?", (run_id,))[0])
    Path(f"{prefix}_run.json").write_text(json.dumps(run_info, indent=2))

    print(f"\n  {green('📦 EXPORTED')} run {run_id} → {EXPORT_DIR}/run_{run_id:04d}_*.json")
    total_bytes = sum(f.stat().st_size for f in EXPORT_DIR.glob(f"run_{run_id:04d}_*.json"))
    print(f"     {total_bytes:,} bytes total")


# ══════════════════════════════════════════════════════════════════
# SCORE / REWARD
# ══════════════════════════════════════════════════════════════════

def compute_reward(correct: bool, energy: int, budget: int,
                   wrong_streak: int = 0) -> int:
    """
    Dual objective — must be BOTH correct AND efficient:
      Wrong:              -5 base, escalating by streak (survival pressure)
      Correct+over-budget:-10 (hard energy gate)
      Correct+in-budget:  +10 to +20 (efficiency bonus)
    """
    if not correct:
        streak_penalty = min(10, wrong_streak * 2)
        return -5 - streak_penalty
    if energy > budget: return -10
    ratio = min(1.0, budget / max(energy, 1))
    return 10 + max(0, int((ratio - 0.5) * 20))


# ══════════════════════════════════════════════════════════════════
# LEADERBOARD
# ══════════════════════════════════════════════════════════════════

def print_leaderboard(agents: list[Agent], round_num: int,
                      market: KnowledgeMarket, db: DB | None = None,
                      concepts: "ConceptRegistry | None" = None):
    sa = sorted(agents, key=lambda a: a.credits, reverse=True)
    w = 76
    print(f"\n{'═'*w}")
    print(bold(f"  🏆  LEADERBOARD — Round {round_num}".center(w)))
    print(f"{'═'*w}")
    print(f"  {'RNK':<4} {'AGENT':<12} {'CREDITS':>8} {'CORRECT':>9} "
          f"{'KAIZEN':>7}  BEST PROGRAMS")
    print(f"  {'─'*70}")
    for rank, agent in enumerate(sa, 1):
        acc  = (f"{agent.total_correct}/{agent.total_tasks}"
                if agent.total_tasks else "0/0")
        medal = ["🥇","🥈","🥉"][rank-1] if rank <= 3 else f" {rank}."
        own_progs = [p for p in agent.library.all()
                     if p.created_by in (agent.name, "concept_algebra", "concept_engine")]
        # Fall back to inherited programs for young agents with no discoveries yet
        display_progs = own_progs or sorted(agent.library.all(), key=lambda p: -p.fitness)
        progs = ", ".join(
            f"{p.name}={p.to_str()[:18]}" for p in display_progs[:2]
        ) or dim("searching…")
        cc = green if agent.credits >= 30 else (yellow if agent.credits >= 10 else red)
        # Only flag stuck on task kinds seen in the last 8 rounds
        recent_kinds = {r.task_kind for r in agent.history[-24:]}
        active_stuck = {k: v for k, v in agent._stagnant_rounds.items()
                        if k in recent_kinds and v >= 3}
        max_stuck = max(active_stuck.values(), default=0)
        stuck_kind = max(active_stuck, key=active_stuck.get) if active_stuck else ""
        stuck_str = red(f" ⚠ stuck={max_stuck}r on {stuck_kind}") if max_stuck >= 3 else ""
        # Show generation lineage and life remaining
        life_left  = agent.lifespan - agent.age
        life_color = red if life_left <= 6 else (yellow if life_left <= 12 else dim)
        gen_str    = dim(f"g{agent.generation}") if agent.generation > 1 else ""
        legacy_str = cyan(f" ✦{agent.legacy_score:.0f}") if agent.legacy_score > 0 else ""
        print(f"  {medal:<4} {agent.name:<12} {cc(str(agent.credits).rjust(7))}  "
              f"{acc:>9}  {str(agent.kaizen_rounds).rjust(6)}  "
              f"{cyan(progs)}{stuck_str}{legacy_str}  "
              f"{gen_str}{life_color(f'⏳{life_left}r')}")
    if market.listings:
        print(f"\n  {magenta('📡 MARKET:')} "
              + ", ".join(cyan(n) for n in market.listings))
    if concepts and concepts.all_concepts():
        print(f"\n  {bold('CONCEPTS:')} ({len(concepts.all_concepts())} discovered)")
        for c in sorted(concepts.all_concepts(), key=lambda x: -x.strength)[:6]:
            parent_str = dim(f"  ← {c.child_of}") if c.child_of else ""
            print(f"    {cyan(c.name):<22} canonical={dim(c.canonical[:32]):<34} "
                  f"str={c.strength:5.1f}  agents={len(c.members)}{parent_str}")
    print(f"{'═'*w}")
    if db:
        db.snapshot_leaderboard(agents, db.run_id, round_num)


# ══════════════════════════════════════════════════════════════════
# QUERY CLI
# ══════════════════════════════════════════════════════════════════

def run_query(target: str):
    if not DB_PATH.exists():
        print(red("No database found.")); return
    db = DB(DB_PATH)
    w = 76

    if target == "agents":
        rows = db.query("SELECT * FROM agents ORDER BY credits DESC")
        print(f"\n{'═'*w}"); print(bold("  AGENTS".center(w))); print(f"{'═'*w}")
        for r in rows:
            lib = json.loads(r["library_json"] or "{}")
            print(f"  {bold(r['name']):<14} credits={yellow(str(r['credits']).rjust(6))}  "
                  f"{r['total_correct']}/{r['total_tasks']}  "
                  f"kaizen={r['kaizen_rounds']}  "
                  f"programs={cyan(str(list(lib.keys())))}")
        print(f"{'═'*w}")

    elif target == "programs":
        rows = db.query("""
            SELECT agent, prog_name, expr, fitness, task_kind, size, run_id, round_num
            FROM discovered_programs
            ORDER BY fitness DESC LIMIT 30
        """)
        print(f"\n{'═'*w}"); print(bold("  TOP DISCOVERED PROGRAMS".center(w))); print(f"{'═'*w}")
        print(f"  {'AGENT':<11} {'NAME':<22} {'FIT':>6} {'SZ':>3} "
              f"{'KIND':<12} {'EXPRESSION'}")
        print(f"  {'─'*70}")
        for r in rows:
            print(f"  {r['agent']:<11} {cyan(r['prog_name']):<22} "
                  f"{r['fitness']:>6.1f} {r['size']:>3}  "
                  f"{r['task_kind']:<12} {dim(r['expr'][:35])}")
        print(f"{'═'*w}")

    elif target == "market":
        listings = db.query("SELECT * FROM market_listings")
        trades   = db.query("SELECT * FROM market_trades ORDER BY id DESC LIMIT 15")
        print(f"\n{'═'*w}"); print(bold("  KNOWLEDGE MARKET".center(w))); print(f"{'═'*w}")
        print(bold("  LISTINGS:"))
        for l in listings:
            print(f"  {cyan(l['prog_name']):<22} by {l['listed_by']:<12} "
                  f"run={l['listed_at_run']}  expr={dim(l['expr'][:40])}")
        if trades:
            print(bold("\n  TRADES:"))
            for t in trades:
                print(f"  run={t['run_id']} r{t['round_num']}  "
                      f"{bold(t['buyer'])} ← {cyan(t['prog_name'])} "
                      f"from {t['seller']}")
        print(f"{'═'*w}")

    elif target == "runs":
        rows = db.query("SELECT * FROM runs ORDER BY id DESC LIMIT 15")
        print(f"\n{'═'*w}"); print(bold("  RUNS".center(w))); print(f"{'═'*w}")
        for r in rows:
            pct = f"{100*r['correct_tasks']//max(r['total_tasks'],1)}%" if r['total_tasks'] else "?"
            print(f"  id={r['id']}  {r['started_at'][:19]}  "
                  f"rounds={r['rounds']}  agents={r['num_agents']}  "
                  f"budget={r['budget']}  pop={r['pop_size']}  "
                  f"acc={r['correct_tasks']}/{r['total_tasks']}({pct})")
        print(f"{'═'*w}")
    else:
        print(red(f"Unknown target. Use: agents | programs | market | runs"))
    db.close()


# ══════════════════════════════════════════════════════════════════
# MAIN SIMULATION
# ══════════════════════════════════════════════════════════════════

def run(num_agents=5, rounds=30, budget=40, tasks_per_round=3,
        trading_enabled=True, leaderboard_every=8, pop_size=10,
        seed=42, fresh=False):

    if fresh and DB_PATH.exists():
        DB_PATH.unlink()
        print(yellow("  🗑  Database wiped\n"))

    db = DB(DB_PATH)
    run_id = db.start_run(num_agents, rounds, budget, pop_size, trading_enabled)

    print(f"\n{'═'*76}")
    print(bold("  🧠  MATH SOCIETY v10  —  Concept Algebra Engine".center(76)))
    print(f"  run_id={run_id}  agents={num_agents}  rounds={rounds}  "
          f"budget={budget}  pop={pop_size}  trading={'on' if trading_enabled else 'off'}")
    print(f"  {dim('Fitness: correctness + generalisation - size_penalty. No hardcoding.')}")
    print(f"{'═'*76}\n")

    random.seed(seed)
    names    = random.sample(AGENT_NAMES, min(num_agents, len(AGENT_NAMES)))
    market   = KnowledgeMarket()
    concepts = ConceptRegistry()

    # Pre-register the b-only Gauss formula as a latent concept.
    # This guarantees the telescope transform always has a telescopeable
    # parent formula for partial_sum, regardless of which form agents
    # happen to discover first for sum_range.
    # This is NOT giving agents the answer — it just ensures the algebra
    # engine has the right structural template to apply to discovered formulas.
    _gauss_b_seed = Program(
        "gauss_b_seed",
        IDiv(Mul(Var("b"), Add(Var("b"), Const(1))), Const(2)),
        created_by="algebra_engine",
        concept_tags=["sum_range"]
    )
    _gauss_b_seed.fitness = 0.0   # no fitness — just a template
    concepts.register("algebra_engine", _gauss_b_seed, 0)

    saved   = db.load_agents(names)

    agents: list[Agent] = []
    for name in names:
        a = Agent(name, budget, pop_size,
                  starting_credits=random.randint(15, 25))
        a.concepts   = concepts
        a.born_round = 1   # founding generation born at round 1
        if name in saved:
            row = saved[name]
            a.credits       = row["credits"]
            a.total_tasks   = row["total_tasks"]
            a.total_correct = row["total_correct"]
            a.kaizen_rounds = row["kaizen_rounds"]
            a.library       = ProgramLibrary.from_json(row.get("library_json") or "{}")
            print(dim(f"  ↩  {name} restored: {a.credits}cr  "
                      f"programs={a.library.names()}"))
        agents.append(a)
    if saved: print()

    agents_by_name = {a.name: a for a in agents}
    env = Environment(seed=seed)
    g_total = g_correct = 0

    for rnd in range(1, rounds + 1):
        tasks = env.next_tasks(tasks_per_round)
        print(f"\n{blue(f'┌── Round {rnd:02d}/{rounds}')}  "
              f"[diff={env.round}]  {dim('─'*36)}")

        for agent in agents:
            if agent.credits <= 0:
                print(f"│  {dim(agent.name):15s}  {red('💀 bankrupt')}")
                continue

            agent.current_round = rnd
            for task in tasks:
                answer, energy, method = agent.solve(task)
                correct = (answer == task.expected)
                streak  = agent._wrong_streak.get(task.kind, 0)
                reward  = compute_reward(correct, energy, budget, streak)

                agent.credits     += reward
                agent.total_tasks += 1
                if correct: agent.total_correct += 1
                g_total += 1
                if correct: g_correct += 1

                agent.history.append(SolutionRecord(
                    task_kind=task.kind, energy_used=energy,
                    method=method, correct=correct, round_number=rnd
                ))
                # Update stagnation clock and wrong streak
                if correct and energy <= budget:
                    agent._last_correct[task.kind] = rnd
                    agent._stagnant_rounds[task.kind] = 0
                    agent._wrong_streak[task.kind] = 0
                    agent._concept_mismatch[task.kind] = 0
                else:
                    agent._wrong_streak[task.kind] =                         agent._wrong_streak.get(task.kind, 0) + 1
                db.log_task(run_id, rnd, agent.name, task, answer,
                            correct, energy, budget, method, reward)

                si = green("✅") if correct else red("❌")
                bi = red("🔴") if energy > budget else green("🟢")
                rs = green(f"+{reward}cr") if reward >= 0 else red(f"{reward}cr")
                print(f"│  {agent.name:<11}  {task.description:<18}  "
                      f"{si}{bi} e={str(energy).rjust(3)}/{budget}  "
                      f"method={cyan(method[:22]):<24} {rs}")

                # ── Legacy credit: reward the original discoverer ─────────
                # If this agent used a program originally discovered by
                # someone else, pay that person or their nearest living heir.
                if correct and method in agent.library._lib:
                    prog_used = agent.library._lib[method]
                    creator = prog_used.created_by
                    if creator and creator != agent.name:
                        if creator in agents_by_name:
                            # Creator is alive — direct credit
                            agents_by_name[creator].credits      += 1
                            agents_by_name[creator].legacy_score += 1
                        else:
                            # Creator is dead — credit their nearest heir:
                            # the agent whose parent_name matches the creator
                            for heir in agents:
                                if heir.name != agent.name and \
                                        heir.parent_name == creator:
                                    heir.credits      += 1
                                    heir.legacy_score += 1
                                    break

            agent.kaizen(market, rnd, agents_by_name, trading_enabled, run_id)

            # Curiosity: if the agent has mastered everything, let them wonder
            if agent.is_comfortable:
                agent.contemplate(env, concepts, rnd)

            # Log any new programs discovered this round
            for prog in agent.library.all():
                if prog.created_at == 0:
                    prog.created_at = rnd
                    db.log_program(run_id, rnd, agent, prog, prog.concept_tags[0]
                                   if prog.concept_tags else "unknown")

        print(f"└{'─'*58}")

        for agent in agents:
            db.upsert_agent(agent)

        # ── Mortality & Lineage ───────────────────────────────────────────
        # Agents who've reached their lifespan die and are replaced by
        # offspring that inherit their best work. This creates generational
        # pressure — agents must contribute while they're alive.
        for i, agent in enumerate(agents):
            if agent.is_dying and agent.credits > 0:
                # Print eulogy
                legacy_str = (f"legacy={agent.legacy_score:.0f}cr  "
                              f"discoveries={agent.discoveries}")
                lineage_str = (f"gen {agent.generation}" +
                               (f" ← {agent.parent_name}" if agent.parent_name else ""))
                print(f"\n  {magenta('💀 DEATH')}  {bold(agent.name)}  "
                      f"age={agent.age}r  {lineage_str}  "
                      f"credits={agent.credits}  {dim(legacy_str)}")

                # Pick a child name — avoid any name currently in use
                used_names = {a.name for a in agents}
                available = [n for n in AGENT_NAMES if n not in used_names]
                if not available:
                    # All names taken — use a numbered variant
                    available = [f"{agent.name[:4]}{agent.generation+1}"]
                child_name = available[0]

                child = agent.spawn_offspring(child_name, rnd)
                agents[i]                    = child
                agents_by_name[child_name]   = child
                agents_by_name.pop(agent.name, None)

                inherited = list(child._best_prog_cache.keys())
                print(f"  {green('🌱 BORN')}    {bold(child_name)}  "
                      f"gen {child.generation}  "
                      f"inherits {dim(str(inherited[:4]))}  "
                      f"starts with {child.credits}cr")

        # Concept unification — check if different programs mean the same thing
        concepts.unify_check(rnd)

        # Open-ended relationship discovery — search for transforms between
        # concept pairs. Runs every 5 rounds once enough concepts exist.
        if rnd >= 20 and rnd % 5 == 0:
            concepts.discover_relationships(rnd)

        # Family clustering — group concepts by growth signature.
        if rnd >= 20 and rnd % 8 == 0:
            concepts.cluster_families(rnd)

        # Law crystallisation — check if any (concept, task_kind) pair
        # satisfies a mathematical identity (e.g. Nicomachus).
        # Build solver map: task_kind → concept_name that solves it.
        if rnd >= 25 and rnd % 5 == 0:
            solver_map: dict[str, str] = {}
            for agent in agents:
                for kind, prog in agent._best_prog_cache.items():
                    if prog.created_by and kind not in solver_map:
                        try:
                            canon = canonicalize(prog.root).to_str()
                        except Exception:
                            canon = prog.root.to_str()
                        for c in concepts.all_concepts():
                            if c.canonical == canon:
                                solver_map[kind] = c.name
                                break
                        # If no concept match, add a synthetic entry so
                        # the law engine can still check behavioral identity
                        if kind not in solver_map:
                            solver_map[kind] = canon  # use canonical as key

            # Also check: does any concept behaviourally solve sum_cubes?
            # Even if not explicitly cached under that name.
            # Test: f(1,n) == (n*(n+1)//2)^2 for n=1..7
            if "sum_cubes" not in solver_map:
                for c in concepts.all_concepts():
                    if c.program_node is None:
                        continue
                    try:
                        cube_ok = all(
                            c.program_node.eval({"a":1,"b":b},[0])
                            == (b*(b+1)//2)**2
                            for b in range(1,8)
                        )
                        if cube_ok:
                            solver_map["sum_cubes"] = c.name
                            break
                    except Exception:
                        pass

            if solver_map:
                concepts.crystallise_laws(solver_map, rnd)

        # Retire a conjecture once enough agents have genuinely mastered it
        # (have a cached solution that works) OR it's been active too long
        if env._active_conjecture:
            ck = env._active_conjecture["kind"]
            # Count agents who have a cached program for this kind
            masters = sum(
                1 for a in agents
                if ck in a._best_prog_cache
                and a._last_correct.get(ck, 0) >= rnd - 3
            )
            rounds_active = rnd - env._active_conjecture.get("activated_round", rnd)
            # Retire if majority mastered OR it's been stuck for 12+ rounds
            if masters >= max(2, len(agents) // 2) or rounds_active >= 12:
                if masters > 0:
                    print(f"  {green('✨ CONJECTURE MASTERED')}  {bold(ck)} "
                          f"solved by {masters}/{len(agents)} agents — moving on")
                else:
                    print(f"  {dim('⏭  CONJECTURE EXPIRED')}  {bold(ck)} "
                          f"after {rounds_active} rounds — too hard, moving on")
                env.retire_conjecture(ck)

        if rnd % leaderboard_every == 0 or rnd == rounds:
            print_leaderboard(agents, rnd, market, db, concepts)

    db.flush()
    db.finish_run(g_total, g_correct)

    print(f"\n{'═'*76}")
    print(bold("  📊  FINAL REPORT".center(76)))
    print(f"{'═'*76}")
    print(f"  Run ID   : {run_id}")
    print(f"  Tasks    : {g_correct}/{g_total} correct "
          f"({100*g_correct//max(g_total,1)}%)")
    print(f"  DB size  : {DB_PATH.stat().st_size:,} bytes")
    print(f"  Task kinds seen: {cyan(str(env.seen_kinds()))}") 

    # Concept summary
    print(concepts.summary())

    # Print all discovered programs across agents — originals only, no acquired copies
    print(f"\n  {bold('DISCOVERED PROGRAMS:')}")
    all_progs: dict[str, tuple[str, Program]] = {}
    for agent in agents:
        for prog in agent.library.all():
            # Only show programs this agent genuinely created
            if prog.created_by not in (agent.name, "concept_algebra", "concept_engine"):
                continue
            # Deduplicate by canonical form — keep highest-fitness version
            try:
                key = canonicalize(prog.root).to_str()
            except Exception:
                key = prog.to_str()
            if key not in all_progs or prog.fitness > all_progs[key][1].fitness:
                all_progs[key] = (agent.name, prog)
    if all_progs:
        for key, (owner, prog) in sorted(all_progs.items(),
                                         key=lambda x: -x[1][1].fitness):
            print(f"  {cyan(prog.name):<26} fit={prog.fitness:5.1f}  "
                  f"size={prog.size():2d}  {dim(prog.to_str()[:50])}"
                  f"  {dim('by '+owner)}")
    else:
        print(f"  {dim('No abstractions promoted yet.')}")

    export_run(db, run_id)
    db.close()


# ══════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    p = argparse.ArgumentParser(
        description="Math Society v4 — Mutation Engine"
    )
    p.add_argument("--agents",     type=int, default=5)
    p.add_argument("--rounds",     type=int, default=30)
    p.add_argument("--budget",     type=int, default=40)
    p.add_argument("--tasks",      type=int, default=3)
    p.add_argument("--board",      type=int, default=8)
    p.add_argument("--pop",        type=int, default=10,
                   help="Mutation population size per task (default 10)")
    p.add_argument("--seed",       type=int, default=42)
    p.add_argument("--no-trading", action="store_true")
    p.add_argument("--fresh",      action="store_true")
    p.add_argument("--query",      metavar="TARGET",
                   help="agents | programs | market | runs")
    p.add_argument("--export",     metavar="RUN_ID", type=int)
    args = p.parse_args()

    if args.query:
        run_query(args.query)
    elif args.export:
        db = DB(DB_PATH); export_run(db, args.export); db.close()
    else:
        run(
            num_agents      = min(args.agents, len(AGENT_NAMES)),
            rounds          = args.rounds,
            budget          = args.budget,
            tasks_per_round = args.tasks,
            trading_enabled = not args.no_trading,
            leaderboard_every = args.board,
            pop_size        = args.pop,
            seed            = args.seed,
            fresh           = args.fresh,
        )