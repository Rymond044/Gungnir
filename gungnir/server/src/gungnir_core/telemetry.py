"""Storage and retrieval of samples and source states."""

import threading
from dataclasses import dataclass
from datetime import UTC, datetime

import psycopg

from gungnir_core.hosts import Host
from gungnir_core.ingest import Batch, Sample


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


def store(conn: psycopg.Connection, ids: SeriesIds, host: Host, batch: Batch) -> int:
    """Writes a batch; returns how many samples were new. Series are created
    first, then host state and samples go in one transaction."""
    keys = {(s.signal, s.entity) for s in batch.samples}
    series = ids.resolve(conn, host.id, keys) if keys else {}
    with conn.transaction():
        conn.execute(
            "UPDATE hosts SET last_seen_at = now(), agent_version = %s, agent_started_at = %s,"
            " utc_offset_s = coalesce(%s, utc_offset_s), dropped_samples = %s WHERE id = %s",
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
        return _insert_samples(conn, series, batch.samples) if batch.samples else 0


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
