"""The ontology of the knowledge base (ADR 0033): kinds, never the concrete
entities of a host.

`load` parses `knowledge/ontology/gungnir.ttl` and checks it in the closed
world, which an OWL reasoner does not: every individual has the properties
its category needs, every reference names an individual of the right
category, every individual carries provenance. `consistency` runs HermiT
for what OWL itself states (disjoint categories, domains and ranges,
functional properties) and returns how it classified the root causes. It
needs Java and owlready2, so it runs in `gungnir-core knowledge check` and
in the tests, never in the running core.
"""

import re
import shutil
import tempfile
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path

from rdflib import RDF, Graph, Literal, Namespace, URIRef
from rdflib.namespace import OWL

from gungnir_core import knowledge
from gungnir_core.knowledge import KnowledgeError

BASE = "https://gungnir.local/ontology"
G = Namespace(BASE + "#")

# Category of the TOML layers -> its class. Individuals of a category live
# in the namespace `BASE/<category>#`, so a TOML name resolves within one.
CATEGORIES = {
    "resource": "Resource",
    "kind": "EntityKind",
    "role": "Role",
    "signal": "Signal",
    "event": "EventType",
    "sign": "ProcessSign",
    "fact": "ContextFact",
    "class": "WorkloadClass",
    "cause": "RootCause",
    "action": "ActionType",
    "protected": "ProtectedEntity",
}
CAUSE_CLASSES = ("DiskCause", "FilesystemCause", "MemoryCause", "CpuCause")
SIGNAL_KINDS = ("level", "rate", "ratio", "count")
EMITTERS = ("kernel", "systemd")
STATES = ("R", "S", "D", "I", "Z", "T")


def path() -> Path:
    return knowledge.directory() / "ontology" / "gungnir.ttl"


@dataclass(frozen=True)
class Role:
    name: str
    kinds: frozenset[str]


@dataclass(frozen=True)
class Signal:
    name: str
    resource: str
    entity: str
    unit: str
    kind: str
    impact: bool


@dataclass(frozen=True)
class EventType:
    name: str
    resource: str | None
    emitted_by: str
    message_id: str | None
    pattern: re.Pattern | None

    def matches(self, source: str, message_id: str | None, message: str) -> bool:
        if source != self.emitted_by:
            return False
        if self.message_id is not None and message_id != self.message_id:
            return False
        return self.pattern is None or self.pattern.search(message) is not None


@dataclass(frozen=True)
class ProcessSign:
    name: str
    resource: str
    pattern: str
    state: str | None

    def matches(self, comm: str, state: str) -> bool:
        return fnmatchcase(comm, self.pattern) and (self.state is None or state == self.state)


@dataclass(frozen=True)
class ContextFact:
    name: str
    resource: str
    entity: str
    values: frozenset[str]


@dataclass(frozen=True)
class RootCause:
    name: str
    resources: frozenset[str]
    culprit: str | None


@dataclass(frozen=True)
class ActionType:
    name: str
    target: str
    resource: str


@dataclass(frozen=True)
class ProtectedEntity:
    name: str
    units: tuple[str, ...]
    commands: tuple[str, ...]


@dataclass(frozen=True)
class Ontology:
    resources: frozenset[str]
    kinds: frozenset[str]
    roles: dict[str, Role]
    signals: dict[str, Signal]
    events: dict[str, EventType]
    signs: dict[str, ProcessSign]
    facts: dict[str, ContextFact]
    classes: frozenset[str]
    causes: dict[str, RootCause]
    actions: dict[str, ActionType]
    protected: dict[str, ProtectedEntity]

    def names(self, category: str):
        return {
            "resource": self.resources,
            "kind": self.kinds,
            "role": self.roles,
            "signal": self.signals,
            "event": self.events,
            "sign": self.signs,
            "fact": self.facts,
            "class": self.classes,
            "cause": self.causes,
            "action": self.actions,
            "protected": self.protected,
        }[category]

    def missing(self, category: str, name: str, where: str) -> str | None:
        """The complaint about a name the ontology lacks, or None."""
        if name in self.names(category):
            return None
        return f"{where}: unknown {category} '{name}'"


def parse(file: Path) -> Graph:
    try:
        return Graph().parse(file, format="turtle")
    except OSError as err:
        raise KnowledgeError(f"cannot read the ontology {file}: {err.strerror}") from err
    except Exception as err:
        raise KnowledgeError(f"{file}: not valid Turtle: {err}") from err


def load(file: Path | None = None) -> Ontology:
    """The ontology, or KnowledgeError listing every problem found."""
    file = file or path()
    reader = _Reader(parse(file), file)
    onto = reader.read()
    if reader.errors:
        raise KnowledgeError("\n".join(reader.errors))
    return onto


class _Reader:
    def __init__(self, graph: Graph, file: Path):
        self.g = graph
        self.file = file
        self.errors: list[str] = []
        self.members = {cat: self._members(cat, cls) for cat, cls in CATEGORIES.items()}

    def read(self) -> Ontology:
        self._unknown_terms()
        for cat in CATEGORIES:
            for node in self.members[cat].values():
                self._provenance(node)
        onto = Ontology(
            resources=frozenset(self.members["resource"]),
            kinds=frozenset(self.members["kind"]),
            roles={n: Role(n, frozenset(self.many(s, "bindsTo", "kind"))) for n, s in self._of("role")},
            signals={n: self._signal(n, s) for n, s in self._of("signal")},
            events={n: self._event(n, s) for n, s in self._of("event")},
            signs={n: self._sign(n, s) for n, s in self._of("sign")},
            facts={n: self._fact(n, s) for n, s in self._of("fact")},
            classes=frozenset(self.members["class"]),
            causes={n: self._cause(n, s) for n, s in self._of("cause")},
            actions={n: self._action(n, s) for n, s in self._of("action")},
            protected={n: self._protected(n, s) for n, s in self._of("protected")},
        )
        for role in onto.roles.values():
            if not role.kinds:
                self.errors.append(f"{self.file}: role {role.name} binds to no kind of entity")
        culprit = onto.roles.get("culprit")
        for cause in onto.causes.values():
            if cause.culprit and culprit and cause.culprit not in culprit.kinds:
                self.errors.append(
                    f"{self.file}: cause {cause.name}: the culprit role cannot bind to {cause.culprit}"
                )
        return onto

    def _members(self, cat: str, cls: str) -> dict[str, URIRef]:
        found = {}
        ns = f"{BASE}/{cat}#"
        for node in self.g.subjects(RDF.type, G[cls]):
            iri = str(node)
            if not iri.startswith(ns) or len(iri) == len(ns):
                self.errors.append(f"{self.file}: {iri} is a {cls} but not in the namespace {ns}")
                continue
            found[iri[len(ns) :]] = node
        return found

    def _of(self, cat: str):
        return sorted(self.members[cat].items())

    def _unknown_terms(self) -> None:
        """A misspelt class, property or individual would otherwise just be
        ignored: everything named in our namespaces must be declared."""
        declared = {URIRef(BASE)}
        for kind in (OWL.Class, OWL.ObjectProperty, OWL.DatatypeProperty, OWL.AnnotationProperty):
            declared.update(s for s in self.g.subjects(RDF.type, kind) if isinstance(s, URIRef))
        for members in self.members.values():
            declared.update(members.values())
        for triple in self.g:
            for term in triple:
                if isinstance(term, URIRef) and str(term).startswith(BASE) and term not in declared:
                    self.errors.append(f"{self.file}: {term} is used but not declared")
                    declared.add(term)

    def _where(self, node: URIRef) -> str:
        return f"{self.file}: {node.n3(self.g.namespace_manager)}"

    def _values(self, node: URIRef, prop: str) -> list:
        return sorted(self.g.objects(node, G[prop]), key=str)

    def _ref(self, node: URIRef, prop: str, value, cat: str) -> str | None:
        name = str(value)[len(f"{BASE}/{cat}#") :]
        if not isinstance(value, URIRef) or self.members[cat].get(name) != value:
            self.errors.append(f"{self._where(node)}: {prop} must name a {CATEGORIES[cat]}, not {value}")
            return None
        return name

    def one(self, node: URIRef, prop: str, cat: str | None = None, required: bool = True):
        values = self._values(node, prop)
        if len(values) > 1:
            self.errors.append(f"{self._where(node)}: more than one {prop}")
        if not values:
            if required:
                self.errors.append(f"{self._where(node)}: no {prop}")
            return None
        value = values[0]
        if cat:
            return self._ref(node, prop, value, cat)
        if not isinstance(value, Literal):
            self.errors.append(f"{self._where(node)}: {prop} must be a value, not {value}")
            return None
        return value.toPython()

    def many(self, node: URIRef, prop: str, cat: str | None = None) -> list:
        out = []
        for value in self._values(node, prop):
            if cat:
                name = self._ref(node, prop, value, cat)
                if name is not None:
                    out.append(name)
            else:
                out.append(str(value))
        return out

    def _choice(self, node: URIRef, prop: str, allowed: tuple[str, ...], required: bool = True):
        value = self.one(node, prop, required=required)
        if value is not None and value not in allowed:
            self.errors.append(f"{self._where(node)}: {prop} must be one of {', '.join(allowed)}")
        return value

    def _provenance(self, node: URIRef) -> None:
        source = self.one(node, "source", required=False)
        if not str(source or "").strip():
            self.errors.append(f"{self._where(node)}: no provenance source")
        self._choice(node, "grade", knowledge.GRADES)

    def _signal(self, name: str, node: URIRef) -> Signal:
        return Signal(
            name=name,
            resource=self.one(node, "measures", "resource"),
            entity=self.one(node, "observedOn", "kind"),
            unit=self.one(node, "unit"),
            kind=self._choice(node, "signalKind", SIGNAL_KINDS),
            impact=bool(self.one(node, "isImpact", required=False)),
        )

    def _event(self, name: str, node: URIRef) -> EventType:
        message_id = self.one(node, "messageId", required=False)
        raw = self.one(node, "messagePattern", required=False)
        if message_id is None and raw is None:
            self.errors.append(f"{self._where(node)}: needs a messageId or a messagePattern")
        pattern = None
        if raw is not None:
            try:
                pattern = re.compile(raw)
            except re.error as err:
                self.errors.append(f"{self._where(node)}: messagePattern: {err}")
        return EventType(
            name=name,
            resource=self.one(node, "about", "resource", required=False),
            emitted_by=self._choice(node, "emittedBy", EMITTERS),
            message_id=message_id,
            pattern=pattern,
        )

    def _sign(self, name: str, node: URIRef) -> ProcessSign:
        pattern = self.one(node, "namePattern")
        if pattern is not None and not pattern.strip():
            self.errors.append(f"{self._where(node)}: empty namePattern")
        return ProcessSign(
            name=name,
            resource=self.one(node, "indicates", "resource"),
            pattern=pattern or "",
            state=self._choice(node, "state", STATES, required=False),
        )

    def _fact(self, name: str, node: URIRef) -> ContextFact:
        return ContextFact(
            name=name,
            resource=self.one(node, "concerns", "resource"),
            entity=self.one(node, "observedOn", "kind"),
            values=frozenset(self.many(node, "allowedValue")),
        )

    def _cause(self, name: str, node: URIRef) -> RootCause:
        resources = frozenset(self.many(node, "concerns", "resource"))
        if not resources:
            self.errors.append(f"{self._where(node)}: concerns no resource")
        return RootCause(name, resources, self.one(node, "culpritKind", "kind", required=False))

    def _action(self, name: str, node: URIRef) -> ActionType:
        return ActionType(name, self.one(node, "targets", "kind"), self.one(node, "controls", "resource"))

    def _protected(self, name: str, node: URIRef) -> ProtectedEntity:
        units = tuple(self.many(node, "unitPattern"))
        commands = tuple(self.many(node, "commandPattern"))
        if not units and not commands:
            self.errors.append(f"{self._where(node)}: needs a unitPattern or a commandPattern")
        return ProtectedEntity(name, units, commands)


# Tables of the TOML layers so far, the field that names something in the
# ontology, and its category.
REFERENCES = (
    ("threshold", "signal", "signal"),
    ("impact", "signal", "signal"),
    ("workload_rule", "class", "class"),
)


def references(onto: Ontology) -> tuple[int, list[str]]:
    """Checks the names the knowledge files use, shipped and host-local.
    Returns the number of entries checked and the problems found."""
    root = knowledge.directory()
    checked, problems = 0, []
    for file in sorted(root.glob("*.toml")) + sorted((root / "local").glob("*.toml")):
        for table, field, category in REFERENCES:
            for i, entry in enumerate(knowledge.entries(file, table), 1):
                checked += 1
                where = f"{file}: [[{table}]] #{i}"
                name = str(entry.get(field))
                problem = onto.missing(category, name, where)
                if problem:
                    problems.append(problem)
                elif table == "impact" and not onto.signals[name].impact:
                    problems.append(f"{where}: {name} does not measure waiting (isImpact)")
    return checked, problems


def consistency(file: Path | None = None) -> dict[str, list[str]]:
    """Runs HermiT over the ontology. Returns the root causes it classified
    under each cause class; raises KnowledgeError when the ontology is
    inconsistent or a class can have no members."""
    try:
        import owlready2
    except ImportError as err:
        raise KnowledgeError(
            "the consistency check needs owlready2 (a development dependency); "
            "run it on the host: cd server && uv run gungnir-core knowledge check"
        ) from err
    if shutil.which(owlready2.JAVA_EXE) is None:
        raise KnowledgeError("the consistency check needs Java for HermiT; none found")

    graph = parse(file or path())
    with tempfile.TemporaryDirectory() as tmp:
        # owlready2 reads N-Triples but not Turtle.
        triples = Path(tmp) / "gungnir.nt"
        graph.serialize(triples, format="nt", encoding="utf-8")
        world = owlready2.World()
        world.get_ontology(triples.as_uri()).load()
        try:
            owlready2.sync_reasoner_hermit(world, infer_property_values=False, debug=0)
        except owlready2.OwlReadyInconsistentOntologyError as err:
            raise KnowledgeError("HermiT: the ontology is inconsistent") from err
        empty = [c.iri for c in world.inconsistent_classes()]
        if empty:
            raise KnowledgeError(f"HermiT: classes that can have no members: {', '.join(empty)}")
        prefix = f"{BASE}/cause#"
        return {
            cls: sorted(i.iri[len(prefix) :] for i in world[G[cls]].instances(world=world))
            for cls in CAUSE_CLASSES
        }
