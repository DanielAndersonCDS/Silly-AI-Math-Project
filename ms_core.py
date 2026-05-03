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