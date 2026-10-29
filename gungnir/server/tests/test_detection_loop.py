from datetime import UTC, datetime, timedelta

from gungnir_core.detection.runner import Runner
from gungnir_core.params import Params
from tests import seed
from tests.test_wire_format import RECENT

START = datetime.fromtimestamp(RECENT / 1000, tz=UTC) - timedelta(minutes=30)
P = Params()


def test_live_loop_writes_one_interval_across_steps_and_restarts(conn):
    conn.autocommit = True
    host = seed.add_host(conn)
    ticks = seed.ticks(START, 120)
    idle = [0.05 if 40 <= i < 52 else 0.5 for i in range(120)]
    rows = [(t, "cpu.idle_ratio", "host", v) for t, v in zip(ticks, idle, strict=True)]
    rows += [(t, "mem.pressure_full_ratio", "host", 0.7 if i >= 100 else 0.0) for i, t in enumerate(ticks)]
    seed.samples(conn, host, rows)

    runner = Runner.live(conn, host, P)
    runner.at = START
    runner.run(START + timedelta(seconds=5 * 45))
    [(started, ended)] = conn.execute("SELECT started_at, ended_at FROM anomalies").fetchall()
    assert started == ticks[40] and ended is None

    # A restart continues from the saved state.
    runner = Runner.live(conn, host, P)
    assert runner.at == START + timedelta(seconds=5 * 45)
    runner.run(START + timedelta(seconds=5 * 120))
    rows = conn.execute(
        "SELECT detector, level, started_at, ended_at, direction, baseline->>'basis'"
        " FROM anomalies ORDER BY id"
    ).fetchall()
    assert rows[0] == ("threshold", None, ticks[40], ticks[52], "down", "threshold")
    # Severe memory pressure needs six samples (oomd's 30 s), and is still on.
    assert rows[1] == ("impact", "severe", ticks[100], None, "up", "threshold")
    assert len(rows) == 2


def test_records_read_back_in_words(conn):
    from datetime import timezone

    from gungnir_core.anomalies import records_of
    from gungnir_core.display import render_records

    test_live_loop_writes_one_interval_across_steps_and_restarts(conn)
    host = seed.hosts.by_name(conn, "laptop")
    now = START + timedelta(minutes=15)
    rows = records_of(conn, host, START)
    assert [r.detector for r in rows] == ["impact", "threshold"]
    assert [r.detector for r in records_of(conn, host, START, open_only=True)] == ["impact"]
    text = render_records(rows, now, timezone(timedelta(hours=3)), "Records")
    assert "2 records, 1 open" in text
    assert "impact severe" in text and "limit above 60.0 %" in text
    assert "threshold" in text and "limit below 10.0 %" in text
