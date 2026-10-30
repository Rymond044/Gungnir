"""The knowledge base as a whole (ADR 0033): the ontology and every layer
that names something in it, loaded and checked together.

The core checks the base when it starts and refuses to start on a broken
one; `gungnir-core knowledge check` also runs the knowledge tests and, on
the host, HermiT.
"""

from dataclasses import dataclass

from gungnir_core import cases, expected, ontology, policy, rules
from gungnir_core.knowledge import KnowledgeError


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


def run_tests(base: Base) -> tuple[int, list[str]]:
    """Runs the knowledge tests; returns how many ran and what failed."""
    found = cases.load(base.ontology, base.rules)
    failures = cases.coverage(base.rules, found)
    for case in found:
        firing = cases.firing(base.rules, case)
        if case.expect is None and firing:
            failures.append(f"{case.where}: expected no cause, got {', '.join(firing)}")
        elif case.expect is not None and case.expect not in firing:
            failures.append(f"{case.where}: {case.expect} does not fire (fired: {', '.join(firing) or '-'})")
        wrong = sorted(set(case.not_) & set(firing))
        if wrong:
            failures.append(f"{case.where}: {', '.join(wrong)} must not fire")
    return len(found), failures
