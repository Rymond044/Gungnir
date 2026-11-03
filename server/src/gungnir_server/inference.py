"""The inference engine (slice S5; ADRs 0005, 0034, 0043, 0044): ranked hypotheses
from the evidence of an interval and the knowledge base.

Each culprit candidate of a rule gives a hypothesis when the rule's
conditions hold, its leading symptom is matched, every characteristic symptom
is matched, pending or unobservable, some impact is matched and no
contradicting symptom is; without its leading symptom it must also match
something besides the impact. Confidence
combines the weights of the matched symptoms as 1 - Π(1 - w) and
lowers it by 1 - alpha * w for each missing one. The inference engine sees
records, never samples (ADR 0001), and reads no database.
"""

import math
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Protocol

from gungnir_server.evidence import (
    HOST,
    Event,
    Evidence,
    Item,
    Record,
    SeenProcess,
    Value,
    at,
    inside,
    related,
    within,
)
from gungnir_server.params import Params
from gungnir_server.policy import Advisory
from gungnir_server.rules import CONTRADICTING, CULPRIT_CLASS, STRENGTHS, Rule, Symptom

if TYPE_CHECKING:
    from gungnir_server.base import Base

MATCHED, MISSING, PENDING, UNOBSERVABLE = "matched", "missing", "pending", "unobservable"
# Why a rule whose leading symptom was seen gives no hypothesis (ADR 0055).
CONTRADICTED = "contradicted"
CHARACTERISTIC_MISSING = "characteristic missing"
NO_IMPACT = "no impact"
ONLY_IMPACT = "only impact"
BELOW_CUTOFF = "below cut-off"
# Near misses shown at most.
NEAR = 5
# A contradicting symptom that was looked for and not seen.
ABSENT = "absent"
# Order in which symptoms take evidence: the leading symptom, then by strength.
ORDER = (*STRENGTHS, CONTRADICTING)


@dataclass(frozen=True)
class Binding:
    """The entities a hypothesis binds its roles to."""

    culprit: str | None = None
    device: str | None = None
    filesystem: str | None = None


@dataclass(frozen=True)
class Found:
    """What became of one symptom of the rule."""

    symptom: Symptom
    status: str
    item: Item | None = None


@dataclass(frozen=True)
class Hypothesis:
    cause: str
    binding: Binding
    # C' of ADR 0005: ranked and shown by it.
    confidence: float
    # C, before missing symptoms lowered it.
    support: float
    # Where the windows are counted from.
    leading_at: float
    # In the rule's order: the explanation tree below the rule.
    found: tuple[Found, ...]
    rule: Rule
    # The culprit's workload class explains its activity (ADR 0002).
    expected: bool = False
    # Why the gate refused it; None for a hypothesis (ADR 0055).
    rejected: str | None = None

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
    # By the impacted resource, when no hypothesis is shown.
    advisories: tuple[Advisory, ...]
    # Causes whose leading symptom was seen but that the gate refused or
    # the cut-off dropped, each with the reason (ADR 0055).
    near: tuple[Hypothesis, ...] = ()

    def causes(self) -> list[str]:
        """Causes in rank order, each once."""
        return list(dict.fromkeys(h.cause for h in self.hypotheses))


class InferenceEngine(Protocol):
    def diagnose(self, evidence: Evidence) -> Diagnosis: ...


class RuleEngine:
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
        near = self._near(current)
        shown = sorted(
            (h for h in current if h.rejected is None and h.confidence >= self.cutoff),
            key=lambda h: (
                -h.confidence,
                -len(h.with_status(MATCHED)),
                -_culprit_score(h),
                h.cause,
                h.binding.culprit or "",
            ),
        )
        explained = {r.id for r in ev.records if self._explained(r, ev)}
        shown = [replace(h, expected=self._expected(h, explained)) for h in shown]
        used = {f.item for h in shown for f in h.with_status(MATCHED)}
        unexplained = [r for r in ev.records if r not in used and r.id not in explained and inside(r, ev)]
        unexplained += [e for e in ev.events if e not in used and inside(e, ev)]
        advisories = () if shown else self._advisories(ev)
        expected = sorted(r.id for r in ev.records if r.id in explained and inside(r, ev))
        return Diagnosis(tuple(shown), tuple(unexplained), tuple(expected), advisories, near)

    def _near(self, current: list[Hypothesis]) -> tuple[Hypothesis, ...]:
        """The best refused candidate of each cause whose leading symptom was
        seen and that gives no hypothesis, most matched symptoms first."""
        best: dict[str, Hypothesis] = {}
        for h in current:
            if h.rejected is None and h.confidence >= self.cutoff:
                continue
            if not any(f.symptom.leading for f in h.with_status(MATCHED)):
                continue
            if h.rejected is None:
                h = replace(h, rejected=BELOW_CUTOFF)
            key = (len(h.with_status(MATCHED)), h.confidence)
            if h.cause not in best or key > (
                len(best[h.cause].with_status(MATCHED)),
                best[h.cause].confidence,
            ):
                best[h.cause] = h
        accepted = {h.cause for h in current if h.rejected is None and h.confidence >= self.cutoff}
        ranked = sorted(
            (h for c, h in best.items() if c not in accepted),
            key=lambda h: (-len(h.with_status(MATCHED)), -h.confidence, h.cause),
        )
        return tuple(ranked[:NEAR])

    # Binding (ADR 0044)

    def _hypotheses(self, rule: Rule, ev: Evidence) -> list[Hypothesis]:
        device = self._most_matched(rule, ev, "device")
        filesystem = self._most_matched(rule, ev, "filesystem")
        candidates = self._culprits(rule, ev)
        out = []
        for culprit in candidates:
            hypothesis = self._bound(rule, ev, Binding(culprit, device, filesystem))
            # A candidate comes from evidence anywhere in the look-back; it is
            # named only when its own evidence fits this hypothesis.
            if hypothesis and (culprit is None or _culprit_items(hypothesis)):
                out.append(hypothesis)
        if not any(h.rejected is None for h in out) and None not in candidates:
            hypothesis = self._bound(rule, ev, Binding(None, device, filesystem))
            if hypothesis and hypothesis.rejected is None:
                out = [h for h in out if h.rejected is None] + [hypothesis]
        return out

    def _bound(self, rule: Rule, ev: Evidence, b: Binding) -> Hypothesis | None:
        return self._evaluate(rule, ev, b) if self._conditions_hold(rule, ev, b) else None

    def _culprits(self, rule: Rule, ev: Evidence) -> list[str | None]:
        """Groups that match the leading symptom, or any culprit symptom when
        the leading symptom is elsewhere; of a group and its ancestor, the deepest."""
        on_culprit = [m for m in rule.symptoms if m.role == "culprit"]
        if not on_culprit:
            return [None]
        source = [rule.leading] if rule.leading.role == "culprit" else on_culprit
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
        for m in rule.symptoms:
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
    def _items(m: Symptom, ev: Evidence) -> tuple[Item, ...]:
        return {"anomaly": ev.records, "value": ev.values, "event": ev.events, "process": ev.processes}[
            m.kind
        ]

    def _matches(self, m: Symptom, item: Item, b: Binding) -> bool:
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
        assert isinstance(item, SeenProcess)
        return item.symptom in m.names and _process_on(m.role, item.group, b)

    def _observable(self, m: Symptom, ev: Evidence) -> bool:
        return any(name not in ev.unobservable for name in m.names)

    # The gate and the confidence (ADR 0043)

    def _evaluate(self, rule: Rule, ev: Evidence, b: Binding) -> Hypothesis | None:
        leading = rule.leading
        starts = sorted({at(i) for i in self._items(leading, ev) if self._matches(leading, i, b)})
        has_leading = bool(starts)
        if not has_leading:
            if self._observable(leading, ev):
                return None
            # Windows then count from the earliest matched symptom.
            times = [
                at(i)
                for m in rule.symptoms
                if not m.leading
                for i in self._items(m, ev)
                if self._matches(m, i, b)
            ]
            if not times:
                return None
            starts = [min(times)]
        best = None
        for t0 in starts:
            hypothesis = self._assign(rule, ev, b, t0, has_leading)
            # A hypothesis beats a refused candidate whatever their confidence.
            if hypothesis and (
                best is None
                or (hypothesis.rejected is None, hypothesis.confidence)
                > (best.rejected is None, best.confidence)
            ):
                best = hypothesis
        return best

    def _assign(
        self, rule: Rule, ev: Evidence, b: Binding, t0: float, has_leading: bool
    ) -> Hypothesis | None:
        """Gives each symptom the earliest unused item inside its
        window: the leading symptom first, then by strength. A candidate the
        gate refuses comes back marked with the reason."""
        used: set = set()
        contradicted = False
        outcome: dict[int, Found] = {}
        order = sorted(
            range(len(rule.symptoms)),
            key=lambda i: (
                not rule.symptoms[i].leading,
                ORDER.index(rule.symptoms[i].strength),
            ),
        )
        for i in order:
            m = rule.symptoms[i]
            lo, hi = m.window or (0, 0)
            if m.leading and not has_leading:
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
            if fits and m.strength == CONTRADICTING:
                contradicted = True
            if fits:
                used.add(fits[0])
                outcome[i] = Found(m, MATCHED, fits[0])
            elif not self._observable(m, ev):
                outcome[i] = Found(m, UNOBSERVABLE)
            elif t0 + hi > ev.until:
                outcome[i] = Found(m, PENDING)
            else:
                outcome[i] = Found(m, ABSENT if m.strength == CONTRADICTING else MISSING)
        found = tuple(outcome[i] for i in range(len(rule.symptoms)))

        impact = [f for f in found if f.symptom.impact]
        rejected = None
        if contradicted:
            rejected = CONTRADICTED
        # Nobody waiting is the reason an operator needs first.
        elif not any(f.status == MATCHED for f in impact) and not all(
            f.status == UNOBSERVABLE for f in impact
        ):
            rejected = NO_IMPACT
        elif any(f.status == MISSING and f.symptom.strength == "characteristic" for f in found):
            rejected = CHARACTERISTIC_MISSING
        # Without its leading symptom a cause must still show something besides the
        # impact, or any impact of the resource would name it.
        elif not has_leading and not any(f.status == MATCHED and not f.symptom.impact for f in found):
            rejected = ONLY_IMPACT
        if rejected and not has_leading:
            return None
        weighed = [f for f in found if f.symptom.strength != CONTRADICTING]
        support = 1 - math.prod(1 - self._weight(f) for f in weighed if f.status == MATCHED)
        confidence = support * math.prod(
            1 - self.alpha * self._weight(f) for f in weighed if f.status == MISSING
        )
        return Hypothesis(rule.cause, b, confidence, support, t0, found, rule, rejected=rejected)

    def _weight(self, f: Found) -> float:
        return self.weights[f.symptom.strength]

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
            if f.symptom.role == "culprit" and not f.symptom.impact and isinstance(f.item, Record)
        ]
        return bool(activity) and all(r.id in explained for r in activity)

    def _advisories(self, ev: Evidence) -> tuple[Advisory, ...]:
        impacted = {
            self.onto.signals[r.signal].resource
            for r in ev.records
            if inside(r, ev)
            and r.signal in self.onto.signals
            and (r.kind == "impact" or self.onto.signals[r.signal].impact)
        }
        return tuple(a for a in self.base.policy.advisories if a.resource in impacted)


def as_dict(d: Diagnosis) -> dict:
    """The diagnosis with its explanation trees, for JSON."""
    return {
        "hypotheses": [_hypothesis_dict(h) for h in d.hypotheses],
        "unexplained": [_item_dict(i) for i in d.unexplained],
        "expected": list(d.expected),
        "advisories": [{"resource": a.resource, "text": a.text, "source": a.source} for a in d.advisories],
        "near": [{**_hypothesis_dict(h), "reason": h.rejected} for h in d.near],
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
        "leading_at": h.leading_at,
        "rule": {"source": h.rule.source, "grade": h.rule.grade},
        "symptoms": [
            {
                "kind": f.symptom.kind,
                "names": list(f.symptom.names),
                "role": f.symptom.role,
                "strength": f.symptom.strength,
                "window": list(f.symptom.window) if f.symptom.window else None,
                "leading": f.symptom.leading,
                "impact": f.symptom.impact,
                "source": f.symptom.source,
                "status": f.status,
                "evidence": _item_dict(f.item) if f.item else None,
            }
            for f in h.found
        ],
    }


def _culprit_items(h: Hypothesis) -> list[Item]:
    return [f.item for f in h.with_status(MATCHED) if f.symptom.role == "culprit" and f.item]


def _culprit_score(h: Hypothesis) -> float:
    """How unusual the culprit's matched records are: of two culprits with
    equal evidence, the more unusual one comes first."""
    scores = [i.score for i in _culprit_items(h) if isinstance(i, Record) and i.score is not None]
    return max(scores, default=0.0)


def _item_dict(item: Item) -> dict:
    kind = {Record: "record", Value: "value", Event: "event", SeenProcess: "process"}[type(item)]
    fields = {k: v for k, v in item.__dict__.items() if v is not None}
    # An event's own type would hide what kind of item it is.
    if isinstance(item, Event):
        fields["event_type"] = fields.pop("type")
    return {"type": kind, **fields}


def _group(item: Item) -> str | None:
    if isinstance(item, SeenProcess):
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


def _process_on(role: str, group: str | None, b: Binding) -> bool:
    if role == "host":
        return True
    if group is None:
        return False
    if role == "culprit":
        return b.culprit is not None and within(group, b.culprit)
    return b.culprit is None or not related(group, b.culprit)
