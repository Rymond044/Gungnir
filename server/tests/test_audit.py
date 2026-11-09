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
    # Memory that grows available hurts nobody; pressure that rises does.
    assert not out[0].harm
    pressure = rec("mem.pressure_some_ratio", HOST, 7220)
    assert audit._cluster([*later, pressure], [], set(), kb.ontology)[0].harm
