from datetime import UTC, datetime, timedelta

from gungnir_server.detection import slices
from gungnir_server.detection.runner import Runner
from gungnir_server.params import Params
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

    from gungnir_server.anomalies import records_of
    from gungnir_server.display import render_records

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


def test_a_disk_record_carries_the_request_size_during_it(conn):
    conn.autocommit = True
    host = seed.add_host(conn)
    ticks = seed.ticks(START, 80)
    slow = range(40, 52)
    rows = [(t, "disk.write_await_ms", "nvme0n1", 30.0 if i in slow else 0.2) for i, t in enumerate(ticks)]
    # Small requests while the disk is slow, large ones before and after;
    # no reads at all, so no read size is sent.
    rows += [
        (t, "disk.write_request_bytes", "nvme0n1", 4096.0 if i in slow else 131072.0)
        for i, t in enumerate(ticks)
    ]
    seed.samples(conn, host, rows)

    runner = Runner.live(conn, host, P)
    runner.at = START
    runner.run(START + timedelta(seconds=5 * 46))
    [(ended, context)] = conn.execute("SELECT ended_at, context FROM anomalies").fetchall()
    assert ended is None and context == {"disk.write_request_bytes": 4096.0}
    runner.run(START + timedelta(seconds=5 * 80))
    [(ended, context)] = conn.execute("SELECT ended_at, context FROM anomalies").fetchall()
    assert ended == ticks[52] and context == {"disk.write_request_bytes": 4096.0}


def test_a_cpu_record_names_the_commands_behind_it(conn):
    conn.autocommit = True
    host = seed.add_host(conn)
    group = "user.slice/kitty.scope"
    ticks = seed.ticks(START, 80)
    busy = range(40, 52)
    seed.samples(
        conn, host, [(t, "cpu.idle_ratio", "host", 0.05 if i in busy else 0.5) for i, t in enumerate(ticks)]
    )
    # A build: make leads its process group, compilers come and go under it.
    procs = []
    for i, t in enumerate(ticks):
        if i in busy:
            procs += [(t, 500, 500, "make", 0.0, group), (t, 1000 + 2 * i, 500, "cc1plus", 0.9, group)]
            procs += [(t, 1001 + 2 * i, 500, "cc1plus", 0.9, group)]
        procs += [
            (t, 400, 400, "zsh", 0.01, group),
            (t, 700, 700, "chromium", 0.3, "app.slice/chromium.scope"),
        ]
    seed.process_groups(conn, host, procs)

    runner = Runner.live(conn, host, P)
    runner.at = START
    runner.run(START + timedelta(seconds=5 * 80))
    [(processes,)] = conn.execute("SELECT processes FROM anomalies").fetchall()
    build, *rest = processes
    assert build["pgid"] == 500 and build["leader"] == "make"
    assert build["names"][0] == ["cc1plus", 24] and build["pids"] == 25
    assert abs(build["cores"] - 1.8) < 0.2
    assert [c["names"][0][0] for c in rest] == ["chromium", "zsh"]

    # Within a control group only its own processes count.
    within = slices.commands(conn, host.id, group, ticks[40], ticks[51], "cores")
    assert [c["names"][0][0] for c in within] == ["cc1plus", "zsh"]
