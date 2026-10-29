from datetime import UTC, datetime, timedelta

import pytest

from gungnir_core import knowledge, workload
from gungnir_core.params import Params
from gungnir_core.workload import Proc, Rule, RuleClassifier, Snapshot, State, accept

T0 = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)
P = Params()
RULES = [
    Rule("build", 20, command=("cc1", "make")),
    Rule("maintenance", 30, unit=("fstrim.service",)),
]


def snap(processes=(), group_io=None, host_io=0.0, host_cpu=0.5):
    return Snapshot(T0, list(processes), group_io or {}, host_io, host_cpu)


def test_shipped_rules_load_with_provenance():
    rules = workload.load_rules()
    assert {r.cls for r in rules} == set(workload.RULED)
    assert all(r.source for r in rules)


def test_a_rule_without_provenance_stops_the_file(tmp_path, monkeypatch):
    (tmp_path / workload.GLOBAL_FILE).write_text('[[workload_rule]]\nclass = "build"\ncommand = ["cc1"]\n')
    monkeypatch.setenv("GUNGNIR_KNOWLEDGE_DIR", str(tmp_path))
    with pytest.raises(knowledge.KnowledgeError, match="provenance"):
        workload.load_rules()


def test_a_rule_counts_from_a_noticeable_share():
    c = RuleClassifier(RULES, P)
    assert c.classify(snap([Proc("cc1", 0.3, None, None)])) == "interactive"
    assert c.classify(snap([Proc("cc1", 0.3, None, None), Proc("make", 0.3, None, None)])) == "build"
    io = {"system.slice/fstrim.service": 30e6, "system.slice/other.service": 70e6}
    assert c.classify(snap(group_io=io, host_io=100e6)) == "maintenance"


def test_the_highest_priority_wins():
    c = RuleClassifier(RULES, P)
    s = snap([Proc("cc1", 4.0, None, None), Proc("fstrim", 0.6, "fstrim.service", None)])
    assert c.classify(s) == "maintenance"


def test_idle_needs_low_cpu_and_almost_no_io():
    c = RuleClassifier(RULES, P)
    assert c.classify(snap(host_cpu=0.02, host_io=10e3)) == "idle"
    assert c.classify(snap(host_cpu=0.02, host_io=50e6)) == "interactive"


def feed(classes, start=T0, state=None):
    state = state or State()
    out = []
    for i, cls in enumerate(classes):
        state, new = accept(state, start + timedelta(seconds=5 * i), cls, P)
        if new:
            out.append(new)
    return state, out


def test_a_class_is_accepted_after_six_samples_and_backdated():
    state, accepted = feed(["idle"] * 6 + ["build"] * 5)
    assert accepted == [("idle", T0)]
    assert state.candidate == "build"
    state, accepted = feed(["build"], T0 + timedelta(seconds=55), state)
    assert accepted == [("build", T0 + timedelta(seconds=30))]


def test_a_short_change_is_not_accepted():
    _, accepted = feed(["idle"] * 6 + ["build"] * 3 + ["idle"] * 3 + ["build"] * 5)
    assert accepted == [("idle", T0)]


def test_a_gap_restarts_the_count():
    state, _ = feed(["idle"] * 6 + ["build"] * 3)
    state, accepted = feed(["build"] * 3, T0 + timedelta(minutes=10), state)
    assert accepted == []


def test_operator_rules_go_to_the_host_file(tmp_path, monkeypatch):
    monkeypatch.setenv("GUNGNIR_KNOWLEDGE_DIR", str(tmp_path))
    (tmp_path / workload.GLOBAL_FILE).write_text("")
    path = workload.assign("laptop", "backup", None, "kopia.service", T0.date())
    assert path.endswith("local/laptop.toml")
    [rule] = workload.load_rules("laptop")
    assert rule.cls == "backup" and rule.unit == ("kopia.service",)
    assert rule.source == "operator, laptop, 2026-10-29"
    assert rule.priority == workload.OPERATOR_PRIORITY
