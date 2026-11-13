"""Series for the interface's graphs (slice S7): samples bucketed to about
as many points as a graph can show, and the band a series is judged
normal in (ADR 0052).

A bucket shorter than a minute reads raw samples, a longer one the minute
rollups, so a graph of a day costs the same as a graph of ten minutes.
Each point carries the mean and the range of its bucket: a spike narrower
than a bucket still shows.
"""

import math
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import pairwise

import numpy as np
import psycopg

from gungnir_server import frame
from gungnir_server.baselines import UNKNOWN, Book, power_labels
from gungnir_server.detection.detectors import MAD_TO_SIGMA, BookBaselines, load_limits
from gungnir_server.detection.growth import SIGNAL as GROWTH_SIGNAL
from gungnir_server.frame import FULL_PERIOD, SeriesMeta, fact_spans
from gungnir_server.hosts import Host
from gungnir_server.params import Params, by_pattern
from gungnir_server.telemetry import RAW_SAMPLES

# Bucket lengths a graph may use, in seconds; the shortest one that keeps
# the points under the asked number wins.
BUCKETS = (5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 21600, 86400)
MAX_SERIES = 16
CONTEXT_TICKS = 2000


@dataclass(frozen=True)
class Points:
    signal: str
    entity: str
    # [ms, mean, min, max], ascending; empty buckets are absent.
    points: list[list[float]]


def bucket_for(since: datetime, until: datetime, points: int, step_s: float) -> int:
    span = (until - since).total_seconds()
    for b in BUCKETS:
        if b >= step_s and span / b <= points:
            return b
    return BUCKETS[-1]


def bucketed(
    conn: psycopg.Connection,
    host: Host,
    keys: list[tuple[str, str]],
    since: datetime,
    until: datetime,
    bucket_s: int,
    step_s: float,
    raw_since: datetime,
) -> tuple[list[Points], int]:
    """Mean, min and max per bucket of each (signal, entity), and the bucket
    length used. Raw samples serve buckets under a minute while they are
    kept, rollups the rest, whose buckets are at least a minute."""
    if not keys:
        return [], bucket_s
    raw = bucket_s < 60 and since >= raw_since
    width = timedelta(seconds=max(bucket_s, 1 if raw else 60))
    if raw:
        query = (
            "SELECT s.signal, s.entity, date_bin(%(w)s, p.ts, %(origin)s) AS b,"
            " sum(p.value), min(p.value), max(p.value), count(*)"
            f" FROM {RAW_SAMPLES} p JOIN series s ON s.id = p.series_id"
            " WHERE s.host_id = %(h)s AND (s.signal, s.entity) IN"
            "  (SELECT * FROM unnest(%(sig)s::text[], %(ent)s::text[]))"
            " AND p.ts >= %(since)s AND p.ts < %(until)s GROUP BY 1, 2, 3 ORDER BY 1, 2, 3"
        )
    else:
        query = (
            "SELECT s.signal, s.entity, date_bin(%(w)s, r.ts, %(origin)s) AS b,"
            " sum(r.sum), min(r.min), max(r.max), sum(r.n)"
            " FROM samples_1m r JOIN series s ON s.id = r.series_id"
            " WHERE s.host_id = %(h)s AND (s.signal, s.entity) IN"
            "  (SELECT * FROM unnest(%(sig)s::text[], %(ent)s::text[]))"
            " AND r.ts >= %(since)s AND r.ts < %(until)s GROUP BY 1, 2, 3 ORDER BY 1, 2, 3"
        )
    rows = conn.execute(
        query,
        {
            "w": width,
            "origin": datetime(2000, 1, 1, tzinfo=UTC),
            "h": host.id,
            "sig": [s for s, _ in keys],
            "ent": [e for _, e in keys],
            "since": since,
            "until": until,
        },
    ).fetchall()
    found: dict[tuple[str, str], list[list[float]]] = {k: [] for k in keys}
    expected = width.total_seconds() / step_s
    for signal, entity, b, total, lo, hi, n in rows:
        # A group's series omits zeros (ADR 0017): its rates are averaged over
        # the samples the bucket should hold, and a missing one counts as zero.
        sparse = frame.is_group(signal) and signal not in frame.CARRIED
        mean = total / max(expected, n) if sparse else total / n
        if sparse and n < expected:
            lo = min(lo, 0.0)
        found[(signal, entity)].append([b.timestamp() * 1000, mean, lo, hi])
    for key, pts in found.items():
        if frame.is_group(key[0]):
            fill = carry if key[0] in frame.CARRIED else fill_zeros
            found[key] = fill(pts, width.total_seconds() * 1000)
    return [Points(s, e, found[(s, e)]) for s, e in keys], int(width.total_seconds())


def fill_zeros(points: list[list[float]], width_ms: float) -> list[list[float]]:
    """Zero buckets between the samples of a sparse rate while its group is
    alive: up to one full snapshot period after a sample (`frame`)."""
    if not points:
        return points
    alive = FULL_PERIOD.total_seconds() * 1000
    out = [points[0]]
    for last, p in pairwise(points):
        t = last[0] + width_ms
        while t < p[0] and t - last[0] <= alive:
            out.append([t, 0.0, 0.0, 0.0])
            t += width_ms
        out.append(p)
    return out


def carry(points: list[list[float]], width_ms: float) -> list[list[float]]:
    """A group's memory is sent only when it changed (ADR 0017): between
    samples, while the group lives, it holds its last value."""
    if not points:
        return points
    alive = FULL_PERIOD.total_seconds() * 1000
    out = [points[0]]
    for last, p in pairwise(points):
        t = last[0] + width_ms
        while t < p[0] and t - last[0] <= alive:
            out.append([t, last[1], last[1], last[1]])
            t += width_ms
        out.append(p)
    return out


@dataclass(frozen=True)
class Span:
    since: datetime
    until: datetime
    workload_class: str
    power_profile: str


@dataclass(frozen=True)
class Band:
    """Where the robust detector leaves a series alone in one class and
    power profile: median ± k·1.4826·MAD', the spread with its floor."""

    workload_class: str
    power_profile: str
    low: float
    high: float
    median: float
    mad: float
    samples: int


@dataclass(frozen=True)
class Limit:
    kind: str  # threshold, impact or cold start
    above: float | None
    below: float | None
    level: str | None
    source: str


@dataclass(frozen=True)
class Minimum:
    hour: datetime
    value: float


@dataclass(frozen=True)
class Norm:
    signal: str
    entity: str
    spans: list[Span]
    bands: list[Band]
    limits: list[Limit]
    # Hourly minima of a group's memory: what the growth check judges (ADR 0058).
    minima: list[Minimum]


def context_spans(conn: psycopg.Connection, host: Host, since: datetime, until: datetime) -> list[Span]:
    """Workload class and power profile in effect over [since, until), as
    spans of constant pairs. The labels are those detection judges by, read
    on a grid of at most CONTEXT_TICKS ticks."""
    span_ms = (until - since).total_seconds() * 1000
    step = max(5000.0, span_ms / CONTEXT_TICKS)
    grid = np.arange(frame.to_ms(since), frame.to_ms(until), step).astype(np.int64)
    if grid.size == 0:
        return []
    classes = frame.labels_on(grid, fact_spans(conn, host, "workload.class", since, until), UNKNOWN)
    powers = power_labels(conn, host, grid)
    out: list[Span] = []
    for t, cls, power in zip(grid, classes, powers, strict=True):
        at = frame.from_ms(int(t))
        if out and (out[-1].workload_class, out[-1].power_profile) == (cls, power):
            continue
        if out:
            out[-1] = Span(out[-1].since, at, out[-1].workload_class, out[-1].power_profile)
        out.append(Span(at, until, str(cls), str(power)))
    return out


class Norms:
    """Bands of a host from the baselines detection uses. The book loads a
    week of histograms, so it is built once per host and hour."""

    def __init__(self, params: Params) -> None:
        self.params = params
        self.books: dict[int, Book] = {}
        # Requests run in a thread pool; a book moving to a new hour reads
        # through the connection of the request that moves it.
        self.lock = threading.Lock()

    def book(self, conn: psycopg.Connection, host: Host, now: datetime) -> Book:
        book = self.books.get(host.id)
        if book is None:
            book = self.books[host.id] = Book(conn, host, self.params)
        book.conn = conn
        return book.at(now)

    def of(
        self,
        conn: psycopg.Connection,
        host: Host,
        signal: str,
        entity: str,
        since: datetime,
        until: datetime,
        now: datetime,
    ) -> Norm | None:
        row = conn.execute(
            "SELECT id FROM series WHERE host_id = %s AND signal = %s AND entity = %s",
            (host.id, signal, entity),
        ).fetchone()
        if row is None:
            return None
        meta = SeriesMeta(row[0], signal, entity)
        spans = context_spans(conn, host, since, until)
        minima = hourly_minima(conn, meta, since, until)
        with self.lock:
            return self._norm(conn, host, meta, spans, minima, now)

    def _norm(
        self,
        conn: psycopg.Connection,
        host: Host,
        meta: SeriesMeta,
        spans: list[Span],
        minima: list[Minimum],
        now: datetime,
    ) -> Norm:
        signal, entity = meta.signal, meta.entity
        book = self.book(conn, host, now)
        bands = []
        if frame.is_checked_kind(signal):
            baselines = BookBaselines(book)
            k = self.params.robust_k
            for cls, power in dict.fromkeys((s.workload_class, s.power_profile) for s in spans):
                found = baselines.lookup(meta, cls, power)
                if found is None:
                    continue
                median, mad, basis = found
                half = k * MAD_TO_SIGMA * mad
                bands.append(Band(cls, power, median - half, median + half, median, mad, basis["samples"]))
        return Norm(signal, entity, spans, bands, limits_of(host, signal, entity, book, self.params), minima)


def hourly_minima(
    conn: psycopg.Connection, meta: SeriesMeta, since: datetime, until: datetime
) -> list[Minimum]:
    """The minimum of each hour of a group's memory, from the minute rollups,
    for every hour that reaches into [since, until). Slices are not judged."""
    if meta.signal != GROWTH_SIGNAL or meta.entity.endswith(".slice"):
        return []
    rows = conn.execute(
        "SELECT date_trunc('hour', ts) AS h, min(min) FROM samples_1m"
        " WHERE series_id = %s AND ts >= date_trunc('hour', %s::timestamptz) AND ts < %s"
        " GROUP BY 1 ORDER BY 1",
        (meta.id, since, until),
    ).fetchall()
    return [Minimum(h, v) for h, v in rows]


def limits_of(host: Host, signal: str, entity: str, book: Book, params: Params) -> list[Limit]:
    out = [
        Limit("threshold", lim.above, lim.below, None, lim.source)
        for lim in load_limits("threshold", "detection/static_thresholds.toml", host.name)
        if lim.signal == signal
    ]
    out += [
        Limit("impact", lim.above, lim.below, lim.level, lim.source)
        for lim in load_limits("impact", "detection/impact.toml", host.name)
        if lim.signal == signal
    ]
    cap = book.capacity.get((signal, entity))
    share = by_pattern(params.cold_start_share, signal)
    if cap is not None and share is not None and math.isfinite(cap):
        out.append(
            Limit(
                "cold start", cap * share, None, None, f"{share:.0%} of the capacity measured in calibration"
            )
        )
    return out
