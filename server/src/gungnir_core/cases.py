"""Knowledge tests (ADR 0033, slice S4): synthetic cases of records, values,
events, process signs and facts, with the root cause the base must
return. Read from `knowledge/tests/*.toml`; the format is described at the
top of `knowledge/tests/disk.toml`.

A case runs through the reasoner (slice S5): the expected cause must rank
within `within`, the causes listed in `not` must stay below the cut-off,
and a case without `expect` is a negative one, with no hypothesis at all.
Roles become fixed entities, so a case needs no group names.
"""

from dataclasses import dataclass
from pathlib import Path

from gungnir_core import knowledge
from gungnir_core.evidence import HOST, Event, Evidence, Record, Sign, Value
from gungnir_core.knowledge import KnowledgeError
from gungnir_core.ontology import Ontology
from gungnir_core.rules import CULPRIT_CLASS, DETECTORS, DIRECTIONS, LEVELS, Rule

ITEMS = ("record", "value", "event", "sign")
FIELDS = {
    "name",
    "expect",
    "within",
    "not",
    "expected",
    "unavailable",
    "facts",
    CULPRIT_CLASS,
    "provenance",
    *ITEMS,
}
# The entities the roles of a case stand for.
ENTITIES = {
    "culprit": "case.slice/culprit.service",
    "others": "case.slice/other.service",
    "device": "sda",
    "filesystem": "/",
    "host": HOST,
}


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
    # The detector of a record: robust unless given.
    detector: str = "robust"


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
    # The top hypothesis must be marked expected behavior.
    expected: bool = False
    # Signals, event types and process signs the host does not provide.
    unavailable: frozenset[str] = frozenset()


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
    expected = item.get("expected", False)
    if not isinstance(expected, bool) or (expected and expect is None):
        fail(f"{where}: expected is true or false, and needs an expected cause")
    unavailable = item.get("unavailable", [])
    for unseen in unavailable if isinstance(unavailable, list) else [None]:
        if not any(unseen in onto.names(c) for c in ("signal", "event", "sign")):
            fail(f"{where}: unavailable: '{unseen}' is not a signal, event type or process sign")
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
        expected,
        frozenset(unavailable),
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
        detector = raw.get("detector", "impact" if record == "impact" else "robust")
        if direction not in DIRECTIONS:
            fail(f"{where}: direction must be up or down")
        if record not in ("anomaly", "impact") or (level is not None and level not in LEVELS):
            fail(f"{where}: record is anomaly or impact, level noticeable or severe")
        if record == "anomaly" and detector not in DETECTORS:
            fail(f"{where}: detector must be one of {', '.join(DETECTORS)}")
        out = Item(kind, name, role, at, direction, record, level, detector=detector)
    elif kind == "value":
        value = raw.get("value")
        if not isinstance(value, int | float) or isinstance(value, bool):
            fail(f"{where}: value must be a number")
        out = Item(kind, name, role, at, value=value)
    return None if len(problems) > before else out


def evidence_of(case: Case) -> Evidence:
    """The case as the reasoner sees it: each role is one entity, and every
    window has closed."""
    records, values, events, signs = [], [], [], []
    for n, it in enumerate(case.items):
        entity = ENTITIES[it.role]
        if it.kind == "record":
            records.append(
                Record(
                    f"r{n}",
                    it.name,
                    entity,
                    it.record,
                    it.detector,
                    it.direction,
                    float(it.at),
                    level=it.level,
                    workload_class=case.culprit_class,
                )
            )
        elif it.kind == "value":
            values.append(Value(it.name, entity, it.value, float(it.at)))
        elif it.kind == "event":
            events.append(Event(f"e{n}", it.name, float(it.at)))
        else:
            group = None if it.role == "host" else entity
            signs.append(Sign(it.name, it.name, n, "R", group, float(it.at)))
    classes = {ENTITIES["culprit"]: case.culprit_class} if case.culprit_class else {}
    return Evidence(
        tuple(records),
        tuple(values),
        tuple(events),
        tuple(signs),
        dict(case.facts),
        classes,
        unobservable=case.unavailable,
    )


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
