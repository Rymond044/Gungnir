"""Writing telemetry straight into the database for tests of the pipeline."""

from datetime import datetime, timedelta

from gungnir_core import hosts
from gungnir_core.telemetry import SeriesIds


def add_host(conn, name: str = "laptop") -> hosts.Host:
    hosts.add(conn, name)
    conn.commit()
    return hosts.by_name(conn, name)


def samples(conn, host, rows: list[tuple[datetime, str, str, float]]) -> None:
    """Rows of (time, signal, entity, value)."""
    ids = SeriesIds().resolve(conn, host.id, {(s, e) for _, s, e, _ in rows})
    with conn.transaction():
        conn.cursor().executemany(
            "INSERT INTO samples (series_id, ts, value) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
            [(ids[(s, e)], ts, v) for ts, s, e, v in rows],
        )


def processes(conn, host, rows: list[tuple[datetime, str, float, str]]) -> None:
    """Rows of (time, command, cores, unit)."""
    with conn.transaction():
        conn.cursor().executemany(
            "INSERT INTO process_samples (host_id, ts, pid, ppid, comm, state, threads, cpu_cores,"
            " rss_bytes, cgroup, unit) VALUES (%s, %s, %s, 1, %s, 'R', 1, %s, 0, %s, %s)",
            [
                (host.id, ts, 100 + i, comm, cores, f"system.slice/{unit}", unit)
                for i, (ts, comm, cores, unit) in enumerate(rows)
            ],
        )


def ticks(start: datetime, n: int, step_s: float = 5.0) -> list[datetime]:
    return [start + timedelta(seconds=i * step_s) for i in range(n)]
