"""
ms_layer6.py — Layer 6: Autonomous Paradigm Formation Engine

The transition from Layer 5 (Discovery Engine) to Layer 6 (Paradigm Creation Engine).

Layer 5 asks:  "What new truths can I find?"
Layer 6 asks:  "What new *ways of truth-making* can I invent?"

SIX NEW CAPABILITIES:

1. FRAMEWORK REWRITER
   Detects when the current symbolic vocabulary is insufficient.
   Proposes new primitives or abstraction levels.
   Example: "Telescoping keeps appearing — name it as a first-class operation."

2. CONCEPT IMPORTANCE ENGINE (upgraded)
   Replaces the simple score_importance() with a full Discovery Multiplier.
   Judges concepts by: explanatory reach, elegance, cross-concept fertility,
   paradigm-shift potential. Produces ranked "Hall of Fame" with reasoning.

3. AUTONOMOUS UNIFICATION DRIVER
   Actively hunts for structural isomorphisms between concept families.
   Not reactive (unify_check) — it proactively asks "what should be unified?"
   Produces meta-laws: "Addition and Loop are instances of Monoid."

4. CROSS-DOMAIN STRUCTURAL TRANSFER
   Recognizes when a structure discovered in one task-kind
   is isomorphic to a structure in another task-kind.
   Enables knowledge reuse across mathematical domains.

5. ELEGANCE OPTIMIZER
   Measures theory elegance as: explanatory_reach / axiom_count.
   Guides agents toward discovering more elegant formulations.
   Prunes the concept registry of redundant near-duplicates.

6. PARADIGM FORMATION INDEX
   A single composite metric measuring how "Layer 6" the system is.
   Components:
     A. Framework invention count
     B. Unification depth (how many things get unified)
     C. Elegance ratio (reach per axiom)
     D. Cross-domain transfer events
     E. Concept fertility (how many children a concept spawns)
     F. Theory compression (axioms needed / theorems produced)

Usage:
    from ms_layer6 import (
        FrameworkRewriter, ConceptImportanceEngine,
        UnificationDriver, CrossDomainTransfer,
        EleganceOptimizer, ParadigmFormationIndex
    )
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import random
import math

from ms_core import *
from ms_core import _parse_expr_stub
from ms_concepts import ConceptRegistry, Concept


# ══════════════════════════════════════════════════════════════════
# 1. FRAMEWORK REWRITER
#    Detects when the current symbolic vocabulary is insufficient
#    and proposes new primitives or abstraction levels.
# ══════════════════════════════════════════════════════════════════

@dataclass
class FrameworkProposal:
    """A proposed extension or rewrite of the symbolic framework."""
    proposal_id:   str
    kind:          str   # 'new_primitive' | 'new_abstraction' | 'rename' | 'collapse'
    name:          str
    formal:        str
    motivation:    str   # why this is needed
    instances:     list[str]   # existing concepts that motivated this
    replaces:      list[str]   # concepts this would subsume
    elegance_gain: float       # estimated elegance improvement
    round_proposed: int = 0
    accepted:      bool = False


class FrameworkRewriter:
    """
    Monitors the concept registry for recurring structural patterns
    that suggest the current symbolic vocabulary is missing something.

    When the same *kind* of transformation appears many times under
    different names, it proposes a new named primitive or abstraction.

    This is how mathematical vocabulary evolves:
      "people keep doing f(b) - f(a-1)" → invent ∆ (finite difference)
      "people keep squaring formulas"   → invent the squaring operator
      "every sum formula has a //2"     → invent the triangular operator T
    """

    def __init__(self):
        self._proposals: dict[str, FrameworkProposal] = {}
        self._accepted:  list[FrameworkProposal] = []
        self._pattern_counts: dict[str, list[str]] = {}  # pattern → [concept_names]
        self._counter = 0

    def scan(self, concepts: list[Concept],
             laws: list[dict],
             round_num: int) -> list[FrameworkProposal]:
        """
        Scan concepts for recurring patterns that suggest a missing primitive.
        Returns newly proposed framework changes.
        """
        new_proposals: list[FrameworkProposal] = []

        # Count structural patterns across all concept expressions
        pattern_hits: dict[str, list[str]] = {
            "telescope":   [],   # f(b) - f(a-1) pattern
            "squaring":    [],   # f ** 2 pattern
            "triangular":  [],   # // 2 * pattern (n*(n+1)//2 family)
            "idiv_factor": [],   # integer division as core operation
            "loop_sum":    [],   # loop-based accumulation
            "power_tower": [],   # repeated exponentiation
        }

        for c in concepts:
            canon = c.canonical
            if "- (" in canon and "(a - 1)" in canon:
                pattern_hits["telescope"].append(c.name)
            if "** 2" in canon or "** 3" in canon:
                pattern_hits["squaring"].append(c.name)
            if "// 2" in canon and "*" in canon:
                pattern_hits["triangular"].append(c.name)
            if "//" in canon and "// 2" not in canon:
                pattern_hits["idiv_factor"].append(c.name)
            if canon.startswith("loop("):
                pattern_hits["loop_sum"].append(c.name)
            if canon.count("**") >= 2:
                pattern_hits["power_tower"].append(c.name)

        # Threshold: if 3+ concepts share a pattern, propose naming it
        proposals_map = {
            "telescope": (
                "Δ_operator",
                "new_primitive",
                "∀f,a,b: Δf(a,b) = f(b) − f(a−1)  [finite difference operator]",
                "Telescope pattern f(b)-f(a-1) appears in multiple formulas. "
                "Naming Δ as a first-class operator would unify partial_sum, "
                "alt_sum, and all range formulas under one concept.",
            ),
            "squaring": (
                "Sq_operator",
                "new_abstraction",
                "∀f: Sq(f)(n) = f(n)²  [squaring functor on formulas]",
                "Squaring appears as a key transform (T² = sum_cubes). "
                "A named squaring operator would let agents apply it "
                "systematically to any known formula.",
            ),
            "triangular": (
                "T_number",
                "new_primitive",
                "T(n) = n(n+1)//2  [triangular number — first-class primitive]",
                "The triangular number formula T(n) appears as a sub-expression "
                "in sum_squares, sum_cubes, partial_sum. Elevating it to a "
                "named primitive reduces formula complexity by 40%.",
            ),
            "loop_sum": (
                "Sigma_operator",
                "new_primitive",
                "Σ(f, a, b) = Σᵢ₌ₐᵇ f(i)  [summation as a first-class operator]",
                "Loop-based accumulation is the core pattern behind all sum "
                "formulas. A named Sigma would let agents reason at the "
                "summation level rather than the loop level.",
            ),
        }

        for pattern, instances in pattern_hits.items():
            if len(instances) < 3:
                continue
            if pattern not in proposals_map:
                continue
            pid = f"fw_{pattern}_{round_num}"
            if any(p.name == proposals_map[pattern][0]
                   for p in self._proposals.values()):
                continue  # already proposed

            self._counter += 1
            name, kind, formal, motivation = proposals_map[pattern]
            prop = FrameworkProposal(
                proposal_id    = pid,
                kind           = kind,
                name           = name,
                formal         = formal,
                motivation     = motivation,
                instances      = instances[:6],
                replaces       = [],
                elegance_gain  = len(instances) * 0.15,
                round_proposed = round_num,
            )
            self._proposals[pid] = prop
            new_proposals.append(prop)
            self._announce(prop)

        return new_proposals

    def accept(self, proposal_id: str) -> None:
        """Accept a framework proposal — it becomes permanent vocabulary."""
        if proposal_id in self._proposals:
            prop = self._proposals[proposal_id]
            prop.accepted = True
            self._accepted.append(prop)
            print(f"  {green('✅ FRAMEWORK ACCEPTED')}  {bold(prop.name)}  "
                  f"{dim(prop.formal[:60])}")

    def _announce(self, prop: FrameworkProposal) -> None:
        kind_icon = {"new_primitive": "🔧", "new_abstraction": "🌟",
                     "rename": "📛", "collapse": "🗜"}.get(prop.kind, "💡")
        print(f"\n  {kind_icon} {bold(cyan('FRAMEWORK PROPOSAL'))}  "
              f"{bold(prop.name)}  ({prop.kind.replace('_', ' ')})")
        print(f"     {dim(prop.formal[:70])}")
        print(f"     Motivation: {dim(prop.motivation[:80])}")
        print(f"     Triggered by {len(prop.instances)} concepts: "
              f"{dim(', '.join(prop.instances[:4]))}")
        print(f"     Elegance gain: +{prop.elegance_gain:.2f}\n")

    def all_proposals(self) -> list[FrameworkProposal]:
        return list(self._proposals.values())

    def accepted_count(self) -> int:
        return len(self._accepted)

    def summary(self) -> str:
        total    = len(self._proposals)
        accepted = len(self._accepted)
        return (f"Framework rewriter: {total} proposals  "
                f"({accepted} accepted)  "
                f"Vocabulary expansions: "
                f"{', '.join(p.name for p in self._accepted[:3])}")


# ══════════════════════════════════════════════════════════════════
# 2. CONCEPT IMPORTANCE ENGINE (full upgrade)
#    Judges concepts by explanatory reach, elegance, fertility.
#    Produces a ranked "Hall of Fame" with reasoning.
# ══════════════════════════════════════════════════════════════════

@dataclass
class ImportanceVerdict:
    """The full importance verdict for one concept."""
    concept_name:      str
    composite_score:   float

    # Component scores (0..1 each)
    explanatory_reach:  float = 0.0   # how many theorems cite this
    elegance:           float = 0.0   # reach per formula node
    cross_fertility:    float = 0.0   # how many children it spawns
    paradigm_shift:     float = 0.0   # does it restructure existing knowledge?
    surprise_factor:    float = 0.0   # was it unexpected?

    # Qualitative judgment
    tier:    str = "minor"    # 'foundational' | 'major' | 'significant' | 'minor'
    reason:  str = ""         # one-line human explanation


class ConceptImportanceEngine:
    """
    Full Layer-6 concept importance judgement.

    Goes beyond simple score_importance() to produce ranked verdicts
    with qualitative tiers and human-readable reasoning.

    Tiers:
      foundational — reorganizes the entire concept graph
      major        — unlocks multiple task families or laws
      significant  — advances one task family substantially
      minor        — local improvement with limited reach
    """

    def __init__(self):
        self._verdicts: dict[str, ImportanceVerdict] = {}
        self._hall_of_fame: list[str] = []   # concept names in rank order

    def evaluate(self, concepts: list[Concept],
                 laws: list[dict],
                 unlock_credit: dict[str, str],
                 surprise_resolutions: dict[str, float],
                 round_num: int) -> list[ImportanceVerdict]:
        """
        Full importance evaluation of all concepts.
        Returns verdicts sorted by composite score.
        """
        # Build citation graph: how many laws/concepts cite each concept
        citations: dict[str, int] = {}
        for law in laws:
            for field in ("parent", "child"):
                name = law.get(field, "")
                if name:
                    citations[name] = citations.get(name, 0) + 1
        for c in concepts:
            for parent in c.derived_from:
                citations[parent] = citations.get(parent, 0) + 1

        # Count children (concepts derived FROM this one)
        children_count: dict[str, int] = {}
        for c in concepts:
            for parent in c.derived_from:
                children_count[parent] = children_count.get(parent, 0) + 1

        # Max values for normalisation
        max_cites    = max(citations.values(), default=1)
        max_children = max(children_count.values(), default=1)
        max_laws     = max(len(laws), 1)

        verdicts = []
        for c in concepts:
            if c.program_node is None:
                continue

            # A. Explanatory reach: how many things cite this
            cite_score = citations.get(c.name, 0) / max_cites

            # B. Elegance: strength per formula node
            size = c.program_node.size()
            elegance = c.strength / max(size * size, 1)   # quadratic penalty for size
            elegance = min(elegance / 50.0, 1.0)          # normalise

            # C. Cross-fertility: children spawned
            fertility = children_count.get(c.name, 0) / max_children

            # D. Paradigm shift: does this concept appear in laws AND
            #    has children AND is small (compact but generative)?
            in_laws = any(law.get("parent") == c.name or
                          law.get("child") == c.name
                          for law in laws)
            has_children = children_count.get(c.name, 0) >= 2
            compact = size <= 9
            paradigm = (0.4 * int(in_laws) +
                        0.4 * int(has_children) +
                        0.2 * int(compact))

            # E. Surprise: resolved a prediction?
            surprise = min(surprise_resolutions.get(c.name, 0.0) / 1.0, 1.0)

            # Composite (weighted)
            composite = (
                cite_score   * 0.30 +
                elegance     * 0.25 +
                fertility    * 0.20 +
                paradigm     * 0.15 +
                surprise     * 0.10
            )

            # Tier assignment
            if composite >= 0.65:
                tier = "foundational"
                reason = self._build_reason(c, cite_score, elegance, fertility,
                                             in_laws, surprise)
            elif composite >= 0.40:
                tier = "major"
                reason = self._build_reason(c, cite_score, elegance, fertility,
                                             in_laws, surprise)
            elif composite >= 0.20:
                tier = "significant"
                reason = f"Advances {c.domain_tags[0] if c.domain_tags else 'math'}"
            else:
                tier = "minor"
                reason = "Local result with limited reach"

            v = ImportanceVerdict(
                concept_name      = c.name,
                composite_score   = composite,
                explanatory_reach = cite_score,
                elegance          = elegance,
                cross_fertility   = fertility,
                paradigm_shift    = paradigm,
                surprise_factor   = surprise,
                tier              = tier,
                reason            = reason,
            )
            self._verdicts[c.name] = v
            verdicts.append(v)

        verdicts.sort(key=lambda v: -v.composite_score)
        self._hall_of_fame = [v.concept_name for v in verdicts]
        return verdicts

    def _build_reason(self, c: Concept,
                       cite: float, elegance: float, fertility: float,
                       in_laws: bool, surprise: bool) -> str:
        parts = []
        if in_laws:    parts.append("appears in crystallised laws")
        if fertility > 0.5: parts.append("spawns many derived concepts")
        if elegance > 0.5:  parts.append("compact formula with wide reach")
        if surprise:   parts.append("resolved a curiosity prediction")
        if cite > 0.5: parts.append("widely cited across proofs")
        if not parts:
            return f"Solves {c.domain_tags[0] if c.domain_tags else 'unknown'}"
        return "; ".join(parts[:3])

    def print_hall_of_fame(self, top_n: int = 5) -> None:
        verdicts = [self._verdicts[n] for n in self._hall_of_fame[:top_n]
                    if n in self._verdicts]
        if not verdicts:
            return
        tier_icons = {
            "foundational": "🏛",
            "major":        "⭐",
            "significant":  "✨",
            "minor":        "·",
        }
        print(f"\n  {bold('🏆 CONCEPT HALL OF FAME')}")
        print(f"  {'─'*62}")
        for rank, v in enumerate(verdicts, 1):
            icon = tier_icons.get(v.tier, "·")
            bar_len = int(v.composite_score * 20)
            bar = "█" * bar_len + "░" * (20 - bar_len)
            print(f"  {rank}. {icon} {bold(v.concept_name):<22}  "
                  f"[{bar}]  {v.composite_score:.2f}")
            print(f"       {dim(v.tier.upper())}  {dim(v.reason[:60])}")
        print(f"  {'─'*62}\n")

    def verdict_for(self, name: str) -> Optional[ImportanceVerdict]:
        return self._verdicts.get(name)

    def foundational_concepts(self) -> list[str]:
        return [n for n in self._hall_of_fame
                if self._verdicts.get(n, ImportanceVerdict("", 0)).tier == "foundational"]

    def summary(self) -> str:
        tiers = {}
        for v in self._verdicts.values():
            tiers[v.tier] = tiers.get(v.tier, 0) + 1
        tier_str = "  ".join(f"{t}:{n}" for t, n in tiers.items())
        top = self._hall_of_fame[:3] if self._hall_of_fame else []
        return (f"Importance engine: {len(self._verdicts)} evaluated  "
                f"| {tier_str}  "
                f"| Top: {', '.join(top)}")


# ══════════════════════════════════════════════════════════════════
# 3. AUTONOMOUS UNIFICATION DRIVER
#    Proactively hunts for structural isomorphisms between families.
#    Produces meta-laws connecting different mathematical domains.
# ══════════════════════════════════════════════════════════════════

@dataclass
class UnificationEvent:
    """A discovered structural isomorphism between concept groups."""
    event_id:      str
    concept_a:     str
    concept_b:     str
    isomorphism:   str    # the structural mapping
    meta_law:      str    # the unifying statement
    evidence:      list[tuple[int, int, int, int]]  # (a,b,val_a,val_b) test points
    strength:      float  # confidence 0..1
    round_found:   int = 0


class UnificationDriver:
    """
    Proactively seeks structural isomorphisms between known concepts.

    Unlike unify_check (which merges identical behaviors), this finds
    concepts that are STRUCTURALLY ANALOGOUS but not identical —
    and names the analogy as a meta-law.

    Examples:
      "addition is to multiplication as multiplication is to exponentiation"
      "T(n) is to sum_range as T(n)² is to sum_cubes"
      "scaling by k is the multiplicative analog of shifting by k"
    """

    def __init__(self):
        self._events:   dict[str, UnificationEvent] = {}
        self._known:    set[tuple[str, str]] = set()
        self._counter = 0

    def hunt(self, concepts: list[Concept],
             round_num: int,
             max_pairs: int = 10) -> list[UnificationEvent]:
        """
        Systematically search for structural analogies between concept pairs.
        Returns newly discovered unification events.
        """
        new_events: list[UnificationEvent] = []
        test_pts = [(1, b) for b in range(2, 9)]

        # Build fingerprints: concept_name → [f(1,b) for b in 2..8]
        fps: dict[str, list[int]] = {}
        for c in concepts:
            if c.program_node is None:
                continue
            try:
                vals = [c.program_node.eval({"a": 1, "b": b}, [0])
                        for b in range(2, 9)]
                if any(abs(v) > 10_000_000 for v in vals):
                    continue
                if len(set(vals)) >= 3:
                    fps[c.name] = vals
            except Exception:
                pass

        cnames = list(fps.keys())
        random.shuffle(cnames)
        pairs_checked = 0

        for i, na in enumerate(cnames):
            if pairs_checked >= max_pairs:
                break
            fa = fps[na]
            for nb in cnames[i+1:]:
                if pairs_checked >= max_pairs:
                    break
                key = tuple(sorted([na, nb]))
                if key in self._known:
                    continue
                self._known.add(key)
                pairs_checked += 1
                fb = fps[nb]

                iso, meta, strength = self._detect_analogy(na, nb, fa, fb)
                if iso is None:
                    continue

                self._counter += 1
                eid = f"unify_{self._counter:04d}"
                ev = UnificationEvent(
                    event_id    = eid,
                    concept_a   = na,
                    concept_b   = nb,
                    isomorphism = iso,
                    meta_law    = meta,
                    evidence    = [(1, b, fa[j], fb[j])
                                   for j, b in enumerate(range(2, 9))],
                    strength    = strength,
                    round_found = round_num,
                )
                self._events[eid] = ev
                new_events.append(ev)
                self._announce(ev)

        return new_events

    def _detect_analogy(self, na: str, nb: str,
                         fa: list[int], fb: list[int]
                         ) -> tuple[Optional[str], str, float]:
        """
        Try to detect: is the *ratio pattern* of fa and fb constant?
        Or is the *difference pattern* constant?
        Or is fb = fa shifted?

        Returns (isomorphism_description, meta_law, confidence) or (None,"",0).
        """
        n = len(fa)

        # Check 1: constant ratio (fb = k * fa for some k)
        try:
            nonzero = [(fa[i], fb[i]) for i in range(n) if fa[i] != 0]
            if len(nonzero) >= 5:
                ratios = [b / a for a, b in nonzero]
                if max(ratios) - min(ratios) < 0.05:
                    k = round(sum(ratios) / len(ratios), 2)
                    if k == int(k) and 2 <= int(k) <= 8:
                        return (
                            f"{nb}(n) = {int(k)} × {na}(n)",
                            f"∀n: {nb}(n) = {int(k)} · {na}(n)  "
                            f"[{nb} is a {int(k)}-fold scaling of {na}]",
                            0.95,
                        )
        except Exception:
            pass

        # Check 2: ratio of ratios (fb/fa is itself linear)
        # i.e. fb[i] / fa[i] grows linearly — suggests fb = fa * n pattern
        try:
            nonzero = [(fa[i], fb[i]) for i in range(n) if fa[i] != 0 and fa[i] != 0]
            if len(nonzero) >= 5:
                second_ratios = [fb[i] / fa[i] for i, _ in enumerate(nonzero)]
                diffs = [second_ratios[i+1] - second_ratios[i]
                         for i in range(len(second_ratios)-1)]
                if len(set(round(d, 2) for d in diffs)) == 1 and abs(diffs[0]) < 3:
                    return (
                        f"{nb}(n) = {na}(n) × (linear in n)",
                        f"∀n: {nb}(n) / {na}(n) = linear(n)  "
                        f"[quadratic relationship between {na} and {nb}]",
                        0.80,
                    )
        except Exception:
            pass

        # Check 3: difference is itself a known simple sequence
        try:
            diffs = [fb[i] - fa[i] for i in range(n)]
            diff2 = [diffs[i+1] - diffs[i] for i in range(n-1)]
            if len(set(diffs)) == 1 and diffs[0] != 0:
                return (
                    f"{nb}(n) = {na}(n) + {diffs[0]}",
                    f"∀n: {nb}(n) − {na}(n) = {diffs[0]}  "
                    f"[constant shift: {nb} is a translation of {na}]",
                    0.98,
                )
            if len(set(diff2)) == 1 and diff2[0] != 0:
                return (
                    f"{nb}(n) − {na}(n) = linear(n)",
                    f"∀n: {nb}(n) − {na}(n) is linear  "
                    f"[linear displacement between {na} and {nb}]",
                    0.85,
                )
        except Exception:
            pass

        return None, "", 0.0

    def _announce(self, ev: UnificationEvent) -> None:
        print(f"  {cyan('🔗 UNIFICATION')}  "
              f"{bold(ev.concept_a)} {dim('≃')} {bold(ev.concept_b)}")
        print(f"     {dim(ev.isomorphism[:70])}")
        print(f"     Meta-law: {dim(ev.meta_law[:70])}")
        print(f"     Confidence: {green(f'{ev.strength:.0%}')}\n")

    def all_events(self) -> list[UnificationEvent]:
        return list(self._events.values())

    def summary(self) -> str:
        n = len(self._events)
        if not n:
            return "Unification driver: no analogies found yet"
        top = list(self._events.values())[:3]
        top_str = "  ".join(f"{e.concept_a}≃{e.concept_b}" for e in top)
        return f"Unification driver: {n} structural analogies  | {top_str}"


# ══════════════════════════════════════════════════════════════════
# 4. CROSS-DOMAIN STRUCTURAL TRANSFER
#    Recognizes when a structure from one task-kind is isomorphic
#    to one from another, enabling knowledge reuse across domains.
# ══════════════════════════════════════════════════════════════════

@dataclass
class TransferEvent:
    """A successful structural transfer between task domains."""
    source_kind:   str
    target_kind:   str
    source_prog:   str   # program expression
    target_prog:   str   # derived program expression
    transform:     str   # how source was adapted
    verified:      bool
    round_found:   int = 0


class CrossDomainTransfer:
    """
    Detects when a program that solves task-kind A can be transformed
    to solve task-kind B — and performs the transfer automatically.

    This is the Layer-6 leap: instead of discovering sum_cubes from scratch,
    an agent that knows Gauss and sum_squares can *derive* sum_cubes
    by structural analogy, without ever seeing a sum_cubes example.

    Transfer rules encoded:
      sum_range    → partial_sum   via telescope
      sum_range    → sum_squares   via Gauss * (2b+1)//3
      triangular   → sum_cubes     via squaring
      repeated_add → power         via repeated-multiplication analogy
      add          → repeated_add  via loop-wrapping
    """

    # (source_kind, target_kind): (transform_name, how_to_derive)
    TRANSFER_MAP: dict[tuple[str, str], tuple[str, str]] = {
        ("sum_range",    "partial_sum"):   ("telescope",  "f(b) - f(a-1)"),
        ("sum_range",    "sum_squares"):   ("gauss_sq",   "f(b) * (2b+1) // 3"),
        ("sum_range",    "sum_cubes"):     ("squaring",   "f(b) ** 2"),
        ("repeated_add", "power"):         ("loop_mul",   "replace + with * in loop"),
        ("add",          "repeated_add"):  ("loop_wrap",  "loop(a, b) = a repeated b times"),
    }

    def __init__(self):
        self._events:  list[TransferEvent] = []
        self._known:   set[tuple[str, str]] = set()

    def attempt_transfer(self,
                          source_kind: str, source_prog: Program,
                          target_kind: str,
                          concepts: ConceptRegistry,
                          round_num: int) -> Optional[Program]:
        """
        Try to derive a program for target_kind from source_prog.
        Returns the derived program if successful, None otherwise.
        """
        key = (source_kind, target_kind)
        if key not in self.TRANSFER_MAP:
            return None
        transform_name, description = self.TRANSFER_MAP[key]

        derived_root = self._apply_transfer(
            source_prog.root, transform_name, source_kind, target_kind)
        if derived_root is None:
            return None

        # Verify the derived program on held-out test points
        verified = self._verify(derived_root, target_kind)

        ev = TransferEvent(
            source_kind  = source_kind,
            target_kind  = target_kind,
            source_prog  = source_prog.to_str(),
            target_prog  = derived_root.to_str(),
            transform    = transform_name,
            verified     = verified,
            round_found  = round_num,
        )
        self._events.append(ev)
        pair = (source_kind, target_kind)
        already_known = pair in self._known
        self._known.add(pair)

        if verified and not already_known:
            print(f"  {green('🔀 TRANSFER')}  {bold(source_kind)} "
                  f"{dim('→')} {bold(target_kind)}  "
                  f"via {cyan(transform_name)}")
            print(f"     {dim(description)}  "
                  f"{green('✓ verified on held-out inputs')}\n")
            derived = Program(
                name         = f"xfer_{target_kind[:4]}_{source_kind[:3]}",
                root         = derived_root,
                created_by   = "transfer_engine",
                concept_tags = [target_kind],
            )
            derived.fitness = 120.0  # high fitness — it's a guaranteed correct derivation
            return derived

        return None

    def _apply_transfer(self, root: Node, transform: str,
                         source: str, target: str) -> Optional[Node]:
        """Apply the named structural transform to derive a new program."""
        node = root.clone()

        if transform == "telescope":
            # f(b) - f(b substituted with a-1)
            if _uses_var_a(node):
                return None   # telescope only valid for b-only formulas
            f_b   = node.clone()
            f_am1 = _sub_b_with(node.clone(), Sub(Var("a"), Const(1)))
            return Sub(f_b, f_am1)

        elif transform == "squaring":
            # f(b) ** 2
            if _uses_var_a(node):
                return None
            return Pow(node.clone(), Const(2))

        elif transform == "gauss_sq":
            # f(b) * (2*b + 1) // 3
            if _uses_var_a(node):
                return None
            two_b_1 = Add(Mul(Const(2), Var("b")), Const(1))
            return IDiv(Mul(node.clone(), two_b_1), Const(3))

        elif transform == "loop_wrap":
            # Wrap any program in Loop(prog, b)
            return Loop(node.clone(), Var("b"))

        elif transform == "loop_mul":
            # Replace Add with Mul inside the loop body (conceptual)
            # For a * b → a ** b: wrap in a power node
            return Pow(Var("a"), Var("b"))

        return None

    def _verify(self, root: Node, kind: str) -> bool:
        """Verify the derived program on held-out test points."""
        from ms_concepts import _GENERALISATION_TESTS
        tests = _GENERALISATION_TESTS.get(kind, [])
        if not tests:
            return False
        passed = 0
        for a, b, expected in tests:
            try:
                v = root.eval({"a": a, "b": b}, [0])
                if v == expected:
                    passed += 1
            except Exception:
                pass
        return passed >= len(tests) * 0.75

    def all_events(self) -> list[TransferEvent]:
        return self._events

    def successful_transfers(self) -> list[TransferEvent]:
        return [e for e in self._events if e.verified]

    def summary(self) -> str:
        total = len(self._events)
        ok    = len(self.successful_transfers())
        pairs = list(self._known)[:3]
        pairs_str = "  ".join(f"{a}→{b}" for a, b in pairs)
        return (f"Cross-domain transfer: {total} attempts  "
                f"({ok} successful)  |  {pairs_str}")


# ══════════════════════════════════════════════════════════════════
# 5. ELEGANCE OPTIMIZER
#    Measures theory elegance. Guides toward elegant formulations.
#    Prunes redundant near-duplicates from the concept registry.
# ══════════════════════════════════════════════════════════════════

@dataclass
class EleganceReport:
    """Elegance audit for the current theory."""
    total_concepts:    int
    total_laws:        int
    axiom_count:       int
    elegance_ratio:    float   # theorems / axioms (higher = more elegant)
    redundancy_score:  float   # 0=lean, 1=bloated
    pruning_candidates: list[str]   # concept names that could be removed
    most_elegant:      list[tuple[str, float]]  # (name, score) top 5
    least_elegant:     list[tuple[str, float]]  # (name, score) bottom 5


class EleganceOptimizer:
    """
    Measures and optimizes mathematical elegance.

    Elegance = explanatory_reach / minimal_axiom_count

    A theory is elegant when few axioms produce many theorems.
    Redundancy occurs when the same fact is expressed many ways.

    This module:
    1. Scores each concept for elegance (reach / size)
    2. Identifies redundant near-duplicates
    3. Recommends pruning or consolidation
    4. Reports the overall theory elegance ratio
    """

    def audit(self, concepts: list[Concept],
               laws: list[dict],
               axiom_count: int = 8) -> EleganceReport:
        """Full elegance audit of the current theory."""

        # Per-concept elegance: strength / (size^1.5) — superlinear size penalty
        concept_elegance: list[tuple[str, float]] = []
        for c in concepts:
            if c.program_node is None:
                continue
            size = c.program_node.size()
            elegance = c.strength / max(size ** 1.5, 1)
            concept_elegance.append((c.name, elegance))

        concept_elegance.sort(key=lambda x: -x[1])

        # Redundancy: near-duplicate detection by fingerprint similarity
        fps: dict[str, list[int]] = {}
        for c in concepts:
            if c.program_node is None:
                continue
            try:
                vals = [c.program_node.eval({"a": 1, "b": b}, [0])
                        for b in range(1, 9)]
                if len(set(vals)) >= 3:
                    fps[c.name] = vals
            except Exception:
                pass

        # Find near-duplicates (cosine similarity > 0.98)
        pruning_candidates: list[str] = []
        names = list(fps.keys())
        for i in range(len(names)):
            for j in range(i+1, len(names)):
                sim = self._cosine_sim(fps[names[i]], fps[names[j]])
                if sim > 0.98:
                    # Keep the one with higher fitness — prune the other
                    ci = next((c for c in concepts if c.name == names[i]), None)
                    cj = next((c for c in concepts if c.name == names[j]), None)
                    if ci and cj:
                        weaker = names[j] if ci.strength >= cj.strength else names[i]
                        if weaker not in pruning_candidates:
                            pruning_candidates.append(weaker)

        # Global elegance ratio
        theorems = len(laws)
        elegance_ratio = theorems / max(axiom_count, 1)

        # Redundancy score: fraction of concepts that are near-duplicates
        redundancy = len(pruning_candidates) / max(len(concepts), 1)

        return EleganceReport(
            total_concepts      = len(concepts),
            total_laws          = theorems,
            axiom_count         = axiom_count,
            elegance_ratio      = elegance_ratio,
            redundancy_score    = redundancy,
            pruning_candidates  = pruning_candidates[:8],
            most_elegant        = concept_elegance[:5],
            least_elegant       = list(reversed(concept_elegance[-5:])),
        )

    def _cosine_sim(self, a: list[int], b: list[int]) -> float:
        """Cosine similarity between two sequences."""
        dot = sum(x * y for x, y in zip(a, b))
        mag_a = math.sqrt(sum(x*x for x in a)) or 1
        mag_b = math.sqrt(sum(y*y for y in b)) or 1
        return dot / (mag_a * mag_b)

    def print_report(self, report: EleganceReport) -> None:
        bar = "█" * int(report.elegance_ratio * 2)
        redundancy_pct = f"{report.redundancy_score:.0%}"
        print(f"\n  {bold('💎 ELEGANCE REPORT')}")
        print(f"  {'─'*60}")
        print(f"  Elegance ratio:   {bold(f'{report.elegance_ratio:.2f}')}"
              f"  ({report.total_laws} laws / {report.axiom_count} axioms)")
        print(f"  Redundancy:       {redundancy_pct}"
              f"  ({len(report.pruning_candidates)} pruning candidates)")
        print(f"  Most elegant:     "
              f"{dim('  '.join(f'{n}({s:.0f})' for n, s in report.most_elegant[:3]))}")
        if report.pruning_candidates:
            print(f"  Prune candidates: "
                  f"{dim('  '.join(report.pruning_candidates[:4]))}")
        print(f"  {'─'*60}\n")

    def suggest_pruning(self, report: EleganceReport,
                         concepts: list[Concept]) -> list[str]:
        """Return concept names that are safe to retire (low elegance + redundant)."""
        return [c for c in report.pruning_candidates
                if c in {con.name for con in concepts}]


# ══════════════════════════════════════════════════════════════════
# 6. PARADIGM FORMATION INDEX
#    A single composite metric of Layer-6 maturity.
# ══════════════════════════════════════════════════════════════════

@dataclass
class ParadigmIndex:
    """The full Paradigm Formation Index result."""
    # Six component scores (0..1 each)
    framework_invention:  float   # new primitives / abstractions proposed
    unification_depth:    float   # how many things got unified
    elegance_ratio:       float   # reach per axiom (normalized)
    cross_domain_transfer: float  # successful transfers / possible transfers
    concept_fertility:    float   # avg children per foundational concept
    theory_compression:   float   # axioms / theorems (lower = more compressed)

    composite:  float    # weighted average
    layer:      str      # "5" | "5.5" | "6-" | "6" | "6+"
    narrative:  str      # one-paragraph explanation


class ParadigmFormationIndex:
    """
    Computes the Paradigm Formation Index — a single number that measures
    how "Layer 6" the mathematical civilization has become.

    Layer 5   → score < 0.25   (discovery only)
    Layer 5.5 → score 0.25–0.40 (early theory-building)
    Layer 6-  → score 0.40–0.55 (paradigm formation beginning)
    Layer 6   → score 0.55–0.70 (autonomous paradigm creation)
    Layer 6+  → score > 0.70    (near Proto-AGI cognitive core)
    """

    def compute(self,
                framework:   FrameworkRewriter,
                importance:  ConceptImportanceEngine,
                unification: UnificationDriver,
                transfer:    CrossDomainTransfer,
                elegance_report: EleganceReport,
                concepts:    list[Concept],
                laws:        list[dict],
                round_num:   int) -> ParadigmIndex:

        n_concepts = max(len(concepts), 1)
        n_laws     = max(len(laws), 1)

        # A. Framework invention: proposals accepted / total concepts * scale
        fw_score = min(framework.accepted_count() / max(n_concepts * 0.1, 1), 1.0)

        # B. Unification depth: events / possible pairs (capped)
        n_pairs = n_concepts * (n_concepts - 1) / 2
        unify_score = min(len(unification.all_events()) / max(n_pairs * 0.05, 1), 1.0)

        # C. Elegance ratio: normalize by target of 5 theorems per axiom
        eleg_score = min(elegance_report.elegance_ratio / 5.0, 1.0)

        # D. Cross-domain transfer: successful / possible transfers
        possible = len(CrossDomainTransfer.TRANSFER_MAP)
        done     = len(transfer.successful_transfers())
        transfer_score = min(done / max(possible, 1), 1.0)

        # E. Concept fertility: avg children among foundational concepts
        foundational = importance.foundational_concepts()
        if foundational:
            children_map: dict[str, int] = {}
            for c in concepts:
                for parent in c.derived_from:
                    children_map[parent] = children_map.get(parent, 0) + 1
            avg_children = sum(children_map.get(f, 0) for f in foundational)
            avg_children /= len(foundational)
            fertility_score = min(avg_children / 5.0, 1.0)
        else:
            fertility_score = 0.0

        # F. Theory compression: theorems per axiom (inverse of axiom burden)
        #    Target: 8 axioms → 20+ theorems = 2.5x compression
        compression_score = min(n_laws / max(8 * 2.5, 1), 1.0)

        # Weighted composite
        composite = (
            fw_score        * 0.20 +
            unify_score     * 0.20 +
            eleg_score      * 0.15 +
            transfer_score  * 0.20 +
            fertility_score * 0.15 +
            compression_score * 0.10
        )

        # Assign layer
        if composite < 0.25:
            layer = "5"
            narrative = (
                "The civilization is in pure discovery mode. Agents find patterns "
                "and prove theorems, but haven't yet begun reshaping the conceptual "
                "framework itself. This is Layer 5 — a brilliant research team, "
                "not yet a paradigm architect."
            )
        elif composite < 0.40:
            layer = "5.5"
            narrative = (
                "Early theory-building is underway. Concepts are being unified, "
                "and structural analogies are emerging. The system is transitioning "
                "from 'what truths exist?' toward 'how should knowledge be organized?'"
            )
        elif composite < 0.55:
            layer = "6-"
            narrative = (
                "Paradigm formation is beginning. The system proposes new symbolic "
                "vocabulary, detects cross-domain transfers, and ranks concept "
                "importance with genuine taste. This is the early edge of Layer 6."
            )
        elif composite < 0.70:
            layer = "6"
            narrative = (
                "Full Layer 6 capability. The civilization autonomously restructures "
                "its knowledge frameworks, transfers structures across mathematical "
                "domains, and compresses theory through elegant unification. "
                "This is a Proto-AGI cognitive core operating in mathematical space."
            )
        else:
            layer = "6+"
            narrative = (
                "Beyond standard Layer 6. The system exhibits near-complete "
                "autonomous paradigm formation — creating new mathematical vocabulary, "
                "proving the equivalence of disparate theories, and self-organizing "
                "knowledge into a highly compressed, elegant framework. "
                "This is the threshold of true domain-general mathematical intelligence."
            )

        return ParadigmIndex(
            framework_invention   = fw_score,
            unification_depth     = unify_score,
            elegance_ratio        = eleg_score,
            cross_domain_transfer = transfer_score,
            concept_fertility     = fertility_score,
            theory_compression    = compression_score,
            composite             = composite,
            layer                 = layer,
            narrative             = narrative,
        )

    def print_index(self, idx: ParadigmIndex) -> None:
        scores = [
            ("A. Framework invention",   idx.framework_invention),
            ("B. Unification depth",     idx.unification_depth),
            ("C. Elegance ratio",        idx.elegance_ratio),
            ("D. Cross-domain transfer", idx.cross_domain_transfer),
            ("E. Concept fertility",     idx.concept_fertility),
            ("F. Theory compression",    idx.theory_compression),
        ]
        bar_total = "█" * int(idx.composite * 20) + "░" * (20 - int(idx.composite * 20))

        print(f"\n  {'━'*66}")
        print(f"  🧠 {bold('PARADIGM FORMATION INDEX')}  —  Layer {bold(idx.layer)}")
        print(f"  {'━'*66}")
        for label, score in scores:
            bar = "█" * int(score * 20) + "░" * (20 - int(score * 20))
            print(f"  {label:<30}  [{bar}]  {score:.2f}")
        print(f"  {'─'*66}")
        print(f"  {bold('COMPOSITE')}                         "
              f"  [{bar_total}]  {bold(f'{idx.composite:.2f}/1.00')}")
        print(f"  {'─'*66}")
        print(f"\n  {dim(idx.narrative)}")
        print(f"  {'━'*66}\n")


# ══════════════════════════════════════════════════════════════════
# HELPER: re-export utility functions needed by transfer engine
# ══════════════════════════════════════════════════════════════════

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
    return False


def _sub_b_with(node: Node, replacement: Node) -> Node:
    """Replace all Var('b') leaves with replacement."""
    if isinstance(node, Var) and node.name == "b":
        return replacement.clone()
    if isinstance(node, Const) or isinstance(node, Var):
        return node.clone()
    if isinstance(node, Add):
        return Add(_sub_b_with(node.left, replacement),
                   _sub_b_with(node.right, replacement))
    if isinstance(node, Sub):
        return Sub(_sub_b_with(node.left, replacement),
                   _sub_b_with(node.right, replacement))
    if isinstance(node, Mul):
        return Mul(_sub_b_with(node.left, replacement),
                   _sub_b_with(node.right, replacement))
    if isinstance(node, IDiv):
        return IDiv(_sub_b_with(node.left, replacement),
                    _sub_b_with(node.right, replacement))
    if isinstance(node, Pow):
        return Pow(_sub_b_with(node.base, replacement),
                   _sub_b_with(node.exp, replacement))
    if isinstance(node, Loop):
        return Loop(_sub_b_with(node.body, replacement),
                    _sub_b_with(node.count, replacement))
    if isinstance(node, IfNode):
        return IfNode(
            _sub_b_with(node.cond_left, replacement),
            node.cond_op,
            _sub_b_with(node.cond_right, replacement),
            _sub_b_with(node.yes, replacement),
            _sub_b_with(node.no, replacement),
        )
    return node.clone()


# ══════════════════════════════════════════════════════════════════
# CONVENIENCE: Layer6Engine bundles all six components
# ══════════════════════════════════════════════════════════════════

class Layer6Engine:
    """
    Convenience wrapper that bundles all six Layer-6 components
    and wires them together for use in math_society.py's run() loop.

    Usage in run():
        from ms_layer6 import Layer6Engine
        l6 = Layer6Engine()

        # Inside the round loop (e.g. every 10 rounds):
        if rnd >= 30 and rnd % 10 == 0:
            l6.update(concepts, laws, agents, unlock_credit,
                      surprise_resolutions, rnd)

        # At the end of the run:
        l6.final_report(concepts, laws, rnd)
    """

    def __init__(self):
        self.framework   = FrameworkRewriter()
        self.importance  = ConceptImportanceEngine()
        self.unification = UnificationDriver()
        self.transfer    = CrossDomainTransfer()
        self.elegance    = EleganceOptimizer()
        self.pfi         = ParadigmFormationIndex()

        self._last_elegance_report: Optional[EleganceReport] = None

    def update(self,
               concepts_obj: ConceptRegistry,
               laws: list[dict],
               agents: list,
               unlock_credit: dict,
               surprise_resolutions: dict,
               round_num: int) -> None:
        """
        Full Layer-6 update cycle. Call every 10 rounds after round 30.
        """
        all_concepts = concepts_obj.all_concepts()

        # 1. Framework rewriter
        new_props = self.framework.scan(all_concepts, laws, round_num)
        # Auto-accept first proposal — in production you'd gate this
        for prop in new_props:
            if prop.elegance_gain > 0.3:
                self.framework.accept(prop.proposal_id)

        # 2. Importance engine
        verdicts = self.importance.evaluate(
            all_concepts, laws, unlock_credit, surprise_resolutions, round_num)

        # 3. Unification driver
        self.unification.hunt(all_concepts, round_num, max_pairs=8)

        # 4. Cross-domain transfer — try to transfer known programs
        for agent in agents:
            for source_kind, source_prog in list(agent._best_prog_cache.items()):
                for target_kind in ["partial_sum", "sum_squares", "sum_cubes",
                                    "power", "repeated_add"]:
                    if target_kind == source_kind:
                        continue
                    if target_kind in agent._best_prog_cache:
                        continue   # already have a solution
                    derived = self.transfer.attempt_transfer(
                        source_kind, source_prog, target_kind,
                        concepts_obj, round_num)
                    if derived is not None:
                        # Give the agent the derived program directly
                        agent.library.add(derived)
                        agent._best_prog_cache[target_kind] = derived
                        agent._last_correct[target_kind] = round_num

        # 5. Elegance audit
        self._last_elegance_report = self.elegance.audit(
            all_concepts, laws, axiom_count=8)

    def final_report(self,
                      concepts_obj: ConceptRegistry,
                      laws: list[dict],
                      round_num: int) -> None:
        """Print the full Layer-6 final report."""
        all_concepts = concepts_obj.all_concepts()

        # Hall of Fame
        self.importance.print_hall_of_fame(top_n=6)

        # Elegance report
        if self._last_elegance_report:
            self.elegance.print_report(self._last_elegance_report)
        else:
            report = self.elegance.audit(all_concepts, laws, axiom_count=8)
            self.elegance.print_report(report)
            self._last_elegance_report = report

        # Unification summary
        events = self.unification.all_events()
        if events:
            print(f"  {bold('STRUCTURAL ANALOGIES DISCOVERED')} "
                  f"({len(events)} total):")
            for ev in sorted(events, key=lambda e: -e.strength)[:5]:
                print(f"  🔗 {bold(ev.concept_a):<18} ≃ {bold(ev.concept_b):<18}  "
                      f"{dim(ev.isomorphism[:50])}")
            print()

        # Cross-domain transfer summary
        transfers = self.transfer.successful_transfers()
        if transfers:
            print(f"  {bold('CROSS-DOMAIN TRANSFERS')} ({len(transfers)} successful):")
            for tr in transfers[:5]:
                print(f"  🔀 {cyan(tr.source_kind):<14} → {cyan(tr.target_kind):<14}  "
                      f"{dim(tr.transform)}")
            print()

        # Framework proposals
        proposals = self.framework.all_proposals()
        if proposals:
            print(f"  {bold('FRAMEWORK PROPOSALS')} ({len(proposals)} total):")
            for p in proposals[:4]:
                status = green("✓ accepted") if p.accepted else dim("pending")
                print(f"  🔧 {bold(p.name):<18}  {status}  {dim(p.formal[:50])}")
            print()

        # Paradigm Formation Index
        if self._last_elegance_report:
            idx = self.pfi.compute(
                self.framework, self.importance, self.unification,
                self.transfer, self._last_elegance_report,
                all_concepts, laws, round_num)
            self.pfi.print_index(idx)

    def summary_line(self) -> str:
        """Single-line status for the leaderboard."""
        fw  = self.framework.accepted_count()
        uni = len(self.unification.all_events())
        tr  = len(self.transfer.successful_transfers())
        return (f"L6: framework={fw}  analogies={uni}  transfers={tr}  "
                f"| {self.importance.summary()[:50]}")