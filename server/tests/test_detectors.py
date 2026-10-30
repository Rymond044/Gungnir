from datetime import UTC, datetime, timedelta

import numpy as np

from gungnir_core.detection import forest
from gungnir_core.detection.detectors import Context, Limit, LimitDetector, Robust, ewma
from gungnir_core.detection.records import Tracker
from gungnir_core.frame import Frame, SeriesMeta
from gungnir_core.params import Params

P = Params()


class Fixed:
    def __init__(self, table, cold=None):
        self.table, self.cold_limits = table, cold or {}

    def lookup(self, meta, cls, power):
        found = self.table.get((meta.signal, cls))
        return None if found is None else (*found, {"basis": "baseline"})

    def cold(self, meta):
        limit = self.cold_limits.get(meta.signal)
        return None if limit is None else (limit, {"basis": "calibration"})


def make_frame(rows: dict[str, np.ndarray]) -> Frame:
    n = len(next(iter(rows.values())))
    series = [SeriesMeta(i + 1, s, "host") for i, s in enumerate(rows)]
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
