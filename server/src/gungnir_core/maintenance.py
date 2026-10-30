"""Background upkeep of stored telemetry: partitions, minute rollups, expiry.

Raw samples and process snapshots live in daily partitions for RAW_DAYS;
dropping a whole partition is instant and leaves no bloat, unlike DELETE.
Minute rollups (min, max, sum, count) live for ROLLUP_DAYS and serve long
graphs and the training windows of the multivariate detector. Ranges listed
in `kept_ranges` (incidents, labeled runs) are copied to `samples_kept`
before their partition goes.
"""

import logging
import threading
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import psycopg
from psycopg import sql
from psycopg_pool import ConnectionPool

log = logging.getLogger(__name__)

RAW_DAYS = 3
ROLLUP_DAYS = 90
CONTEXT_DAYS = 90
# Partitions are created ahead, so ingest never meets a missing one.
AHEAD_DAYS = 2
# Data of the current minute is still arriving; roll up only older minutes.
SETTLE = timedelta(seconds=30)
# Ingest holds this lock shared, rollup exclusively, so that late data and a
# running rollup never miss each other.
ROLLUP_LOCK = 7_370_002
INTERVAL = timedelta(minutes=10)

RAW_TABLES = ("samples", "process_samples")


def utc_day(ts: datetime) -> date:
    return ts.astimezone(UTC).date()


def midnight(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


def raw_window(now: datetime) -> tuple[datetime, datetime]:
    """Times that raw tables can store; rows outside are refused at ingest."""
    today = utc_day(now)
    return midnight(today - timedelta(days=RAW_DAYS)), midnight(today + timedelta(days=AHEAD_DAYS + 1))


@dataclass
class Report:
    rolled_up: int = 0
    dropped_partitions: int = 0
    kept_samples: int = 0


def run(conn: psycopg.Connection, now: datetime | None = None) -> Report:
    now = now or datetime.now(UTC)
    report = Report()
    ensure_partitions(conn, now)
    report.rolled_up = rollup(conn, now)
    expire(conn, now, report)
    return report


def ensure_partitions(conn: psycopg.Connection, now: datetime) -> None:
    today = utc_day(now)
    last = today + timedelta(days=AHEAD_DAYS)
    with conn.transaction():
        for table in RAW_TABLES:
            conn.execute(
                "SELECT ensure_daily_partitions(%s, %s, %s)", (table, today - timedelta(days=RAW_DAYS), last)
            )
        rollup_first = today - timedelta(days=ROLLUP_DAYS)
        conn.execute("SELECT ensure_daily_partitions('samples_1m', %s, %s)", (rollup_first, last))


def rollup(conn: psycopg.Connection, now: datetime) -> int:
    """Aggregates complete minutes since `rollup_from` and moves it forward."""
    until = (now - SETTLE).replace(second=0, microsecond=0)
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (ROLLUP_LOCK,))
        (since,) = conn.execute("SELECT at FROM maintenance_state WHERE key = 'rollup_from'").fetchone()
        if since >= until:
            return 0
        cur = conn.execute(
            "INSERT INTO samples_1m (series_id, ts, min, max, sum, n)"
            " SELECT series_id, date_trunc('minute', ts), min(value), max(value), sum(value), count(*)"
            " FROM samples WHERE ts >= %s AND ts < %s GROUP BY 1, 2"
            " ON CONFLICT (series_id, ts) DO UPDATE SET min = EXCLUDED.min, max = EXCLUDED.max,"
            " sum = EXCLUDED.sum, n = EXCLUDED.n",
            (since, until),
        )
        conn.execute("UPDATE maintenance_state SET at = %s WHERE key = 'rollup_from'", (until,))
        return cur.rowcount


def partitions(conn: psycopg.Connection, table: str) -> list[tuple[str, date]]:
    """Daily partitions of a table with their day, oldest first."""
    rows = conn.execute(
        "SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid"
        " JOIN pg_class p ON p.oid = i.inhparent WHERE p.relname = %s",
        (table,),
    ).fetchall()
    found = []
    for (name,) in rows:
        suffix = name.removeprefix(table + "_")
        if len(suffix) == 8 and suffix.isdigit():
            found.append((name, datetime.strptime(suffix, "%Y%m%d").date()))
    return sorted(found, key=lambda p: p[1])


def expire(conn: psycopg.Connection, now: datetime, report: Report) -> None:
    today = utc_day(now)
    raw_first = today - timedelta(days=RAW_DAYS)
    with conn.transaction():
        for name, day in partitions(conn, "samples"):
            if day < raw_first:
                cur = conn.execute(
                    sql.SQL(
                        "INSERT INTO samples_kept SELECT p.* FROM {} p JOIN series s ON s.id = p.series_id"
                        " JOIN kept_ranges k ON k.host_id = s.host_id AND p.ts >= k.since AND p.ts < k.until"
                        " ON CONFLICT DO NOTHING"
                    ).format(sql.Identifier(name))
                )
                report.kept_samples += cur.rowcount
                drop(conn, name, report)
        for name, day in partitions(conn, "process_samples"):
            if day < raw_first:
                drop(conn, name, report)
        for name, day in partitions(conn, "samples_1m"):
            if day < today - timedelta(days=ROLLUP_DAYS):
                drop(conn, name, report)
        cutoff = midnight(today - timedelta(days=CONTEXT_DAYS))
        conn.execute("DELETE FROM events WHERE ts < %s", (cutoff,))
        conn.execute("DELETE FROM facts WHERE ts < %s", (cutoff,))


def drop(conn: psycopg.Connection, name: str, report: Report) -> None:
    conn.execute(sql.SQL("DROP TABLE {}").format(sql.Identifier(name)))
    report.dropped_partitions += 1
    log.info("dropped expired partition %s", name)


class Worker:
    """Runs maintenance every INTERVAL in a thread of the core process."""

    def __init__(self, pool: ConnectionPool, interval: timedelta = INTERVAL) -> None:
        self._pool = pool
        self._interval = interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="maintenance", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=30)

    def _loop(self) -> None:
        while not self._stop.wait(self._interval.total_seconds()):
            try:
                with self._pool.connection() as conn:
                    report = run(conn)
                log.info("maintenance: %s", report)
            except Exception:
                log.exception("maintenance failed; retrying next round")
