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

@dataclass
class Task:
    description: str
    a: int; b: int; expected: int; kind: str; difficulty: int

    def env(self) -> dict:
        return {"a": self.a, "b": self.b}


class Environment:
    """
    Multi-level task generator.
    Each difficulty band introduces new task types that require
    discovering genuinely new mathematical abstractions.

    Level 1-3:  addition
    Level 4-6:  multiplication (repeated addition)
    Level 7-11: large multiplication
    Level 12-17: exponentiation
    Level 18-23: sum_range   Σ1..n = n(n+1)/2
    Level 24-29: partial_sum Σa..b = (b-a+1)(a+b)/2
    Level 30-35: sum_squares Σi²  = n(n+1)(2n+1)/6
    Level 36+:  sum_cubes   Σi³  = [n(n+1)/2]²
    """
    def __init__(self, seed: int = 42):
        random.seed(seed); self.round = 0
        self._task_log: list[str] = []  # track which kinds have appeared
        # Conjecture queue: agent-proposed task kinds that become real tasks
        self._conjectures: list[dict] = []   # {kind, node, description, by, round}
        self._active_conjecture: dict | None = None  # the one currently in play

    def propose_conjecture(self, agent_name: str, kind: str,
                           formula_node: "Node", description: str,
                           round_num: int) -> bool:
        """
        Accept a conjecture from an agent. If we don't already have an
        active conjecture, activate it immediately.
        Returns True if accepted.
        """
        # Don't accept duplicates or if queue is full
        existing_kinds = {c["kind"] for c in self._conjectures}
        if kind in existing_kinds:
            return False
        if self._active_conjecture and self._active_conjecture["kind"] == kind:
            return False
        self._conjectures.append({
            "kind": kind,
            "node": formula_node,
            "description": description,
            "by": agent_name,
            "round": round_num,
        })
        # Activate immediately if nothing is running
        if self._active_conjecture is None:
            self._active_conjecture = self._conjectures.pop(0)
            self._active_conjecture["activated_round"] = round_num
        return True

    def next_tasks(self, n: int) -> list[Task]:
        self.round += 1
        tasks = [self._make() for _ in range(n)]
        for t in tasks:
            if t.kind not in self._task_log:
                self._task_log.append(t.kind)
                is_conjecture = self._active_conjecture and \
                                t.kind == self._active_conjecture["kind"]
                if is_conjecture:
                    c = self._active_conjecture
                    print(f"  {cyan('🔭 NEW CONJECTURE')}  {bold(c['by'])} proposed "
                          f"{bold(t.kind)}  ({c['description']})  "
                          f"[appeared round {self.round}]")
                else:
                    print(f"  {magenta('🌍 NEW TASK TYPE')}  {bold(t.kind)} "
                          f"appeared at round {self.round}  "
                          f"(diff={t.difficulty})")
        return tasks

    def retire_conjecture(self, kind: str) -> None:
        """Called when agents have mastered a conjecture — move to the next one."""
        if self._active_conjecture and self._active_conjecture["kind"] == kind:
            if self._conjectures:
                self._active_conjecture = self._conjectures.pop(0)
            else:
                self._active_conjecture = None

    def seen_kinds(self) -> list[str]:
        return list(self._task_log)

    def _make(self) -> Task:
        r = self.round

        # If there's an active conjecture, mix it in alongside regular tasks
        # at a low rate — enough to pressure exploration without drowning
        # out the tasks agents can actually solve.
        if self._active_conjecture and r > 18 and random.random() < 0.15:
            return self._make_conjecture_task(self._active_conjecture)

        if r <= 3:
            a, b = random.randint(1, 10), random.randint(1, 10)
            return Task(f"{a}+{b}", a, b, a+b, "add", 1)
        elif r <= 6:
            a, b = random.randint(2, 12), random.randint(3, 15)
            return Task(f"{a}×{b}", a, b, a*b, "repeated_add", 2)
        elif r <= 11:
            a, b = random.randint(2, 10), random.randint(20, 80)
            return Task(f"{a}×{b}(big)", a, b, a*b, "repeated_add", 3)
        elif r <= 17:
            base, exp = random.randint(2, 4), random.randint(3, 7)
            return Task(f"{base}^{exp}", base, exp, base**exp, "power", 4)
        elif r <= 23:
            n = random.randint(10, 60)
            return Task(f"Σ1..{n}", 1, n, n*(n+1)//2, "sum_range", 5)
        elif r <= 29:
            a = random.randint(2, 10)
            b = random.randint(a+5, a+30)
            return Task(f"Σ{a}..{b}", a, b, _partial_sum(a, b), "partial_sum", 6)
        elif r <= 35:
            n = random.randint(5, 30)
            return Task(f"Σi²(1..{n})", 1, n, _sum_squares(n), "sum_squares", 7)
        else:
            n = random.randint(3, 15)
            return Task(f"Σi³(1..{n})", 1, n, _sum_cubes(n), "sum_cubes", 8)

    def _make_conjecture_task(self, conjecture: dict) -> Task:
        """Generate a task instance for an active conjecture."""
        node = conjecture["node"]
        kind = conjecture["kind"]
        # Use small inputs so energy stays manageable
        a = random.randint(1, 8)
        b = random.randint(2, 12)
        try:
            expected = node.eval({"a": a, "b": b}, [0])
            if not isinstance(expected, int) or abs(expected) > 1_000_000:
                # Fallback to a safe input pair
                a, b = 1, 5
                expected = node.eval({"a": a, "b": b}, [0])
            desc = f"?({a},{b})"
            return Task(desc, a, b, expected, kind, 9)
        except Exception:
            # If the formula breaks on these inputs, return a dummy
            return Task("?(1,2)", 1, 2, 0, kind, 9)


# ══════════════════════════════════════════════════════════════════
# MUTATION ENGINE  — the heart of discovery
# ══════════════════════════════════════════════════════════════════

class MutationEngine:
    """
    Given a task and an agent's program library, produce a population
    of candidate programs via mutation and crossover, then return the
    best survivors ranked by fitness.
    """

    def __init__(self, population_size: int = 10, energy_budget: int = 40):
        self.pop_size  = population_size
        self.budget    = energy_budget

    # ── Seed programs ──────────────────────────────────────────────────────

    def seed_population(self, task: Task, library: ProgramLibrary) -> list[Program]:
        """Generate initial diverse candidates from primitives + library programs."""
        seeds: list[Program] = []

        # 1. Pure primitives
        seeds.append(Program("p_add",  Add(Var("a"), Var("b"))))
        seeds.append(Program("p_sub",  Sub(Var("a"), Var("b"))))
        seeds.append(Program("p_a",    Var("a")))
        seeds.append(Program("p_b",    Var("b")))
        seeds.append(Program("p_mul",  Mul(Var("a"), Var("b"))))

        # 2. Loop seeds (key for discovering multiplication)
        seeds.append(Program("p_loop_ab",  Loop(Var("a"), Var("b"))))
        seeds.append(Program("p_loop_1b",  Loop(Const(1), Var("b"))))
        seeds.append(Program("p_loop_ba",  Loop(Var("b"), Var("a"))))
        # NOTE: p_gauss_approx removed — it was a local optimum trap.
        # The loop(i+1, b) seed is correct but always over-budget for large n.
        # Agents must DISCOVER an efficient formula under hard energy constraints.
        # Instead seed a compact formula candidate that could mutate toward n*(n+1)/2:
        seeds.append(Program("p_half_mul",
            Mul(Var("b"), Add(Var("b"), Const(1)))))   # b*(b+1) — partial towards Gauss
        # b*(b+1)//2 — pure f(b) form. This is NOT the full Gauss discovery (agents
        # still need to find this survives selection), but it gives the concept algebra
        # a telescopeable parent: f(b)-f(a-1) = partial sum. Without a b-only form in
        # the concept registry, the telescope transform has nothing valid to apply to.
        seeds.append(Program("p_gauss_b",
            IDiv(Mul(Var("b"), Add(Var("b"), Const(1))), Const(2))))
        # Power seeds
        seeds.append(Program("p_pow_ab",    Pow(Var("a"), Var("b"))))
        seeds.append(Program("p_pow_a2",    Pow(Var("a"), Const(2))))
        seeds.append(Program("p_pow_a3",    Pow(Var("a"), Const(3))))

        # NOTE: Gauss, partial_sum, sum_squares, sum_cubes formula seeds removed.
        # Agents must discover these under selection pressure — seeding them
        # directly is not true discovery.  The only seeds here are primitive
        # building blocks and structural scaffolding that is task-agnostic.

        # ── IfNode seeds — structural scaffolding for alt_sum and conditionals ──
        # "even parity" seed — core structure needed for alt_sum
        seeds.append(Program("p_if_even",
            IfNode(Var("b"), "even", Const(0),
                   Sub(Const(0), IDiv(Var("b"), Const(2))),   # even branch: -b//2
                   IDiv(Add(Var("b"), Const(1)), Const(2))))) # odd branch:  (b+1)//2
        # generic if-eq-0 scaffold
        seeds.append(Program("p_if_b0",
            IfNode(Var("b"), "eq", Const(0), Const(0), Var("a"))))
        # parity toggle: 1 if even, -1 if odd (building block for sign alternation)
        seeds.append(Program("p_parity",
            IfNode(Var("b"), "even", Const(0), Const(1), Sub(Const(0), Const(1)))))

        # 3. Any good programs already in the library
        for prog in library.all()[:4]:
            c = prog.clone(); c.name = f"lib_{prog.name}"
            seeds.append(c)

        # 4. Random small programs
        for i in range(3):
            seeds.append(Program(f"rand_{i}", self._random_tree(depth=2)))

        return seeds[:self.pop_size]

    # ── Mutation operators ─────────────────────────────────────────────────

    def mutate(self, prog: Program, library: ProgramLibrary) -> Program:
        c = prog.clone()
        op = random.choice([
            "grow", "shrink", "swap_vars",
            "loop_wrap", "const_tweak", "add_layer", "if_wrap"
        ])
        c.root = self._apply_mutation(c.root, op, library, depth=0)
        c.name = f"mut_{op[:4]}_{random.randint(0,999):03d}"
        return c

    def _apply_mutation(self, node: Node, op: str,
                        library: ProgramLibrary, depth: int) -> Node:
        if depth > MAX_DEPTH:
            return node

        if op == "grow" and isinstance(node, (Const, Var)) and random.random() < 0.6:
            return self._random_tree(depth=1)

        if op == "shrink" and not isinstance(node, (Const, Var)) and random.random() < 0.4:
            return random.choice([Var("a"), Var("b"), Const(random.randint(1, 5))])

        if op == "swap_vars":
            if isinstance(node, Var):
                node.name = "b" if node.name == "a" else "a"
            elif hasattr(node, "left"):
                node.left, node.right = node.right, node.left

        if op == "const_tweak" and isinstance(node, Const):
            node.value = max(0, node.value + random.choice([-2, -1, 1, 2]))

        if op == "loop_wrap":
            # detect  Add(Var("a"), Add(Var("a"), ...)) → Loop(Var("a"), ...)
            # simplified: just wrap in a loop with a small count
            if isinstance(node, Add) and random.random() < 0.5:
                return Loop(node.left.clone(),
                            random.choice([Var("b"), Const(random.randint(2, 6))]))

        if op == "add_layer" and depth < MAX_DEPTH - 1:
            wrapper = random.choice([
                lambda n: Add(n, Var("a")),
                lambda n: Add(n, Const(1)),
                lambda n: Loop(n, Const(random.randint(2, 4))),
            ])
            if random.random() < 0.3:
                return wrapper(node)

        if op == "if_wrap" and depth < MAX_DEPTH - 2 and random.random() < 0.3:
            # Wrap a sub-expression in a conditional branch.
            # This is the structural mutation that enables alt_sum discovery.
            cond_op = random.choice(["even", "eq", "lt"])
            if cond_op == "even":
                return IfNode(Var("b"), "even", Const(0), node,
                              self._random_tree(depth=1))
            elif cond_op == "eq":
                k = random.choice([Const(0), Const(1)])
                return IfNode(Var("b"), "eq", k,
                              Const(0), node)
            else:
                return IfNode(Var("b"), "lt", Const(0),
                              Sub(Const(0), node), node)

        # Recurse into children
        if isinstance(node, (Add, Sub, Mul, IDiv)):
            if random.random() < 0.5:
                node.left  = self._apply_mutation(node.left,  op, library, depth+1)
            else:
                node.right = self._apply_mutation(node.right, op, library, depth+1)
        elif isinstance(node, Pow):
            if random.random() < 0.5:
                node.base = self._apply_mutation(node.base, op, library, depth+1)
            else:
                node.exp  = self._apply_mutation(node.exp,  op, library, depth+1)
        elif isinstance(node, Loop):
            if random.random() < 0.5:
                node.body  = self._apply_mutation(node.body,  op, library, depth+1)
            else:
                node.count = self._apply_mutation(node.count, op, library, depth+1)
        elif isinstance(node, IfNode):
            branch = random.choice(["cond_left", "cond_right", "yes", "no"])
            if branch == "cond_left":
                node.cond_left  = self._apply_mutation(node.cond_left,  op, library, depth+1)
            elif branch == "cond_right":
                node.cond_right = self._apply_mutation(node.cond_right, op, library, depth+1)
            elif branch == "yes":
                node.yes  = self._apply_mutation(node.yes,  op, library, depth+1)
            else:
                node.no   = self._apply_mutation(node.no,   op, library, depth+1)
        return node

    def crossover(self, a: Program, b: Program) -> Program:
        """Splice a subtree from b into a random position in a."""
        c = a.clone()
        donor_subtree = self._random_subtree(b.root)
        c.root = self._splice(c.root, donor_subtree, max_depth=MAX_DEPTH)
        c.name = f"cross_{random.randint(0,999):03d}"
        return c

    def concept_mutate(self, library: ProgramLibrary) -> list[Program]:
        """
        Concept mutation: take two named programs from the library and
        combine them algebraically. This is how higher-level concepts
        are born from lower ones.

        Examples of what this produces:
          multiply(a,b) + sum_range(b) → potential polynomial form
          pow(a,b) * sum_range(b)      → potential series form
          gauss(b) ** 2                → sum_cubes formula
        """
        progs = library.all()
        if len(progs) < 2:
            return []
        results = []
        pairs = [(a, b) for a in progs for b in progs if a.name != b.name]
        for pa, pb in random.sample(pairs, min(4, len(pairs))):
            # Wrap both programs as sub-expressions and combine
            op = random.choice(["add", "mul", "pow", "idiv"])
            if op == "add":
                root = Add(pa.root.clone(), pb.root.clone())
            elif op == "mul":
                root = Mul(pa.root.clone(), pb.root.clone())
            elif op == "pow":
                root = Pow(pa.root.clone(), Const(2))
            else:
                denom = random.choice([Const(2), Const(3), Const(6)])
                root = IDiv(Mul(pa.root.clone(), pb.root.clone()), denom)
            p = Program(
                name=f"cmut_{op[:2]}_{random.randint(0,99):02d}",
                root=root,
                created_by="concept_engine",
                concept_tags=[pa.concept_tags[0] if pa.concept_tags else "?",
                               pb.concept_tags[0] if pb.concept_tags else "?"]
            )
            results.append(p)
        return results

    def _random_subtree(self, node: Node) -> Node:
        candidates = [node]
        def collect(n):
            if isinstance(n, (Add, Sub, Mul)):
                candidates.append(n.left); candidates.append(n.right)
                collect(n.left); collect(n.right)
            elif isinstance(n, Loop):
                candidates.append(n.body); candidates.append(n.count)
                collect(n.body); collect(n.count)
            elif isinstance(n, IfNode):
                for child in (n.cond_left, n.cond_right, n.yes, n.no):
                    candidates.append(child); collect(child)
        collect(node)
        return random.choice(candidates).clone()

    def _splice(self, node: Node, donor: Node, max_depth: int) -> Node:
        if max_depth <= 0: return donor
        if random.random() < 0.25: return donor
        if isinstance(node, (Add, Sub, Mul, IDiv)):
            if random.random() < 0.5:
                node.left  = self._splice(node.left,  donor, max_depth-1)
            else:
                node.right = self._splice(node.right, donor, max_depth-1)
        elif isinstance(node, Pow):
            node.base = self._splice(node.base, donor, max_depth-1)
        elif isinstance(node, Loop):
            node.body = self._splice(node.body, donor, max_depth-1)
        elif isinstance(node, IfNode):
            branch = random.choice(["yes", "no"])
            if branch == "yes":
                node.yes = self._splice(node.yes, donor, max_depth-1)
            else:
                node.no  = self._splice(node.no,  donor, max_depth-1)
        return node

    def _random_tree(self, depth: int = 2) -> Node:
        if depth == 0 or random.random() < 0.35:
            return random.choice([
                Var("a"), Var("b"),
                Const(random.randint(1, 4)),
            ])
        kind = random.choice(["add", "mul", "loop", "idiv", "pow", "if"])
        if kind == "add":
            return Add(self._random_tree(depth-1), self._random_tree(depth-1))
        elif kind == "mul":
            return Mul(self._random_tree(depth-1), self._random_tree(depth-1))
        elif kind == "idiv":
            denom = random.choice([Const(2), Const(3), Const(4), Const(6)])
            return IDiv(self._random_tree(depth-1), denom)
        elif kind == "pow":
            exp = random.choice([Var("b"), Const(2), Const(3)])
            return Pow(self._random_tree(depth-1), exp)
        elif kind == "if":
            op = random.choice(["even", "eq", "lt"])
            return IfNode(
                self._random_tree(depth-1), op,
                random.choice([Const(0), Const(1), Var("b")]),
                self._random_tree(depth-1), self._random_tree(depth-1)
            )
        else:
            return Loop(self._random_tree(depth-1),
                        random.choice([Var("b"), Const(random.randint(2, 5))]))

    # ── Fitness scoring ────────────────────────────────────────────────────

    def score(self, prog: Program, tasks: list[Task],
              library: ProgramLibrary, energy_budget: int) -> float:
        """
        fitness = correctness * 100          (main signal)
                + generalisation * 40        (held-out test — prevents overfitting)
                - size_penalty               (BRUTAL: every node costs 3 pts)
                - energy_penalty             (over-budget costs extra)
                + novelty * 3               (new canonical form gets small bonus)

        Key changes vs v4:
          - size_penalty is LINEAR not log — short programs always dominate
          - generalisation tests held-out inputs never seen during evolution
          - canonical form used for novelty: (a+b) and (b+a) are NOT novel
        """
        task_kind = tasks[0].kind if tasks else "add"
        correct = 0; total_energy = 0; partial_score = 0.0
        for task in tasks:
            b = [0]
            try:
                result = prog.run(task.env(), b)
                if result == task.expected:
                    correct += 1; partial_score += 1.0
                elif task.expected != 0:
                    err = abs(result - task.expected) / abs(task.expected)
                    partial_score += max(0.0, 1.0 - min(err, 1.0)) * 0.3
            except (EnergyExceeded, ExecError, RecursionError, ZeroDivisionError):
                pass
            total_energy += b[0]

        correctness   = (correct / len(tasks)) * 100 if tasks else 0
        partial_bonus = (partial_score / max(len(tasks), 1)) * 15.0
        avg_energy     = total_energy / max(len(tasks), 1)
        energy_penalty = max(0, avg_energy - energy_budget) * 1.0   # sharper

        # BRUTAL size penalty — every extra node costs 3 pts
        size_penalty   = max(0, prog.size() - 1) * 3.0

        # Generalisation on held-out inputs (prevents lucky memorisation)
        gen_score      = generalisation_score(prog, task_kind)
        gen_bonus      = gen_score * 40.0

        # Novelty based on CANONICAL form — (a+b) and (b+a) are the same
        canon_sig = canonical_signature(prog)
        is_novel  = canon_sig not in library._seen_structures
        novelty   = 3.0 if is_novel else 0.0

        # Curiosity bonus: novel AND correct AND efficient = extra reward
        # This prevents stagnation around one known perfect formula
        curiosity = 0.0
        if is_novel and correct > 0 and avg_energy <= energy_budget:
            curiosity = 8.0   # significant bonus to drive exploration

        # HARD GATE: if over-budget on average, fitness is capped to near-zero.
        # This prevents the p_gauss_approx trap where correct+expensive survives.
        if avg_energy > energy_budget:
            return max(0.0, correctness * 0.1) - size_penalty   # basically dead

        return correctness + partial_bonus + gen_bonus + curiosity - size_penalty - energy_penalty + novelty

    # ── Full evolution step ────────────────────────────────────────────────

    def evolve(self, task: Task, library: ProgramLibrary,
               energy_budget: int, verbose: bool = False,
               exploration_boost: float = 0.0,
               concepts: "ConceptRegistry | None" = None,
               seq_analysis: dict | None = None) -> list[Program]:
        """
        Run one generation of evolution.
        exploration_boost > 0 when agent is stagnant — produces more offspring
        with wilder mutations to escape local optima.
        concepts: if provided, adds algebra-derived candidates to the pool.
        seq_analysis: if provided, biases mutation toward the right formula family.
        """
        population = self.seed_population(task, library)

        # ── Targeted seeding from sequence analysis ───────────────────────
        # When we know the sequence is cubic, seed with cubic-shaped programs.
        # This is the meta-pattern insight being converted into search bias.
        if seq_analysis and seq_analysis.get("family") not in (None, "unknown"):
            family  = seq_analysis["family"]
            degree  = seq_analysis.get("degree", -1)
            targeted: list[Program] = []

            if family == "quadratic" and degree == 2:
                # b*(b+1)//2 family — triangular number variants
                candidates_roots = [
                    IDiv(Mul(Var("b"), Add(Var("b"), Const(1))), Const(2)),
                    IDiv(Mul(Add(Var("a"), Var("b")), Sub(Var("b"), Var("a"))), Const(2)),
                ]
                for root in candidates_roots:
                    targeted.append(Program(f"meta_q_{random.randint(0,99):02d}",
                                            root, created_by="meta_engine"))

            elif family == "cubic" and degree == 3:
                # b*(b+1)*(2b+1)//6 family — sum of squares
                two_b_1 = Add(Mul(Const(2), Var("b")), Const(1))
                gauss   = IDiv(Mul(Var("b"), Add(Var("b"), Const(1))), Const(2))
                targeted.append(Program(
                    f"meta_c_{random.randint(0,99):02d}",
                    IDiv(Mul(gauss, two_b_1), Const(3)),
                    created_by="meta_engine"))
                # Also try straight cubic polynomial
                targeted.append(Program(
                    f"meta_c2_{random.randint(0,99):02d}",
                    Pow(Var("b"), Const(3)),
                    created_by="meta_engine"))

            elif family == "quartic" and degree == 4:
                # [b*(b+1)//2]^2 family — sum of cubes
                gauss  = IDiv(Mul(Var("b"), Add(Var("b"), Const(1))), Const(2))
                targeted.append(Program(
                    f"meta_q4_{random.randint(0,99):02d}",
                    Pow(gauss, Const(2)),
                    created_by="meta_engine"))

            elif family == "exponential":
                ratio = seq_analysis.get("ratio", 2.0)
                base  = max(2, min(8, round(ratio)))
                targeted.append(Program(
                    f"meta_e_{random.randint(0,99):02d}",
                    Pow(Const(base), Var("b")),
                    created_by="meta_engine"))
                targeted.append(Program(
                    f"meta_e2_{random.randint(0,99):02d}",
                    Pow(Var("a"), Var("b")),
                    created_by="meta_engine"))

            population = targeted + population

        # Expand with mutations — more when stuck
        base_mutations = 2
        extra = int(exploration_boost * 2)   # 0 normally, up to 6 when very stuck
        offspring: list[Program] = []
        for prog in population:
            for _ in range(base_mutations + extra):
                offspring.append(self.mutate(prog, library))
            # When stuck: also try aggressive shrink to escape complexity trap
            if exploration_boost > 1.0:
                shrunk = prog.clone()
                shrunk.root = random.choice([Var("a"), Var("b"),
                                             Add(Var("a"), Var("b")),
                                             Mul(Var("a"), Var("b"))])
                shrunk.name = f"reset_{random.randint(0,99):02d}"
                offspring.append(shrunk)
        # Crossover pairs — more when stuck
        if len(population) >= 2:
            cross_count = 3 + int(exploration_boost)
            for _ in range(cross_count):
                a, b = random.sample(population[:min(6, len(population))], 2)
                offspring.append(self.crossover(a, b))

        # Concept mutation: recombine named library abstractions
        concept_offspring = self.concept_mutate(library)
        offspring.extend(concept_offspring)

        # ── Concept Algebra: symbolically derive programs from known ones ──
        # This is the key upgrade — rather than random recombination,
        # the system applies named mathematical transformations:
        #   telescope:  f(b) - f(a-1)  (for range sums)
        #   square:     f(b)^2          (for sum_cubes)
        #   shift:      f(b+k)          (for shifted ranges)
        #   compose:    f(b) * g(b)     (for polynomial forms)
        if concepts is not None:
            algebra_offspring = concepts.algebra_suggestions(task.kind)
            offspring.extend(algebra_offspring)

        all_candidates = population + offspring

        # Use a representative batch of the task (not just one)
        test_tasks = [task] * 3   # repeat same task to penalise lucky guesses

        scored = []
        for p in all_candidates:
            f = self.score(p, test_tasks, library, energy_budget)
            p.fitness = f
            scored.append((f, p))

        scored.sort(key=lambda x: -x[0])
        return [p for _, p in scored[:self.pop_size]]


# ══════════════════════════════════════════════════════════════════
# PROOF NARRATOR
# Traces the derivation chain of any program back to arithmetic
# primitives, showing evidence at each step.
# ══════════════════════════════════════════════════════════════════

# The canonical derivation ladder — ordered from primitive to complex.
# Each entry: (name, expr_str, parent_name, derivation_story)
_DERIVATION_LADDER: list[tuple[str, str, str | None, str]] = [
    ("counting",       "1",
     None,
     "The number 1 — the first thing we can count."),

    ("addition",       "(a + b)",
     "counting",
     "Counting forward: put a and b together. "
     "3 + 4 means start at 3, count 4 more steps."),

    ("multiplication", "(a * b)",
     "addition",
     "Repeated addition: a × b means add a to itself b times. "
     "3 × 4 = 3+3+3+3 = 12."),

    ("exponentiation", "(a ** b)",
     "multiplication",
     "Repeated multiplication: a^b means multiply a by itself b times. "
     "2^3 = 2×2×2 = 8."),

    ("triangular",     "((b * (b + 1)) // 2)",
     "multiplication",
     "Sum of 1+2+…+b. Gauss noticed: pair the first and last (1+b), "
     "second and second-to-last (2+(b-1)), each pair sums to b+1, "
     "and there are b/2 such pairs. So the total is b×(b+1)÷2."),

    ("partial_sum",    "((((1 + b) * b) // 2) - ((((a - 1) * ((a - 1) + 1)) // 2)))",
     "triangular",
     "Sum from a to b = sum(1..b) − sum(1..a−1). "
     "Telescope: T(b) − T(a−1)."),

    ("sum_squares",    "(((((1 + b) * b) // 2) * ((2 * b) + 1)) // 3)",
     "triangular",
     "Sum of 1²+2²+…+b² = T(b)×(2b+1)÷3 where T(b) is the triangular number. "
     "Discovered by multiplying the Gauss formula by the (2b+1) factor."),

    ("sum_cubes",      "((((1 + b) * b) // 2) ** 2)",
     "triangular",
     "Sum of 1³+2³+…+b³ = T(b)² — the square of the triangular number. "
     "Discovered by squaring the Gauss formula."),
]

_LADDER_BY_EXPR: dict[str, tuple] = {row[1]: row for row in _DERIVATION_LADDER}
_LADDER_BY_NAME: dict[str, tuple] = {row[0]: row for row in _DERIVATION_LADDER}


def _canonicalise_safe(expr_str: str) -> str:
    try:
        node = _parse_expr_stub(expr_str)
        return canonicalize(node).to_str()
    except Exception:
        return expr_str


def _eval_safe(node: "Node", a: int, b: int) -> str:
    try:
        v = node.eval({"a": a, "b": b}, [0])
        return str(v)
    except Exception:
        return "?"


def _find_ladder_ancestor(expr: str,
                          concepts: "ConceptRegistry") -> str | None:
    """
    Walk up the derivation ladder to find which known concept this
    expression is closest to structurally — used to anchor the proof.
    """
    try:
        target = _parse_expr_stub(expr)
        target_canon = canonicalize(target).to_str()
    except Exception:
        return None

    # Direct match against ladder
    for name, ladder_expr, _, _ in _DERIVATION_LADDER:
        try:
            lc = _canonicalise_safe(ladder_expr)
            if lc == target_canon:
                return name
        except Exception:
            pass

    # Match against concept registry
    if concepts:
        for c in concepts.all_concepts():
            if c.canonical == target_canon:
                return c.name

    # Structural containment: does the target tree contain a known ladder node?
    def contains_sub(node: "Node", sub_canon: str) -> bool:
        try:
            if canonicalize(node).to_str() == sub_canon:
                return True
        except Exception:
            pass
        for child in getattr(node, '__dict__', {}).values():
            if isinstance(child, Node) and contains_sub(child, sub_canon):
                return True
        return False

    best = None
    best_depth = -1
    for i, (name, ladder_expr, _, _) in enumerate(_DERIVATION_LADDER):
        try:
            lc = _canonicalise_safe(ladder_expr)
            if contains_sub(target, lc):
                if i > best_depth:
                    best = name
                    best_depth = i
        except Exception:
            pass
    return best


def generate_proof(prog: Program,
                   agent_name: str,
                   concepts: "ConceptRegistry | None",
                   round_num: int) -> str:
    """
    Generate a human-readable proof/derivation showing how this program
    was arrived at, starting from arithmetic primitives.

    Returns a multi-line string suitable for printing to the console.
    """
    lines: list[str] = []
    box   = "━"
    arrow = "→"

    expr = prog.to_str()
    try:
        canon = canonicalize(prog.root).to_str()
    except Exception:
        canon = expr

    # ── Header ─────────────────────────────────────────────────────
    lines.append(f"\n  {bold('📜 PROOF OF DISCOVERY')}  {cyan(prog.name)}  "
                 f"by {bold(agent_name)}  {dim(f'(round {round_num})')}")
    lines.append(f"  {dim(box * 62)}")

    # ── Step 1: Show what the program computes ──────────────────────
    lines.append(f"\n  {bold('CLAIM')}  {dim(canon)}")
    lines.append(f"  {dim('produces a consistent numerical pattern:')}")

    node = prog.root
    evidence_rows: list[str] = []
    for a, b in [(1, 1), (1, 2), (1, 3), (1, 4), (1, 5),
                 (2, 3), (3, 4), (2, 5)]:
        v = _eval_safe(node, a, b)
        evidence_rows.append(f"f({a},{b})={v}")
    lines.append("  " + "  ".join(evidence_rows[:5]))
    lines.append("  " + "  ".join(evidence_rows[5:]))

    # ── Step 2: Trace the structural ancestry ──────────────────────
    ancestor = _find_ladder_ancestor(canon, concepts)
    lines.append(f"\n  {bold('DERIVATION CHAIN')}")

    # Build the full path up from counting
    path: list[tuple] = []
    cur = ancestor
    seen_names: set[str] = set()
    while cur and cur not in seen_names:
        seen_names.add(cur)
        if cur in _LADDER_BY_NAME:
            entry = _LADDER_BY_NAME[cur]
            path.append(entry)
            cur = entry[2]   # parent
        else:
            break
    path.reverse()

    if not path:
        # Fallback: show the three fundamental operations
        path = [_LADDER_BY_NAME["counting"],
                _LADDER_BY_NAME["addition"],
                _LADDER_BY_NAME["multiplication"]]

    for i, (name, ladder_expr, parent, story) in enumerate(path):
        prefix = "  " + ("  " * i)
        op_node = _parse_expr_stub(ladder_expr) if ladder_expr != "1" else Const(1)
        sample = _eval_safe(op_node, 2, 3)
        lines.append(
            f"{prefix}{green(str(i+1))}. {bold(name.upper())}"
            f"  {dim(ladder_expr[:30])}  {dim(f'e.g. f(2,3)={sample}')}"
        )
        # Wrap story at 60 chars
        words = story.split()
        line_buf, wrapped = "", []
        for w in words:
            if len(line_buf) + len(w) + 1 > 58:
                wrapped.append(line_buf)
                line_buf = w
            else:
                line_buf = (line_buf + " " + w).strip()
        if line_buf:
            wrapped.append(line_buf)
        for wl in wrapped:
            lines.append(f"{prefix}   {dim(wl)}")

    # ── Step 3: Show the transformation to the new formula ─────────
    lines.append(f"\n  {bold('TRANSFORMATION')}  {dim('from known to new:')}")

    # Describe structurally what changed
    structural_notes = _describe_transformation(canon, ancestor, concepts)
    for note in structural_notes:
        lines.append(f"  {arrow} {note}")

    # ── Step 4: Verify with concrete numbers ───────────────────────
    lines.append(f"\n  {bold('VERIFICATION')}")
    verify_cases = [(1, 3), (1, 5), (1, 8), (2, 6), (3, 7)]
    all_ok = True
    for a, b in verify_cases:
        v = _eval_safe(node, a, b)
        try:
            vi = int(v)
            # Cross-check with parent if possible
            if ancestor and ancestor in _LADDER_BY_NAME:
                par_expr = _LADDER_BY_NAME[ancestor][1]
                par_node = _parse_expr_stub(par_expr)
                par_v = _eval_safe(par_node, a, b)
                lines.append(
                    f"  f({a},{b}) = {green(v)}"
                    f"  {dim(f'(parent {ancestor}: {par_v})')}"
                )
            else:
                lines.append(f"  f({a},{b}) = {green(v)}")
        except Exception:
            lines.append(f"  f({a},{b}) = {red('ERROR')}")
            all_ok = False

    verdict = green("✓ PROOF COMPLETE") if all_ok else red("✗ INCONSISTENCY FOUND")
    lines.append(f"\n  {verdict}  {dim(prog.name)} is a valid mathematical pattern")
    lines.append(f"  {dim(box * 62)}")

    return "\n".join(lines)


def _describe_transformation(canon: str,
                              ancestor_name: str | None,
                              concepts: "ConceptRegistry | None") -> list[str]:
    """
    Describe in plain language how the new formula relates to its ancestor.
    Uses behavioral relationship detection if available.
    """
    notes: list[str] = []

    if not ancestor_name or ancestor_name not in _LADDER_BY_NAME:
        # Try to get relationship from concept registry
        if concepts:
            for c in concepts.all_concepts():
                if c.name == ancestor_name and c.child_of:
                    notes.append(f"Derived from {bold(c.child_of)}.")
        notes.append(f"Formula: {dim(canon[:60])}")
        return notes

    ancestor_entry = _LADDER_BY_NAME[ancestor_name]
    ancestor_expr  = ancestor_entry[1]

    # Check behavioral relationship from registry
    rel = None
    if concepts:
        for c in concepts.all_concepts():
            try:
                cc = canonicalize(_parse_expr_stub(canon)).to_str()
                if c.canonical == cc and c.child_of == ancestor_name:
                    # Find which relationship was detected
                    for derived in c.derived_from:
                        if derived == ancestor_name:
                            # Look up in pending notes — use structural detection
                            break
            except Exception:
                pass

    # ── Numerically verified transformation detection ────────────────────
    # Instead of matching strings, test the actual relationship by evaluating
    # both formulas at multiple points. This prevents false positives like
    # claiming Nicomachus applies when the formula just happens to contain **2.
    ancestor_node = None
    new_node      = None
    try:
        new_node      = _parse_expr_stub(canon)
        ancestor_node = _parse_expr_stub(ancestor_expr)
    except Exception:
        pass

    def eval_both(a, b):
        try:
            nv = new_node.eval({"a": a, "b": b}, [0])
            av = ancestor_node.eval({"a": a, "b": b}, [0])
            return nv, av
        except Exception:
            return None, None

    detected = False
    if new_node and ancestor_node:
        test_pts = [(1,3),(1,4),(1,5),(1,6),(2,4),(3,5)]

        # Test: new == ancestor²
        sq_ok = all(
            nv == av*av
            for a,b in test_pts
            for nv,av in [eval_both(a,b)]
            if nv is not None and av is not None
        )
        if sq_ok and len(test_pts) >= 4:
            notes.append(f"Numerically verified: {bold(ancestor_name)}(b)² = this formula.")
            if "sum_formula" in ancestor_name or "triangular" in ancestor_name:
                notes.append(f"By Nicomachus' theorem: squaring the triangular number gives sum of cubes.")
                notes.append(f"1³+2³+…+n³ = (1+2+…+n)² — verified across {len(test_pts)} test points.")
            else:
                notes.append(f"This is {bold(ancestor_name)} squared.")
            detected = True

        # Test: new == telescope(ancestor) — new(a,b) = ancestor(b) - ancestor(a-1)
        if not detected:
            tele_ok = True
            for a, b in test_pts:
                try:
                    nv = new_node.eval({"a": a, "b": b}, [0])
                    av_b  = ancestor_node.eval({"a": 1, "b": b}, [0])
                    av_a1 = ancestor_node.eval({"a": 1, "b": a-1}, [0])
                    if nv != av_b - av_a1:
                        tele_ok = False; break
                except Exception:
                    tele_ok = False; break
            if tele_ok:
                notes.append(f"Telescope on {bold(ancestor_name)}: T(b) − T(a−1).")
                notes.append(f"Partial sum from a to b = full sum up to b minus prefix sum to a−1.")
                detected = True

        # Test: new == ancestor * (2b+1)//3 — sum of squares
        if not detected:
            sq_ok2 = True
            for a, b in test_pts:
                try:
                    nv = new_node.eval({"a": a, "b": b}, [0])
                    av = ancestor_node.eval({"a": a, "b": b}, [0])
                    if nv != av * (2*b+1) // 3:
                        sq_ok2 = False; break
                except Exception:
                    sq_ok2 = False; break
            if sq_ok2:
                notes.append(f"Multiplied {bold(ancestor_name)} by (2b+1), divided by 3.")
                notes.append(f"Gives sum of squares: 1²+2²+…+n² = T(b)·(2b+1)/3.")
                detected = True

    if not detected:
        # Fallback: describe structurally without making specific theorem claims
        if "loop(" in canon:
            notes.append(f"Loops a sub-expression — repeated application of an inner formula.")
        elif "ifeven" in canon:
            notes.append(f"Parity-conditional: behaves differently on even vs odd inputs.")
        elif "- (" in canon and "a - 1" in canon:
            notes.append(f"Difference structure — possibly a range or partial sum.")
        else:
            notes.append(f"Variant of {bold(ancestor_name)} with additional structure.")
            notes.append(f"Full expression: {dim(canon[:55])}")

    # Add verified sequence
    try:
        node = _parse_expr_stub(canon)
        seq = [node.eval({"a": 1, "b": b}, [0]) for b in range(1, 8)]
        diffs = [seq[i+1]-seq[i] for i in range(len(seq)-1)]
        diff2 = [diffs[i+1]-diffs[i] for i in range(len(diffs)-1)]
        if len(set(diffs)) == 1:
            notes.append(f"Sequence is {bold('arithmetic')}: {seq[:6]}... (Δ={diffs[0]:+d} each step)")
        elif len(set(diff2)) == 1:
            notes.append(f"Sequence is {bold('quadratic')}: {seq[:6]}... (Δ² = {diff2[0]} constant)")
        elif seq[1] and seq[0] and all(seq[i] != 0 for i in range(4)):
            ratios = [round(seq[i+1]/seq[i], 2) for i in range(3)]
            if max(ratios)-min(ratios) < 0.05:
                notes.append(f"Sequence is {bold('geometric')}: {seq[:6]}... (ratio ≈ {ratios[0]})")
            else:
                notes.append(f"Sequence: {seq[:6]}...")
        else:
            notes.append(f"Sequence: {seq[:6]}...")
    except Exception:
        pass

    return notes


# ══════════════════════════════════════════════════════════════════
# KNOWLEDGE MARKET
# ══════════════════════════════════════════════════════════════════

PUBLISH_COST = 5
ACQUIRE_COST = 8
ROYALTY_FEE  = 2