from datetime import UTC, datetime, timedelta

import numpy as np

from gungnir_core.detection import forest
from gungnir_core.detection.detectors import (
    Context,
    Limit,
    LimitDetector,
    Robust,
    ewma,
    group_cold,
    pressure_levels,
)
from gungnir_core.detection.records import Tracker
from gungnir_core.frame import Frame, SeriesMeta, is_group
from gungnir_core.params import Params

P = Params()


class Fixed:
    def __init__(self, table, cold=None):
        self.table, self.cold_limits = table, cold or {}

    def lookup(self, meta, cls, power):
        found = self.table.get((meta.signal, cls))
        return None if found is None else (*found, {"basis": "baseline"})

    def cold(self, meta, ctx):
        limit = self.cold_limits.get(meta.signal)
        return None if limit is None else (limit, {"basis": "calibration"})


class GroupsOnly(Fixed):
    """No baselines at all: every group is new."""

    def __init__(self, capacity=None, pressure=None):
        super().__init__({})
        self.capacity, self.pressure = capacity or {}, pressure or {}

    def cold(self, meta, ctx):
        return group_cold(meta, ctx, self.capacity, self.pressure) if is_group(meta.signal) else None


def make_frame(rows: dict[str, np.ndarray], entities: dict[str, str] | None = None) -> Frame:
    n = len(next(iter(rows.values())))
    entities = entities or {}
    series = [SeriesMeta(i + 1, s, entities.get(s, "host")) for i, s in enumerate(rows)]
    return Frame(np.arange(n, dtype=np.int64) * 5000, series, np.array(list(rows.values()), dtype=float))


def context(f, cls="interactive"):
    n = f.ts.size
    return Context(f, 0, np.full(n, cls, dtype=object), np.full(n, "eco/ac", dtype=object), P)


def records(detector, ctx):
    t = Tracker(P)
    out = []
    for ev in detector.evaluate(ctx):
        out += [(ev.detector, e) for e in t.feed(ev)]
    return out


def test_robust_flags_a_jump_against_the_class_baseline():
    x = np.full(40, 0.20)
    x[20:26] = 0.60
    ctx = context(make_frame({"cpu.user_ratio": x}))
    found = records(Robust(Fixed({("cpu.user_ratio", "interactive"): (0.2, 0.02)}), ewma=False), ctx)
    [(_, opened), (_, closed)] = found
    assert opened.started_ms == 100_000 and opened.records[0].direction == "up"
    assert closed.ended_ms == 130_000
    # The same jump is normal in a class whose baseline is high.
    ctx = context(make_frame({"cpu.user_ratio": x}), cls="build")
    assert records(Robust(Fixed({("cpu.user_ratio", "build"): (0.5, 0.1)}), ewma=False), ctx) == []


def test_cold_start_judges_against_capacity():
    x = np.full(20, 1e8)
    x[5:10] = 2e9
    ctx = context(make_frame({"disk.write_bytes_per_s": x}))
    found = records(Robust(Fixed({}, cold={"disk.write_bytes_per_s": 1.6e9})), ctx)
    assert [d for d, _ in found] == ["robust.mad", "robust.mad"]
    assert found[0][1].records[0].baseline == {"basis": "calibration"}


GROUP = "user.slice/user-1000.slice/user@1000.service/app.slice/app-cp.scope"


def group_records(rows: dict[str, np.ndarray], **cold) -> list:
    """Records of the group series in `rows`, the others being host series."""
    entities = {s: GROUP for s in rows if s.startswith("cgroup.")}
    found = records(Robust(GroupsOnly(**cold), ewma=False), context(make_frame(rows, entities)))
    return [(e.records[0].baseline["of"], e.records[0].direction) for _, e in found if hasattr(e, "records")]


def test_a_new_group_is_judged_against_noticeable_levels():
    busy = np.full(20, 0.1)
    busy[5:10] = 1.5
    assert group_records({"cgroup.cpu_cores": busy}) == [("noticeable cores", "up")]
    # A slice sums its children, which are judged themselves.
    slice_ctx = context(make_frame({"cgroup.cpu_cores": busy}, {"cgroup.cpu_cores": "user.slice"}))
    assert records(Robust(GroupsOnly(), ewma=False), slice_ctx) == []
    writes = np.full(20, 1e6)
    writes[5:10] = 9e8
    capacity = {("disk.write_bytes_per_s", "nvme0n1"): 2e9}
    assert group_records({"cgroup.write_bytes_per_s": writes}, capacity=capacity) == [
        ("20% of measured disk.write_bytes_per_s", "up")
    ]
    # Without a calibration there is nothing to judge I/O against.
    assert group_records({"cgroup.write_bytes_per_s": writes}) == []
    stalled = np.full(20, 0.0)
    stalled[5:10] = 0.4
    levels = {"io.pressure_some_ratio": 0.3}
    assert group_records({"cgroup.io_pressure_some_ratio": stalled}, pressure=levels) == [
        ("noticeable impact of io.pressure_some_ratio", "up")
    ]


def test_a_new_group_s_memory_is_judged_against_a_share_of_host_memory():
    n = 20
    total, used = 16e9, 0.5
    grows = np.full(n, 1e9)
    grows[5:10] = 2e9
    rows = {
        "mem.available_bytes": np.full(n, total * (1 - used)),
        "mem.used_ratio": np.full(n, used),
        "cgroup.mem_bytes": grows,
    }
    assert group_records(rows) == [("10% of host memory", "up")]


def test_group_pressure_follows_the_host_s_lowest_impact_level():
    levels = pressure_levels()
    assert levels["io.pressure_some_ratio"] == 0.30
    assert levels["mem.pressure_full_ratio"] == 0.60
    assert not any(name.startswith("cgroup.") for name in levels)


def test_ewma_catches_drift_the_sample_test_misses():
    x = 1e9 + np.arange(300) * 1e5  # slow growth, each sample within 3 MAD
    baselines = Fixed({("mem.anon_bytes", "interactive"): (1e9, 1e7)})
    found = records(Robust(baselines), context(make_frame({"mem.anon_bytes": x})))
    assert {d for d, _ in found} == {"robust.ewma"}
    # The same creep in a control group is no drift alarm.
    baselines = Fixed({("cgroup.mem_bytes", "interactive"): (1e9, 1e7)})
    assert records(Robust(baselines), context(make_frame({"cgroup.mem_bytes": x}))) == []
    s, last = ewma(np.array([1.0, np.nan, 3.0]), 0.5, None)
    assert np.isnan(s[1]) and s[2] == 2.0 and last == 2.0


def test_limits_hold_for_every_entity_and_below_works():
    f = make_frame({"cpu.idle_ratio": np.r_[np.full(5, 0.5), np.full(5, 0.05)]})
    d = LimitDetector("threshold", "anomaly", [Limit("cpu.idle_ratio", None, 0.10, "test")])
    [(_, opened)] = records(d, context(f))
    assert opened.records[0].direction == "down" and opened.started_ms == 25_000


def test_forest_names_three_signals_of_an_odd_window():
    rng = np.random.default_rng(3)
    n_train = 2000
    mean = np.column_stack([rng.normal(0.1, 0.01, n_train) for _ in forest.NAMES])
    train = forest.to_features(np.abs(mean), np.abs(mean) * 1.1)
    model = forest.train(train, "interactive", datetime(2026, 10, 29, tzinfo=UTC), P)
    assert model is not None

    n = 60
    rows = {s: np.abs(rng.normal(0.1, 0.01, n)) for s in forest.NAMES}
    # A write storm moves many signals at once; the further three go, the
    # more the records name them.
    storm = [
        "disk.write_ops_per_s",
        "disk.util_ratio",
        "io.pressure_full_ratio",
        "cpu.iowait_ratio",
        "cpu.pressure_some_ratio",
    ]
    for s in storm:
        rows[s][30:] = 5.0
    for s in ("disk.write_bytes_per_s", "disk.write_await_ms", "io.pressure_some_ratio"):
        rows[s][30:] = 500.0
    f = make_frame(rows)
    det = forest.ForestDetector(None, None, P, store=False)
    det.models = {"interactive": model}
    found = records(det, context(f))
    opened = found[0][1]
    named = {f.series[[m.id for m in f.series].index(r.series_id)].signal for r in opened.records}
    assert named == {"disk.write_bytes_per_s", "disk.write_await_ms", "io.pressure_some_ratio"}
    assert opened.started_ms >= 150_000
    assert not forest.train(train[:100], "idle", datetime(2026, 10, 29, tzinfo=UTC), P)
    assert timedelta(seconds=P.iforest_window * P.step_s) == timedelta(minutes=1)


def test_growth_needs_rising_hourly_minima_by_a_noticeable_share():
    from gungnir_core.detection.growth import Growth

    # Five hours at one sample a minute; caches swing by 200 MiB within the
    # hour, while the floor under them rises by 100 MiB an hour from 1 GiB.
    mib = 1024 * 1024
    minutes = np.arange(5 * 60)
    swing = np.where(minutes % 7 == 0, 0, 200 * mib)
    leak = 1024 * mib + (minutes // 60) * 100 * mib + swing
    flat = 1024 * mib + swing
    f = Frame(
        minutes.astype(np.int64) * 60_000,
        [
            SeriesMeta(1, "cgroup.mem_bytes", "app.slice/leaky.service"),
            SeriesMeta(2, "cgroup.mem_bytes", "app.slice/steady.service"),
        ],
        np.array([leak, flat], dtype=float),
    )
    ctx = context(f)
    found = records(Growth(), ctx)
    opened = [e for _, e in found if hasattr(e, "records")]
    # Hour 4 is the first with four completed hours, minima rising three
    # times by 300 MiB in all, over 10 % of 1 GiB.
    assert [e.records[0].series_id for e in opened] == [1]
    assert opened[0].started_ms == 4 * 3_600_000
    assert opened[0].records[0].baseline["rise"] == 300 * mib
    # State keeps a few hours per group, as JSON.
    assert set(ctx.state["minima"]) == {"1", "2"}
