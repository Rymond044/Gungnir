"""The reasoner (slice S5; ADRs 0005, 0034, 0043, 0044): ranked hypotheses
from the evidence of an interval and the knowledge base.

Each culprit candidate of a rule gives a hypothesis when the rule's
conditions hold, its anchor is matched, every characteristic manifestation
is matched, pending or unobservable, and some harm is matched. Confidence
combines the weights of the matched manifestations as 1 - Π(1 - w) and
lowers it by 1 - alpha * w for each missing one. The reasoner sees
records, never samples (ADR 0001), and reads no database.
"""

import math
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Protocol

from gungnir_core.evidence import (
    HOST,
    Event,
    Evidence,
    Item,
    Record,
    Sign,
    Value,
    at,
    inside,
    related,
    within,
)
from gungnir_core.params import Params
from gungnir_core.policy import Advisory
from gungnir_core.rules import CULPRIT_CLASS, STRENGTHS, Manifestation, Rule

if TYPE_CHECKING:
    from gungnir_core.base import Base

MATCHED, MISSING, PENDING, UNOBSERVABLE = "matched", "missing", "pending", "unobservable"


@dataclass(frozen=True)
class Binding:
    """The entities a hypothesis binds its roles to."""

    culprit: str | None = None
    device: str | None = None
    filesystem: str | None = None


@dataclass(frozen=True)
class Found:
    """What became of one manifestation of the rule."""

    manifestation: Manifestation
    status: str
    item: Item | None = None


@dataclass(frozen=True)
class Hypothesis:
    cause: str
    binding: Binding
    # C' of ADR 0005: ranked and shown by it.
    confidence: float
    # C, before missing manifestations lowered it.
    support: float
    # Where the windows are counted from.
    anchor_at: float
    # In the rule's order: the derivation tree below the rule.
    found: tuple[Found, ...]
    rule: Rule
    # The culprit's workload class explains its activity (ADR 0002).
    expected: bool = False

    def with_status(self, status: str) -> list[Found]:
        return [f for f in self.found if f.status == status]


@dataclass(frozen=True)
class Diagnosis:
    hypotheses: tuple[Hypothesis, ...]
    # Records and events no shown hypothesis uses and expected behavior
    # does not explain.
    unexplained: tuple[Record | Event, ...]
    # Ids of the records the expected behavior layer explains.
    expected: tuple[str, ...]
    # By the harmed resource, when no hypothesis is shown.
    advisories: tuple[Advisory, ...]

    def causes(self) -> list[str]:
        """Causes in rank order, each once."""
        return list(dict.fromkeys(h.cause for h in self.hypotheses))


class Reasoner(Protocol):
    def diagnose(self, evidence: Evidence) -> Diagnosis: ...


class RuleReasoner:
    def __init__(self, base: "Base", params: Params) -> None:
        self.base = base
        self.onto = base.ontology
        self.weights = params.strength_weights
        self.alpha = params.penalty_alpha
        self.cutoff = params.hypothesis_cutoff

    def diagnose(self, ev: Evidence) -> Diagnosis:
        found = [h for rule in self.base.rules.values() for h in self._hypotheses(rule, ev)]
        # Evidence before the interval only supports what reaches into it.
        current = [h for h in found if any(inside(f.item, ev) for f in h.with_status(MATCHED))]
        shown = sorted(
            (h for h in current if h.confidence >= self.cutoff),
            key=lambda h: (-h.confidence, -len(h.with_status(MATCHED)), h.cause, h.binding.culprit or ""),
        )
        explained = {r.id for r in ev.records if self._explained(r, ev)}
        shown = [replace(h, expected=self._expected(h, explained)) for h in shown]
        used = {f.item for h in shown for f in h.with_status(MATCHED)}
        unexplained = [r for r in ev.records if r not in used and r.id not in explained and inside(r, ev)]
        unexplained += [e for e in ev.events if e not in used and inside(e, ev)]
        advisories = () if shown else self._advisories(ev)
        expected = sorted(r.id for r in ev.records if r.id in explained and inside(r, ev))
        return Diagnosis(tuple(shown), tuple(unexplained), tuple(expected), advisories)

    # Binding (ADR 0044)

    def _hypotheses(self, rule: Rule, ev: Evidence) -> list[Hypothesis]:
        device = self._most_matched(rule, ev, "device")
        filesystem = self._most_matched(rule, ev, "filesystem")
        out = []
        for culprit in self._culprits(rule, ev):
            binding = Binding(culprit, device, filesystem)
            if self._conditions_hold(rule, ev, binding):
                hypothesis = self._evaluate(rule, ev, binding)
                if hypothesis:
                    out.append(hypothesis)
        return out

    def _culprits(self, rule: Rule, ev: Evidence) -> list[str | None]:
        """Groups that match the anchor, or any culprit manifestation when
        the anchor is elsewhere; of a group and its ancestor, the deepest."""
        on_culprit = [m for m in rule.manifestations if m.role == "culprit"]
        if not on_culprit:
            return [None]
        source = [rule.anchor] if rule.anchor.role == "culprit" else on_culprit
        groups = {
            group
            for m in source
            for item in self._items(m, ev)
            if (group := _group(item)) and self._matches(m, item, Binding(culprit=group))
        }
        deepest = sorted(g for g in groups if not any(h != g and within(h, g) for h in groups))
        return deepest or [None]

    def _most_matched(self, rule: Rule, ev: Evidence, role: str) -> str | None:
        counts: dict[str, int] = {}
        for m in rule.manifestations:
            if m.role != role:
                continue
            for entity in {item.entity for item in self._items(m, ev) if self._matches(m, item, Binding())}:
                counts[entity] = counts.get(entity, 0) + 1
        return min(counts, key=lambda e: (-counts[e], e)) if counts else None

    def _conditions_hold(self, rule: Rule, ev: Evidence, b: Binding) -> bool:
        def value(name: str) -> str | None:
            if name == CULPRIT_CLASS:
                return ev.group_class.get(b.culprit) if b.culprit else None
            return ev.facts.get(name)

        if any(value(name) not in allowed for name, allowed in rule.requires.items()):
            return False
        return not any(value(name) in banned for name, banned in rule.excludes.items())

    # Matching

    @staticmethod
    def _items(m: Manifestation, ev: Evidence) -> tuple[Item, ...]:
        return {"anomaly": ev.records, "value": ev.values, "event": ev.events, "sign": ev.signs}[m.kind]

    def _matches(self, m: Manifestation, item: Item, b: Binding) -> bool:
        if m.kind == "anomaly":
            assert isinstance(item, Record)
            if item.signal not in m.names or item.direction != m.direction:
                return False
            if m.record not in ("any", item.kind) or (m.level is not None and item.level != m.level):
                return False
            if m.detectors and item.detector.split(".")[0] not in m.detectors:
                return False
            return _on(m.role, item.entity, b)
        if m.kind == "value":
            assert isinstance(item, Value)
            if item.signal not in m.names or not _on(m.role, item.entity, b):
                return False
            return item.value <= m.at_most if m.at_most is not None else item.value > m.above
        if m.kind == "event":
            return item.type in m.names
        assert isinstance(item, Sign)
        return item.sign in m.names and _sign_on(m.role, item.group, b)

    def _observable(self, m: Manifestation, ev: Evidence) -> bool:
        return any(name not in ev.unobservable for name in m.names)

    # The gate and the confidence (ADR 0043)

    def _evaluate(self, rule: Rule, ev: Evidence, b: Binding) -> Hypothesis | None:
        anchor = rule.anchor
        starts = sorted({at(i) for i in self._items(anchor, ev) if self._matches(anchor, i, b)})
        anchored = bool(starts)
        if not anchored:
            if self._observable(anchor, ev):
                return None
            # Windows then count from the earliest matched manifestation.
            times = [
                at(i)
                for m in rule.manifestations
                if not m.anchor
                for i in self._items(m, ev)
                if self._matches(m, i, b)
            ]
            if not times:
                return None
            starts = [min(times)]
        best = None
        for t0 in starts:
            hypothesis = self._assign(rule, ev, b, t0, anchored)
            if hypothesis and (best is None or hypothesis.confidence > best.confidence):
                best = hypothesis
        return best

    def _assign(self, rule: Rule, ev: Evidence, b: Binding, t0: float, anchored: bool) -> Hypothesis | None:
        """Gives each manifestation the earliest unused item inside its
        window: the anchor first, then by strength."""
        used: set = set()
        outcome: dict[int, Found] = {}
        order = sorted(
            range(len(rule.manifestations)),
            key=lambda i: (
                not rule.manifestations[i].anchor,
                STRENGTHS.index(rule.manifestations[i].strength),
            ),
        )
        for i in order:
            m = rule.manifestations[i]
            lo, hi = m.window or (0, 0)
            if m.anchor and not anchored:
                outcome[i] = Found(m, UNOBSERVABLE)
                continue
            fits = sorted(
                (
                    item
                    for item in self._items(m, ev)
                    if item not in used and t0 + lo <= at(item) <= t0 + hi and self._matches(m, item, b)
                ),
                key=at,
            )
            if fits:
                used.add(fits[0])
                outcome[i] = Found(m, MATCHED, fits[0])
            elif not self._observable(m, ev):
                outcome[i] = Found(m, UNOBSERVABLE)
            elif t0 + hi > ev.until:
                outcome[i] = Found(m, PENDING)
            else:
                outcome[i] = Found(m, MISSING)
        found = tuple(outcome[i] for i in range(len(rule.manifestations)))

        if any(f.status == MISSING and f.manifestation.strength == "characteristic" for f in found):
            return None
        harm = [f for f in found if f.manifestation.harm]
        if not any(f.status == MATCHED for f in harm) and not all(f.status == UNOBSERVABLE for f in harm):
            return None
        support = 1 - math.prod(1 - self._weight(f) for f in found if f.status == MATCHED)
        confidence = support * math.prod(
            1 - self.alpha * self._weight(f) for f in found if f.status == MISSING
        )
        return Hypothesis(rule.cause, b, confidence, support, t0, found, rule)

    def _weight(self, f: Found) -> float:
        return self.weights[f.manifestation.strength]

    # Expected behavior and advisories

    def _explained(self, r: Record, ev: Evidence) -> bool:
        if r.kind == "impact":
            return False
        for e in self.base.expected:
            cls = ev.group_class.get(r.entity) if e.members else r.workload_class
            if cls and e.explains(cls, r.signal, r.direction):
                return True
        return False

    @staticmethod
    def _expected(h: Hypothesis, explained: set[str]) -> bool:
        """Every matched activity record on the culprit is explained."""
        activity = [
            f.item
            for f in h.with_status(MATCHED)
            if f.manifestation.role == "culprit" and not f.manifestation.harm and isinstance(f.item, Record)
        ]
        return bool(activity) and all(r.id in explained for r in activity)

    def _advisories(self, ev: Evidence) -> tuple[Advisory, ...]:
        harmed = {
            self.onto.signals[r.signal].resource
            for r in ev.records
            if inside(r, ev)
            and r.signal in self.onto.signals
            and (r.kind == "impact" or self.onto.signals[r.signal].impact)
        }
        return tuple(a for a in self.base.policy.advisories if a.resource in harmed)


def as_dict(d: Diagnosis) -> dict:
    """The diagnosis with its derivation trees, for JSON."""
    return {
        "hypotheses": [_hypothesis_dict(h) for h in d.hypotheses],
        "unexplained": [_item_dict(i) for i in d.unexplained],
        "expected": list(d.expected),
        "advisories": [{"resource": a.resource, "text": a.text, "source": a.source} for a in d.advisories],
    }


def _hypothesis_dict(h: Hypothesis) -> dict:
    return {
        "cause": h.cause,
        "culprit": h.binding.culprit,
        "device": h.binding.device,
        "filesystem": h.binding.filesystem,
        "confidence": round(h.confidence, 4),
        "support": round(h.support, 4),
        "expected": h.expected,
        "anchor_at": h.anchor_at,
        "rule": {"source": h.rule.source, "grade": h.rule.grade},
        "manifestations": [
            {
                "kind": f.manifestation.kind,
                "names": list(f.manifestation.names),
                "role": f.manifestation.role,
                "strength": f.manifestation.strength,
                "window": list(f.manifestation.window) if f.manifestation.window else None,
                "anchor": f.manifestation.anchor,
                "harm": f.manifestation.harm,
                "source": f.manifestation.source,
                "status": f.status,
                "evidence": _item_dict(f.item) if f.item else None,
            }
            for f in h.found
        ],
    }


def _item_dict(item: Item) -> dict:
    kind = {Record: "record", Value: "value", Event: "event", Sign: "sign"}[type(item)]
    return {"type": kind, **{k: v for k, v in item.__dict__.items() if v is not None}}


def _group(item: Item) -> str | None:
    if isinstance(item, Sign):
        return item.group
    if isinstance(item, Record | Value) and item.entity != HOST:
        return item.entity
    return None


def _on(role: str, entity: str, b: Binding) -> bool:
    """A record or value on `entity` is on `role` under the binding; an
    unbound device or filesystem takes any."""
    if role == "host":
        return entity == HOST
    if role == "culprit":
        return entity == b.culprit
    if role == "others":
        return b.culprit is None or not related(entity, b.culprit)
    bound = b.device if role == "device" else b.filesystem
    return bound is None or entity == bound


def _sign_on(role: str, group: str | None, b: Binding) -> bool:
    if role == "host":
        return True
    if group is None:
        return False
    if role == "culprit":
        return b.culprit is not None and within(group, b.culprit)
    return b.culprit is None or not related(group, b.culprit)
