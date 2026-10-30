import time

import pytest
from pydantic import ValidationError

from gungnir_core.ingest import MAX_SAMPLES, Batch

# Ten minutes ago on a minute boundary: inside raw retention, and its minute
# is complete for rollups.
RECENT = (int(time.time()) // 60 - 10) * 60_000


def batch(**overrides) -> dict:
    body = {
        "agent": {"version": "0.1.0", "started_at": 1_761_000_000_000, "utc_offset_s": 10800},
        "seq": 1,
        "sources": [{"module": "cpu", "state": "preferred", "origin": "/proc/stat"}],
        "dropped": 0,
        "samples": [{"ts": RECENT, "signal": "cpu.iowait_ratio", "entity": "host", "value": 0.02}],
    }
    return body | overrides


def sample(**overrides) -> dict:
    return {"ts": RECENT, "signal": "disk.read_await_ms", "entity": "sda", "value": 1.5} | overrides


def process(**overrides) -> dict:
    return {
        "ts": RECENT,
        "pid": 4242,
        "ppid": 1,
        "comm": "stress-ng",
        "state": "R",
        "threads": 1,
        "cpu_cores": 0.98,
        "rss_bytes": 8 << 20,
        "cgroup": "system.slice/load.service",
        "unit": "load.service",
    } | overrides


def event(**overrides) -> dict:
    return {
        "ts": RECENT,
        "source": "kernel",
        "priority": 3,
        "message": "Out of memory: Killed process 4242 (stress-ng)",
        "key": "s=1;i=1",
    } | overrides


def test_valid_batch_parses():
    parsed = Batch.model_validate(batch())
    assert parsed.samples[0].signal == "cpu.iowait_ratio"


@pytest.mark.parametrize("name", ["cpu", "CPU.idle", "cpu..idle", "cpu.idle-ratio", "1cpu.idle"])
def test_signal_name_must_be_resource_dot_quantity(name):
    with pytest.raises(ValidationError):
        Batch.model_validate(batch(samples=[sample(signal=name)]))


@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_non_finite_values_are_rejected(value):
    with pytest.raises(ValidationError):
        Batch.model_validate(batch(samples=[sample(value=value)]))


def test_unknown_fields_are_rejected():
    with pytest.raises(ValidationError):
        Batch.model_validate(batch(extra="x"))


def test_batch_size_is_bounded():
    with pytest.raises(ValidationError):
        Batch.model_validate(batch(samples=[sample(ts=i) for i in range(MAX_SAMPLES + 1)]))


def test_context_is_optional_for_older_agents():
    parsed = Batch.model_validate(batch())
    assert parsed.facts == parsed.processes == parsed.events == []


def test_event_source_is_closed():
    with pytest.raises(ValidationError):
        Batch.model_validate(batch(events=[event(source="dmesg")]))
