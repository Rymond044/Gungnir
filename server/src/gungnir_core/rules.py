"""Causal rules (ADR 0034): a root cause and the manifestations it is
expected to produce, read from `knowledge/rules/*.toml`.

Loading checks every rule against the ontology and the semantics of ADR
0034, and lists every problem found rather than the first. The format is
described at the top of `knowledge/rules/disk.toml`.
"""

from dataclasses import dataclass, replace
from pathlib import Path

from gungnir_core import frame, knowledge
from gungnir_core.knowledge import KnowledgeError
from gungnir_core.ontology import Ontology

KINDS = ("anomaly", "value", "event", "sign")
# The words of ADR 0034; the reasoner maps them to numbers.
STRENGTHS = ("characteristic", "typical", "possible")
# Evidence against the cause (ADR 0049): when it is seen in its window, the
# cause is ruled out, as the dossiers' "tell it from" says.
CONTRADICTING = "contradicting"
DIRECTIONS = ("up", "down")
RECORDS = ("any", "anomaly", "impact")
# Detectors an anomaly manifestation may name; a record of "robust.mad" or
# "robust.ewma" counts as "robust".
DETECTORS = ("threshold", "robust", "iforest", "growth")
LEVELS = ("noticeable", "severe")
# The workload class of the culprit's group, a condition beside the facts.
CULPRIT_CLASS = "culprit_class"
SIGN_ROLES = ("culprit", "others", "host")
# Where each kind looks its names up in the ontology.
CATEGORY = {"anomaly": "signal", "value": "signal", "event": "event", "sign": "sign"}
FIELDS = {
    "anomaly": {"direction", "record", "level", "detector"},
    "value": {"at_most", "above"},
    "event": set(),
    "sign": set(),
}
COMMON = {"role", "anchor", "window", "strength", "harm", "provenance"}


@dataclass(frozen=True)
class Manifestation:
    kind: str
    # Any one of them matches; it counts once.
    names: tuple[str, ...]
    role: str
    strength: str
    anchor: bool
    harm: bool
    # Seconds from the anchor's start; None for the anchor itself.
    window: tuple[int, int] | None
    direction: str | None = None
    record: str = "any"
    level: str | None = None
    # Detectors whose records count; empty means any.
    detectors: tuple[str, ...] = ()
    at_most: float | None = None
    above: float | None = None
    source: str = ""
    grade: str = ""


@dataclass(frozen=True)
class Rule:
    cause: str
    manifestations: tuple[Manifestation, ...]
    # Fact name, or culprit_class -> the values that satisfy it.
    requires: dict[str, frozenset[str]]
    excludes: dict[str, frozenset[str]]
    source: str
    grade: str
    where: str

    @property
    def anchor(self) -> Manifestation:
        return next(m for m in self.manifestations if m.anchor)


def directory() -> Path:
    return knowledge.directory() / "rules"


def load(onto: Ontology, root: Path | None = None) -> dict[str, Rule]:
    """Every rule of the layer by cause, or KnowledgeError listing every
    problem found."""
    root = root or directory()
    files = sorted(root.glob("*.toml"))
    if not files:
        raise KnowledgeError(f"no causal rules in {root}")
    problems: list[str] = []
    rules: dict[str, Rule] = {}
    for file in files:
        for i, item in enumerate(knowledge.load(file).get("rule", []), 1):
            rule = _Checker(onto, f"{file}: [[rule]] #{i}", problems).rule(item)
            if rule is None:
                continue
            if rule.cause in rules:
                problems.append(
                    f"{rule.where}: a second rule for {rule.cause} (first in {rules[rule.cause].where})"
                )
                continue
            rules[rule.cause] = rule
    if problems:
        raise KnowledgeError("\n".join(problems))
    return rules


def names_of(value) -> tuple[str, ...]:
    """A name or a list of names, as the TOML layers write them."""
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list) and value and all(isinstance(v, str) for v in value):
        return tuple(value)
    return ()


class _Checker:
    def __init__(self, onto: Ontology, where: str, problems: list[str]):
        self.onto = onto
        self.where = where
        self.problems = problems

    def fail(self, message: str) -> None:
        self.problems.append(f"{self.where}: {message}")

    def rule(self, item: dict) -> Rule | None:
        before = len(self.problems)
        problem = knowledge.provenance_problem(item, self.where)
        if problem:
            self.problems.append(problem)
        cause = self.onto.causes.get(str(item.get("cause")))
        if cause is None:
            self.fail(f"unknown cause '{item.get('cause')}'")
            return None
        self.where = f"{self.where} ({cause.name})"
        unknown = set(item) - {"cause", "requires", "excludes", "manifestation", "provenance"}
        if unknown:
            self.fail(f"unknown fields {', '.join(sorted(unknown))}")
        items = item.get("manifestation", [])
        found = [self.manifestation(m, n) for n, m in enumerate(items, 1)]
        manifestations = tuple(m for m in found if m is not None)
        requires = self.conditions(item.get("requires", {}), "requires")
        excludes = self.conditions(item.get("excludes", {}), "excludes")
        if len(manifestations) == len(items):
            self.whole(manifestations, cause.culprit, requires | excludes)
            self.reflected(manifestations, cause.resources)
        if len(self.problems) > before:
            return None
        return Rule(
            cause.name,
            manifestations,
            requires,
            excludes,
            item["provenance"]["source"],
            item["provenance"]["grade"],
            self.where,
        )

    def manifestation(self, m: dict, n: int) -> Manifestation | None:
        where = f"manifestation #{n}"
        before = len(self.problems)
        problem = knowledge.provenance_problem(m, f"{self.where}: {where}")
        if problem:
            self.problems.append(problem)
        kinds = [k for k in KINDS if k in m]
        if len(kinds) != 1:
            self.fail(f"{where}: give exactly one of {', '.join(KINDS)}")
            return None
        kind = kinds[0]
        unknown = set(m) - COMMON - FIELDS[kind] - {kind}
        if unknown:
            self.fail(f"{where}: fields {', '.join(sorted(unknown))} do not belong to a {kind}")
        names = names_of(m[kind])
        if not names:
            self.fail(f"{where}: {kind} must be a name or a list of names")
        for name in names:
            problem = self.onto.missing(CATEGORY[kind], name, f"{self.where}: {where}")
            if problem:
                self.problems.append(problem)
        role = m.get("role", "host" if kind == "event" else None)
        if role not in self.onto.roles:
            self.fail(f"{where}: role must be one of {', '.join(sorted(self.onto.roles))}")
        strength = m.get("strength")
        if strength not in (*STRENGTHS, CONTRADICTING):
            self.fail(f"{where}: strength must be one of {', '.join((*STRENGTHS, CONTRADICTING))}")
        anchor, harm = m.get("anchor", False), m.get("harm", False)
        if not isinstance(anchor, bool) or not isinstance(harm, bool):
            self.fail(f"{where}: anchor and harm are true or false")
        if strength == CONTRADICTING and (anchor or harm):
            self.fail(f"{where}: a contradicting manifestation is neither the anchor nor harm")
        window = self.window(m.get("window"), anchor, where)
        if len(self.problems) > before:
            return None
        out = Manifestation(
            kind,
            names,
            role,
            strength,
            anchor,
            harm,
            window,
            source=m["provenance"]["source"],
            grade=m["provenance"]["grade"],
        )
        check = getattr(self, f"_{kind}")
        out = check(m, out, where)
        return None if len(self.problems) > before else out

    def window(self, window, anchor: bool, where: str) -> tuple[int, int] | None:
        if anchor:
            if window is not None:
                self.fail(f"{where}: the anchor has no window, the others are counted from it")
            return None
        if (
            not isinstance(window, list)
            or len(window) != 2
            or not all(isinstance(w, int) and not isinstance(w, bool) for w in window)
            or window[0] > window[1]
        ):
            self.fail(f"{where}: window must be [min, max] in whole seconds, min <= max")
            return None
        return (window[0], window[1])

    def _signals(self, out: Manifestation, where: str) -> None:
        """A signal is expected on its role's kind of entity."""
        kinds = self.onto.roles[out.role].kinds
        for name in out.names:
            entity = self.onto.signals[name].entity
            if entity not in kinds:
                binds = ", ".join(sorted(kinds))
                self.fail(f"{where}: {name} is observed on a {entity}; role {out.role} binds to {binds}")

    def _anomaly(self, m: dict, out: Manifestation, where: str) -> Manifestation:
        self._signals(out, where)
        direction, record, level = m.get("direction"), m.get("record", "any"), m.get("level")
        if direction not in DIRECTIONS:
            self.fail(f"{where}: direction must be up or down")
        if record not in RECORDS:
            self.fail(f"{where}: record must be one of {', '.join(RECORDS)}")
        if level is not None and (record != "impact" or level not in LEVELS):
            self.fail(f'{where}: level is noticeable or severe, and only with record = "impact"')
        for name in out.names:
            if not frame.is_checked_kind(name):
                self.fail(f"{where}: detection never checks {name}, so it has no records")
            if record == "impact" and not self.onto.signals[name].impact:
                self.fail(f"{where}: {name} is not an impact signal")
        detectors = names_of(m["detector"]) if "detector" in m else ()
        if "detector" in m and (not detectors or not set(detectors) <= set(DETECTORS)):
            self.fail(f"{where}: detector must name some of {', '.join(DETECTORS)}")
        if detectors and record == "impact":
            self.fail(f"{where}: impact records come from the impact detector only")
        return replace(out, direction=direction, record=record, level=level, detectors=detectors)

    def _value(self, m: dict, out: Manifestation, where: str) -> Manifestation:
        self._signals(out, where)
        for name in out.names:
            if name not in frame.CONTEXT_SIGNALS:
                self.fail(f"{where}: detection attaches no value of {name} to its records")
        at_most, above = m.get("at_most"), m.get("above")
        if (at_most is None) == (above is None):
            self.fail(f"{where}: give exactly one of at_most or above")
        elif not isinstance(at_most if above is None else above, int | float):
            self.fail(f"{where}: the bound must be a number")
        return replace(out, at_most=at_most, above=above)

    def _event(self, m: dict, out: Manifestation, where: str) -> Manifestation:
        if out.role != "host":
            self.fail(f"{where}: events are seen on the host")
        return out

    def _sign(self, m: dict, out: Manifestation, where: str) -> Manifestation:
        if out.role not in SIGN_ROLES:
            self.fail(f"{where}: a process sign is looked for on {', '.join(SIGN_ROLES)}")
        return out

    def conditions(self, table, field: str) -> dict[str, frozenset[str]]:
        if not isinstance(table, dict):
            self.fail(f"{field} must be a table")
            return {}
        out = {}
        for name, value in table.items():
            values = names_of(value)
            if name == CULPRIT_CLASS:
                allowed = self.onto.classes
            elif name in self.onto.facts:
                allowed = self.onto.facts[name].values
            else:
                self.fail(f"{field}: '{name}' is neither a context fact nor {CULPRIT_CLASS}")
                continue
            bad = [v for v in values if v not in allowed] if values else [repr(value)]
            if bad:
                self.fail(f"{field}: {name} cannot be {', '.join(bad)}")
                continue
            out[name] = frozenset(values)
        return out

    def reflected(self, ms: tuple[Manifestation, ...], resources: frozenset[str]) -> None:
        """A signal driven by waiting on another resource is evidence only
        for a cause that concerns that resource (cross-effects dossier)."""
        for m in ms:
            if m.kind not in ("anomaly", "value"):
                continue
            for name in m.names:
                reflects = self.onto.signals[name].reflects
                if reflects and not reflects & resources:
                    waits = ", ".join(sorted(reflects))
                    self.fail(f"{name} reflects waiting on {waits}, which this cause does not concern")

    def whole(self, ms: tuple[Manifestation, ...], culprit: str | None, conditions: dict) -> None:
        """What ADR 0034 asks of a rule as a whole."""
        anchors = [m for m in ms if m.anchor]
        if len(anchors) != 1:
            self.fail("needs exactly one anchor")
        elif anchors[0].strength != "characteristic":
            self.fail("the anchor marks the mechanism, so it is characteristic")
        if not any(m.harm for m in ms):
            self.fail("no harm: without it the same activity is expected behavior")
        on_culprit = [m for m in ms if m.role == "culprit"]
        if any(m.harm for m in on_culprit):
            self.fail("harm is done to the others, the device, the filesystem or the host, not the culprit")
        if culprit is None:
            if on_culprit:
                self.fail("the cause has no culprit, so no manifestation can be on one")
            if CULPRIT_CLASS in conditions:
                self.fail(f"the cause has no culprit, so no {CULPRIT_CLASS}")
        elif not on_culprit:
            self.fail("the cause has a culprit but no manifestation of its activity")
