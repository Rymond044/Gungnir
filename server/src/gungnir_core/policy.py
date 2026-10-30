"""The action policy (ADR 0035): which action treats which root cause, with
a parameter relative to the target's consumption, read from
`knowledge/policy/*.toml` together with advisories and contraindications.

The loader uses the ontology and the causal rules to check each entry:
the action targets the kind of entity the cause's culprit is, controls a
resource the cause concerns, takes its parameter from a signal of that
kind, and is not contraindicated for the cause.
"""

from dataclasses import dataclass
from pathlib import Path

from gungnir_core import knowledge
from gungnir_core.knowledge import KnowledgeError
from gungnir_core.ontology import Ontology
from gungnir_core.rules import STRENGTHS, Rule, names_of

FIELDS = {
    "policy": {"cause", "action", "target", "when", "parameter", "effectiveness", "side_effects"}
    | {"requires", "excludes", "provenance"},
    "advisory": {"cause", "text", "provenance"},
    "contraindication": {"cause", "actions", "reason", "provenance"},
}
TARGETS = ("culprit",)


@dataclass(frozen=True)
class SideEffect:
    effect: str
    likelihood: str


@dataclass(frozen=True)
class Entry:
    cause: str
    action: str
    target: str
    # The culprit manifestation's alternative that must have matched.
    when: str | None
    # The parameter is `share` of the target's current value of `signal`,
    # or of the action's own current setting (a weight) when there is none.
    signal: str | None
    share: float
    effectiveness: str
    side_effects: tuple[SideEffect, ...]
    requires: dict[str, frozenset[str]]
    excludes: dict[str, frozenset[str]]
    source: str


@dataclass(frozen=True)
class Advisory:
    cause: str
    text: str
    source: str


@dataclass(frozen=True)
class Policy:
    entries: tuple[Entry, ...]
    advisories: tuple[Advisory, ...]
    # Cause -> actions never proposed for it.
    contraindicated: dict[str, frozenset[str]]

    def for_cause(self, cause: str) -> list[Entry]:
        return [e for e in self.entries if e.cause == cause]


def directory() -> Path:
    return knowledge.directory() / "policy"


def load(onto: Ontology, rules: dict[str, Rule], root: Path | None = None) -> Policy:
    root = root or directory()
    files = sorted(root.glob("*.toml"))
    if not files:
        raise KnowledgeError(f"no action policy in {root}")
    problems: list[str] = []
    tables = {t: [] for t in FIELDS}
    for file in files:
        data = knowledge.load(file)
        for table in FIELDS:
            for i, item in enumerate(data.get(table, []), 1):
                where = f"{file}: [[{table}]] #{i}"
                problem = knowledge.provenance_problem(item, where)
                unknown = set(item) - FIELDS[table]
                if problem:
                    problems.append(problem)
                elif unknown:
                    problems.append(f"{where}: unknown fields {', '.join(sorted(unknown))}")
                elif str(item.get("cause")) not in rules:
                    problems.append(f"{where}: '{item.get('cause')}' is not a cause with a rule")
                else:
                    tables[table].append((where, item))

    contraindicated: dict[str, set[str]] = {}
    for where, item in tables["contraindication"]:
        actions = names_of(item.get("actions"))
        if not actions or not str(item.get("reason", "")).strip():
            problems.append(f"{where}: needs actions and a reason")
        for name in actions:
            problem = onto.missing("action", name, where)
            if problem:
                problems.append(problem)
        contraindicated.setdefault(item["cause"], set()).update(actions)

    advisories = []
    for where, item in tables["advisory"]:
        if not str(item.get("text", "")).strip():
            problems.append(f"{where}: no text")
            continue
        advisories.append(Advisory(item["cause"], item["text"], item["provenance"]["source"]))

    checker = _EntryChecker(onto, rules, contraindicated, problems)
    entries = [e for where, item in tables["policy"] if (e := checker.entry(where, item))]
    if problems:
        raise KnowledgeError("\n".join(problems))
    return Policy(
        tuple(entries),
        tuple(advisories),
        {cause: frozenset(actions) for cause, actions in contraindicated.items()},
    )


class _EntryChecker:
    def __init__(self, onto: Ontology, rules: dict[str, Rule], contraindicated: dict, problems: list[str]):
        self.onto = onto
        self.rules = rules
        self.contraindicated = contraindicated
        self.problems = problems

    def entry(self, where: str, item: dict) -> Entry | None:
        before = len(self.problems)
        fail = self.problems.append
        cause = self.onto.causes[item["cause"]]
        action = self.onto.actions.get(str(item.get("action")))
        if action is None:
            fail(f"{where}: unknown action '{item.get('action')}'")
            return None
        target = item.get("target")
        if target not in TARGETS:
            fail(f"{where}: target must be one of {', '.join(TARGETS)}")
        elif cause.culprit is None:
            fail(f"{where}: {cause.name} has no culprit to act on")
        elif action.target != cause.culprit:
            fail(f"{where}: {action.name} acts on a {action.target}, the culprit is a {cause.culprit}")
        if action.resource not in cause.resources:
            fail(f"{where}: {action.name} controls {action.resource}, which {cause.name} does not concern")
        if action.name in self.contraindicated.get(cause.name, ()):
            fail(f"{where}: {action.name} is contraindicated for {cause.name}")

        when = item.get("when")
        if when is not None:
            culprit_signals = {
                n
                for m in self.rules[cause.name].manifestations
                if m.role == "culprit" and m.kind == "anomaly"
                for n in m.names
            }
            if when not in culprit_signals:
                fail(f"{where}: when = {when} is not a signal of the rule's culprit manifestations")

        param = item.get("parameter")
        signal, share = None, None
        if not isinstance(param, dict) or set(param) not in ({"signal", "share"}, {"share"}):
            fail(f"{where}: parameter must be {{ signal = ..., share = ... }} or {{ share = ... }}")
        else:
            signal, share = param.get("signal"), param["share"]
            if signal is None:
                if not action.proportional:
                    fail(f"{where}: only a weight takes a share of its own setting; name a signal")
            elif problem := self.onto.missing("signal", str(signal), where):
                fail(problem)
            elif self.onto.signals[signal].entity != action.target:
                fail(f"{where}: the parameter's signal {signal} is not observed on a {action.target}")
            if not isinstance(share, int | float) or isinstance(share, bool) or share <= 0:
                fail(f"{where}: share must be a positive number")

        if item.get("effectiveness") not in STRENGTHS:
            fail(f"{where}: effectiveness must be one of {', '.join(STRENGTHS)}")
        effects = []
        for effect in item.get("side_effects", []):
            if (
                not isinstance(effect, dict)
                or not str(effect.get("effect", "")).strip()
                or effect.get("likelihood") not in STRENGTHS
            ):
                fail(f"{where}: a side effect needs an effect and a likelihood ({', '.join(STRENGTHS)})")
                continue
            effects.append(SideEffect(effect["effect"], effect["likelihood"]))
        requires = self.conditions(where, item.get("requires", {}), "requires")
        excludes = self.conditions(where, item.get("excludes", {}), "excludes")
        if len(self.problems) > before:
            return None
        return Entry(
            cause.name,
            action.name,
            target,
            when,
            signal,
            float(share),
            item["effectiveness"],
            tuple(effects),
            requires,
            excludes,
            item["provenance"]["source"],
        )

    def conditions(self, where: str, table, field: str) -> dict[str, frozenset[str]]:
        """Context facts only: whether an action can work depends on the
        host, not on the culprit's class."""
        if not isinstance(table, dict):
            self.problems.append(f"{where}: {field} must be a table")
            return {}
        out = {}
        for name, value in table.items():
            values = names_of(value)
            fact = self.onto.facts.get(name)
            if fact is None:
                self.problems.append(f"{where}: {field}: unknown fact '{name}'")
            elif not values or not set(values) <= fact.values:
                self.problems.append(f"{where}: {field}: {name} cannot be {value!r}")
            else:
                out[name] = frozenset(values)
        return out
