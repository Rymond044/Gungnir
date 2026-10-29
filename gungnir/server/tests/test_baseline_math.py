import numpy as np
import pytest

from gungnir_core import baselines, frame
from gungnir_core.params import Params, by_pattern


def test_buckets_keep_one_percent_relative_accuracy():
    values = np.array([1e-6, 0.003, 0.5, 1.0, 7.3, 1234.5, 3.2e9, -42.0])
    back = baselines.values_of(baselines.keys_of(values))
    assert np.all(np.abs(back - values) <= 0.0101 * np.abs(values))


def test_zero_has_its_own_bucket():
    keys = baselines.keys_of(np.array([0.0, 1e-12, 1e-6]))
    assert keys[0] == keys[1] == 0 and keys[2] != 0
    assert baselines.values_of(np.array([0]))[0] == 0.0


def test_summed_histograms_give_the_median_and_mad_of_all_samples():
    rng = np.random.default_rng(1)
    hours = [rng.lognormal(3, 0.5, 720) for _ in range(5)]
    merged = baselines.merge([baselines.histogram(h) for h in hours])
    median, mad = baselines.median_mad(*merged)
    allv = np.concatenate(hours)
    true_median = np.median(allv)
    assert median == pytest.approx(true_median, rel=0.02)
    assert mad == pytest.approx(np.median(np.abs(allv - true_median)), rel=0.03)


def hist_row(sid, cls, power, values):
    keys, counts = baselines.histogram(np.asarray(values, dtype=float))
    return (sid, cls, power, keys, counts)


def test_a_class_needs_a_day_of_history():
    p = Params(baseline_min_hours=1)
    short = [hist_row(1, "idle", "eco/ac", [0.1] * 700)]
    assert baselines.compute(short, p) == {}
    enough = [hist_row(1, "idle", "eco/ac", [0.1] * 720)]
    assert (1, "idle") in baselines.compute(enough, p)


def test_power_factor_is_the_ratio_of_medians_and_is_clamped():
    p = Params(baseline_min_hours=1, power_min_hours=1)
    rows = [
        hist_row(1, "interactive", "eco/battery", [0.1] * 720),
        hist_row(1, "interactive", "performance/ac", [0.2] * 1440),
        hist_row(1, "interactive", "balanced/ac", [10.0] * 720),
        hist_row(1, "interactive", "eco/ac", [0.2] * 100),
    ]
    b = baselines.compute(rows, p)[(1, "interactive")]
    assert b.factor("eco/battery") == pytest.approx(0.5, rel=0.02)
    assert b.factor("balanced/ac") == 2.0
    # Too little data in the profile: no correction.
    assert b.factor("eco/ac") == 1.0


def test_one_baseline_for_the_host_sums_the_classes():
    p = Params(baseline_min_hours=1)
    rows = [hist_row(1, "idle", "eco/ac", [0.1] * 400), hist_row(1, "build", "eco/ac", [0.9] * 400)]
    assert baselines.compute(rows, p) == {}
    assert (1, baselines.ALL_CLASSES) in baselines.compute(rows, p, per_class=False)


def test_sparse_group_series_expand_to_zero_rates_and_unchanged_memory():
    grid = np.arange(0, 60_000, 5_000, dtype=np.int64)
    ts = np.array([10_000, 30_000], dtype=np.int64)
    v = np.array([2.0, 3.0])
    rates = frame.expand_sparse(ts, v, grid, carried=False, alive_ms=300_000)
    assert np.isnan(rates[:2]).all()
    assert rates[2:].tolist() == [2.0, 0, 0, 0, 3.0, 0, 0, 0, 0, 0]
    memory = frame.expand_sparse(ts, v, grid, carried=True, alive_ms=300_000)
    assert memory[2:].tolist() == [2.0] * 4 + [3.0] * 6
    # Past a full snapshot period without samples the group has ended.
    late = frame.expand_sparse(ts, v, grid, carried=False, alive_ms=10_000)
    assert np.isnan(late[-1])


def test_floors_take_the_most_specific_pattern():
    floors = Params().floors
    assert by_pattern(floors, "cpu.user_ratio") == 0.01
    assert by_pattern(floors, "disk.read_ops_per_s") == 5.0
    assert by_pattern(floors, "mem.major_faults_per_s") == 1.0
    assert by_pattern(floors, "unknown.thing") is None
