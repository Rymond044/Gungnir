import dataclasses

import pytest

from gungnir_server import base
from gungnir_server.evidence import HOST, Evidence, Record, SeenProcess, Value
from gungnir_server.inference import MATCHED, MISSING, PENDING, UNOBSERVABLE, RuleEngine
from gungnir_server.params import Params

USER = "user.slice/user-1000.slice/user@1000.service"
APPS = f"{USER}/app.slice"
WRITER = f"{APPS}/app-cp.scope"
READER = f"{APPS}/app-editor.scope"
DISK = "nvme0n1"


@pytest.fixture(scope="module")
def engine():
    return RuleEngine(base.load(), Params())


@pytest.fixture(scope="module")
def gate():
    """Shows every hypothesis that passes the gate, however weak: a few
    records leave many typical symptoms missing."""
    return RuleEngine(base.load(), Params(hypothesis_cutoff=0.0))


def rec(signal, entity, start, direction="up", kind="anomaly", level=None, detector="robust", cls=None):
    return Record(
        f"{signal}@{entity}@{start}",
        signal,
        entity,
        kind,
        detector,
        direction,
        float(start),
        level=level,
        workload_class=cls,
    )


def ev(*items, **context) -> Evidence:
    return Evidence(
        records=tuple(i for i in items if isinstance(i, Record)),
        values=tuple(i for i in items if isinstance(i, Value)),
        processes=tuple(i for i in items if isinstance(i, SeenProcess)),
        facts=context.get("facts", {}),
        group_class=context.get("classes", {}),
        until=context.get("until", float("inf")),
        unobservable=frozenset(context.get("unobservable", ())),
    )


def copy(writer=WRITER, victim=READER, at=100):
    """A large copy that makes a reader wait."""
    return [
        rec("cgroup.write_bytes_per_s", writer, at),
        rec("disk.write_bytes_per_s", DISK, at + 5),
        Value("disk.write_request_bytes", DISK, 131072, at + 5),
        rec("cgroup.io_pressure_some_ratio", victim, at + 10),
    ]


def harmful(d) -> list[str]:
    return list(dict.fromkeys(h.cause for h in d.harmful()))


def test_a_rule_needs_its_manifestations_in_their_windows(engine):
    seen = copy()
    assert "bulk_sequential_writer" in harmful(engine.diagnose(ev(*seen)))
    # The impact long after the writes is not this cause's: the writes hurt nobody.
    late = dataclasses.replace(seen[3], start=400.0)
    d = engine.diagnose(ev(*seen[:3], late))
    assert harmful(d) == [] and d.causes() == ["bulk_sequential_writer"]
    # Small requests contradict a characteristic symptom.
    small = dataclasses.replace(seen[2], value=4096)
    assert "bulk_sequential_writer" not in engine.diagnose(ev(seen[0], seen[1], small, seen[3])).causes()
    # A maintenance culprit makes it filesystem maintenance instead.
    causes = engine.diagnose(ev(*seen, classes={WRITER: "maintenance"})).causes()
    assert causes == ["filesystem_maintenance"]


def test_conditions_on_facts(gate):
    engine = gate
    seen = [
        rec("swap.out_pages_per_s", HOST, 0),
        rec("swap.in_pages_per_s", HOST, 10),
        rec("mem.pressure_some_ratio", HOST, 20),
        rec("mem.direct_reclaims_per_s", HOST, 20),
    ]
    assert "swap_io" in engine.diagnose(ev(*seen, facts={"swap.enabled": "yes"})).causes()
    assert "swap_io" not in engine.diagnose(ev(*seen, facts={"swap.enabled": "no"})).causes()
    # A fact the host did not report does not satisfy a requirement.
    assert "swap_io" not in engine.diagnose(ev(*seen)).causes()


def test_an_impact_manifestation_needs_an_impact_record(engine):
    def causes(*records):
        return engine.diagnose(ev(*records)).causes()

    assert causes(rec("fs.used_ratio", "/", 0, kind="impact", level="severe", detector="impact")) == [
        "filesystem_full"
    ]
    assert causes(rec("fs.used_ratio", "/", 0)) == []
    assert causes(rec("fs.used_ratio", "/", 0, kind="impact", level="noticeable", detector="impact")) == []


def test_a_slow_leak_anchors_on_a_growth_record(gate):
    engine = gate
    impact = rec("mem.direct_reclaims_per_s", HOST, 3600)
    assert "memory_leak" not in engine.diagnose(ev(rec("cgroup.mem_bytes", WRITER, 0), impact)).causes()
    growth = rec("cgroup.mem_bytes", WRITER, 0, detector="growth")
    assert "memory_leak" in engine.diagnose(ev(growth, impact)).causes()


def test_the_deepest_group_is_the_culprit_and_its_ancestors_are_not_the_others(engine):
    # A slice writes whatever its children write.
    writes = [rec("cgroup.write_bytes_per_s", g, 100) for g in (WRITER, APPS, "user.slice")]
    seen = writes + copy()[1:3]
    # The pressure of the slice that holds the writer is not impact to others.
    on_own_slice = rec("cgroup.io_pressure_some_ratio", APPS, 110)
    assert harmful(engine.diagnose(ev(*seen, on_own_slice))) == []
    d = engine.diagnose(ev(*seen, rec("cgroup.io_pressure_some_ratio", READER, 110)))
    [writer] = [h for h in d.hypotheses if h.cause == "bulk_sequential_writer"]
    assert writer.binding.culprit == WRITER
    assert writer.binding.device == DISK


def test_two_writers_are_two_hypotheses(engine):
    other = f"{APPS}/app-rsync.scope"
    seen = [*copy(), rec("cgroup.write_bytes_per_s", other, 102)]
    d = engine.diagnose(ev(*seen))
    culprits = [h.binding.culprit for h in d.hypotheses if h.cause == "bulk_sequential_writer"]
    assert sorted(culprits) == sorted([WRITER, other])


def test_a_culprit_is_named_only_by_its_own_evidence(gate):
    swapping = [
        rec("swap.out_pages_per_s", HOST, 3600),
        rec("swap.in_pages_per_s", HOST, 3610),
        rec("mem.pressure_some_ratio", HOST, 3620),
        rec("mem.direct_reclaims_per_s", HOST, 3620),
    ]
    yes = {"swap.enabled": "yes"}
    # A group whose memory grew an hour earlier is no culprit of this swapping.
    stale = dataclasses.replace(rec("cgroup.mem_bytes", READER, 0), end=60.0)
    [h] = [h for h in gate.diagnose(ev(*swapping, stale, facts=yes)).hypotheses if h.cause == "swap_io"]
    assert h.binding.culprit is None
    grew = rec("cgroup.mem_bytes", WRITER, 3500)
    [h] = [h for h in gate.diagnose(ev(*swapping, stale, grew, facts=yes)).hypotheses if h.cause == "swap_io"]
    assert h.binding.culprit == WRITER


def test_of_equal_hypotheses_the_more_unusual_culprit_comes_first(engine):
    other = f"{APPS}/app-rsync.scope"
    seen = copy()
    seen[0] = dataclasses.replace(seen[0], score=2.0)
    d = engine.diagnose(
        ev(*seen, dataclasses.replace(rec("cgroup.write_bytes_per_s", other, 100), score=9.0))
    )
    writers = [h.binding.culprit for h in d.hypotheses if h.cause == "bulk_sequential_writer"]
    assert writers == [other, WRITER]


def test_contradicting_evidence_rules_a_cause_out(engine):
    # A writer that waits on its own writes: forced syncs, or buffered
    # writes throttled at the dirty limit. Dirty pages tell them apart.
    seen = [
        rec("cgroup.write_bytes_per_s", WRITER, 100),
        rec("cgroup.io_pressure_some_ratio", WRITER, 100),
        rec("disk.write_ops_per_s", DISK, 100),
        rec("cgroup.io_pressure_some_ratio", READER, 105),
    ]
    d = engine.diagnose(ev(*seen))
    [h] = [h for h in d.hypotheses if h.cause == "sync_write_storm"]
    [against] = [f for f in h.found if f.symptom.strength == "contradicting"]
    assert against.status == "absent"
    assert "sync_write_storm" not in engine.diagnose(ev(*seen, rec("mem.dirty_bytes", HOST, 110))).causes()
    # Not yet seen while its window is open.
    open_window = engine.diagnose(ev(*seen, until=120.0))
    [h] = [h for h in open_window.hypotheses if h.cause == "sync_write_storm"]
    assert [f.status for f in h.found if f.symptom.strength == "contradicting"] == [PENDING]


def test_a_process_sign_on_the_others_is_outside_the_culprits_line(engine):
    seen = copy()[:3]
    own = SeenProcess("waiting_on_io", "cp", 10, "D", WRITER, 110.0)
    theirs = dataclasses.replace(own, comm="editor", pid=11, group=READER)
    no_psi = {"cgroup.io_pressure_some_ratio", "cgroup.io_pressure_full_ratio"}
    assert harmful(engine.diagnose(ev(*seen, own, unobservable=no_psi))) == []
    assert "bulk_sequential_writer" in harmful(engine.diagnose(ev(*seen, theirs, unobservable=no_psi)))


def test_a_window_still_open_is_pending_not_missing(engine):
    seen = copy()

    def writer(**context):
        d = engine.diagnose(ev(*seen, **context))
        return next(h for h in d.hypotheses if h.cause == "bulk_sequential_writer")

    done, running = writer(), writer(until=120.0)
    # disk.read_await_ms may still come until 100 + 60 s.
    status = {f.symptom.names[0]: f.status for f in running.found}
    assert status["disk.read_await_ms"] == PENDING
    assert {f.symptom.names[0]: f.status for f in done.found}["disk.read_await_ms"] == MISSING
    assert running.support == done.support
    assert running.confidence > done.confidence
    # A characteristic symptom not yet due does not stop the hypothesis.
    no_device = [seen[0], seen[3]]
    assert "bulk_sequential_writer" in engine.diagnose(ev(*no_device, until=105.0)).causes()
    assert "bulk_sequential_writer" not in engine.diagnose(ev(*no_device)).causes()


def test_an_unobservable_manifestation_neither_counts_nor_weakens(engine):
    seen = copy()
    full = next(h for h in engine.diagnose(ev(*seen)).hypotheses if h.cause == "bulk_sequential_writer")
    hidden = {"disk.queue_depth"}
    h = next(
        h
        for h in engine.diagnose(ev(*seen, unobservable=hidden)).hypotheses
        if h.cause == "bulk_sequential_writer"
    )
    assert {f.symptom.names[0]: f.status for f in h.found}["disk.queue_depth"] == UNOBSERVABLE
    assert h.support == full.support
    assert h.confidence > full.confidence


def test_harm_alone_does_not_stand_for_an_unobservable_anchor(gate):
    # No group has a CPU quota, so throttling is never seen; a program that
    # waits for the CPU is not evidence of it.
    waiting = rec("cgroup.cpu_pressure_some_ratio", READER, 0)
    no_quota = {"cgroup.throttled_ratio"}
    assert "cpu_quota_throttling" not in gate.diagnose(ev(waiting, unobservable=no_quota)).causes()
    throttled = rec("cgroup.throttled_ratio", READER, 0)
    assert "cpu_quota_throttling" in gate.diagnose(ev(throttled, waiting)).causes()


def test_one_record_supports_one_symptom(engine):
    d = engine.diagnose(ev(*copy()))
    for h in d.hypotheses:
        items = [f.item for f in h.with_status(MATCHED)]
        assert len(items) == len(set(items))


def test_expected_behavior_marks_without_weakening(engine):
    seen = [
        rec("cgroup.read_bytes_per_s", WRITER, 0),
        rec("disk.read_bytes_per_s", DISK, 0),
        Value("disk.read_request_bytes", DISK, 131072, 0),
        rec("cgroup.io_pressure_some_ratio", READER, 10),
    ]
    plain = engine.diagnose(ev(*seen)).hypotheses[0]
    d = engine.diagnose(ev(*seen, classes={WRITER: "backup"}))
    backup = d.hypotheses[0]
    assert (plain.cause, backup.cause) == ("bulk_reader", "bulk_reader")
    assert not plain.expected and backup.expected
    assert backup.confidence == plain.confidence
    assert seen[0].id in d.expected
    # Impact is never expected.
    assert seen[3].id not in d.expected


def test_harm_without_a_cause_gets_the_advisory_of_its_resource(engine):
    alone = rec("io.pressure_some_ratio", HOST, 0, kind="impact", level="noticeable", detector="impact")
    d = engine.diagnose(ev(alone))
    assert d.hypotheses == ()
    assert [a.resource for a in d.advisories] == ["disk"]
    assert d.unexplained == (alone,)
    # Unusual but harmless: nothing to advise.
    quiet = engine.diagnose(ev(rec("cgroup.write_bytes_per_s", WRITER, 0)))
    assert quiet.advisories == () and len(quiet.unexplained) == 1
    # Impact that ended before the interval asked about is only context.
    past = dataclasses.replace(alone, end=60.0)
    assert engine.diagnose(dataclasses.replace(ev(past), since=90.0)).advisories == ()


def test_evidence_a_hypothesis_uses_is_not_unexplained(engine):
    stray = rec("cpu.forks_per_s", HOST, 50)
    d = engine.diagnose(ev(*copy(), stray))
    assert d.hypotheses[0].cause == "bulk_sequential_writer"
    assert d.unexplained == (stray,)
    # A record that ended before the interval asked about is only context.
    before = dataclasses.replace(stray, end=60.0)
    d = engine.diagnose(dataclasses.replace(ev(*copy(), before), since=90.0))
    assert d.unexplained == ()
    # Nor is it shown as expected behavior.
    build = dataclasses.replace(rec("cgroup.cpu_cores", WRITER, 0, cls="build"), end=60.0)
    d = engine.diagnose(dataclasses.replace(ev(build, classes={WRITER: "build"}), since=90.0))
    assert d.expected == ()


def test_a_hypothesis_must_reach_into_the_interval(engine):
    ended = [dataclasses.replace(r, end=r.start + 20) if isinstance(r, Record) else r for r in copy()]
    assert engine.diagnose(dataclasses.replace(ev(*ended), since=110.0)).causes()
    assert engine.diagnose(dataclasses.replace(ev(*ended), since=200.0)).causes() == []


def test_an_event_keeps_its_kind_and_type_apart_in_json():
    from gungnir_server.evidence import Event
    from gungnir_server.inference import item_dict

    assert item_dict(Event("7", "oom_kill", 1.0, "Out of memory")) == {
        "type": "event",
        "event_type": "oom_kill",
        "id": "7",
        "at": 1.0,
        "message": "Out of memory",
    }


def test_a_writer_that_hurts_nobody_is_explained_without_harm(engine):
    from gungnir_server.inference import as_dict

    quiet = copy()[:3]  # the writes, without the reader waiting
    d = engine.diagnose(ev(*quiet))
    [writer] = [h for h in d.hypotheses if h.cause == "bulk_sequential_writer"]
    assert not writer.harm and writer.binding.culprit == WRITER
    assert as_dict(d)["hypotheses"][0]["harm"] is False
    assert "bulk_sequential_writer" not in {h.cause for h in d.near}
    # Nobody waiting does not make the writer less likely the writer.
    hurt = engine.diagnose(ev(*copy()))
    [same] = [h for h in hurt.hypotheses if h.cause == "bulk_sequential_writer"]
    assert same.harm and same.confidence >= writer.confidence


def test_each_burst_of_the_leading_symptom_is_its_own_hypothesis(engine):
    first, second = copy(at=100)[:3], copy(at=4000)[:3]
    d = engine.diagnose(ev(*first, *second))
    starts = sorted(h.leading_at for h in d.hypotheses if h.cause == "bulk_sequential_writer")
    assert starts == [100.0, 4000.0]


def test_an_advisory_answers_impact_that_no_harmful_hypothesis_explains(engine):
    # A reader waits on the disk while no writer is seen.
    alone = engine.diagnose(ev(rec("cgroup.io_pressure_some_ratio", READER, 10)))
    assert [a.resource for a in alone.advisories] == ["disk"]
    assert engine.diagnose(ev(*copy())).advisories == ()
