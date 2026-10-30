"""Tunable parameters of the workload classifier, baselines and detectors.

Initial values were agreed for S3 and are to be calibrated on collected data
(`docs/open-questions.md`). A TOML file named by GUNGNIR_PARAMS overrides
any of them by field name; `floors` and `cold_start_share` merge by key.
"""

import os
import tomllib
from dataclasses import dataclass, field, fields, replace
from datetime import timedelta
from fnmatch import fnmatchcase
from pathlib import Path

# Floors of the spread (MAD) by signal name pattern, in the signal's unit.
# Throughput signals use a share of measured capacity instead, when one exists.
DEFAULT_FLOORS = {
    "*_ratio": 0.01,
    "*_bytes_per_s": 64 * 1024,
    "*_ops_per_s": 5.0,
    "*_per_s": 1.0,
    "*_ms": 0.5,
    "*_tasks": 1.0,
    "*_c": 2.0,
    "cpu.thermal_throttles": 1.0,
    "*_cores": 0.05,
    # A desktop's services wake up and go back to sleep all the time.
    "cgroup.*_cores": 0.1,
    "*_events": 1.0,
    "*.oom_kills": 1.0,
    "proc.*": 1.0,
}
# Level signals, where slow drift matters. An EWMA estimates the mean; on a
# skewed rate series the mean sits above the median for good, so the EWMA
# against the baseline median would alarm all the time there.
DEFAULT_EWMA_SIGNALS = ("*_bytes", "*.used_ratio", "proc.*")


@dataclass(frozen=True)
class Params:
    # Seconds between samples; the agent's sampling period.
    step_s: float = 5.0

    # Workload class (ADR 0023).
    class_accept_samples: int = 6
    noticeable_cores: float = 0.5
    noticeable_io_share: float = 0.2
    idle_cpu: float = 0.05
    idle_io_bytes_per_s: float = 1_000_000.0

    # Baselines (ADR 0022).
    baseline_days: int = 7
    # Hours of samples a class needs; a laptop is on a few hours a day and
    # splits them between classes, so a full day would rarely be reached.
    baseline_min_hours: float = 6.0
    power_min_hours: float = 6.0
    power_factor_clamp: tuple[float, float] = (0.5, 2.0)
    # Memory spread floor: a share of the median. A group's memory moves by
    # tens of percent as its caches fill and drain, the host's much less.
    memory_floor_share: float = 0.01
    group_memory_floor_share: float = 0.1
    # Throughput spread floor: a share of measured capacity.
    capacity_floor_share: float = 0.01
    floors: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_FLOORS))
    histogram_days: int = 14

    # Calibration (ADR 0024): label tail kept out of baselines after each
    # phase, warm-up skipped when measuring, quantile taken as capacity.
    label_tail: timedelta = timedelta(seconds=30)
    capacity_warmup: timedelta = timedelta(seconds=10)
    capacity_quantile: float = 0.95
    cold_start_share: dict[str, float] = field(default_factory=lambda: {"*": 0.8})

    # Records (ADR 0021).
    open_after: int = 3
    close_after: int = 3
    # A series without samples this long closes its open records.
    stale_after: int = 12

    # Robust branch.
    robust_k: float = 3.0
    ewma_lambda: float = 0.05
    ewma_l: float = 3.0
    ewma_signals: tuple[str, ...] = DEFAULT_EWMA_SIGNALS

    # Isolation Forest (ADR 0026).
    iforest_window: int = 12
    iforest_every: int = 2
    iforest_trees: int = 100
    iforest_subsample: int = 256
    iforest_quantile: float = 0.999
    iforest_min_minutes: int = 1440
    iforest_named: int = 3

    # Growth of group memory (docs/research/memory.md MG3): hours in a row
    # whose minima rose, and the smallest total rise that counts. Initial
    # values, to be calibrated on the S9 scenarios.
    growth_hours: int = 3
    growth_min_share: float = 0.1
    growth_min_bytes: float = 100 * 1024 * 1024

    # Detection loop.
    detect_every: timedelta = timedelta(seconds=10)
    # Batches arrive every 10 s; ticks younger than this may be incomplete.
    detect_settle: timedelta = timedelta(seconds=20)


def load(path: str | Path | None = None) -> Params:
    path = path or os.environ.get("GUNGNIR_PARAMS")
    params = Params()
    if not path:
        return params
    with open(path, "rb") as f:
        overrides = tomllib.load(f)
    known = {f.name: f for f in fields(Params)}
    changes = {}
    for name, value in overrides.items():
        if name not in known:
            raise ValueError(f"{path}: unknown parameter '{name}'")
        default = getattr(params, name)
        if isinstance(default, dict):
            value = {**default, **value}
        elif isinstance(default, timedelta):
            value = timedelta(seconds=value)
        elif isinstance(default, tuple):
            value = tuple(value)
        changes[name] = value
    return replace(params, **changes)


def by_pattern(table: dict[str, float], signal: str) -> float | None:
    """The value of the most specific pattern matching a signal name: an exact
    name first, then the longest glob."""
    if signal in table:
        return table[signal]
    matches = [p for p in table if fnmatchcase(signal, p)]
    return table[max(matches, key=len)] if matches else None
