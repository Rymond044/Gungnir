import pytest

from gungnir_core import base, expected, ontology, policy, rules
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


AGAINST = """
[[rule.manifestation]]
anomaly = "mem.dirty_bytes"
direction = "up"
role = "host"
window = [-15, 60]
strength = "contradicting"
provenance = { source = "test", grade = "documented" }
"""


def test_a_contradicting_manifestation_is_neither_anchor_nor_harm(onto, tmp_path):
    [rule] = load_rule(onto, tmp_path, GOOD_RULE + AGAINST).values()
    assert rule.manifestations[2].strength == "contradicting"
    for old, new in (
        ("window = [-15, 60]", "anchor = true"),
        ('role = "host"', 'role = "host"\nharm = true'),
    ):
        with pytest.raises(KnowledgeError, match="contradicting manifestation"):
            load_rule(onto, tmp_path, GOOD_RULE + AGAINST.replace(old, new))


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

[[advisory]]
resource = "cpu"
text = "test"
provenance = { source = "test", grade = "documented" }

[[advisory]]
resource = "memory"
text = "test"
provenance = { source = "test", grade = "documented" }

[[advisory]]
resource = "disk"
text = "test"
provenance = { source = "test", grade = "documented" }

[[advisory]]
resource = "filesystem"
text = "test"
provenance = { source = "test", grade = "documented" }
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


def test_advisories_name_a_cause_or_a_resource_and_cover_every_harmed_resource(onto, shipped, tmp_path):
    p = load_policy(onto, shipped, tmp_path, GOOD_POLICY)
    assert {a.resource for a in p.advisories} == {"cpu", "memory", "disk", "filesystem"}
    both = GOOD_POLICY.replace('resource = "cpu"', 'resource = "cpu"\ncause = "cpu_hog"')
    with pytest.raises(KnowledgeError, match="a cause or a resource"):
        load_policy(onto, shipped, tmp_path, both)
    with pytest.raises(KnowledgeError, match="unknown resource"):
        load_policy(onto, shipped, tmp_path, GOOD_POLICY.replace('resource = "cpu"', 'resource = "gpu"'))
    with pytest.raises(KnowledgeError, match="impact is measured on cpu"):
        load_policy(onto, shipped, tmp_path, GOOD_POLICY.replace('resource = "cpu"', 'cause = "cpu_hog"'))


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


CPU_RULE = """
[[rule]]
cause = "cpu_hog"
provenance = { source = "test", grade = "documented" }

[[rule.manifestation]]
anomaly = "cgroup.cpu_cores"
direction = "up"
role = "culprit"
anchor = true
strength = "characteristic"
provenance = { source = "test", grade = "documented" }

[[rule.manifestation]]
anomaly = "cpu.iowait_ratio"
direction = "up"
role = "host"
window = [0, 30]
strength = "typical"
harm = true
provenance = { source = "test", grade = "documented" }
"""


def test_a_signal_that_reflects_the_disk_is_no_evidence_for_the_cpu(onto, tmp_path):
    with pytest.raises(KnowledgeError, match="reflects waiting on disk"):
        load_rule(onto, tmp_path, CPU_RULE)
    # The disk's own rules may use it.
    [rule] = load_rule(
        onto,
        tmp_path,
        CPU_RULE.replace("cpu_hog", "bulk_reader").replace("cgroup.cpu_cores", "cgroup.read_bytes_per_s"),
    ).values()
    assert rule.manifestations[1].names == ("cpu.iowait_ratio",)


def test_an_anomaly_may_name_its_detector(onto, tmp_path, shipped):
    with pytest.raises(KnowledgeError, match="detector must name"):
        load_rule(
            onto, tmp_path, GOOD_RULE.replace('direction = "up"', 'direction = "up"\ndetector = "magic"', 1)
        )
    assert shipped["memory_leak"].anchor.detectors == ("growth",)


def test_a_weight_takes_a_share_of_its_own_setting(onto, shipped, tmp_path):
    text = GOOD_POLICY.replace(
        'cause = "bulk_sequential_writer"\naction = "io_max_wbps"', 'cause = "cpu_hog"\naction = "cpu_weight"'
    )
    text = text.replace(
        'parameter = { signal = "cgroup.write_bytes_per_s", share = 0.5 }', "parameter = { share = 0.25 }"
    )
    [entry] = load_policy(onto, shipped, tmp_path, text).for_cause("cpu_hog")
    assert (entry.signal, entry.share) == (None, 0.25)
    with pytest.raises(KnowledgeError, match="only a weight"):
        load_policy(onto, shipped, tmp_path, GOOD_POLICY.replace('signal = "cgroup.write_bytes_per_s", ', ""))
