import json
from datetime import UTC, datetime, timedelta

import numpy as np

from gungnir_core import baselines, capacity, labels, workload
from gungnir_core.params import Params
from tests import seed
from tests.test_wire_format import RECENT

HOUR = datetime.fromtimestamp(RECENT / 1000, tz=UTC).replace(minute=0, second=0) - timedelta(hours=1)
P = Params()


def phase_line(**overrides):
    base = {
        "run": "r1-calibration",
        "kind": "calibration",
        "phase": "disk-seqwrite",
        "start_ms": int((HOUR + timedelta(minutes=10)).timestamp() * 1000),
        "end_ms": int((HOUR + timedelta(minutes=11)).timestamp() * 1000),
        "expected": ["disk.write_bytes_per_s"],
    }
    return json.dumps(base | overrides)


def test_label_files_are_validated():
    [p] = labels.parse([phase_line(), ""])
    assert p.phase == "disk-seqwrite" and not p.normal
    try:
        labels.parse([phase_line(end_ms=0)])
    except ValueError as err:
        assert "line 1" in str(err)
    else:
        raise AssertionError("an inverted phase was accepted")


def test_import_keeps_the_run_and_capacity_comes_from_its_samples(conn):
    host = seed.add_host(conn)
    phases = labels.parse([phase_line()])
    labels.import_phases(conn, host, phases, P.label_tail)
    labels.import_phases(conn, host, phases, P.label_tail)
    assert conn.execute("SELECT count(*) FROM labeled_runs").fetchone() == (1,)
    assert conn.execute("SELECT count(*) FROM kept_ranges").fetchone() == (1,)

    start = HOUR + timedelta(minutes=10)
    rows = [(t, "disk.write_bytes_per_s", "nvme0n1", 2e9) for t in seed.ticks(start, 12)]
    rows += [(t, "disk.write_bytes_per_s", "sda", 1e6) for t in seed.ticks(start, 12)]
    seed.samples(conn, host, rows)
    [c] = capacity.calibrate(conn, host, "r1-calibration", P)
    assert (c.signal, c.entity, c.value) == ("disk.write_bytes_per_s", "nvme0n1", 2e9)
    assert capacity.capacities(conn, host) == {("disk.write_bytes_per_s", "nvme0n1"): 2e9}
    # Not known before the run ended.
    assert capacity.capacities(conn, host, start) == {}


def test_classified_hours_give_histograms_without_labeled_phases(conn):
    host = seed.add_host(conn)
    ticks = seed.ticks(HOUR, 720)
    rng = np.random.default_rng(0)
    rows = [(t, "cpu.idle_ratio", "host", 0.5) for t in ticks]
    writes = rng.lognormal(10, 1, 720)
    rows += [(t, "disk.write_bytes_per_s", "nvme0n1", float(v)) for t, v in zip(ticks, writes, strict=True)]
    rows += [(t, "agent.cpu_cores", "host", 0.01) for t in ticks]
    # A sparse group: one sample every 5 minutes, zero rates in between.
    rows += [(t, "cgroup.cpu_cores", "system.slice/x.service", 1.0) for t in ticks[::60]]
    seed.samples(conn, host, rows)
    seed.processes(conn, host, [(t, "cc1", 2.0, "build.service") for t in ticks])
    labels.import_phases(conn, host, labels.parse([phase_line()]), P.label_tail)

    classifier = workload.RuleClassifier(workload.load_rules(), P)
    state, accepted = workload.run(conn, host, classifier, P, HOUR + timedelta(hours=2))
    assert accepted == 1 and state.current == "build"
    assert baselines.build_due(conn, host, P, HOUR + timedelta(hours=2)) == 1

    rows = baselines.load_hists(conn, host, HOUR, HOUR + timedelta(hours=1))
    signals = {
        sid: sig for sid, sig in conn.execute("SELECT id, signal FROM series WHERE host_id = %s", (host.id,))
    }
    by_signal = {signals[r[0]]: r for r in rows}
    assert "agent.cpu_cores" not in by_signal
    assert {r[1] for r in rows} == {"build"} and {r[2] for r in rows} == {"unknown/ac"}
    # The labeled minute and its tail stay out: 720 ticks minus 18.
    assert by_signal["cpu.idle_ratio"][5].sum() == 720 - 18
    group = by_signal["cgroup.cpu_cores"]
    assert group[5].sum() == 720 - 18
    assert baselines.values_of(group[4]).tolist().count(0.0) == 1

    book = baselines.Book(conn, host, Params(baseline_min_hours=0.5)).at(HOUR + timedelta(hours=1, minutes=5))
    idle_id = next(k for k, v in signals.items() if v == "cpu.idle_ratio")
    b = book.get(idle_id, "build")
    assert b is not None and abs(b.median - 0.5) < 0.005
