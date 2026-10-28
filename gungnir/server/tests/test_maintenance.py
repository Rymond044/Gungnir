from datetime import UTC, datetime, timedelta

from gungnir_core import maintenance
from tests.test_ingest_api import auth
from tests.test_wire_format import RECENT, batch, sample


def at(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=UTC)


def rollup_row(conn):
    return conn.execute("SELECT min, max, sum, n FROM samples_1m ORDER BY ts").fetchall()


def test_complete_minutes_are_rolled_up(client, token, pool):
    values = [1.0, 3.0, 2.0]
    body = batch(samples=[sample(ts=RECENT + i * 5000, value=v) for i, v in enumerate(values)])
    client.post("/api/v1/ingest", json=body, headers=auth(token))
    with pool.connection() as conn:
        assert maintenance.rollup(conn, at(RECENT) + timedelta(minutes=2)) == 1
        assert rollup_row(conn) == [(1.0, 3.0, 6.0, 3)]
        # Nothing new, nothing to do.
        assert maintenance.rollup(conn, at(RECENT) + timedelta(minutes=2)) == 0


def test_late_samples_redo_their_minute(client, token, pool):
    client.post("/api/v1/ingest", json=batch(samples=[sample(value=1.0)]), headers=auth(token))
    with pool.connection() as conn:
        maintenance.rollup(conn, at(RECENT) + timedelta(minutes=2))
    # A spooled batch arrives after the minute was rolled up.
    late = batch(seq=2, samples=[sample(ts=RECENT + 30_000, value=5.0)])
    client.post("/api/v1/ingest", json=late, headers=auth(token))
    with pool.connection() as conn:
        maintenance.rollup(conn, at(RECENT) + timedelta(minutes=2))
        assert rollup_row(conn) == [(1.0, 5.0, 6.0, 2)]


def test_expired_partitions_go_but_kept_ranges_stay(client, token, pool):
    body = batch(samples=[sample(ts=RECENT), sample(ts=RECENT + 5000)])
    client.post("/api/v1/ingest", json=body, headers=auth(token))
    with pool.connection() as conn:
        conn.execute(
            "INSERT INTO kept_ranges (host_id, since, until, reason)"
            " SELECT id, %s, %s, 'labeled run' FROM hosts",
            (at(RECENT), at(RECENT + 1000)),
        )
        conn.commit()
        later = at(RECENT) + timedelta(days=maintenance.RAW_DAYS + 2)
        report = maintenance.run(conn, later)
        assert report.kept_samples == 1
        assert report.dropped_partitions >= 2
        assert conn.execute("SELECT count(*) FROM samples").fetchone() == (0,)
        assert conn.execute("SELECT count(*) FROM samples_1m").fetchone() == (1,)
