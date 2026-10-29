"""Storage and retrieval of samples and source states."""

import threading
from dataclasses import dataclass
from datetime import UTC, datetime

import psycopg

from gungnir_core import maintenance
from gungnir_core.hosts import Host
from gungnir_core.ingest import Batch, Process, Sample

# Raw samples including ranges kept past retention; the two never overlap,
# since kept rows are copied only when their partition is dropped.
RAW_SAMPLES = (
    "(SELECT series_id, ts, value FROM samples UNION ALL SELECT series_id, ts, value FROM samples_kept)"
)


def from_ms(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=UTC)


class SeriesIds:
    """Maps (host, signal, entity) to a series id, creating series on first
    sight. Ids never change, so they are cached for the life of the process."""

    def __init__(self) -> None:
        self._ids: dict[tuple[int, str, str], int] = {}
        self._lock = threading.Lock()

    def resolve(self, conn: psycopg.Connection, host_id: int, keys: set[tuple[str, str]]) -> dict:
        with self._lock:
            missing = [k for k in keys if (host_id, *k) not in self._ids]
        if missing:
            signals, entities = [k[0] for k in missing], [k[1] for k in missing]
            # Committed on its own: a cached id must never point to a series
            # row that a later rollback removed.
            with conn.transaction():
                conn.execute(
                    "INSERT INTO series (host_id, signal, entity)"
                    " SELECT %s, s, e FROM unnest(%s::text[], %s::text[]) AS t(s, e)"
                    " ON CONFLICT DO NOTHING",
                    (host_id, signals, entities),
                )
                rows = conn.execute(
                    "SELECT s.id, s.signal, s.entity FROM series s"
                    " JOIN unnest(%s::text[], %s::text[]) AS t(sig, ent)"
                    " ON s.signal = t.sig AND s.entity = t.ent WHERE s.host_id = %s",
                    (signals, entities, host_id),
                ).fetchall()
            with self._lock:
                for series_id, signal, entity in rows:
                    self._ids[(host_id, signal, entity)] = series_id
        with self._lock:
            return {k: self._ids[(host_id, *k)] for k in keys}


@dataclass(frozen=True)
class Stored:
    accepted: int
    inserted: int
    # Rows with times outside raw retention, e.g. from a badly set clock.
    out_of_window: int


def store(conn: psycopg.Connection, ids: SeriesIds, host: Host, batch: Batch, now: datetime) -> Stored:
    """Writes a batch. Series are created first, then host state, samples
    and context go in one transaction."""
    first, last = maintenance.raw_window(now)
    in_window = [s for s in batch.samples if first <= from_ms(s.ts) < last]
    processes = [p for p in batch.processes if first <= from_ms(p.ts) < last]
    rejected = len(batch.samples) - len(in_window) + len(batch.processes) - len(processes)

    keys = {(s.signal, s.entity) for s in in_window}
    series = ids.resolve(conn, host.id, keys) if keys else {}
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock_shared(%s)", (maintenance.ROLLUP_LOCK,))
        conn.execute(
            "UPDATE hosts SET last_seen_at = now(), agent_version = %s, agent_started_at = %s,"
            " utc_offset_s = coalesce(%s, utc_offset_s), dropped_rows = %s WHERE id = %s",
            (
                batch.agent.version,
                from_ms(batch.agent.started_at),
                batch.agent.utc_offset_s,
                batch.dropped,
                host.id,
            ),
        )
        for s in batch.sources:
            conn.execute(
                "INSERT INTO source_states (host_id, module, state, origin, reason, reported_at)"
                " VALUES (%s, %s, %s, %s, %s, now())"
                " ON CONFLICT (host_id, module) DO UPDATE SET state = EXCLUDED.state,"
                " origin = EXCLUDED.origin, reason = EXCLUDED.reason, reported_at = EXCLUDED.reported_at",
                (host.id, s.module, s.state, s.origin, s.reason),
            )
        inserted = _insert_samples(conn, series, in_window) if in_window else 0
        if in_window:
            # Late data (from the spool) makes its minutes due for rollup again.
            oldest = from_ms(min(s.ts for s in in_window)).replace(second=0, microsecond=0)
            conn.execute(
                "UPDATE maintenance_state SET at = %s WHERE key = 'rollup_from' AND at > %s", (oldest, oldest)
            )
            # And their hour's histograms.
            hour = oldest.replace(minute=0)
            conn.execute(
                "UPDATE histogram_state SET next_hour = %s WHERE host_id = %s AND next_hour > %s",
                (hour, host.id, hour),
            )
        _insert_context(conn, host, batch, processes)
    return Stored(len(batch.samples), inserted, rejected)


def _insert_samples(conn: psycopg.Connection, series: dict, samples: list[Sample]) -> int:
    cur = conn.execute(
        "INSERT INTO samples (series_id, ts, value)"
        " SELECT * FROM unnest(%s::integer[], %s::timestamptz[], %s::float8[])"
        " ON CONFLICT DO NOTHING",
        (
            [series[(s.signal, s.entity)] for s in samples],
            [from_ms(s.ts) for s in samples],
            [s.value for s in samples],
        ),
    )
    return cur.rowcount


def _insert_context(conn: psycopg.Connection, host: Host, batch: Batch, processes: list[Process]) -> None:
    if batch.facts:
        conn.cursor().executemany(
            "INSERT INTO facts (host_id, name, entity, ts, value) VALUES (%s, %s, %s, %s, %s)"
            " ON CONFLICT DO NOTHING",
            [(host.id, f.name, f.entity, from_ms(f.ts), f.value) for f in batch.facts],
        )
    if processes:
        conn.cursor().executemany(
            "INSERT INTO process_samples (host_id, ts, pid, ppid, comm, state, threads, cpu_cores,"
            " rss_bytes, cgroup, unit) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
            " ON CONFLICT DO NOTHING",
            [
                (
                    host.id,
                    from_ms(p.ts),
                    p.pid,
                    p.ppid,
                    p.comm,
                    p.state,
                    p.threads,
                    p.cpu_cores,
                    p.rss_bytes,
                    p.cgroup,
                    p.unit,
                )
                for p in processes
            ],
        )
    if batch.events:
        conn.cursor().executemany(
            "INSERT INTO events (host_id, ts, source, priority, unit, message_id, message, key)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
            [
                (host.id, from_ms(e.ts), e.source, e.priority, e.unit, e.message_id, e.message, e.key)
                for e in batch.events
            ],
        )


@dataclass(frozen=True)
class SourceState:
    module: str
    state: str
    origin: str | None
    reason: str | None
    reported_at: datetime


def source_states(conn: psycopg.Connection, host: Host) -> list[SourceState]:
    rows = conn.execute(
        "SELECT module, state, origin, reason, reported_at FROM source_states"
        " WHERE host_id = %s ORDER BY module",
        (host.id,),
    ).fetchall()
    return [SourceState(*row) for row in rows]


@dataclass(frozen=True)
class SeriesInfo:
    signal: str
    entity: str
    last_ts: datetime | None
    last_value: float | None


def series_of(conn: psycopg.Connection, host: Host) -> list[SeriesInfo]:
    rows = conn.execute(
        "SELECT s.signal, s.entity, last.ts, last.value FROM series s"
        " LEFT JOIN LATERAL (SELECT ts, value FROM samples WHERE series_id = s.id"
        "  ORDER BY ts DESC LIMIT 1) last ON true"
        " WHERE s.host_id = %s ORDER BY s.signal, s.entity",
        (host.id,),
    ).fetchall()
    return [SeriesInfo(*row) for row in rows]


def samples_of(
    conn: psycopg.Connection,
    host: Host,
    signal: str,
    entity: str,
    since: datetime,
    until: datetime,
    limit: int,
) -> list[tuple[datetime, float]]:
    return conn.execute(
        "SELECT p.ts, p.value FROM samples p JOIN series s ON s.id = p.series_id"
        " WHERE s.host_id = %s AND s.signal = %s AND s.entity = %s AND p.ts >= %s AND p.ts < %s"
        " ORDER BY p.ts LIMIT %s",
        (host.id, signal, entity, since, until, limit),
    ).fetchall()


@dataclass(frozen=True)
class EventRow:
    ts: datetime
    source: str
    priority: int
    unit: str | None
    message_id: str | None
    message: str


def events_of(
    conn: psycopg.Connection, host: Host, since: datetime, until: datetime, limit: int
) -> list[EventRow]:
    rows = conn.execute(
        "SELECT ts, source, priority, unit, message_id, message FROM events"
        " WHERE host_id = %s AND ts >= %s AND ts < %s ORDER BY ts LIMIT %s",
        (host.id, since, until, limit),
    ).fetchall()
    return [EventRow(*row) for row in rows]


@dataclass(frozen=True)
class ProcessRow:
    ts: datetime
    pid: int
    ppid: int
    comm: str
    state: str
    threads: int
    cpu_cores: float
    rss_bytes: int
    cgroup: str | None
    unit: str | None


def processes_at(conn: psycopg.Connection, host: Host, at: datetime) -> list[ProcessRow]:
    """The latest process snapshot taken at or before `at`."""
    rows = conn.execute(
        "SELECT ts, pid, ppid, comm, state, threads, cpu_cores, rss_bytes, cgroup, unit FROM process_samples"
        " WHERE host_id = %(h)s AND ts = (SELECT max(ts) FROM process_samples"
        "  WHERE host_id = %(h)s AND ts <= %(at)s AND ts > %(at)s - interval '1 minute')"
        " ORDER BY cpu_cores DESC, rss_bytes DESC",
        {"h": host.id, "at": at},
    ).fetchall()
    return [ProcessRow(*row) for row in rows]


@dataclass(frozen=True)
class FactRow:
    name: str
    entity: str
    ts: datetime
    value: str


def latest_facts(conn: psycopg.Connection, host: Host) -> list[FactRow]:
    rows = conn.execute(
        "SELECT DISTINCT ON (name, entity) name, entity, ts, value FROM facts"
        " WHERE host_id = %s ORDER BY name, entity, ts DESC",
        (host.id,),
    ).fetchall()
    return [FactRow(*row) for row in rows]


def rollups_of(
    conn: psycopg.Connection,
    host: Host,
    signal: str,
    entity: str,
    since: datetime,
    until: datetime,
    limit: int,
) -> list[tuple[datetime, float, float, float, int]]:
    return conn.execute(
        "SELECT r.ts, r.min, r.max, r.sum, r.n FROM samples_1m r JOIN series s ON s.id = r.series_id"
        " WHERE s.host_id = %s AND s.signal = %s AND s.entity = %s AND r.ts >= %s AND r.ts < %s"
        " ORDER BY r.ts LIMIT %s",
        (host.id, signal, entity, since, until, limit),
    ).fetchall()
