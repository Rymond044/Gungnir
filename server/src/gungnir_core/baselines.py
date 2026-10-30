"""Hourly log-bucket histograms and the baselines computed from them (ADR 0022).

Every complete hour each checked series gets a histogram of its samples per
(workload class, power profile). Buckets are logarithmic with about 1 %
relative accuracy, as in DDSketch; zero has its own bucket. Histograms add
up, so a seven-day baseline (median and MAD) comes from the sum of 168
hourly ones with an error within one bucket.

Hours are built only after the workload classifier has settled them and
before the raw partition expires; samples inside labeled phases are left
out, except phases marked normal.
"""

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np
import psycopg

from gungnir_core import frame, workload
from gungnir_core.capacity import capacities
from gungnir_core.hosts import Host
from gungnir_core.labels import excluded, phases_of
from gungnir_core.params import Params, by_pattern

log = logging.getLogger(__name__)

ALPHA = 0.01
GAMMA = (1 + ALPHA) / (1 - ALPHA)
LN_GAMMA = np.log(GAMMA)
# Magnitudes below this fall into the zero bucket.
ZERO = 1e-9
# Keys of positive values are OFFSET + k, of negative ones -(OFFSET + k),
# where k = ceil(log_gamma |x|) stays far inside ±OFFSET for doubles.
OFFSET = 100_000
UNKNOWN = "unknown"
# A class name that sums all classes: one baseline for the whole host.
ALL_CLASSES = "*"
# The next hour is built this long after it ends: group samples of its last
# minutes and a late batch have arrived by then.
HOUR_SETTLE = timedelta(minutes=6)


def keys_of(values: np.ndarray) -> np.ndarray:
    mag = np.abs(values)
    k = np.ceil(np.log(np.where(mag > ZERO, mag, 1.0)) / LN_GAMMA).astype(np.int64) + OFFSET
    return np.where(mag > ZERO, np.sign(values).astype(np.int64) * k, 0)


def values_of(keys: np.ndarray) -> np.ndarray:
    """Representative value of each bucket: within ALPHA of every value in it."""
    k = np.abs(keys) - OFFSET
    v = 2 * np.power(GAMMA, k.astype(np.float64)) / (GAMMA + 1)
    return np.where(keys == 0, 0.0, np.sign(keys) * v)


def histogram(values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    keys, counts = np.unique(keys_of(values), return_counts=True)
    return keys, counts


def merge(parts: list[tuple[np.ndarray, np.ndarray]]) -> tuple[np.ndarray, np.ndarray]:
    if len(parts) == 1:
        return parts[0]
    keys = np.concatenate([k for k, _ in parts])
    counts = np.concatenate([c for _, c in parts])
    unique, inverse = np.unique(keys, return_inverse=True)
    return unique, np.bincount(inverse, weights=counts).astype(np.int64)


def weighted_median(values: np.ndarray, counts: np.ndarray) -> float:
    order = np.argsort(values)
    v, c = values[order], np.cumsum(counts[order])
    return float(v[np.searchsorted(c, c[-1] / 2)])


def median_mad(keys: np.ndarray, counts: np.ndarray) -> tuple[float, float]:
    values = values_of(keys)
    median = weighted_median(values, counts)
    return median, weighted_median(np.abs(values - median), counts)


def power_labels(conn: psycopg.Connection, host: Host, grid: np.ndarray) -> np.ndarray:
    """Power profile at each tick, as `<profile>/<ac|battery>`."""
    if grid.size == 0:
        return np.empty(0, dtype=object)
    since, until = frame.from_ms(int(grid[0])), frame.from_ms(int(grid[-1]) + 1)
    profile = frame.labels_on(grid, frame.fact_spans(conn, host, "power.profile", since, until), UNKNOWN)
    rows = conn.execute(
        "(SELECT r.ts, r.value FROM samples r JOIN series s ON s.id = r.series_id WHERE s.host_id = %(h)s"
        "  AND s.signal = 'power.on_ac' AND r.ts <= %(since)s ORDER BY r.ts DESC LIMIT 1)"
        " UNION ALL (SELECT r.ts, r.value FROM samples r JOIN series s ON s.id = r.series_id"
        "  WHERE s.host_id = %(h)s AND s.signal = 'power.on_ac' AND r.ts > %(since)s AND r.ts < %(until)s)"
        " ORDER BY 1",
        {"h": host.id, "since": since, "until": until},
    ).fetchall()
    # A host without a battery reports no power supply; it runs on mains.
    supply = frame.labels_on(grid, [(ts, "ac" if v >= 0.5 else "battery") for ts, v in rows], "ac")
    return np.array([f"{p}/{s}" for p, s in zip(profile, supply, strict=True)], dtype=object)


def context_on(conn: psycopg.Connection, host: Host, f: frame.Frame, tail: timedelta):
    """Class, power profile and the labeled-phase mask at each tick of a frame."""
    if f.ts.size == 0:
        empty = np.empty(0, dtype=object)
        return empty, empty, np.empty(0, dtype=bool)
    since, until = frame.from_ms(int(f.ts[0])), frame.from_ms(int(f.ts[-1]) + 1)
    classes = frame.labels_on(f.ts, frame.fact_spans(conn, host, workload.FACT, since, until), UNKNOWN)
    powers = power_labels(conn, host, f.ts)
    mask = frame.inside(f.ts, excluded(phases_of(conn, host, since, until), tail))
    return classes, powers, mask


def hour_rows(f: frame.Frame, classes: np.ndarray, powers: np.ndarray, skip: np.ndarray) -> list[tuple]:
    """Histogram rows (series id, class, power, keys, counts) of one frame."""
    rows = []
    usable = (classes != UNKNOWN) & ~skip
    combos = defaultdict(list)
    for i in np.flatnonzero(usable):
        combos[(classes[i], powers[i])].append(i)
    for (cls, power), ticks in combos.items():
        cols = np.array(ticks)
        for s, meta in enumerate(f.series):
            values = f.values[s, cols]
            values = values[~np.isnan(values)]
            if values.size:
                keys, counts = histogram(values)
                rows.append((meta.id, cls, power, keys.tolist(), counts.tolist()))
    return rows


def build_hour(conn: psycopg.Connection, host: Host, hour: datetime, params: Params) -> int:
    f = frame.load(conn, host, hour, hour + timedelta(hours=1))
    classes, powers, skip = context_on(conn, host, f, params.label_tail)
    rows = hour_rows(f, classes, powers, skip)
    with conn.transaction():
        conn.execute(
            "DELETE FROM histograms h USING series s WHERE s.id = h.series_id AND s.host_id = %s"
            " AND h.hour = %s",
            (host.id, hour),
        )
        if rows:
            conn.cursor().executemany(
                "INSERT INTO histograms (series_id, class, power, hour, keys, counts)"
                " VALUES (%s, %s, %s, %s, %s, %s)",
                [(sid, cls, power, hour, k, c) for sid, cls, power, k, c in rows],
            )
    return len(rows)


def floor_hour(ts: datetime) -> datetime:
    return ts.replace(minute=0, second=0, microsecond=0)


def build_due(conn: psycopg.Connection, host: Host, params: Params, now: datetime) -> int:
    """Builds every complete hour the classifier has settled; returns how many."""
    row = conn.execute("SELECT next_hour FROM histogram_state WHERE host_id = %s", (host.id,)).fetchone()
    if row:
        (hour,) = row
    else:
        (first,) = conn.execute(
            "SELECT min(r.ts) FROM samples r JOIN series s ON s.id = r.series_id WHERE s.host_id = %s",
            (host.id,),
        ).fetchone()
        if first is None:
            return 0
        hour = floor_hour(first)
    settled = workload.settled(conn, host)
    if settled is None:
        return 0
    built = 0
    while hour + timedelta(hours=1) <= min(settled, now - HOUR_SETTLE):
        n = build_hour(conn, host, hour, params)
        hour += timedelta(hours=1)
        conn.execute(
            "INSERT INTO histogram_state (host_id, next_hour) VALUES (%s, %s)"
            " ON CONFLICT (host_id) DO UPDATE SET next_hour = EXCLUDED.next_hour",
            (host.id, hour),
        )
        conn.commit()
        log.info("histograms of %s for %s: %d rows", host.name, hour - timedelta(hours=1), n)
        built += 1
    return built


@dataclass(frozen=True)
class Baseline:
    median: float
    mad: float
    samples: int
    # Power correction factor by profile, for profiles with enough data.
    factors: dict[str, float] = field(default_factory=dict)

    def factor(self, power: str) -> float:
        return self.factors.get(power, 1.0)


def compute(
    hists: list[tuple[int, str, str, np.ndarray, np.ndarray]], params: Params, per_class: bool = True
) -> dict[tuple[int, str], Baseline]:
    """Baselines by (series id, class) from histogram rows (series, class,
    power, keys, counts). Without `per_class`, one baseline for the host
    under the class ALL_CLASSES."""
    per_hour = 3600 / params.step_s
    min_class = params.baseline_min_hours * per_hour
    min_power = params.power_min_hours * per_hour
    lo, hi = params.power_factor_clamp
    parts: dict[tuple[int, str], list] = defaultdict(list)
    power_parts: dict[tuple[int, str], dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for sid, cls, power, keys, counts in hists:
        key = (sid, cls if per_class else ALL_CLASSES)
        parts[key].append((keys, counts))
        power_parts[key][power].append((keys, counts))
    out = {}
    for key, items in parts.items():
        keys, counts = merge(items)
        n = int(counts.sum())
        if n < min_class:
            continue
        median, mad = median_mad(keys, counts)
        factors = {}
        for power, pitems in power_parts[key].items():
            pk, pc = merge(pitems)
            if pc.sum() >= min_power and median != 0:
                factors[power] = float(np.clip(weighted_median(values_of(pk), pc) / median, lo, hi))
        out[key] = Baseline(median, mad, n, factors)
    return out


def collected(hists: list[tuple], params: Params, per_class: bool = True) -> dict[str, float]:
    """Hours of samples each class has, from histogram rows (series, class,
    power, keys, counts): the most any of its series has."""
    per_series: dict[tuple[int, str], int] = defaultdict(int)
    for sid, cls, _power, _keys, counts in hists:
        per_series[(sid, cls if per_class else ALL_CLASSES)] += int(counts.sum())
    hours: dict[str, float] = {}
    for (_sid, cls), n in per_series.items():
        hours[cls] = max(hours.get(cls, 0.0), n * params.step_s / 3600)
    return hours


def load_hists(conn: psycopg.Connection, host: Host, since: datetime, until: datetime) -> list[tuple]:
    rows = conn.execute(
        "SELECT h.series_id, h.class, h.power, h.hour, h.keys, h.counts FROM histograms h"
        " JOIN series s ON s.id = h.series_id WHERE s.host_id = %s AND h.hour >= %s AND h.hour < %s",
        (host.id, since, until),
    ).fetchall()
    return [(sid, cls, power, hour, np.array(k), np.array(c)) for sid, cls, power, hour, k, c in rows]


class Book:
    """Baselines of one host as of a moving time: recomputed once per hour
    from the histograms of the `baseline_days` before it. Live detection and
    replays both use it; a replay simply moves the clock forward."""

    def __init__(self, conn: psycopg.Connection, host: Host, params: Params, per_class: bool = True) -> None:
        self.conn, self.host, self.params, self.per_class = conn, host, params, per_class
        self.version: datetime | None = None
        self.baselines: dict[tuple[int, str], Baseline] = {}
        self.capacity: dict[tuple[str, str], float] = {}
        # Hours of samples per class, also of classes without a baseline yet.
        self.collected: dict[str, float] = {}
        self._hours: dict[datetime, list[tuple]] = {}

    def at(self, t: datetime) -> "Book":
        version = floor_hour(t)
        if version == self.version:
            return self
        first = version - timedelta(days=self.params.baseline_days)
        for hour in [h for h in self._hours if h < first]:
            del self._hours[hour]
        start = max([first, *[h + timedelta(hours=1) for h in self._hours]])
        for row in load_hists(self.conn, self.host, start, version):
            self._hours.setdefault(row[3], []).append(row)
        rows = [(r[0], r[1], r[2], r[4], r[5]) for hour in self._hours.values() for r in hour]
        self.baselines = compute(rows, self.params, self.per_class)
        self.collected = collected(rows, self.params, self.per_class)
        self.capacity = capacities(self.conn, self.host, t)
        self.version = version
        return self

    def get(self, series_id: int, cls: str) -> Baseline | None:
        return self.baselines.get((series_id, cls if self.per_class else ALL_CLASSES))

    def floor(self, signal: str, entity: str, median: float) -> float:
        p = self.params
        cap = self.capacity.get((signal, entity))
        if cap is not None and signal.endswith(("_bytes_per_s", "_ops_per_s")):
            return cap * p.capacity_floor_share
        if signal.endswith("_bytes"):
            share = p.group_memory_floor_share if frame.is_group(signal) else p.memory_floor_share
            return max(abs(median), 1.0) * share
        return by_pattern(p.floors, signal) or ZERO
