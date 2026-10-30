"""Reading anomaly and impact records back, for the command line and later
for the incident view."""

from dataclasses import dataclass
from datetime import datetime

import psycopg

from gungnir_core.hosts import Host


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


def records_of(
    conn: psycopg.Connection,
    host: Host,
    since: datetime,
    replay_id: int | None = None,
    open_only: bool = False,
    detector: str | None = None,
    limit: int = 50,
) -> list[RecordRow]:
    """Records that overlap [since, now), newest first; live ones unless a
    replay is named."""
    rows = conn.execute(
        "SELECT a.id, a.kind, a.detector, a.level, s.signal, s.entity, a.started_at, a.ended_at,"
        " a.direction, a.peak_score, a.value, a.workload_class, a.power_profile, a.baseline, a.episode"
        " FROM anomalies a JOIN series s ON s.id = a.series_id"
        " WHERE a.host_id = %(h)s AND a.replay_id IS NOT DISTINCT FROM %(r)s"
        " AND (a.ended_at IS NULL OR a.ended_at >= %(since)s)"
        " AND (NOT %(open)s OR a.ended_at IS NULL)"
        " AND (%(det)s::text IS NULL OR a.detector LIKE %(det)s || '%%')"
        " ORDER BY a.started_at DESC LIMIT %(limit)s",
        {"h": host.id, "r": replay_id, "since": since, "open": open_only, "det": detector, "limit": limit},
    ).fetchall()
    return [RecordRow(*row) for row in rows]
