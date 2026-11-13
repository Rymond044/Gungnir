"""Reading anomaly and impact records back, for the command line and the
interface."""

from dataclasses import dataclass
from datetime import datetime

import psycopg

from gungnir_server.hosts import Host


@dataclass(frozen=True)
class RecordRow:
    id: int
    kind: str
    detector: str
    level: str | None
    signal: str
    entity: str
    started_at: datetime
    ended_at: datetime | None
    direction: str
    peak_score: float
    value: float | None
    workload_class: str | None
    power_profile: str | None
    baseline: dict
    episode: int | None
    # Medians of the entity's context signals over the interval.
    context: dict | None
    # Expected behavior explains it (ADR 0002); None before S5 judged it.
    expected: bool | None = None
    # The incident it is evidence of (ADR 0060).
    incident_id: int | None = None


COLUMNS = (
    "SELECT a.id, a.kind, a.detector, a.level, s.signal, s.entity, a.started_at, a.ended_at,"
    " a.direction, a.peak_score, a.value, a.workload_class, a.power_profile, a.baseline, a.episode,"
    " a.context, a.expected, a.incident_id FROM anomalies a JOIN series s ON s.id = a.series_id"
)


def records_of(
    conn: psycopg.Connection,
    host: Host,
    since: datetime,
    replay_id: int | None = None,
    open_only: bool = False,
    detector: str | None = None,
    limit: int = 50,
    until: datetime | None = None,
    kind: str | None = None,
    signal: str | None = None,
    entity: str | None = None,
    incident: int | None = None,
) -> list[RecordRow]:
    """Records that overlap [since, until), newest first; live ones unless a
    replay is named."""
    rows = conn.execute(
        COLUMNS + " WHERE a.host_id = %(h)s AND a.replay_id IS NOT DISTINCT FROM %(r)s"
        " AND (a.ended_at IS NULL OR a.ended_at >= %(since)s)"
        " AND (%(until)s::timestamptz IS NULL OR a.started_at < %(until)s)"
        " AND (NOT %(open)s OR a.ended_at IS NULL)"
        " AND (%(det)s::text IS NULL OR a.detector LIKE %(det)s || '%%')"
        " AND (%(kind)s::text IS NULL OR a.kind = %(kind)s)"
        " AND (%(sig)s::text IS NULL OR s.signal = %(sig)s)"
        " AND (%(ent)s::text IS NULL OR s.entity = %(ent)s)"
        " AND (%(inc)s::bigint IS NULL OR a.incident_id = %(inc)s)"
        " ORDER BY a.started_at DESC LIMIT %(limit)s",
        {
            "h": host.id,
            "r": replay_id,
            "since": since,
            "until": until,
            "open": open_only,
            "det": detector,
            "kind": kind,
            "sig": signal,
            "ent": entity,
            "inc": incident,
            "limit": limit,
        },
    ).fetchall()
    return [RecordRow(*row) for row in rows]


def record(conn: psycopg.Connection, host: Host, record_id: int) -> RecordRow | None:
    row = conn.execute(COLUMNS + " WHERE a.host_id = %s AND a.id = %s", (host.id, record_id)).fetchone()
    return RecordRow(*row) if row else None
