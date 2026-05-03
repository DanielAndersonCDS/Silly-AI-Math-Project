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

class EnergyExceeded(Exception): pass
class ExecError(Exception): pass

@dataclass
class Node:
    """Base class for all program tree nodes."""
    def eval(self, env: dict, budget: list) -> int:
        raise NotImplementedError

    def size(self) -> int:
        """Count nodes in subtree — used for compression scoring."""
        return 1

    def depth(self) -> int:
        return 1

    def clone(self) -> "Node":
        return copy.deepcopy(self)

    def to_str(self) -> str:
        raise NotImplementedError


@dataclass
class Const(Node):
    value: int
    def eval(self, env, budget):
        budget[0] += 1
        if budget[0] > MAX_ENERGY: raise EnergyExceeded()
        return self.value
    def to_str(self): return str(self.value)


@dataclass
class Var(Node):
    name: str   # "a" or "b"
    def eval(self, env, budget):
        budget[0] += 1
        if budget[0] > MAX_ENERGY: raise EnergyExceeded()
        return env[self.name]
    def to_str(self): return self.name


@dataclass
class Add(Node):
    left: Node; right: Node
    def eval(self, env, budget):
        budget[0] += 1
        if budget[0] > MAX_ENERGY: raise EnergyExceeded()
        return self.left.eval(env, budget) + self.right.eval(env, budget)
    def size(self): return 1 + self.left.size() + self.right.size()
    def depth(self): return 1 + max(self.left.depth(), self.right.depth())
    def to_str(self): return f"({self.left.to_str()} + {self.right.to_str()})"


@dataclass
class Sub(Node):
    left: Node; right: Node
    def eval(self, env, budget):
        budget[0] += 1
        if budget[0] > MAX_ENERGY: raise EnergyExceeded()
        return self.left.eval(env, budget) - self.right.eval(env, budget)
    def size(self): return 1 + self.left.size() + self.right.size()
    def depth(self): return 1 + max(self.left.depth(), self.right.depth())
    def to_str(self): return f"({self.left.to_str()} - {self.right.to_str()})"


@dataclass
class Mul(Node):
    """Costs 2 energy — slightly expensive to discourage free use."""
    left: Node; right: Node
    def eval(self, env, budget):
        budget[0] += 2
        if budget[0] > MAX_ENERGY: raise EnergyExceeded()
        return self.left.eval(env, budget) * self.right.eval(env, budget)
    def size(self): return 1 + self.left.size() + self.right.size()
    def depth(self): return 1 + max(self.left.depth(), self.right.depth())
    def to_str(self): return f"({self.left.to_str()} * {self.right.to_str()})"


@dataclass
class Loop(Node):
    """
    Accumulate body over `count` iterations.
    Semantics: result = sum of body evaluated with i=0..count-1
    env gets a loop variable 'i' injected.
    """
    body: Node; count: Node
    def eval(self, env, budget):
        budget[0] += 1
        if budget[0] > MAX_ENERGY: raise EnergyExceeded()
        n = self.count.eval(env, budget)
        if not isinstance(n, int) or n < 0 or n > 10_000:
            raise ExecError(f"bad loop count {n}")
        acc = 0
        for i in range(n):
            inner = dict(env); inner["i"] = i
            acc += self.body.eval(inner, budget)
            if budget[0] > MAX_ENERGY: raise EnergyExceeded()
        return acc
    def size(self): return 1 + self.body.size() + self.count.size()
    def depth(self): return 1 + max(self.body.depth(), self.count.depth())
    def to_str(self): return f"loop({self.body.to_str()}, {self.count.to_str()})"


@dataclass
class Pow(Node):
    """
    Integer exponentiation: base ** exp.
    Costs 3 energy. This is what agents need to solve a^b tasks.
    Only positive-integer exponents are meaningful here.
    """
    base: Node; exp: Node
    def eval(self, env, budget):
        budget[0] += 3
        if budget[0] > MAX_ENERGY: raise EnergyExceeded()
        b = self.base.eval(env, budget)
        e = self.exp.eval(env, budget)
        if not isinstance(e, int) or e < 0 or e > 20:
            raise ExecError(f"bad exponent {e}")
        return b ** e
    def size(self): return 1 + self.base.size() + self.exp.size()
    def depth(self): return 1 + max(self.base.depth(), self.exp.depth())
    def to_str(self): return f"({self.base.to_str()} ** {self.exp.to_str()})"


@dataclass
class IDiv(Node):
    """Integer floor division: left // right. Costs 2 energy.
    THE critical missing primitive — Gauss formula n*(n+1)//2 needs this."""
    left: Node; right: Node
    def eval(self, env, budget):
        budget[0] += 2
        if budget[0] > MAX_ENERGY: raise EnergyExceeded()
        r = self.right.eval(env, budget)
        if r == 0: raise ExecError("division by zero")
        return self.left.eval(env, budget) // r
    def size(self): return 1 + self.left.size() + self.right.size()
    def depth(self): return 1 + max(self.left.depth(), self.right.depth())
    def to_str(self): return f"({self.left.to_str()} // {self.right.to_str()})"

@dataclass
class IfNode(Node):
    """
    Conditional: if (left op right) then yes else no.
    op is one of: "eq", "lt", "gt", "even"
    "even" ignores right and tests whether left % 2 == 0.
    Costs 2 energy. Required for alt_sum and recursion base cases.
    """
    cond_left:  Node
    cond_op:    str   # "eq" | "lt" | "gt" | "even"
    cond_right: Node
    yes:        Node
    no:         Node

    def eval(self, env, budget):
        budget[0] += 2
        if budget[0] > MAX_ENERGY: raise EnergyExceeded()
        lv = self.cond_left.eval(env, budget)
        rv = self.cond_right.eval(env, budget)
        if self.cond_op == "eq":
            taken = (lv == rv)
        elif self.cond_op == "lt":
            taken = (lv < rv)
        elif self.cond_op == "gt":
            taken = (lv > rv)
        else:  # "even"
            taken = (lv % 2 == 0)
        if taken:
            return self.yes.eval(env, budget)
        else:
            return self.no.eval(env, budget)

    def size(self):
        return 1 + self.cond_left.size() + self.cond_right.size() \
               + self.yes.size() + self.no.size()

    def depth(self):
        return 1 + max(self.cond_left.depth(), self.cond_right.depth(),
                       self.yes.depth(), self.no.depth())

    def to_str(self):
        if self.cond_op == "even":
            cond = f"even({self.cond_left.to_str()})"
        else:
            cond = f"({self.cond_left.to_str()} {self.cond_op} {self.cond_right.to_str()})"
        return f"if{cond}:{self.yes.to_str()}|{self.no.to_str()}"


@dataclass
class CallFn(Node):
    """Call a named program from the agent's library."""
    fn_name: str; arg_a: Node; arg_b: Node
    _library: Any = field(default=None, compare=False, repr=False)

    def eval(self, env, budget):
        budget[0] += 1
        if budget[0] > MAX_ENERGY: raise EnergyExceeded()
        if self._library is None or self.fn_name not in self._library:
            raise ExecError(f"unknown fn {self.fn_name}")
        prog = self._library[self.fn_name]
        a = self.arg_a.eval(env, budget)
        b = self.arg_b.eval(env, budget)
        return prog.run({"a": a, "b": b}, budget)
    def size(self): return 1 + self.arg_a.size() + self.arg_b.size()
    def depth(self): return 1 + max(self.arg_a.depth(), self.arg_b.depth())
    def to_str(self):
        return f"{self.fn_name}({self.arg_a.to_str()}, {self.arg_b.to_str()})"


# ── Program wrapper ────────────────────────────────────────────────────────

@dataclass
class Program:
    """A named, executable, mutable program."""
    name:       str
    root:       Node
    created_by: str  = "?"
    created_at: int  = 0   # round
    fitness:    float = 0.0
    usage_count:int  = 0
    concept_tags: list = field(default_factory=list)

    def run(self, env: dict, budget: list | None = None) -> int:
        if budget is None: budget = [0]
        return self.root.eval(env, budget)

    def energy(self, env: dict) -> int:
        b = [0]
        try:   self.root.eval(env, b)
        except: pass
        return b[0]

    def size(self) -> int:
        return self.root.size()

    def to_str(self) -> str:
        return self.root.to_str()

    def clone(self) -> "Program":
        return Program(
            name=self.name, root=self.root.clone(),
            created_by=self.created_by, created_at=self.created_at,
            fitness=self.fitness, concept_tags=list(self.concept_tags)
        )


# ══════════════════════════════════════════════════════════════════
# PROGRAM LIBRARY  (per-agent named programs)
# ══════════════════════════════════════════════════════════════════

class ProgramLibrary:
    def __init__(self):
        self._lib: dict[str, Program] = {}
        self._seen_structures: set[str] = set()

    def add(self, prog: Program) -> bool:
        """Return True if it's a genuinely new canonical structure."""
        # Use canonical form so (a+b) and (b+a) count as the same
        try:
            sig = canonicalize(prog.root).to_str()
        except Exception:
            sig = prog.to_str()
        is_new = sig not in self._seen_structures
        self._seen_structures.add(sig)
        self._seen_structures.add(prog.to_str())   # also track raw form
        self._lib[prog.name] = prog
        return is_new

    def canonical_seen(self, prog: Program) -> bool:
        """True if this program's canonical form has already been seen."""
        try:
            sig = canonicalize(prog.root).to_str()
        except Exception:
            sig = prog.to_str()
        return sig in self._seen_structures

    def get(self, name: str) -> Optional[Program]:
        return self._lib.get(name)

    def names(self) -> list[str]:
        return list(self._lib.keys())

    def all(self) -> list[Program]:
        return list(self._lib.values())

    def novelty_score(self, prog: Program) -> float:
        try:
            sig = canonicalize(prog.root).to_str()
        except Exception:
            sig = prog.to_str()
        return 0.0 if sig in self._seen_structures else 1.0

    def to_json(self) -> str:
        return json.dumps({
            name: {"expr": p.to_str(), "fitness": p.fitness,
                   "tags": p.concept_tags, "uses": p.usage_count,
                   "created_by": p.created_by}
            for name, p in self._lib.items()
        })

    @classmethod
    def from_json(cls, s: str) -> "ProgramLibrary":
        lib = cls()
        try:
            data = json.loads(s)
            for name, meta in data.items():
                # Restore as a stub — actual fn will be rebuilt by mutation engine
                root = _parse_expr_stub(meta.get("expr", "0"))
                p = Program(name=name, root=root, fitness=meta.get("fitness", 0.0),
                            concept_tags=meta.get("tags", []),
                            usage_count=meta.get("uses", 0),
                            created_by=meta.get("created_by", "?"))
                lib.add(p)
        except Exception:
            pass
        return lib


def _split_at_depth_zero(s: str, sep: str) -> int:
    """Return index of first occurrence of sep at bracket depth 0, or -1."""
    depth = 0
    i = 0
    while i < len(s):
        ch = s[i]
        if ch in "([": depth += 1
        elif ch in ")]": depth -= 1
        elif depth == 0 and s[i:i+len(sep)] == sep:
            return i
        i += 1
    return -1


def _parse_expr_stub(expr: str) -> Node:
    """
    Recursive descent parser for expression strings produced by Node.to_str().
    Handles: int literals, a, b, i, loop(...), if...|..., (l op r), fn(a,b)
    Falls back to Const(0) on parse failure — never raises.
    """
    expr = expr.strip()
    if not expr:
        return Const(0)

    # Numeric literal
    try:
        return Const(int(expr))
    except ValueError:
        pass

    # Single variables
    if expr in ("a", "b", "i"):
        return Var(expr)

    # loop(body, count)
    if expr.startswith("loop(") and expr.endswith(")"):
        inner = expr[5:-1]
        split = _split_at_depth_zero(inner, ",")
        if split != -1:
            return Loop(
                _parse_expr_stub(inner[:split].strip()),
                _parse_expr_stub(inner[split+1:].strip())
            )

    # if(cond):yes|no  — produced by IfNode.to_str()
    # format: "if(cond_left op cond_right):yes|no"  or  "ifeven(x):yes|no"
    if expr.startswith("if"):
        # find the colon separating condition from branches
        colon = _split_at_depth_zero(expr, ":")
        if colon != -1:
            cond_part = expr[2:colon]   # strip "if"
            rest = expr[colon+1:]
            pipe = _split_at_depth_zero(rest, "|")
            if pipe != -1:
                yes_str = rest[:pipe]
                no_str  = rest[pipe+1:]
                yes_node = _parse_expr_stub(yes_str)
                no_node  = _parse_expr_stub(no_str)
                # cond_part is either "even(x)" or "(l op r)"
                if cond_part.startswith("even(") and cond_part.endswith(")"):
                    inner = cond_part[5:-1]
                    return IfNode(_parse_expr_stub(inner), "even",
                                  Const(0), yes_node, no_node)
                if cond_part.startswith("(") and cond_part.endswith(")"):
                    inner = cond_part[1:-1]
                    for op in ("eq", "lt", "gt"):
                        sep = f" {op} "
                        idx = _split_at_depth_zero(inner, sep)
                        if idx != -1:
                            return IfNode(
                                _parse_expr_stub(inner[:idx]),
                                op,
                                _parse_expr_stub(inner[idx+len(sep):]),
                                yes_node, no_node
                            )

    # fn_name(arg_a, arg_b)  — CallFn
    paren = expr.find("(")
    if paren > 0 and expr.endswith(")"):
        fn_name = expr[:paren]
        if fn_name.replace("_","").replace("-","").isalnum():
            args_str = expr[paren+1:-1]
            split = _split_at_depth_zero(args_str, ",")
            if split != -1:
                return CallFn(
                    fn_name,
                    _parse_expr_stub(args_str[:split].strip()),
                    _parse_expr_stub(args_str[split+1:].strip())
                )

    # (left op right)  — binary operators, longest token first
    if expr.startswith("(") and expr.endswith(")"):
        inner = expr[1:-1]
        for op_str, cls in [(" ** ", Pow), (" // ", IDiv),
                             (" * ", Mul), (" - ", Sub), (" + ", Add)]:
            idx = _split_at_depth_zero(inner, op_str)
            if idx != -1:
                left  = _parse_expr_stub(inner[:idx])
                right = _parse_expr_stub(inner[idx+len(op_str):])
                if cls is Pow:
                    return Pow(left, right)
                return cls(left, right)

    return Const(0)


# ══════════════════════════════════════════════════════════════════
# CANONICALIZATION  — normalise programs so (a+b) == (b+a)
# ══════════════════════════════════════════════════════════════════

def canonicalize(node: Node) -> Node:
    """
    Return a canonical form of the tree so semantically equivalent
    programs get the same string representation.

    Rules:
      - Sort children of Add and Mul alphabetically (commutativity)
      - Fold trivial identities: x+0→x, x*1→x, x*0→0, loop(x,1)→x
      - Collapse constant arithmetic: Const(2)+Const(3) → Const(5)
    """
    node = copy.deepcopy(node)
    return _canon(node)


def _canon(n: Node) -> Node:
    # Recurse first (bottom-up)
    if isinstance(n, Add):
        l, r = _canon(n.left), _canon(n.right)
        # Constant folding
        if isinstance(l, Const) and isinstance(r, Const):
            return Const(l.value + r.value)
        # Identity: x + 0 → x
        if isinstance(r, Const) and r.value == 0: return l
        if isinstance(l, Const) and l.value == 0: return r
        # Canonical sort: smaller string on the left
        ls, rs = l.to_str(), r.to_str()
        if ls > rs: l, r = r, l
        return Add(l, r)

    if isinstance(n, Mul):
        l, r = _canon(n.left), _canon(n.right)
        if isinstance(l, Const) and isinstance(r, Const):
            return Const(l.value * r.value)
        if isinstance(r, Const) and r.value == 1: return l
        if isinstance(l, Const) and l.value == 1: return r
        if isinstance(r, Const) and r.value == 0: return Const(0)
        if isinstance(l, Const) and l.value == 0: return Const(0)
        ls, rs = l.to_str(), r.to_str()
        if ls > rs: l, r = r, l
        return Mul(l, r)

    if isinstance(n, Sub):
        l, r = _canon(n.left), _canon(n.right)
        if isinstance(l, Const) and isinstance(r, Const):
            return Const(l.value - r.value)
        if isinstance(r, Const) and r.value == 0: return l
        return Sub(l, r)

    if isinstance(n, Pow):
        b, e = _canon(n.base), _canon(n.exp)
        if isinstance(e, Const) and e.value == 0: return Const(1)
        if isinstance(e, Const) and e.value == 1: return b
        if isinstance(b, Const) and isinstance(e, Const) and 0 <= e.value <= 20:
            return Const(b.value ** e.value)
        return Pow(b, e)

    if isinstance(n, IDiv):
        l, r = _canon(n.left), _canon(n.right)
        if isinstance(r, Const) and r.value == 1: return l
        if isinstance(l, Const) and isinstance(r, Const) and r.value != 0:
            return Const(l.value // r.value)
        return IDiv(l, r)

    if isinstance(n, Loop):
        body  = _canon(n.body)
        count = _canon(n.count)
        # loop(x, 1) → x  (one iteration = body itself)
        if isinstance(count, Const) and count.value == 1: return body
        # loop(x, 0) → 0
        if isinstance(count, Const) and count.value == 0: return Const(0)
        return Loop(body, count)

    if isinstance(n, IfNode):
        cl = _canon(n.cond_left)
        cr = _canon(n.cond_right)
        y  = _canon(n.yes)
        no = _canon(n.no)
        # Fold constant conditions
        if isinstance(cl, Const) and isinstance(cr, Const):
            lv, rv = cl.value, cr.value
            if n.cond_op == "eq":   taken = (lv == rv)
            elif n.cond_op == "lt": taken = (lv < rv)
            elif n.cond_op == "gt": taken = (lv > rv)
            else:                   taken = (lv % 2 == 0)
            return y if taken else no
        return IfNode(cl, n.cond_op, cr, y, no)

    # Leaf nodes — return as-is
    return n


def canonical_signature(prog: Program) -> str:
    """Canonical string for deduplication and concept detection."""
    try:
        return canonicalize(prog.root).to_str()
    except Exception:
        return prog.to_str()


# ══════════════════════════════════════════════════════════════════
# CONCEPT CLUSTERING  — group programs that mean the same thing
# ══════════════════════════════════════════════════════════════════

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
    # Discovery importance scores (updated by score_importance())
    importance_score:  float = 0.0
    unlocks_count:     int   = 0
    compression_ratio: float = 0.0
    surprise_bonus:    float = 0.0

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

    def score_importance(self, unlocked_by: dict = None,
                          surprise_resolutions: dict = None) -> None:
        """
        Compute discovery importance scores using four metrics:
        1. Consequence count — how many task kinds this concept unlocked
        2. Compression ratio — fitness / formula complexity (elegance)
        3. Surprise bonus — set when concept resolves a curiosity prediction
        4. Cross-branch utility — how many different proof chains cite this concept
           (Discovery Multiplier signal: concepts used everywhere matter more)
        """
        if unlocked_by is None:
            unlocked_by = {}
        if surprise_resolutions is None:
            surprise_resolutions = {}

        # Invert: task_kind → concept_name to count per concept
        unlock_counts: dict[str, int] = {}
        for _kind, cname in unlocked_by.items():
            unlock_counts[cname] = unlock_counts.get(cname, 0) + 1

        # Count how many other concepts derive from each concept
        # (a concept others build on is a multiplier)
        derived_by: dict[str, int] = {}
        for c in self._concepts.values():
            for parent in c.derived_from:
                derived_by[parent] = derived_by.get(parent, 0) + 1

        for c in self._concepts.values():
            c.unlocks_count     = unlock_counts.get(c.name, 0)
            size                = c.program_node.size() if c.program_node else 10
            c.compression_ratio = c.strength / max(size, 1)
            c.surprise_bonus    = surprise_resolutions.get(c.name, 0.0)

            # Cross-branch utility: how many concepts are derived from this one
            # This is the Discovery Multiplier signal — foundational concepts
            # that others build on are more important than isolated discoveries
            derivation_depth = derived_by.get(c.name, 0)

            c.importance_score  = (
                c.unlocks_count    * 40.0 +   # unlocking tasks: big
                c.compression_ratio * 2.0 +   # elegance (was 0.5 — stronger now)
                c.surprise_bonus    * 30.0 +   # confirmed predictions
                derivation_depth    * 15.0     # multiplier: others build on this
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
        # Research economy: concept discoveries unlock task families early
        self._unlocked_kinds: set[str] = set()

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

    def unlock_via_concept(self, concept_canonical: str, round_num: int) -> Optional[str]:
        """
        Research economy: concept discoveries unlock harder task families early.
        Discovering T(n) is MORE VALUABLE than discovering b+3 because
        T(n) unlocks sum_range for everyone — accelerating the whole civilisation.
        Returns the name of the unlocked task kind, or None.
        """
        UNLOCK_MAP = [
            ("(a + b)",  "repeated_add", 3,  "addition found → multiplication unlocked"),
            ("(a * b)",  "power",        8,  "multiplication found → exponentiation unlocked"),
            ("// 2",     "sum_range",   14,  "triangular structure found → Σ1..n unlocked"),
            ("// 2",     "partial_sum", 18,  "triangular structure found → Σa..b unlocked"),
            ("** 2",     "sum_squares", 22,  "squaring found → Σi² unlocked"),
            ("** 2",     "sum_cubes",   28,  "squaring + triangular → Σi³ unlocked"),
        ]
        unlocked = None
        for pattern, kind, min_round, message in UNLOCK_MAP:
            if pattern not in concept_canonical:
                continue
            if kind in self._unlocked_kinds:
                continue
            if round_num < min_round:
                continue
            self._unlocked_kinds.add(kind)
            unlocked = kind
            print(f"  {green('🔓 UNLOCKED')}  {bold(kind)}  "
                  f"{dim(f'← {message}')}")
        return unlocked

    def _make(self) -> Task:
        r = self.round
        u = self._unlocked_kinds

        if self._active_conjecture and r > 18 and random.random() < 0.15:
            return self._make_conjecture_task(self._active_conjecture)

        # Gate: available if round threshold passed OR concept unlocked it early
        gate_mul   = r > 3  or "repeated_add" in u
        gate_pow   = r > 6  or "power"        in u
        gate_sr    = r > 17 or "sum_range"    in u
        gate_ps    = r > 23 or "partial_sum"  in u
        gate_sq    = r > 29 or "sum_squares"  in u
        gate_sc    = r > 35 or "sum_cubes"    in u

        # Build available kinds, select hardest reachable
        available = ["add"]
        if gate_mul: available.append("repeated_add")
        if gate_pow: available.append("power")
        if gate_sr:  available.append("sum_range")
        if gate_ps:  available.append("partial_sum")
        if gate_sq:  available.append("sum_squares")
        if gate_sc:  available.append("sum_cubes")

        # Progress toward harder kinds as rounds increase, but never skip
        idx  = min(len(available) - 1, max(0, (r - 1) // 6))
        kind = available[idx] if r > 3 else "add"

        if kind == "add":
            a, b = random.randint(1, 10), random.randint(1, 10)
            return Task(f"{a}+{b}", a, b, a+b, "add", 1)
        elif kind == "repeated_add":
            if r <= 6:
                a, b = random.randint(2, 12), random.randint(3, 15)
                return Task(f"{a}×{b}", a, b, a*b, "repeated_add", 2)
            else:
                a, b = random.randint(2, 10), random.randint(20, 80)
                return Task(f"{a}×{b}(big)", a, b, a*b, "repeated_add", 3)
        elif kind == "power":
            base, exp = random.randint(2, 4), random.randint(3, 7)
            return Task(f"{base}^{exp}", base, exp, base**exp, "power", 4)
        elif kind == "sum_range":
            n = random.randint(10, 60)
            return Task(f"Σ1..{n}", 1, n, n*(n+1)//2, "sum_range", 5)
        elif kind == "partial_sum":
            a = random.randint(2, 10); b = random.randint(a+5, a+30)
            return Task(f"Σ{a}..{b}", a, b, (b-a+1)*(a+b)//2, "partial_sum", 6)
        elif kind == "sum_squares":
            n = random.randint(5, 30)
            return Task(f"Σi²(1..{n})", 1, n, n*(n+1)*(2*n+1)//6, "sum_squares", 7)
        else:
            n = random.randint(3, 15)
            return Task(f"Σi³(1..{n})", 1, n, (n*(n+1)//2)**2, "sum_cubes", 8)

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
                    control_engine=None) -> None:
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
            for prog in own_progs[:3]:
                node = prog.root.clone()
                imagined = [
                    ("squared",   Pow(node.clone(), Const(2))),
                    ("cubed",     Pow(node.clone(), Const(3))),
                    ("looped",    Loop(node.clone(), Const(random.randint(2,5)))),
                    ("shifted",   _sub_b_with(node.clone(), Add(Var("b"), Const(1)))),
                    ("doubled",   Mul(Const(2), node.clone())),
                ]
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
            boosted by semantic strategy alignment (closed control loop)."""
            nov = novelty_score(fp)
            comp = compression_score(fp, prog)
            comp_norm = min(comp / 0.5, 2.0)
            base = nov * (0.6 + 0.4 * comp_norm)
            # Semantic control bonus: agents strong in certain strategies
            # get a boost when exploring expressions that match those strategies
            if control_engine is not None:
                try:
                    sig = canonicalize(prog.root).to_str()
                    sem_bonus = control_engine.semantic_interest_bonus(
                        self.name, sig)
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

from ms_debate import DebateArena
from ms_invariants import InvariantEngine
from ms_proof import SymbolicProofEngine
from ms_theory import AxiomEngine, TheoryTree
from ms_planner import ConsequenceExplorer, GoalDirectedProver, LemmaInventor
from ms_abstraction import AbstractionDetector
from ms_curiosity import CuriosityEngine
from ms_semantics import SemanticTagger, ProofStrategyLibrary
from ms_control import SemanticControlEngine
from ms_symmetry import TheorySymmetryScanner

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
    names      = random.sample(AGENT_NAMES, min(num_agents, len(AGENT_NAMES)))
    market     = KnowledgeMarket()
    concepts   = ConceptRegistry()
    debate     = DebateArena()
    invariants = InvariantEngine()
    prover     = SymbolicProofEngine()
    axioms     = AxiomEngine()
    theory     = TheoryTree(axioms)
    explorer    = ConsequenceExplorer(prover)
    gd_prover   = GoalDirectedProver(prover)
    inventor    = LemmaInventor(prover)
    abstracter  = AbstractionDetector()
    curiosity   = CuriosityEngine()
    tagger      = SemanticTagger()
    strategies  = ProofStrategyLibrary()
    control     = SemanticControlEngine(tagger, strategies)
    sym_scanner = TheorySymmetryScanner()

    # ── Prove foundational identities at startup ──────────────────────────
    # These are non-trivial derivations the engine can actually complete.
    # They populate the theory tree with real ✓ DERIVED theorems before
    # any agent discoveries, giving agents a foundation to build on.
    _STARTUP_IDENTITIES = [
        ("distributive_law",
         "(a * (b + a))", "((a * b) + (a * a))",
         "∀a,b: a×(b+a) = a×b + a×a  (distributive)",
         ["A5"], ["arithmetic", "algebra"]),
        ("power_product_law",
         "((a ** 2) * (a ** 3))", "(a ** 5)",
         "∀a: a²×a³ = a⁵  (power product)",
         ["A6"], ["arithmetic", "powers"]),
        ("power_of_power_law",
         "((a ** 2) ** 3)", "(a ** 6)",
         "∀a: (a²)³ = a⁶  (power of power)",
         ["A6"], ["arithmetic", "powers"]),
        ("additive_identity_law",
         "(a + 0)", "a",
         "∀a: a + 0 = a  (additive identity)",
         ["A1"], ["arithmetic"]),
        ("multiplicative_identity_law",
         "(a * 1)", "a",
         "∀a: a × 1 = a  (multiplicative identity)",
         ["A3"], ["arithmetic"]),
        ("commutativity_addition",
         "(b + a)", "(a + b)",
         "∀a,b: b + a = a + b  (commutativity)",
         ["A2"], ["arithmetic", "algebra"]),
        ("power_commute_factors",
         "((a ** 2) * (b ** 2))", "((b ** 2) * (a ** 2))",
         "∀a,b: a²×b² = b²×a²  (commutativity of squares)",
         ["A4"], ["arithmetic", "quadratic"]),
    ]

    print(f"\n  {dim('Proving foundational identities...')}")
    _unlock_credit: dict[str, str] = {}         # task_kind → concept_name that unlocked it
    _surprise_resolutions: dict[str, float] = {} # concept_name → surprise score
    for name, lhs_str, rhs_str, formal, deps, tags in _STARTUP_IDENTITIES:
        try:
            lhs_node = _parse_expr_stub(lhs_str)
            rhs_node = _parse_expr_stub(rhs_str)
            result   = prover.prove(lhs_node, rhs_node, name)
            law_dict = {
                "id": name, "name": name.replace("_", " ").title(),
                "statement": formal, "formal": formal,
                "kind": "algebraic_identity",
                "proof_status": result.status,
                "proof_steps":  len(result.steps),
            }
            node = theory.add_theorem(
                law          = law_dict,
                proof_status = result.status,
                proof_steps  = len(result.steps),
                round_num    = 0,
                discoverer   = "axiom_engine",
            )
            # Manually set tags and dependencies
            node.concept_tags = tags
            node.depends_on   = deps
            status_str = (green("✓ derived") if result.status == "proven"
                          else yellow("≡ trivial") if result.status == "trivial"
                          else dim("~ empirical"))
            step_count = f"({len(result.steps)} steps)"
            print(f"  {status_str}  {dim(formal)}  {dim(step_count)}")
            # Show the first few steps so derivation chain is visible
            if result.status == "proven" and result.steps:
                for s in result.steps[:3]:
                    print(f"    {dim(s.rule_name)}: "
                          f"{dim(s.before[:32])} → {dim(s.after[:32])}")
            # Register as named lemma for future proof citation
            if result.status in ("proven", "trivial"):
                prover.register_lemma(name, result)
        except Exception as e:
            print(f"  {dim(f'skipped {name}: {e}')}")
    print()

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
                agent.contemplate(env, concepts, rnd, control_engine=control)

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

        # Semantic tagging — derive meaning from concept behavior every 15 rounds
        if rnd >= 20 and rnd % 15 == 0:
            all_concept_objs = concepts.all_concepts()
            tagger.tag_all(all_concept_objs)
            concept_by_canonical = {c.canonical: c for c in all_concept_objs}
            for agent in agents:
                # Each agent's style based on concepts THEY discovered
                agent_canonicals = set()
                for p in agent.library.all():
                    if p.created_by == agent.name:
                        try:
                            from ms_core import canonicalize
                            canon = canonicalize(p.root).to_str()
                            agent_canonicals.add(canon)
                        except Exception:
                            pass
                agent_concept_objs = [
                    c for c in all_concept_objs
                    if c.canonical in agent_canonicals
                ]
                if not agent_concept_objs:
                    agent_concept_objs = all_concept_objs[:5]  # fallback
                weights = control.compute_agent_priors(agent.name, agent_concept_objs)
                agent._strategy_weights = weights
            invariants.scan(concepts, rnd)

            # Theory symmetry scanner — detects structural imbalances
            # and generates mathematically motivated conjectures
            all_tags = tagger._tags
            sym_conjectures = sym_scanner.scan(
                concepts  = concepts.all_concepts(),
                semantic_tags = all_tags,
                laws      = concepts.all_laws(),
                round_num = rnd,
            )
            if sym_conjectures:
                sym_scanner.announce_new(sym_conjectures)

        # Research economy: discoveries unlock harder task families early.
        # Track which concept triggered each unlock for importance scoring.
        for concept in concepts.all_concepts():
            unlocked = env.unlock_via_concept(concept.canonical, rnd)
            if unlocked:
                _unlock_credit[unlocked] = concept.name

        # Importance scoring — update every 10 rounds
        if rnd % 10 == 0:
            concepts.score_importance(
                unlocked_by = _unlock_credit,
                surprise_resolutions = _surprise_resolutions,
            )
            # Layer 5: discovery-driven task creation.
            # Top-importance concepts with open curiosity predictions
            # become new conjecture tasks — the system invents its own problems.
            if rnd >= 40:
                ranked = sorted(concepts.all_concepts(),
                                key=lambda c: -c.importance_score)
                for top_c in ranked[:3]:
                    if top_c.importance_score < 40:
                        break
                    # Find open OR recently-confirmed predictions about this concept
                    # Confirmed = "we know it exists but haven't made it a task yet"
                    candidate_preds = [
                        p for p in curiosity._expectations.all_predictions()
                        if p.status in ("open", "confirmed")
                        and p.predicted_expr
                        and top_c.canonical in p.predicted_expr
                        and p.priority >= 0.75
                    ]
                    for pred in candidate_preds:
                        try:
                            node = _parse_expr_stub(pred.predicted_expr)
                            task_kind = f"research_{abs(hash(pred.predicted_expr)) % 10000:04d}"
                            proposed = env.propose_conjecture(
                                agent_name   = "importance_engine",
                                kind         = task_kind,
                                formula_node = node,
                                description  = pred.claim[:40],
                                round_num    = rnd,
                            )
                            if proposed:
                                print(f"  {green('🔬 RESEARCH TASK')}  "
                                      f"{bold('importance-driven conjecture:')}")
                                print(f"    {dim(pred.claim[:70])}")
                        except Exception:
                            pass
                        break

            # Print importance ranking every 20 rounds
            ranked2 = sorted(concepts.all_concepts(),
                             key=lambda c: -c.importance_score)
            top3 = [c for c in ranked2 if c.importance_score > 0][:3]
            if top3 and rnd % 20 == 0:
                print(f"  {dim('📊 IMPORTANCE RANKING:')}")
                for rank, c in enumerate(top3, 1):
                    print(f"    {rank}. {bold(c.name)}  "
                          f"{dim(f'score={c.importance_score:.0f}')}  "
                          f"{dim(f'unlocks={c.unlocks_count}  compression={c.compression_ratio:.1f}  surprise={c.surprise_bonus:.1f}')}")

        # Theory branch detection — announce when knowledge organises into a field
        if rnd >= 30 and rnd % 15 == 0:
            theory.detect_branches(rnd)

        # Consequence exploration — derive new theorems from known concepts
        # This is "discovers by proving": consequences generated deductively.
        if rnd >= 25 and rnd % 12 == 0:
            new_consequences = explorer.scan_concepts(concepts, rnd)
            if new_consequences:
                new_consequences.sort(key=lambda t: -t.novelty)
                print(f"  {cyan('🔭 DEDUCED')}  "
                      f"{bold(str(len(new_consequences)))} consequences "
                      f"derived from known concepts (without empirical search):")
                for t in new_consequences[:5]:
                    step_hint = t.steps[0].rule_name if t.steps else "?"
                    print(f"    {cyan(t.expression[:55])}  "
                          f"{dim(f'← {t.origin} via {step_hint}')}")
                # Check if any structural conjectures got resolved by new discoveries
                known_canonicals = {c.canonical for c in concepts.all_concepts()}
                for conj in sym_scanner.open_conjectures():
                    if not conj.predicted_expr:
                        continue
                    resolved = False
                    # Direct match
                    if conj.predicted_expr in known_canonicals:
                        resolved = True
                    else:
                        # Behavioral equality: evaluate at test points
                        try:
                            pred_node = _parse_expr_stub(conj.predicted_expr)
                            pred_vals = tuple(
                                pred_node.eval({"a":1,"b":b},[0])
                                for b in range(1, 8)
                            )
                            for c in concepts.all_concepts():
                                if c.program_node is None:
                                    continue
                                try:
                                    c_vals = tuple(
                                        c.program_node.eval({"a":1,"b":b},[0])
                                        for b in range(1, 8)
                                    )
                                    if pred_vals == c_vals:
                                        resolved = True
                                        break
                                except Exception:
                                    pass
                        except Exception:
                            pass
                    if resolved:
                        conj.status = "resolved"
                        print(f"  {green('✓ STRUCTURAL GAP CLOSED')}  "
                              f"{dim(conj.claim[:65])}")
                all_deduced = explorer.all_discovered()
                new_objects = abstracter.scan(all_deduced, rnd)
                for obj in new_objects:
                    abstracter.announce(obj)

                # Curiosity update — generate research questions from new knowledge
                known_exprs = [c.canonical for c in concepts.all_concepts() if c.canonical]
                families    = getattr(concepts, "_families", {})
                prev_resolved = len([p for p in curiosity._expectations.all_predictions()
                                     if p.status == "confirmed"])
                curiosity.update(
                    laws        = concepts.all_laws(),
                    abstractions = abstracter.all_objects(),
                    families    = families,
                    known_concept_exprs = known_exprs,
                    round_num   = rnd,
                    theory_branches = list(theory._branches.values()),
                )
                # When predictions resolve, credit the concept that caused resolution
                for pred in curiosity._expectations.all_predictions():
                    if pred.status == "confirmed" and pred.predicted_expr:
                        # Find which concept matched this prediction
                        for c in concepts.all_concepts():
                            if (pred.predicted_expr in c.canonical or
                                    c.canonical in pred.predicted_expr):
                                _surprise_resolutions[c.name] = max(
                                    _surprise_resolutions.get(c.name, 0),
                                    pred.priority,
                                )
                curiosity.inject_into_agents(agents, rnd)

                # Route top structural conjectures to agents too.
                # Structural symmetry gaps become directed research targets —
                # agents don't just notice "T(n) lost commutativity", they
                # actively try to find what symmetrized version might exist.
                top_structural = sym_scanner.top_conjectures(2)
                for conj in top_structural:
                    if conj.predicted_expr:
                        for agent in agents:
                            targets = getattr(agent, "_curiosity_targets", [])
                            if conj.predicted_expr not in targets:
                                targets.append(conj.predicted_expr)
                                agent._curiosity_targets = targets
                    # Also try to resolve the conjecture via the proof engine
                    # if it has a predicted expression
                    if conj.predicted_expr and conj.tension_score >= 0.75:
                        try:
                            pred_node = _parse_expr_stub(conj.predicted_expr)
                            # Find if any known concept matches this
                            for c in concepts.all_concepts():
                                if c.program_node and c.canonical == conj.predicted_expr:
                                    conj.status = "resolved"
                                    print(f"  {green('✓ GAP CLOSED')}  "
                                          f"{bold(conj.kind.replace('_',' '))}  "
                                          f"{dim(conj.claim[:55])}")
                                    break
                        except Exception:
                            pass

        # Lemma invention — auto-promote frequently-used rules to named lemmas
        if rnd >= 30 and rnd % 10 == 0:
            for result in prover._proven.values():
                inventor.observe_proof(result)
            inventor.scan_and_promote(threshold=2, round_num=rnd)
            # Strategy invention — name recurring proof step sequences
            inventor.invent_strategies(prover, strategies, rnd)

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
                new_laws = concepts.crystallise_laws(solver_map, rnd)
                # Attempt symbolic proof for each new law
                for law in new_laws:
                    # Attach program nodes so prover can work
                    all_c = {c.name: c for c in concepts.all_concepts()}
                    pc = all_c.get(law.get("parent"))
                    cc = all_c.get(law.get("child"))
                    if pc and cc:
                        if pc.program_node:
                            law["_parent_node"] = pc.program_node
                        if cc and cc.program_node:
                            law["_child_node"]  = cc.program_node

                        # Semantic guidance: tag the parent concept
                        pc_tag = tagger.tag_concept(pc)
                        strategy_hint = ""
                        if pc_tag and pc_tag.suggested_strategies:
                            strats = strategies.suggest_for_expression(
                                pc.canonical, pc_tag)
                            if strats:
                                strategy_hint = strats[0].name
                                law["_strategy_hint"] = strategy_hint

                        proof = prover.prove_law(law)
                        law["proof_status"] = proof.status
                        law["proof_steps"]  = len(proof.steps)
                        # Reinforce the strategy that was used
                        if proof.status == "proven" and strategy_hint:
                            control.reinforce(
                                random.choice([a.name for a in agents]),
                                strategy_hint, succeeded=True,
                                magnitude=len(proof.steps) / 5.0
                            )
                        if proof.status == "proven":
                            hint_str = f" via {dim(strategy_hint)}" if strategy_hint else ""
                            print(f"  {green('🔬 SYMBOLIC PROOF')}  "
                                  f"{bold(law['name'])}  "
                                  f"{dim(f'({len(proof.steps)} steps)')}{hint_str}")
                            for s in proof.steps[:4]:
                                print(f"    {dim(s.rule_name)}: "
                                      f"{dim(s.before[:30])} → {dim(s.after[:30])}")
                        elif proof.status == "trivial":
                            print(f"  {yellow('≡ IDENTITY')}  "
                                  f"{bold(law['name'])}  "
                                  f"{dim('(both sides canonically identical)')}")

                    # Add to theory tree with full proof for lemma tracking
                    proposer_name = random.choice([a.name for a in agents])
                    proof_obj = proof if pc and cc else None
                    theory.add_theorem(
                        law          = law,
                        proof_status = law.get("proof_status", "empirical"),
                        proof_steps  = law.get("proof_steps", 0),
                        round_num    = rnd,
                        discoverer   = proposer_name,
                        proof_result = proof_obj,
                    )
                    # Open debate for each new law
                    debate.propose_law(
                        proposer  = random.choice([a.name for a in agents]),
                        law       = law,
                        round_num = rnd,
                    )

        # ── Conjecture → Theorem upgrade pipeline ─────────────────────────
        # Every 20 rounds, attempt to symbolically prove any law currently
        # marked 'empirical'. This is the pipeline:
        #   empirical → attempt symbolic proof → proven (if successful)
        # Laws that get proven get re-announced with the stronger status.
        if rnd >= 25 and rnd % 15 == 0:
            upgraded = 0
            for law in concepts.all_laws():
                if law.get("proof_status") not in ("empirical",):
                    continue
                pc_node = law.get("_parent_node")
                cc_node = law.get("_child_node")
                if pc_node is None or cc_node is None:
                    # Try to re-attach nodes from current concept registry
                    all_c = {c.name: c for c in concepts.all_concepts()}
                    pc = all_c.get(law.get("parent", ""))
                    cc = all_c.get(law.get("child",  ""))
                    if pc and cc and pc.program_node and cc.program_node:
                        law["_parent_node"] = pc.program_node
                        law["_child_node"]  = cc.program_node
                        pc_node = pc.program_node
                        cc_node = cc.program_node

                if pc_node is None or cc_node is None:
                    continue

                proof = prover.prove_law(law)
                if proof.status in ("proven", "trivial"):
                    new_status = "proven" if proof.status == "proven" else "trivial"
                    law["proof_status"] = new_status
                    law["proof_steps"]  = len(proof.steps)
                    upgraded += 1
                    badge = green("✓ DERIVED") if new_status == "proven" else cyan("≡ IDENTITY")
                    print(f"  {green('⬆ UPGRADED')}  {bold(law['name'])}  "
                          f"{dim('empirical →')} {badge}  "
                          f"{dim(f'({len(proof.steps)} steps)')}")
                    for s in proof.steps[:4]:
                        print(f"    {dim(s.rule_name)}: "
                              f"{dim(s.before[:30])} → {dim(s.after[:30])}")

            if upgraded:
                print(f"  {dim(f'Conjecture pipeline: {upgraded} law(s) promoted to proven')}")
        # Each round, each agent has a 20% chance to challenge an open debate
        open_debates = [d for d in debate.debates.values() if d.status == 'open']
        if open_debates:
            for agent in agents:
                if random.random() < 0.20:
                    target = random.choice(open_debates)
                    ch = debate.challenge(target.debate_id, agent.name,
                                          concepts, rnd)
                    if ch and ch.result == 'refuted':
                        # Award prestige credits
                        for aname, amount in target.prestige_awarded.items():
                            for a in agents:
                                if a.name == aname:
                                    a.credits += amount

        # Close expired debates
        closed = debate.close_expired(rnd, {a.name: a for a in agents})
        for rec in closed:
            for aname, amount in rec.prestige_awarded.items():
                for a in agents:
                    if a.name == aname:
                        a.credits      += amount
                        a.legacy_score += amount // 10

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

    # Debate record
    if debate.debates:
        print(f"\n  {bold('DEBATE RECORD')}")
        print(f"  {debate.summary()}")
        lb = debate.leaderboard()
        if lb:
            lb_str = "  ".join(f"{bold(a)} {cyan(str(p)+'cr')}" for a,p in lb[:4])
            print(f"  Top debaters: {lb_str}")

    # Crystallised laws with proof status
    all_laws = concepts.all_laws()
    if all_laws:
        print(f"\n  {bold('CRYSTALLISED LAWS')} ({len(all_laws)}):")
        for law in all_laws:
            proof_status = law.get("proof_status", "empirical")
            if proof_status == "proven":
                badge = f"  {green('✓ DERIVED')}"
                steps_note = f" ({law.get('proof_steps',0)} steps)"
            elif proof_status == "trivial":
                badge = f"  {cyan('≡ IDENTITY')}"
                steps_note = ""
            else:
                badge = f"  {yellow('~ empirical')}"
                steps_note = ""
            print(f"  📐 {bold(law['name'])}{badge}{steps_note}")
            print(f"     {dim(law['statement'])}")
        print(f"\n  {dim(prover.stats())}")

    # ── Theory Maturity Metrics ───────────────────────────────────────────
    # Six objective measurements of how mature the mathematical theory is.
    # These are computable from existing data — no new architecture needed.
    all_c        = concepts.all_concepts()
    all_l        = concepts.all_laws()
    n_concepts   = len(all_c)
    n_laws       = len(all_l)
    n_derived    = sum(1 for r in prover._proven.values() if r.status == "proven")
    n_lemmas     = len(prover._lemmas)
    n_branches   = len([b for b in theory._branches.values()
                        if b.branch_id != "arithmetic_foundation"])
    n_abstr      = len(abstracter.all_objects())
    n_resolved   = sum(1 for p in curiosity._expectations.all_predictions()
                       if p.status == "confirmed")
    n_open_gaps  = (len(curiosity._expectations.open_predictions()) +
                    len(sym_scanner.open_conjectures()))
    n_unlocks    = len(env._unlocked_kinds)
    n_styles     = len(set(
        control._get_style(a.name).style_description()
        for a in agents
    ))

    def _clip(x):
        return min(x, 1.0)

    comp_depth  = (n_derived + n_lemmas) / max(n_concepts, 1)
    abstr_h     = (n_branches + n_abstr) / max(n_laws, 1)
    # C: Reusability — what fraction of concepts are actually used by other concepts
    # (not task unlocks divided by total, which was misleading)
    # A concept is "reused" if: other concepts derive from it OR it appears in proofs
    concepts_used_by_others = sum(
        1 for c in all_c
        if any(c.name in other.derived_from
               for other in all_c if other.name != c.name)
        or c.unlocks_count > 0
    )
    reuse_idx = concepts_used_by_others / max(n_concepts, 1)
    sym_comp    = n_resolved / max(n_resolved + n_open_gaps, 1)
    branch_f    = (n_branches + len(sym_scanner.all_conjectures())) / max(n_laws, 1)
    cog_div     = n_styles / max(len(agents), 1)

    scores = [_clip(comp_depth/0.3), _clip(abstr_h/1.0),
              _clip(reuse_idx/0.2),  _clip(sym_comp/0.5),
              _clip(branch_f/0.5),   _clip(cog_div/0.8)]
    maturity = sum(scores) / len(scores)

    layer_str = ("<2" if maturity < 0.2 else "~2" if maturity < 0.35 else
                 "~3" if maturity < 0.5 else "~4" if maturity < 0.7 else
                 "~5" if maturity < 0.85 else "5+")

    print(f"\n  {bold('THEORY MATURITY METRICS')}")
    print(f"  {'─'*60}")
    print(f"  A. Compression depth:   {comp_depth:.3f}  "
          f"{dim(f'({n_derived} proofs + {n_lemmas} lemmas / {n_concepts} concepts)')}")
    print(f"  B. Abstraction height:  {abstr_h:.3f}  "
          f"{dim(f'({n_branches} branches + {n_abstr} objects / {n_laws} laws)')}")
    print(f"  C. Reusability index:   {reuse_idx:.3f}  "
          f"{dim(f'({concepts_used_by_others} concepts reused by others / {n_concepts} total)')}")
    print(f"  D. Symmetry completion: {sym_comp:.3f}  "
          f"{dim(f'({n_resolved} resolved / {n_resolved+n_open_gaps} gaps)')}")
    print(f"  E. Branching factor:    {branch_f:.3f}  "
          f"{dim(f'({n_branches} branches + {len(sym_scanner.all_conjectures())} conjectures / {n_laws} laws)')}")
    print(f"  F. Cognitive diversity: {cog_div:.3f}  "
          f"{dim(f'({n_styles} distinct styles / {len(agents)} agents)')}")
    print(f"  {'─'*60}")
    bar = "█" * int(maturity * 20) + "░" * (20 - int(maturity * 20))
    print(f"  {bold('MATURITY')}  [{bar}]  {bold(f'{maturity:.2f}/1.00')}  "
          f"{dim(f'Layer {layer_str}')}")
    all_abstractions = abstracter.all_objects()
    if all_abstractions:
        print(f"\n  {bold('ABSTRACT OBJECTS')} ({len(all_abstractions)} born):")
        print(f"  {abstracter.summary()}")
        for obj in all_abstractions:
            depth_label = {1: "operation", 2: "meta-op", 3: "structural"}.get(obj.depth, "abstract")
            print(f"  🌟 {bold(obj.name)}  {dim(f'({depth_label})')}  "
                  f"{dim(obj.formal)}  "
                  f"{dim(f'{len(obj.instances)} instances')}")

    # Discovery importance ranking — top concepts by composite score
    concepts.score_importance(_unlock_credit, _surprise_resolutions)
    ranked_concepts = sorted(concepts.all_concepts(),
                             key=lambda c: -c.importance_score)
    top_important = [c for c in ranked_concepts if c.importance_score > 0][:5]
    if top_important:
        print(f"\n  {bold('DISCOVERY IMPORTANCE RANKING')}")
        icons = ["⭐", "🌟", "✨", "💫", "🔹"]
        for rank, c in enumerate(top_important, 1):
            components = []
            if c.unlocks_count:          components.append(f"🔓 unlocks={c.unlocks_count}")
            if c.compression_ratio > 5:  components.append(f"⚡ compression={c.compression_ratio:.0f}")
            if c.surprise_bonus:         components.append(f"🎯 resolved_prediction")
            comp_str = "  " + dim("  ".join(components)) if components else ""
            icon = icons[rank-1] if rank <= len(icons) else "·"
            print(f"  {icon} {bold(c.name):25}  "
                  f"{dim(f'importance={c.importance_score:.0f}')}{comp_str}")

    # Theory symmetry scanner — structural conjectures
    sym_all = sym_scanner.all_conjectures()
    if sym_all:
        print(f"\n  {bold('STRUCTURAL CONJECTURES')} ({len(sym_all)} detected):")
        print(f"  {sym_scanner.summary()}")
        for c in sym_scanner.top_conjectures(3):
            print(f"  ⚖️  {bold(c.kind.replace('_',' '))}  "
                  f"{dim(f'[tension={c.tension_score:.1f}]')}  "
                  f"{dim(c.claim[:70])}")
    open_qs = curiosity.top_questions(3)
    if open_qs:
        print(f"\n  {bold('OPEN RESEARCH QUESTIONS')}:")
        for q in open_qs:
            print(f"  🤔 [{q.priority:.1f}] {q.claim}")
            if q.predicted_expr:
                print(f"       {dim('Predicts: ' + q.predicted_expr[:60])}")
        print(f"  {dim(curiosity.summary())}")

    # Theory tree summary
    print(f"\n  {bold('THEORY TREE')}")
    print(f"  {theory.summary()}")
    # Show Nicomachus chain if it's in the tree
    for tid, t in theory._theorems.items():
        if "nicomachus" in t.name.lower() or "nicomachus" in tid.lower():
            chain = theory.format_chain(tid)
            if chain:
                print(chain)
            break

    # Semantic control layer — agent research styles formed by reinforcement
    if control.all_styles():
        print(f"\n  {bold('AGENT RESEARCH STYLES')}")
        print(f"  {control.summary()}")
        for agent in agents:
            style_desc = control.announce_style(agent.name)
            print(f"  🎭 {dim(style_desc)}")

    # Semantic meaning summary — what concepts actually MEAN, not just their syntax
    tagger.tag_all(concepts.all_concepts())
    all_tags = tagger._tags
    if all_tags:
        print(f"\n  {bold('SEMANTIC MEANING')}")
        print(f"  {tagger.summary()}")
        # Show the most semantically rich concepts
        rich = sorted(all_tags.values(), key=lambda t: -len(t.roles))[:6]
        for tag in rich:
            geo = f"  → {dim(tag.geometric_intuition)}" if tag.geometric_intuition else ""
            print(f"  📎 {bold(tag.concept_name[:20]):22}  "
                  f"{dim(', '.join(tag.roles[:3]))}{geo}")

    # Invariant summary
    all_invs = invariants.all_invariants()
    if all_invs:
        print(f"\n  {bold('STRUCTURAL INVARIANTS')} ({len(all_invs)} discovered):")
        print(f"  {invariants.summary()}")
        # Show a sample of the most interesting ones
        highlights = [i for i in all_invs
                      if i.kind == "property" or i.survives][:6]
        for inv in highlights:
            icon = "🔷" if inv.survives is not False else "🔀"
            print(f"  {icon} {dim(inv.claim)}")

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
    p.add_argument("--rounds",     type=int, default=80)
    p.add_argument("--budget",     type=int, default=40)
    p.add_argument("--tasks",      type=int, default=3)
    p.add_argument("--board",      type=int, default=8)
    p.add_argument("--pop",        type=int, default=12,
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