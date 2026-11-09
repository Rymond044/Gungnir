"""The knowledge base as a whole (ADR 0033): the ontology and every layer
that names something in it, loaded and checked together.

The server checks the base when it starts and refuses to start on a broken
one; `gungnir-server knowledge check` also runs the knowledge tests and, on
the host, HermiT.
"""

from dataclasses import dataclass

from gungnir_server import cases, expected, ontology, policy, rules
from gungnir_server.inference import RuleEngine
from gungnir_server.knowledge import KnowledgeError
from gungnir_server.params import Params
from gungnir_server.params import load as load_params


@dataclass(frozen=True)
class Base:
    ontology: ontology.Ontology
    rules: dict[str, rules.Rule]
    expected: list[expected.Expected]
    policy: policy.Policy
    # Detection and workload entries checked against the ontology.
    references: int


def load() -> Base:
    onto = ontology.load()
    checked, problems = ontology.references(onto)
    if problems:
        raise KnowledgeError("\n".join(problems))
    found = rules.load(onto)
    return Base(onto, found, expected.load(onto), policy.load(onto, found), checked)


def run_tests(base: Base, params: Params | None = None) -> tuple[int, list[str]]:
    """Runs the knowledge tests through the inference engine; returns how many ran
    and what failed."""
    engine = RuleEngine(base, params or load_params())
    found = cases.load(base.ontology, base.rules)
    failures = cases.coverage(base.rules, found)
    for case in found:
        diagnosis = engine.diagnose(cases.evidence_of(case))
        harmful = list(dict.fromkeys(h.cause for h in diagnosis.harmful()))
        shown = (
            ", ".join(
                f"{h.cause} {h.confidence:.2f}{'' if h.harm else ' (no harm)'}" for h in diagnosis.hypotheses
            )
            or "-"
        )
        if (case.expect is None or not case.harm) and harmful:
            failures.append(f"{case.where}: expected no cause that harms, got {shown}")
        if case.expect is None:
            continue
        ranked = harmful if case.harm else diagnosis.causes()
        if case.expect not in ranked[: case.within]:
            failures.append(f"{case.where}: {case.expect} is not within the top {case.within} (got {shown})")
        wrong = sorted(set(case.not_) & set(diagnosis.causes()))
        if wrong:
            failures.append(f"{case.where}: {', '.join(wrong)} must stay below the cut-off (got {shown})")
        if case.expected and not (diagnosis.hypotheses and diagnosis.hypotheses[0].expected):
            failures.append(f"{case.where}: the top hypothesis is not marked expected behavior")
    return len(found), failures
