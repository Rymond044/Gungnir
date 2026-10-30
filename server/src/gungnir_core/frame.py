"""A frame: the samples of many series of one host aligned on its ticks.

All samples of one agent round share a time stamp, so the ticks are the
distinct times of the dense (host, disk, mount point) series. Control group
series are sparse (ADR 0017) and are expanded onto the ticks: while a group
is alive a missing rate is zero and missing memory is unchanged. A group is
taken as alive up to one full-snapshot period after its last sample; the
agent sends every value of every group at least that often.

Frames are plain arrays, so detectors, histograms and replays share them,
and the public dataset can be fed through the same code.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import numpy as np
import psycopg

from gungnir_core.hosts import Host
from gungnir_core.telemetry import RAW_SAMPLES

# The agent's full group snapshot comes every five minutes; some slack for
# a late round.
FULL_PERIOD = timedelta(minutes=5, seconds=10)
CARRIED = ("cgroup.mem_bytes",)
# Context, never checked or put into baselines (ADR 0021).
CONTEXT_PREFIXES = ("agent.", "power.")
# Group signals that get baselines and may be checked once they have one.
KEY_GROUP_SIGNALS = (
    "cgroup.cpu_cores",
    "cgroup.mem_bytes",
    "cgroup.read_bytes_per_s",
    "cgroup.write_bytes_per_s",
    "cgroup.cpu_pressure_some_ratio",
    "cgroup.mem_pressure_some_ratio",
    "cgroup.mem_pressure_full_ratio",
    "cgroup.io_pressure_some_ratio",
    "cgroup.io_pressure_full_ratio",
)


def is_group(signal: str) -> bool:
    return signal.startswith("cgroup.")


def is_checked_kind(signal: str) -> bool:
    """Series that get histograms: dense series except context, and the key
    group signals."""
    if signal.startswith(CONTEXT_PREFIXES):
        return False
    return not is_group(signal) or signal in KEY_GROUP_SIGNALS


def to_ms(ts: datetime) -> int:
    return round(ts.timestamp() * 1000)


def from_ms(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=UTC)


@dataclass(frozen=True)
class SeriesMeta:
    id: int
    signal: str
    entity: str


@dataclass
class Frame:
    ts: np.ndarray  # int64 milliseconds, ascending
    series: list[SeriesMeta]
    values: np.ndarray  # float64 (series, ticks), NaN where absent

    def index(self) -> dict[tuple[str, str], int]:
        return {(s.signal, s.entity): i for i, s in enumerate(self.series)}

    def row(self, signal: str, entity: str = "host") -> np.ndarray | None:
        for i, s in enumerate(self.series):
            if s.signal == signal and s.entity == entity:
                return self.values[i]
        return None

    def rows_of(self, signal: str) -> list[int]:
        return [i for i, s in enumerate(self.series) if s.signal == signal]

    def tail(self, since_ms: int) -> "Frame":
        keep = self.ts >= since_ms
        return Frame(self.ts[keep], self.series, self.values[:, keep])


def expand_sparse(
    sample_ts: np.ndarray, sample_values: np.ndarray, grid: np.ndarray, carried: bool, alive_ms: int
) -> np.ndarray:
    """Values of a sparse series on the grid; NaN where the group is not alive."""
    out = np.full(grid.shape, np.nan)
    if sample_ts.size == 0 or grid.size == 0:
        return out
    # Index of the last sample at or before each tick.
    last = np.searchsorted(sample_ts, grid, side="right") - 1
    has_prev = last >= 0
    prev_ts = np.where(has_prev, sample_ts[np.clip(last, 0, None)], 0)
    alive = has_prev & (grid - prev_ts <= alive_ms)
    exact = alive & (prev_ts == grid)
    if carried:
        out[alive] = sample_values[last[alive]]
    else:
        out[alive] = 0.0
        out[exact] = sample_values[last[exact]]
    return out


def build(
    grid: np.ndarray, series: list[SeriesMeta], samples: dict[int, tuple[np.ndarray, np.ndarray]]
) -> Frame:
    """A frame from per-series (times, values) arrays sorted by time."""
    values = np.full((len(series), grid.size), np.nan)
    alive_ms = int(FULL_PERIOD.total_seconds() * 1000)
    for i, s in enumerate(series):
        ts, v = samples.get(s.id, (np.empty(0, np.int64), np.empty(0)))
        if is_group(s.signal):
            values[i] = expand_sparse(ts, v, grid, s.signal in CARRIED, alive_ms)
        elif ts.size:
            pos = np.searchsorted(grid, ts)
            ok = (pos < grid.size) & (grid[np.clip(pos, 0, grid.size - 1)] == ts)
            values[i, pos[ok]] = v[ok]
    return Frame(grid, series, values)


def host_series(conn: psycopg.Connection, host: Host, checked_only: bool = True) -> list[SeriesMeta]:
    rows = conn.execute(
        "SELECT id, signal, entity FROM series WHERE host_id = %s ORDER BY signal, entity", (host.id,)
    ).fetchall()
    found = [SeriesMeta(*row) for row in rows]
    return [s for s in found if is_checked_kind(s.signal)] if checked_only else found


def load(
    conn: psycopg.Connection,
    host: Host,
    since: datetime,
    until: datetime,
    series: list[SeriesMeta] | None = None,
) -> Frame:
    """The frame of ticks in [since, until). Group samples are read from a
    full period earlier, so that the first ticks know which groups live."""
    every = host_series(conn, host, checked_only=False)
    series = [s for s in every if is_checked_kind(s.signal)] if series is None else series
    wanted = {s.id for s in series}
    # Dense series mark the ticks, whether checked or not.
    dense = {s.id for s in every if not is_group(s.signal)}
    groups = {s.id for s in series if is_group(s.signal)}
    rows = conn.execute(
        f"SELECT r.series_id, r.ts, r.value FROM {RAW_SAMPLES} r"
        " WHERE r.series_id = ANY(%s) AND r.ts >= %s AND r.ts < %s ORDER BY r.series_id, r.ts",
        (sorted(wanted | dense), since - FULL_PERIOD, until),
    ).fetchall()
    since_ms = to_ms(since)
    grid_set: set[int] = set()
    grouped: dict[int, tuple[list[int], list[float]]] = {}
    for series_id, ts, value in rows:
        ms = to_ms(ts)
        if ms < since_ms and series_id not in groups:
            continue
        if series_id in dense:
            grid_set.add(ms)
        if series_id in wanted:
            lists = grouped.setdefault(series_id, ([], []))
            lists[0].append(ms)
            lists[1].append(value)
    grid = np.array(sorted(grid_set), dtype=np.int64)
    samples = {k: (np.array(t, dtype=np.int64), np.array(v)) for k, (t, v) in grouped.items()}
    return build(grid, series, samples)


def fact_spans(
    conn: psycopg.Connection, host: Host, name: str, since: datetime, until: datetime
) -> list[tuple[datetime, str]]:
    """Values of a host fact over [since, until): the one in effect at
    `since` and every change after it, as (from, value)."""
    return conn.execute(
        "(SELECT ts, value FROM facts WHERE host_id = %(h)s AND name = %(n)s AND entity = 'host'"
        "  AND ts <= %(since)s ORDER BY ts DESC LIMIT 1)"
        " UNION ALL (SELECT ts, value FROM facts WHERE host_id = %(h)s AND name = %(n)s"
        "  AND entity = 'host' AND ts > %(since)s AND ts < %(until)s) ORDER BY 1",
        {"h": host.id, "n": name, "since": since, "until": until},
    ).fetchall()


def labels_on(grid: np.ndarray, spans: list[tuple[datetime, str]], missing: str) -> np.ndarray:
    """The span value in effect at each tick."""
    out = np.full(grid.shape, missing, dtype=object)
    if not spans:
        return out
    starts = np.array([to_ms(ts) for ts, _ in spans], dtype=np.int64)
    values = np.array([v for _, v in spans] + [missing], dtype=object)
    idx = np.searchsorted(starts, grid, side="right") - 1
    return values[np.where(idx >= 0, idx, len(spans))]


def inside(grid: np.ndarray, intervals: list[tuple[datetime, datetime]]) -> np.ndarray:
    mask = np.zeros(grid.shape, dtype=bool)
    for a, b in intervals:
        mask |= (grid >= to_ms(a)) & (grid < to_ms(b))
    return mask
