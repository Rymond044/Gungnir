import dataclasses

import pytest

from gungnir_core import base, cases, expected, ontology, policy, rules
from gungnir_core.knowledge import KnowledgeError


@pytest.fixture(scope="module")
def onto():
    return ontology.load()


@pytest.fixture(scope="module")
def shipped(onto):
    return rules.load(onto)


GOOD_RULE = """
[[rule]]
cause = "bulk_sequential_writer"
provenance = { source = "test", grade = "documented" }

[[rule.manifestation]]
anomaly = "cgroup.write_bytes_per_s"
direction = "up"
role = "culprit"
anchor = true
strength = "characteristic"
provenance = { source = "test", grade = "documented" }

[[rule.manifestation]]
anomaly = "cgroup.io_pressure_some_ratio"
direction = "up"
role = "others"
window = [-15, 60]
strength = "characteristic"
harm = true
provenance = { source = "test", grade = "documented" }
"""


def load_rule(onto, tmp_path, text: str):
    (tmp_path / "r.toml").write_text(text)
    return rules.load(onto, tmp_path)


def test_the_shipped_base_loads_and_its_tests_pass():
    kb = base.load()
    assert set(kb.rules) == set(kb.ontology.causes)
    ran, failures = base.run_tests(kb)
    assert ran > 0
    assert failures == []


def test_a_good_rule_loads(onto, tmp_path):
    [rule] = load_rule(onto, tmp_path, GOOD_RULE).values()
    assert rule.anchor.names == ("cgroup.write_bytes_per_s",)
    assert rule.manifestations[1].window == (-15, 60)


@pytest.mark.parametrize(
    ("old", "new", "complaint"),
    [
        ('anomaly = "cgroup.write_bytes_per_s"', 'anomaly = "cgroup.write_bytes"', "unknown signal"),
        ('role = "culprit"', 'role = "device"', "role device binds to block_device"),
        ("window = [-15, 60]", "anchor = true", "exactly one anchor"),
        ("harm = true", "harm = false", "no harm"),
        ('role = "others"', 'role = "culprit"', "not the culprit"),
        ("window = [-15, 60]", "window = [60, -15]", "min <= max"),
        ('strength = "characteristic"\nprovenance', 'strength = "strong"\nprovenance', "strength must be"),
        (
            'direction = "up"\nrole = "culprit"',
            'direction = "up"\nrecord = "impact"\nrole = "culprit"',
            "not an impact",
        ),
        (
            'anomaly = "cgroup.write_bytes_per_s"\ndirection = "up"',
            'value = "cgroup.write_bytes_per_s"\nabove = 1',
            "attaches no value",
        ),
        ('anomaly = "cgroup.write_bytes_per_s"', 'anomaly = "disk.write_request_bytes"', "never checks"),
        ('cause = "bulk_sequential_writer"', 'cause = "filesystem_full"', "has no culprit"),
        (
            "provenance = { source",
            'excludes = { "disk.scheduler" = "cfq" }\nprovenance = { source',
            "cannot be cfq",
        ),
        (
            "provenance = { source",
            'requires = { "swap" = "yes" }\nprovenance = { source',
            "neither a context fact",
        ),
    ],
)
def test_a_broken_rule_is_refused(onto, tmp_path, old, new, complaint):
    with pytest.raises(KnowledgeError, match=complaint):
        load_rule(onto, tmp_path, GOOD_RULE.replace(old, new, 1))


def test_a_rule_without_provenance_on_a_manifestation_is_refused(onto, tmp_path):
    head = GOOD_RULE.rsplit("provenance", 1)[0]
    with pytest.raises(KnowledgeError, match="manifestation #2 has no provenance"):
        load_rule(onto, tmp_path, head + "\n")


GOOD_POLICY = """
[[policy]]
cause = "bulk_sequential_writer"
action = "io_max_wbps"
target = "culprit"
parameter = { signal = "cgroup.write_bytes_per_s", share = 0.5 }
effectiveness = "typical"
provenance = { source = "test", grade = "documented" }

[[contraindication]]
cause = "swap_io"
actions = ["io_max_rbps"]
reason = "test"
provenance = { source = "test", grade = "unverified" }
"""


def load_policy(onto, shipped, tmp_path, text: str):
    (tmp_path / "p.toml").write_text(text)
    return policy.load(onto, shipped, tmp_path)


def test_a_good_policy_loads(onto, shipped, tmp_path):
    p = load_policy(onto, shipped, tmp_path, GOOD_POLICY)
    [entry] = p.for_cause("bulk_sequential_writer")
    assert (entry.action, entry.signal, entry.share) == ("io_max_wbps", "cgroup.write_bytes_per_s", 0.5)
    assert p.contraindicated == {"swap_io": {"io_max_rbps"}}


@pytest.mark.parametrize(
    ("old", "new", "complaint"),
    [
        ('action = "io_max_wbps"', 'action = "ionice"', "acts on a process"),
        ('action = "io_max_wbps"', 'action = "memory_high"', "controls memory"),
        ('cause = "bulk_sequential_writer"', 'cause = "device_errors"', "has no culprit"),
        (
            'signal = "cgroup.write_bytes_per_s"',
            'signal = "disk.write_bytes_per_s"',
            "not observed on a control_group",
        ),
        ("share = 0.5", "share = 0", "positive number"),
        (
            'target = "culprit"',
            'target = "culprit"\nwhen = "cgroup.read_bytes_per_s"',
            "not a signal of the rule",
        ),
        ('effectiveness = "typical"', 'effectiveness = "high"', "effectiveness must be"),
        ('target = "culprit"', 'target = "culprit"\nrequires = { "disk.scheduler" = "cfq" }', "cannot be"),
    ],
)
def test_a_broken_policy_entry_is_refused(onto, shipped, tmp_path, old, new, complaint):
    with pytest.raises(KnowledgeError, match=complaint):
        load_policy(onto, shipped, tmp_path, GOOD_POLICY.replace(old, new, 1))


def test_a_contraindicated_action_is_refused(onto, shipped, tmp_path):
    text = GOOD_POLICY.replace('"bulk_sequential_writer"', '"swap_io"').replace(
        '"io_max_wbps"', '"io_max_rbps"'
    )
    text = text.replace("cgroup.write_bytes_per_s", "cgroup.read_bytes_per_s")
    with pytest.raises(KnowledgeError, match="contraindicated for swap_io"):
        load_policy(onto, shipped, tmp_path, text)


@pytest.mark.parametrize(
    ("entry", "complaint"),
    [
        ('signals = ["cpu.user_ratio"]\ndirection = "up"\non = "members"', "only with them"),
        ('signals = ["cgroup.cpu_cores"]\ndirection = "up"', "only with them"),
        ('signals = ["io.pressure_some_ratio"]\ndirection = "up"', "impact is never expected"),
    ],
)
def test_expected_behavior_is_checked(onto, tmp_path, entry, complaint):
    file = tmp_path / "e.toml"
    file.write_text(
        f'[[expected]]\nclass = "build"\n{entry}\nprovenance = {{ source = "t", grade = "documented" }}\n'
    )
    with pytest.raises(KnowledgeError, match=complaint):
        expected.load(onto, file)


def case(*items, **context) -> cases.Case:
    return cases.Case(
        "t", None, 1, (), context.get("facts", {}), context.get("cls"), tuple(items), "t", "disk"
    )


def rec(signal, role, at, direction="up", record="anomaly", level=None):
    return cases.Item("record", signal, role, at, direction, record, level)


def test_a_rule_fires_only_with_its_manifestations_in_their_windows(shipped):
    rule = shipped["bulk_sequential_writer"]
    seen = [
        rec("cgroup.write_bytes_per_s", "culprit", 100),
        rec("disk.write_bytes_per_s", "device", 105),
        cases.Item("value", "disk.write_request_bytes", "device", 105, value=131072),
        rec("cgroup.io_pressure_some_ratio", "others", 110),
    ]
    assert cases.fires(rule, case(*seen))
    # The harm long after the writes is not this cause.
    late = dataclasses.replace(seen[3], at=300)
    assert not cases.fires(rule, case(*seen[:3], late))
    # Small requests are not a sequential writer.
    small = dataclasses.replace(seen[2], value=4096)
    assert not cases.fires(rule, case(seen[0], seen[1], small, seen[3]))
    # A maintenance culprit makes it D9 instead.
    assert not cases.fires(rule, case(*seen, cls="maintenance"))
    assert cases.fires(shipped["filesystem_maintenance"], case(*seen, cls="maintenance"))


def test_conditions_on_facts(shipped):
    rule = shipped["swap_io"]
    seen = [
        rec("swap.out_pages_per_s", "host", 0),
        rec("swap.in_pages_per_s", "host", 10),
        rec("mem.pressure_some_ratio", "host", 20),
    ]
    assert cases.fires(rule, case(*seen, facts={"swap.enabled": "yes"}))
    assert not cases.fires(rule, case(*seen, facts={"swap.enabled": "no"}))
    # A fact the host did not report does not satisfy a requirement.
    assert not cases.fires(rule, case(*seen))


def test_an_impact_manifestation_needs_an_impact_record(shipped):
    rule = shipped["filesystem_full"]
    assert cases.fires(rule, case(rec("fs.used_ratio", "filesystem", 0, record="impact", level="severe")))
    assert not cases.fires(rule, case(rec("fs.used_ratio", "filesystem", 0)))
    assert not cases.fires(
        rule, case(rec("fs.used_ratio", "filesystem", 0, record="impact", level="noticeable"))
    )
