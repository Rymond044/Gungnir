"""Expected behavior (ADR 0002): which anomalies a workload class explains,
read from `knowledge/expected/expected_behavior.toml`.

The reasoner marks such records expected; they stay evidence, and an
impact record is never expected.
"""

from dataclasses import dataclass
from pathlib import Path

from gungnir_core import frame, knowledge, workload
from gungnir_core.knowledge import KnowledgeError
from gungnir_core.ontology import Ontology
from gungnir_core.rules import DIRECTIONS, names_of

TABLE = "expected"
FIELDS = {"class", "signals", "direction", "on", "provenance"}


@dataclass(frozen=True)
class Expected:
    cls: str
    signals: tuple[str, ...]
    direction: str
    # Only the groups the class's workload rules match.
    members: bool
    source: str

    def explains(self, cls: str, signal: str, direction: str) -> bool:
        return cls == self.cls and signal in self.signals and direction == self.direction


def path() -> Path:
    return knowledge.directory() / "expected" / "expected_behavior.toml"


def load(onto: Ontology, file: Path | None = None) -> list[Expected]:
    file = file or path()
    problems: list[str] = []
    out = []
    for i, item in enumerate(knowledge.entries(file, TABLE), 1):
        where = f"{file}: [[{TABLE}]] #{i}"
        before = len(problems)
        unknown = set(item) - FIELDS
        if unknown:
            problems.append(f"{where}: unknown fields {', '.join(sorted(unknown))}")
        cls = item.get("class")
        if cls not in workload.RULED:
            problems.append(
                f"{where}: class must be one of {', '.join(workload.RULED)}, the classes rules assign"
            )
        signals = names_of(item.get("signals"))
        if not signals:
            problems.append(f"{where}: signals must be a list of names")
        members = item.get("on") == "members"
        if item.get("on") not in (None, "members"):
            problems.append(f'{where}: on is "members" or absent')
        for name in signals:
            problem = onto.missing("signal", name, where)
            if problem:
                problems.append(problem)
                continue
            if not frame.is_checked_kind(name):
                problems.append(f"{where}: detection never checks {name}")
            if onto.signals[name].impact:
                problems.append(f"{where}: {name} measures waiting; impact is never expected")
            if (onto.signals[name].entity == "control_group") != members:
                problems.append(f'{where}: on = "members" goes with group signals, and only with them')
        direction = item.get("direction")
        if direction not in DIRECTIONS:
            problems.append(f"{where}: direction must be up or down")
        if len(problems) == before:
            out.append(Expected(cls, signals, direction, members, item["provenance"]["source"]))
    if problems:
        raise KnowledgeError("\n".join(problems))
    return out
