"""Knowledge tests (ADR 0033, slice S4): synthetic cases of records, values,
events, process signs and facts, with the root cause the base must
return. Read from `knowledge/tests/*.toml`; the format is described at the
top of `knowledge/tests/disk.toml`.

Until the reasoner ranks hypotheses (S5), a case checks what the rules
alone decide: the expected cause's rule fires, and the causes listed in
`not` do not. A rule fires when its conditions hold, its anchor is
present, every characteristic manifestation is matched within its window
from the anchor, and some harm is matched. A case without `expect` is a
negative one: no rule may fire.
"""

from dataclasses import dataclass
from pathlib import Path

from gungnir_core import knowledge
from gungnir_core.knowledge import KnowledgeError
from gungnir_core.ontology import Ontology
from gungnir_core.rules import CULPRIT_CLASS, DIRECTIONS, LEVELS, Manifestation, Rule

ITEMS = ("record", "value", "event", "sign")
FIELDS = {"name", "expect", "within", "not", "facts", CULPRIT_CLASS, "provenance", *ITEMS}


@dataclass(frozen=True)
class Item:
    """One thing seen in a case, `at` seconds from the case's start."""

    kind: str  # record (an anomaly or impact record), value, event, sign
    name: str
    role: str
    at: int
    direction: str | None = None
    record: str = "anomaly"
    level: str | None = None
    value: float | None = None


@dataclass(frozen=True)
class Case:
    name: str
    expect: str | None
    # The rank the reasoner must give it, checked from S5 on.
    within: int
    not_: tuple[str, ...]
    facts: dict[str, str]
    culprit_class: str | None
    items: tuple[Item, ...]
    where: str
    # The test file's stem: disk, memory, cpu, cross.
    domain: str


def directory() -> Path:
    return knowledge.directory() / "tests"


def load(onto: Ontology, rules: dict[str, Rule], root: Path | None = None) -> list[Case]:
    root = root or directory()
    files = sorted(root.glob("*.toml"))
    if not files:
        raise KnowledgeError(f"no knowledge tests in {root}")
    problems: list[str] = []
    cases = []
    for file in files:
        for i, item in enumerate(knowledge.load(file).get("case", []), 1):
            case = _read(onto, rules, item, f"{file}: [[case]] #{i}", file.stem, problems)
            if case:
                cases.append(case)
    if problems:
        raise KnowledgeError("\n".join(problems))
    return cases


def _read(
    onto: Ontology, rules: dict, item: dict, where: str, domain: str, problems: list[str]
) -> Case | None:
    before = len(problems)
    fail = problems.append
    problem = knowledge.provenance_problem(item, where)
    if problem:
        fail(problem)
    name = str(item.get("name", "")).strip()
    if not name:
        fail(f"{where}: no name")
    where = f"{where} ({name})"
    unknown = set(item) - FIELDS
    if unknown:
        fail(f"{where}: unknown fields {', '.join(sorted(unknown))}")
    expect = item.get("expect")
    for cause in ([expect] if expect is not None else []) + list(item.get("not", [])):
        if cause not in rules:
            fail(f"{where}: '{cause}' is not a cause with a rule")
    within = item.get("within", 1)
    if not isinstance(within, int) or within < 1:
        fail(f"{where}: within is a rank, 1 or more")
    facts = item.get("facts", {})
    for fact, value in facts.items():
        if fact not in onto.facts or value not in onto.facts[fact].values:
            fail(f"{where}: fact {fact} = {value!r} is not in the ontology")
    culprit_class = item.get(CULPRIT_CLASS)
    if culprit_class is not None and culprit_class not in onto.classes:
        fail(f"{where}: unknown class '{culprit_class}'")
    items = []
    for kind in ITEMS:
        for n, raw in enumerate(item.get(kind, []), 1):
            got = _item(onto, kind, raw, f"{where}: {kind} #{n}", problems)
            if got:
                items.append(got)
    if not items:
        fail(f"{where}: nothing seen")
    if len(problems) > before:
        return None
    return Case(
        name,
        expect,
        within,
        tuple(item.get("not", [])),
        dict(facts),
        culprit_class,
        tuple(items),
        where,
        domain,
    )


def _item(onto: Ontology, kind: str, raw: dict, where: str, problems: list[str]) -> Item | None:
    before = len(problems)
    fail = problems.append
    category = {"record": "signal", "value": "signal", "event": "event", "sign": "sign"}[kind]
    key = "signal" if category == "signal" else kind
    name = str(raw.get(key))
    problem = onto.missing(category, name, where)
    if problem:
        fail(problem)
    role = raw.get("role", "host")
    if role not in onto.roles:
        fail(f"{where}: unknown role '{role}'")
    elif category == "signal" and not problem and onto.signals[name].entity not in onto.roles[role].kinds:
        fail(f"{where}: {name} cannot be seen on the {role}")
    at = raw.get("at")
    if not isinstance(at, int) or isinstance(at, bool):
        fail(f"{where}: at must be whole seconds")
    out = Item(kind, name, role, at)
    if kind == "record":
        direction, record, level = raw.get("direction"), raw.get("record", "anomaly"), raw.get("level")
        if direction not in DIRECTIONS:
            fail(f"{where}: direction must be up or down")
        if record not in ("anomaly", "impact") or (level is not None and level not in LEVELS):
            fail(f"{where}: record is anomaly or impact, level noticeable or severe")
        out = Item(kind, name, role, at, direction, record, level)
    elif kind == "value":
        value = raw.get("value")
        if not isinstance(value, int | float) or isinstance(value, bool):
            fail(f"{where}: value must be a number")
        out = Item(kind, name, role, at, value=value)
    return None if len(problems) > before else out


def fires(rule: Rule, case: Case) -> bool:
    if not _conditions_hold(rule, case):
        return False
    anchor = rule.anchor
    characteristic = [m for m in rule.manifestations if m.strength == "characteristic" and not m.anchor]
    harm = [m for m in rule.manifestations if m.harm]
    for t0 in (it.at for it in case.items if _matches(anchor, it)):

        def seen(m: Manifestation, t0=t0) -> bool:
            if m.anchor:
                return True
            lo, hi = m.window
            return any(_matches(m, it) and lo <= it.at - t0 <= hi for it in case.items)

        if all(seen(m) for m in characteristic) and any(seen(m) for m in harm):
            return True
    return False


def firing(rules: dict[str, Rule], case: Case) -> list[str]:
    return sorted(cause for cause, rule in rules.items() if fires(rule, case))


def _conditions_hold(rule: Rule, case: Case) -> bool:
    def value(name: str) -> str | None:
        return case.culprit_class if name == CULPRIT_CLASS else case.facts.get(name)

    if any(value(name) not in allowed for name, allowed in rule.requires.items()):
        return False
    return not any(value(name) in banned for name, banned in rule.excludes.items())


def _matches(m: Manifestation, it: Item) -> bool:
    kind = "record" if m.kind == "anomaly" else m.kind
    if it.kind != kind or it.name not in m.names or it.role != m.role:
        return False
    if m.kind == "anomaly":
        if it.direction != m.direction or m.record not in ("any", it.record):
            return False
        return m.level is None or it.level == m.level
    if m.kind == "value":
        return it.value <= m.at_most if m.at_most is not None else it.value > m.above
    return True


def coverage(rules: dict[str, Rule], cases: list[Case]) -> list[str]:
    """What the tests leave uncovered: a rule without a positive case, a
    domain without a negative one."""
    problems = [
        f"rule {cause} has no positive case" for cause in rules if not any(c.expect == cause for c in cases)
    ]
    for domain in sorted({c.domain for c in cases}):
        if not any(c.expect is None for c in cases if c.domain == domain):
            problems.append(f"tests/{domain}.toml has no negative case")
    return problems
