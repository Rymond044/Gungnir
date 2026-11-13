from datetime import UTC, datetime, timedelta

import pytest
from psycopg.types.json import Jsonb

from gungnir_server import base, incidents
from gungnir_server.incidents import Current, Harm, advance
from gungnir_server.inference import RuleEngine
from gungnir_server.params import Params
from gungnir_server.telemetry import SeriesIds
from tests import seed
from tests.test_inference import DISK, READER, WRITER

P = Params()
QUIET = P.incident_quiet.total_seconds()


def test_an_incident_opens_on_harm_and_closes_after_the_quiet_period():
    assert advance(None, [], 1000, None, QUIET).incident is None
    step = advance(None, [Harm(100, None)], 200, None, QUIET)
    assert step.opened and step.incident == Current(100, 200) and step.closed_at is None
    # Harm that lasts keeps it open; its end starts the quiet period.
    step = advance(step.incident, [Harm(100, 300)], 400, None, QUIET)
    assert step.incident.last_harm == 300 and step.closed_at is None
    step = advance(step.incident, [Harm(100, 300)], 300 + QUIET, None, QUIET)
    assert step.closed_at == 300 + QUIET


def test_harm_of_the_closed_incident_opens_no_new_one():
    assert advance(None, [Harm(100, 300)], 1000, 300, QUIET).incident is None
    later = advance(None, [Harm(100, 300), Harm(900, 950)], 1000, 300, QUIET)
    assert later.incident == Current(900, 950) and later.opened


def test_harm_that_ended_long_ago_opens_and_closes_in_one_step():
    # Catching up on detection: the whole incident lies inside the step.
    step = advance(None, [Harm(100, 130)], 900, None, QUIET)
    assert step.opened and step.closed_at == 130 + QUIET


START = datetime(2026, 11, 13, 9, 0, tzinfo=UTC)


def record(conn, host, signal, entity, at_s, end_s=None, kind="anomaly", context=None):
    ids = SeriesIds().resolve(conn, host.id, {(signal, entity)})
    conn.execute(
        "INSERT INTO anomalies (host_id, series_id, kind, detector, level, started_at, ended_at,"
        " direction, peak_score, baseline, context)"
        " VALUES (%s, %s, %s, 'robust.mad', %s, %s, %s, 'up', 9, '{}', %s)",
        (
            host.id,
            ids[(signal, entity)],
            kind,
            "noticeable" if kind == "impact" else None,
            START + timedelta(seconds=at_s),
            None if end_s is None else START + timedelta(seconds=end_s),
            Jsonb(context) if context else None,
        ),
    )


def detected_until(conn, host, at_s):
    conn.execute(
        "INSERT INTO detection_state (host_id, at, state) VALUES (%s, %s, '{}')"
        " ON CONFLICT (host_id) DO UPDATE SET at = EXCLUDED.at",
        (host.id, START + timedelta(seconds=at_s)),
    )
    conn.commit()


@pytest.fixture(scope="module")
def manager():
    kb = base.load()
    return incidents.Manager(kb, RuleEngine(kb, P))


def test_a_copy_that_makes_a_reader_wait_gives_an_incident_and_a_proposal(conn, manager):
    host = seed.add_host(conn)
    # The copy wrote 80 MB/s for the last minute.
    seed.samples(
        conn,
        host,
        [(START + timedelta(seconds=s), "cgroup.write_bytes_per_s", WRITER, 80e6) for s in range(60, 180, 5)],
    )
    record(conn, host, "cgroup.write_bytes_per_s", WRITER, 100, 170)
    record(conn, host, "disk.write_bytes_per_s", DISK, 105, 170, context={"disk.write_request_bytes": 131072})
    record(conn, host, "cgroup.io_pressure_some_ratio", READER, 110, 170)
    # A record of the host before the incident and far from it is not its evidence.
    record(conn, host, "cpu.user_ratio", "host", -3000, -2900)
    conn.execute(
        "INSERT INTO incident_state (host_id, at) VALUES (%s, %s)", (host.id, START + timedelta(seconds=60))
    )
    detected_until(conn, host, 180)
    manager(conn, host, P, START)

    [found] = incidents.summaries(conn, host, START - timedelta(hours=1), START + timedelta(hours=1))
    assert found.closed_at is None and found.cause == "bulk_sequential_writer" and found.culprit == WRITER
    assert found.records == 3
    incident = incidents.one(conn, host, found.id)
    top = incident.hypotheses[0]
    assert top.harm and top.rank == 1
    # The writer is the culprit of more than one cause; its writes are limited once,
    # for the best of them.
    [proposal] = [p for p in incident.proposals if p.action == "io_max_wbps"]
    assert proposal.hypothesis_id == top.id and proposal.rank == min(p.rank for p in incident.proposals)
    assert proposal.target == WRITER and proposal.state == "proposed"
    assert proposal.parameter["current"] == 80e6 and proposal.parameter["value"] == 40e6
    assert proposal.risk == "characteristic" and proposal.rollback_after_s == 1800
    assert incidents.open_summary(conn, host).id == found.id

    decided = incidents.decide(conn, host, found.id, proposal.id, "reject", "operator", START)
    assert decided.state == "rejected" and decided.decided_by == "operator"
    with pytest.raises(incidents.Refused):
        incidents.decide(conn, host, found.id, proposal.id, "confirm", "operator", START)

    # Two quiet minutes after the reader stopped waiting it closes.
    detected_until(conn, host, 170 + QUIET + 10)
    manager(conn, host, P, START)
    closed = incidents.one(conn, host, found.id)
    assert closed.summary.closed_at == START + timedelta(seconds=170 + QUIET)
    assert {p.state for p in closed.proposals if p.id != proposal.id} <= {"expired"}
    assert incidents.open_summary(conn, host) is None


def test_a_slice_is_never_the_target_of_an_action(conn, manager):
    host = seed.add_host(conn)
    record(conn, host, "cgroup.write_bytes_per_s", "system.slice", 100, 170)
    record(conn, host, "disk.write_bytes_per_s", DISK, 105, 170, context={"disk.write_request_bytes": 131072})
    record(conn, host, "cgroup.io_pressure_some_ratio", READER, 110, 170)
    detected_until(conn, host, 180)
    conn.execute("UPDATE incident_state SET at = %s", (START,))
    manager(conn, host, P, START)
    [found] = incidents.summaries(conn, host, START - timedelta(hours=1), START + timedelta(hours=1))
    assert found.culprit == "system.slice"
    assert incidents.one(conn, host, found.id).proposals == []


def test_activity_that_hurt_nobody_opens_no_incident(conn, manager):
    host = seed.add_host(conn)
    record(conn, host, "cgroup.write_bytes_per_s", WRITER, 100, 170)
    record(conn, host, "disk.write_bytes_per_s", DISK, 105, 170, context={"disk.write_request_bytes": 131072})
    detected_until(conn, host, 400)
    manager(conn, host, P, START)
    assert incidents.summaries(conn, host, START - timedelta(hours=1), START + timedelta(hours=1)) == []


def test_a_parameter_is_taken_while_the_culprit_was_active_and_is_never_zero(conn):
    host = seed.add_host(conn)
    # Wrote 60 MB/s from 100 s to 160 s, then nothing (a sparse group sends no zeros).
    signal = "cgroup.write_bytes_per_s"
    wrote = [(START + timedelta(seconds=s), signal, WRITER, 60e6) for s in range(100, 160, 5)]
    seed.samples(conn, host, wrote)
    active = (START + timedelta(seconds=100), START + timedelta(seconds=160))
    assert incidents.consumption(conn, host, P, WRITER, "cgroup.write_bytes_per_s", active) == 60e6
    # Long after it stopped there is nothing to take a share of.
    later = (START + timedelta(seconds=600), START + timedelta(seconds=700))
    assert incidents.consumption(conn, host, P, WRITER, "cgroup.write_bytes_per_s", later) is None
