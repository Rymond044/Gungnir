from datetime import timedelta

import numpy as np

from gungnir_server import base, comparison, incidents, labels
from gungnir_server.comparison import Alert, Score, Suspect, Window, join, score, score_causes
from gungnir_server.inference import RuleEngine
from gungnir_server.params import Params
from tests import seed
from tests.test_incidents import DISK, READER, START, WRITER, record

P = Params()


def test_records_closer_than_the_quiet_period_are_one_alert():
    assert join([(0, 10), (100, 200), (400, 420)], 120) == [Alert(0, 200), Alert(400, 420)]
    assert join([(50, 60), (0, 100)], 120) == [Alert(0, 100)]


def test_faults_harmless_loads_and_alerts_outside_phases():
    fault = Window("r/cpu", 100, 200, True, "cpu_hog", "gungnir-sc-cpu_hog")
    harmless = Window("r/build", 300, 400, False, None, None)
    labeled = [(100, 200), (300, 400)]
    ticks = np.arange(0, 3600, 5.0)
    alerts = [Alert(110, 150), Alert(320, 330), Alert(1000, 1010), Alert(2000, 2010)]
    s = score(alerts, [fault, harmless], labeled, [(2000, 2100)], ticks, 5.0)
    assert (s.faults, s.found, s.opened_inside, s.delays_s) == (1, 1, 1, [10.0])
    assert (s.harmless, s.alerted_harmless) == (1, 1)
    # Two alerts start outside the phases, in about 0.94 h of data there.
    assert s.false_alerts == 2 and round(s.observed_h, 2) == 0.94
    assert s.quiet_alerts == 1
    missed = score([Alert(500, 600)], [fault], labeled, [], ticks, 5.0)
    assert missed.missed == ["r/cpu"] and missed.recall == 0


def test_the_cause_is_scored_on_hypotheses_seen_during_the_phase():
    w = Window("r/cpu", 100, 200, True, "cpu_hog", "gungnir-sc-cpu_hog.scope")
    group = "user.slice/user-1000.slice/user@1000.service/app.slice/gungnir-sc-cpu_hog.scope"
    suspects = [
        Suspect(1, "process_churn", None, 150, 160, 0.9),
        Suspect(1, "cpu_hog", group, 120, 190, 0.8),
        # Seen long after the phase: not its hypothesis.
        Suspect(1, "thermal_throttling", None, 900, 950, 0.99),
    ]
    s = Score()
    score_causes(s, [Alert(110, 300, 1)], [w], suspects, {1: {group}})
    assert (s.top1, s.top3, s.culprit, s.proposed) == (0, 1, 1, 1)
    assert s.wrong == ["r/cpu: process_churn, cpu_hog"]


def test_three_approaches_from_one_replay(conn):
    host = seed.add_host(conn)
    until = START + timedelta(minutes=30)
    seed.samples(conn, host, [(t, "cpu.idle_ratio", "host", 0.9) for t in seed.ticks(START, 360)])
    (replay_id,) = conn.execute(
        "INSERT INTO replays (host_id, variant, since, until, params) VALUES (%s, 'live', %s, %s, '{}')"
        " RETURNING id",
        (host.id, START, until),
    ).fetchone()
    ms = int(START.timestamp() * 1000)
    phase = labels.PhaseLabel(
        run="s9",
        kind="scenario",
        phase="copy",
        start_ms=ms + 90_000,
        end_ms=ms + 180_000,
        cause="bulk_sequential_writer",
        harm=True,
        scope=WRITER.rsplit("/", 1)[-1],
    )
    labels.import_phases(conn, host, [phase], P.label_tail)
    for at, end, signal, entity, extra, detector in (
        (100, 170, "cgroup.write_bytes_per_s", WRITER, None, None),
        (105, 170, "disk.write_bytes_per_s", DISK, {"disk.write_request_bytes": 131072}, None),
        (110, 170, "cgroup.io_pressure_some_ratio", READER, None, None),
        (1200, 1210, "cpu.idle_ratio", "host", None, "threshold"),
    ):
        record(conn, host, signal, entity, at, end, "anomaly", extra, replay_id, detector)
    conn.commit()
    kb = base.load()
    incidents.Manager(kb, RuleEngine(kb, P)).replay(conn, host, P, replay_id, START, until)

    c = comparison.compare(conn, host, replay_id, P)
    stats, thresholds, hybrid = (c.scores[n] for n in ("statistics", "thresholds", "hybrid"))
    assert (stats.found, stats.faults, stats.delays_s) == (1, 1, [10.0])
    assert (thresholds.found, thresholds.false_alerts) == (0, 1)
    assert (hybrid.found, hybrid.top1, hybrid.culprit, hybrid.proposed) == (1, 1, 1, 1)
    assert "| hybrid | 1 / 1 |" in comparison.table(c)
