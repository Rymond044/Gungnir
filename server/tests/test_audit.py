import dataclasses

import pytest

from gungnir_server import audit, base
from gungnir_server.evidence import HOST
from gungnir_server.inference import RuleEngine
from gungnir_server.params import Params
from tests.test_inference import WRITER, copy, ev, rec


@pytest.fixture(scope="module")
def kb():
    return base.load()


@pytest.fixture(scope="module")
def engine(kb):
    return RuleEngine(kb, Params())


def command(pgid, name="cp"):
    return {
        "pgid": pgid,
        "leader": name,
        "names": [[name, 1]],
        "pids": 1,
        "cores": 0.5,
        "rss_bytes": 1,
        "unit": None,
    }


def closed(seen):
    """Records that ended 20 s after they started."""
    return [dataclasses.replace(r, end=r.start + 20) if hasattr(r, "end") else r for r in seen]


def stretches_of(engine, kb, seen, slices):
    d = engine.diagnose(ev(*seen))
    return audit._join(d.harmful() or d.hypotheses, slices, kb.ontology)


def test_two_bursts_of_one_process_group_are_one_stretch(engine, kb):
    first, second = closed(copy(at=100)), closed(copy(at=300))
    slices = {first[0].id: [command(42)], second[0].id: [command(42)]}
    [one] = stretches_of(engine, kb, [*first, *second], slices)
    assert len(one.bursts) == 2 and one.lane == "disk"


def test_a_new_process_group_is_a_new_stretch(engine, kb):
    first, second = closed(copy(at=100)), closed(copy(at=300))
    slices = {first[0].id: [command(42)], second[0].id: [command(77)]}
    assert len(stretches_of(engine, kb, [*first, *second], slices)) == 2
    # Without process groups nothing tells one copy from two.
    assert len(stretches_of(engine, kb, [*first, *second], {})) == 2


def test_a_long_record_does_not_join_what_happens_hours_later(kb):
    long = rec("mem.available_bytes", HOST, 0, direction="up")
    later = [rec("mem.shmem_bytes", HOST, 7200), rec("cgroup.mem_bytes", WRITER, 7210)]
    out = audit._cluster([long, *later], [], set(), kb.ontology)
    assert [len(s.records) for s in out] == [1, 2]
    # Memory that grows available hurts nobody; pressure above its usual
    # level is waiting, not harm, until it reaches an impact level (ADR 0059).
    assert not out[0].harm
    pressure = rec("mem.pressure_some_ratio", HOST, 7220)
    assert not audit._cluster([*later, pressure], [], set(), kb.ontology)[0].harm
    assert audit._waits(pressure, kb.ontology)
    severe = rec("mem.pressure_some_ratio", HOST, 7220, kind="impact", level="noticeable")
    assert audit._cluster([*later, severe], [], set(), kb.ontology)[0].harm


def test_a_program_using_more_than_usual_is_its_activity_not_unexplained(kb):
    # A terminal's CPU and the host's context switches rose together, and no
    # cause fits: the activity of the terminal, with the host's record in it.
    terminal = "user.slice/user-1000.slice/session.slice/kitty.scope"
    group = dataclasses.replace(rec("cgroup.cpu_cores", terminal, 100), score=9.0)
    slice_ = dataclasses.replace(rec("cgroup.cpu_cores", "user.slice", 100), score=12.0)
    switches = rec("cpu.context_switches_per_s", HOST, 110)
    [s] = audit._cluster([group, slice_, switches], [], set(), kb.ontology)
    # Of a group and the slice it is in, the deepest did it.
    assert audit._actor(s.records, kb.ontology) == terminal
    # Only the host's own signals unusual: nobody to name.
    assert audit._actor([switches], kb.ontology) is None


def test_host_records_alongside_an_explained_stretch_go_with_it(engine, kb):
    seen = closed(copy(at=100))
    d = engine.diagnose(ev(*seen))
    explained = audit._join(d.hypotheses, {}, kb.ontology)
    util = rec("disk.util_ratio", "nvme0n1", 110)
    later = rec("disk.util_ratio", "nvme0n1", 2000)
    impact = rec("io.pressure_some_ratio", HOST, 110, kind="impact", level="noticeable")
    assert audit._alongside(util, explained, kb.ontology)
    assert not audit._alongside(later, explained, kb.ontology)
    # Impact no hypothesis used stays harm of its own.
    assert not audit._alongside(impact, explained, kb.ontology)
    assert explained[0].also == [util]
