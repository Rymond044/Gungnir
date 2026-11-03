"""The state of a host now, for the interface's overview (slice S7): the
agent's contact, workload class, collectors, the latest value of every
host, device and filesystem series, sizes derived from them, and the open
records."""

from dataclasses import dataclass
from datetime import datetime

import psycopg

from gungnir_server import hosts, telemetry
from gungnir_server.hosts import Host


@dataclass(frozen=True)
class Latest:
    signal: str
    entity: str
    ts: datetime
    value: float


@dataclass(frozen=True)
class OpenRecords:
    anomalies: int
    impact: int
    # Of the anomalies, those expected behavior explains.
    expected: int


@dataclass(frozen=True)
class Overview:
    host: hosts.HostSummary
    now: datetime
    workload_class: str | None
    workload_since: datetime | None
    power_profile: str | None
    collectors: list[telemetry.CollectorState]
    facts: list[telemetry.FactRow]
    latest: list[Latest]
    memory_bytes: float | None
    swap_bytes: float | None
    devices: list[str]
    filesystems: list[str]
    open: OpenRecords


def latest_values(conn: psycopg.Connection, host: Host) -> list[Latest]:
    """The newest sample of each series that is not a control group's."""
    rows = conn.execute(
        "SELECT s.signal, s.entity, last.ts, last.value FROM series s"
        " JOIN LATERAL (SELECT ts, value FROM samples WHERE series_id = s.id"
        "  ORDER BY ts DESC LIMIT 1) last ON true"
        " WHERE s.host_id = %s AND s.signal NOT LIKE 'cgroup.%%' ORDER BY s.signal, s.entity",
        (host.id,),
    ).fetchall()
    return [Latest(*row) for row in rows]


def sizes(latest: list[Latest]) -> tuple[float | None, float | None]:
    """Host memory and swap from values the agent sends together: the
    available memory and the used share, the swap used and its share."""
    v = {(x.signal, x.entity): x.value for x in latest}
    available, used = v.get(("mem.available_bytes", "host")), v.get(("mem.used_ratio", "host"))
    memory = available / (1 - used) if available is not None and used is not None and used < 1 else None
    swap_used, swap_ratio = v.get(("swap.used_bytes", "host")), v.get(("swap.used_ratio", "host"))
    swap = swap_used / swap_ratio if swap_used is not None and swap_ratio else None
    return memory, swap


def open_records(conn: psycopg.Connection, host: Host) -> OpenRecords:
    row = conn.execute(
        "SELECT count(*) FILTER (WHERE kind = 'anomaly'), count(*) FILTER (WHERE kind = 'impact'),"
        " count(*) FILTER (WHERE kind = 'anomaly' AND expected)"
        " FROM anomalies WHERE host_id = %s AND ended_at IS NULL AND replay_id IS NULL",
        (host.id,),
    ).fetchone()
    return OpenRecords(*row)


def of(conn: psycopg.Connection, host: Host, now: datetime) -> Overview:
    summary = hosts.summary(conn, host.name)
    facts = telemetry.latest_facts(conn, host)
    by_name = {(f.name, f.entity): f for f in facts}
    cls = by_name.get(("workload.class", "host"))
    power = by_name.get(("power.profile", "host"))
    latest = latest_values(conn, host)
    memory, swap = sizes(latest)
    return Overview(
        host=summary,
        now=now,
        workload_class=cls.value if cls else None,
        workload_since=cls.ts if cls else None,
        power_profile=power.value if power else None,
        collectors=telemetry.collector_states(conn, host),
        facts=facts,
        latest=latest,
        memory_bytes=memory,
        swap_bytes=swap,
        devices=sorted({x.entity for x in latest if x.signal.startswith("disk.")}),
        filesystems=sorted({x.entity for x in latest if x.signal.startswith("fs.")}),
        open=open_records(conn, host),
    )
