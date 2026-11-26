"""Memory activity needs measured waiting or OOM before it shows impact."""

from dataclasses import replace

import numpy as np
import pytest

from gungnir_server import base
from gungnir_server.detection.detectors import Context, impact, thresholds
from gungnir_server.detection.records import Opened, Tracker
from gungnir_server.evidence import HOST, Evidence, Record
from gungnir_server.frame import Frame, SeriesMeta
from gungnir_server.inference import RuleEngine
from gungnir_server.params import Params

LEAK = "user.slice/user-1000.slice/user@1000.service/app.slice/leak.scope"
AGENT = "system.slice/gungnir-agent.service"
OTHER = "system.slice/editor.service"


@pytest.fixture(scope="module")
def engine():
    return RuleEngine(base.load(), Params())


def rec(signal, entity, at, value=1.0, **kwargs):
    return Record(
        f"{signal}@{entity}@{at}",
        signal,
        entity,
        "anomaly",
        "robust.mad",
        "up",
        float(at),
        value=value,
        **kwargs,
    )


def diagnose(engine, records):
    return engine.diagnose(
        Evidence(records=tuple(records), facts={"swap.enabled": "yes"}, since=3600.0, until=3800.0)
    )


def swaps(engine, *, pressure=0.01, other=0.02, fresh_growth=False):
    records = [
        replace(rec("cgroup.mem_bytes", LEAK, 0, 6e9), detector="growth"),
        rec("swap.out_pages_per_s", HOST, 3600, 1000),
        rec("swap.in_pages_per_s", HOST, 3610, 1000),
        rec("mem.pressure_some_ratio", HOST, 3620, pressure),
        rec("mem.direct_reclaims_per_s", HOST, 3620, 10),
        rec("cgroup.mem_pressure_some_ratio", OTHER, 3620, other),
    ]
    if fresh_growth:
        records.append(rec("cgroup.mem_bytes", LEAK, 3500, 6e9))
    diagnosis = diagnose(engine, records)
    return next(h for h in diagnosis.hypotheses if h.cause == "swap_io")


def test_unbound_swap_with_low_pressure_does_not_turn_reclaim_into_impact(engine):
    hypothesis = swaps(engine)
    assert hypothesis.binding.culprit is None
    assert not hypothesis.harm
    assert hypothesis.hurt == ()


@pytest.mark.parametrize("measured", ["host", "other"])
def test_unbound_swap_with_noticeable_waiting_remains_harmful(engine, measured):
    hypothesis = swaps(engine, **{"pressure" if measured == "host" else "other": 0.12})
    assert hypothesis.binding.culprit is None
    assert hypothesis.harm
    assert any("pressure" in item.signal for item in hypothesis.hurt)
    assert all(item.signal != "mem.direct_reclaims_per_s" for item in hypothesis.hurt)


def test_bound_swap_does_not_use_its_own_host_waiting_as_impact(engine):
    hypothesis = swaps(engine, pressure=0.12, fresh_growth=True)
    assert hypothesis.binding.culprit == LEAK
    assert not hypothesis.harm


def test_bound_swap_retains_independent_other_group_waiting(engine):
    hypothesis = swaps(engine, other=0.12, fresh_growth=True)
    assert hypothesis.binding.culprit == LEAK
    assert hypothesis.harm
    assert {item.entity for item in hypothesis.hurt} == {OTHER}


@pytest.mark.parametrize(
    ("pressure", "oom", "harm"), [(0.0008368, False, False), (0.12, False, True), (0.0008368, True, True)]
)
def test_local_memory_max_needs_group_waiting_or_oom(engine, pressure, oom, harm):
    records = [
        rec("cgroup.mem_max_events", AGENT, 3600, 709),
        rec("cgroup.mem_pressure_some_ratio", AGENT, 3610, pressure),
    ]
    if oom:
        records.append(
            replace(rec("cgroup.oom_kills", AGENT, 3620), kind="impact", detector="impact", level="severe")
        )
    diagnosis = diagnose(engine, records)
    hypothesis = next(h for h in diagnosis.hypotheses if h.cause == "group_memory_limit")
    assert hypothesis.binding.culprit is None
    assert hypothesis.harm is harm
    assert all(item.signal != "cgroup.mem_max_events" for item in hypothesis.hurt)


def test_max_event_is_detected_as_activity_without_an_impact_record():
    params = Params()
    values = np.zeros(20)
    values[5:10] = 709
    frame = Frame(
        np.arange(20, dtype=np.int64) * 5000, [SeriesMeta(1, "cgroup.mem_max_events", AGENT)], values[None, :]
    )
    context = Context(
        frame, 0, np.full(20, "interactive", dtype=object), np.full(20, "balanced/ac", dtype=object), params
    )
    impact_tracker = Tracker(params)
    assert not [
        event
        for evaluation in impact().evaluate(context)
        for event in impact_tracker.feed(evaluation)
        if isinstance(event, Opened)
    ]

    activity_tracker = Tracker(params)
    opened = [
        event
        for evaluation in thresholds().evaluate(context)
        for event in activity_tracker.feed(evaluation)
        if isinstance(event, Opened)
    ]
    assert len(opened) == 1
    assert opened[0].kind == "anomaly"
    assert opened[0].records[0].series_id == 1


@pytest.mark.parametrize("unrelated", ["pressure", "oom"])
def test_memory_limit_does_not_take_another_groups_impact(engine, unrelated):
    records = [
        rec("cgroup.mem_max_events", AGENT, 3600, 709),
        rec("cgroup.mem_pressure_some_ratio", AGENT, 3610, 0.0008),
    ]
    if unrelated == "pressure":
        records.append(rec("cgroup.mem_pressure_some_ratio", OTHER, 3605, 0.20))
    else:
        records.append(
            replace(rec("cgroup.oom_kills", OTHER, 3605), kind="impact", detector="impact", level="severe")
        )
    diagnosis = diagnose(engine, records)
    hypothesis = next(h for h in diagnosis.hypotheses if h.cause == "group_memory_limit")
    assert not hypothesis.harm
    assert all(found.item.entity != OTHER for found in hypothesis.found if isinstance(found.item, Record))


@pytest.mark.parametrize(
    ("cause", "leading_signal", "pressure_signal"),
    [
        ("group_memory_throttled", "cgroup.mem_high_events", "cgroup.mem_pressure_some_ratio"),
        ("cpu_quota_throttling", "cgroup.throttled_ratio", "cgroup.cpu_pressure_some_ratio"),
    ],
)
def test_a_group_limit_matches_pressure_on_its_own_subject(engine, cause, leading_signal, pressure_signal):
    diagnosis = diagnose(
        engine,
        [
            rec(leading_signal, AGENT, 3600, 1),
            rec(pressure_signal, OTHER, 3605, 0.20),
            rec(pressure_signal, AGENT, 3610, 0.02),
        ],
    )
    hypothesis = next(h for h in diagnosis.hypotheses if h.cause == cause)
    pressure = [
        found.item
        for found in hypothesis.found
        if isinstance(found.item, Record) and found.item.signal == pressure_signal
    ]
    assert [item.entity for item in pressure] == [AGENT]
    assert all(item.entity != OTHER for item in hypothesis.hurt)


def test_simultaneous_memory_limits_keep_their_own_subjects(engine):
    diagnosis = diagnose(
        engine,
        [
            rec("cgroup.mem_max_events", AGENT, 3600, 709),
            rec("cgroup.mem_max_events", OTHER, 3600, 1),
            rec("cgroup.mem_pressure_some_ratio", AGENT, 3610, 0.0008),
            rec("cgroup.mem_pressure_some_ratio", OTHER, 3605, 0.20),
        ],
    )
    hypotheses = [h for h in diagnosis.hypotheses if h.cause == "group_memory_limit"]
    subjects = {next(f.item.entity for f in h.found if f.symptom.leading): h.harm for h in hypotheses}
    assert len(hypotheses) == 2
    assert subjects == {AGENT: False, OTHER: True}
