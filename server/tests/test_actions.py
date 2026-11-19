from datetime import timedelta

import pytest

from gungnir_server import actions, auth, base, hosts, incidents
from gungnir_server.inference import RuleEngine
from gungnir_server.ingest import ActionEntry
from tests import seed
from tests.conftest import PASSWORD
from tests.test_incidents import START, P, detected_until, record
from tests.test_inference import DISK, READER, WRITER
from tests.test_wire_format import batch


@pytest.fixture(scope="module")
def kb():
    return base.load()


@pytest.fixture(scope="module")
def engine(kb):
    return RuleEngine(kb, P)


def copy_incident(conn, kb, engine, host=None):
    """The copy of test_incidents: 80 MB/s of writes make a reader wait."""
    host = host or seed.add_host(conn)
    seed.samples(
        conn,
        host,
        [(START + timedelta(seconds=s), "cgroup.write_bytes_per_s", WRITER, 80e6) for s in range(60, 180, 5)],
    )
    record(conn, host, "cgroup.write_bytes_per_s", WRITER, 100, 170)
    record(conn, host, "disk.write_bytes_per_s", DISK, 105, 170, context={"disk.write_request_bytes": 131072})
    record(conn, host, "cgroup.io_pressure_some_ratio", READER, 110, 170)
    conn.execute(
        "INSERT INTO incident_state (host_id, at) VALUES (%s, %s)", (host.id, START + timedelta(seconds=60))
    )
    detected_until(conn, host, 180)
    incidents.Manager(kb, engine)(conn, host, P, START)
    [found] = incidents.summaries(conn, host, START - timedelta(hours=1), START + timedelta(hours=1))
    [proposal] = [p for p in incidents.one(conn, host, found.id).proposals if p.action == "io_max_wbps"]
    conn.commit()
    return host, found.id, proposal


def applied(conn, host, action_id, at_s):
    actions.record(
        conn,
        host,
        [
            ActionEntry(
                seq=1,
                id=action_id,
                at=int((START + timedelta(seconds=at_s)).timestamp() * 1000),
                event="applied",
                files=[
                    {
                        "group": WRITER,
                        "file": "io.max",
                        "key": "259:0 wbps",
                        "before": "max",
                        "after": "40000000",
                    }
                ],
            )
        ],
    )


def test_a_confirmed_proposal_is_applied_verified_and_kept(conn, kb, engine):
    host, incident_id, proposal = copy_incident(conn, kb, engine)
    incidents.decide(conn, host, incident_id, proposal.id, "confirm", "admin", START)
    action_id = actions.queue(conn, kb, P, host, proposal.id, "admin", START)
    [command] = actions.commands(conn, host, START)
    assert command == {
        "id": action_id,
        "op": "apply",
        "action": "io_max_wbps",
        "target": WRITER,
        "value": 40e6,
        "share": None,
        "revert_after_s": 1800,
    }
    # Repeated until the actuator's log says what became of it.
    assert actions.commands(conn, host, START) == [command]
    applied(conn, host, action_id, 160)
    applied(conn, host, action_id, 160)
    assert actions.commands(conn, host, START) == []
    with pytest.raises(actions.Refused, match="after verification"):
        actions.ask(conn, host, action_id, "keep", "admin", START)

    # The reader waited before the limit and not at the end of the window.
    found = actions.one(conn, host, action_id)
    detected_until(conn, host, 160 + found.verify_after_s + 5)
    actions.Verifier(kb, engine)(conn, host, P, START)
    found = actions.one(conn, host, action_id)
    assert found.state == "applied" and found.outcome == "worked"
    assert [e.event for e in found.log] == ["confirmed", "applied", "verified"]
    assert found.log[-1].detail["before"] == [f"cgroup.io_pressure_some_ratio@{READER}"]

    actions.ask(conn, host, action_id, "keep", "admin", START)
    assert actions.commands(conn, host, START) == [{"id": action_id, "op": "keep"}]
    actions.record(conn, host, [ActionEntry(seq=2, id=action_id, at=0, event="kept")])
    assert actions.one(conn, host, action_id).state == "kept"
    assert incidents.one(conn, host, incident_id).proposals[0].action_state in {"kept", None}


def test_a_long_rule_window_is_cut_to_be_judged_before_the_rollback(kb):
    # A leak's symptoms may follow its growth by 12 h, but a limit acts in
    # minutes and the actuator undoes it after 30 (ADR 0063).
    assert actions.verify_after(kb, P, "memory_leak") == P.verify_max < P.rollback_after
    assert actions.verify_after(kb, P, "bulk_sequential_writer") < P.verify_max


def test_an_action_that_leaves_others_waiting_is_rolled_back(conn, kb, engine):
    host, incident_id, proposal = copy_incident(conn, kb, engine)
    incidents.decide(conn, host, incident_id, proposal.id, "confirm", "admin", START)
    action_id = actions.queue(conn, kb, P, host, proposal.id, "admin", START)
    applied(conn, host, action_id, 120)
    window = actions.one(conn, host, action_id).verify_after_s
    # The reader still waits at the end of the window.
    record(conn, host, "cgroup.io_pressure_some_ratio", READER, 171, 120 + window + 60, kind="impact")
    detected_until(conn, host, 120 + window + 5)
    actions.Verifier(kb, engine)(conn, host, P, START)
    found = actions.one(conn, host, action_id)
    assert found.outcome == "failed"
    assert actions.commands(conn, host, START) == [
        {"id": action_id, "op": "revert", "reason": "verification failed"}
    ]
    actions.record(
        conn, host, [ActionEntry(seq=2, id=action_id, at=0, event="reverted", reason="verification failed")]
    )
    found = actions.one(conn, host, action_id)
    assert found.state == "reverted" and found.asked is None
    assert actions.commands(conn, host, START) == []


def test_the_hosts_own_pressure_is_not_counted_against_a_limit(conn, kb, engine):
    # Throttling the writer makes it stall, and the host's pressure counts that.
    host, incident_id, proposal = copy_incident(conn, kb, engine)
    incidents.decide(conn, host, incident_id, proposal.id, "confirm", "admin", START)
    action_id = actions.queue(conn, kb, P, host, proposal.id, "admin", START)
    applied(conn, host, action_id, 160)
    window = actions.one(conn, host, action_id).verify_after_s
    record(conn, host, "io.pressure_some_ratio", "host", 165, 160 + window + 60, kind="impact")
    detected_until(conn, host, 160 + window + 5)
    actions.Verifier(kb, engine)(conn, host, P, START)
    found = actions.one(conn, host, action_id)
    assert found.outcome == "worked", found.log[-1].detail


def test_a_limit_without_a_known_use_is_not_queued_and_an_unanswered_one_is_given_up(conn, kb, engine):
    host, _, proposal = copy_incident(conn, kb, engine)
    conn.execute(
        "UPDATE proposals SET parameter = parameter || '{\"value\": null}' WHERE id = %s", (proposal.id,)
    )
    with pytest.raises(actions.Refused, match="nothing to compute"):
        actions.queue(conn, kb, P, host, proposal.id, "admin", START)
    conn.execute(
        "UPDATE proposals SET parameter = parameter || '{\"value\": 4e7}' WHERE id = %s", (proposal.id,)
    )
    action_id = actions.queue(conn, kb, P, host, proposal.id, "admin", START)
    assert actions.commands(conn, host, START + actions.UNANSWERED + timedelta(seconds=1)) == []
    found = actions.one(conn, host, action_id)
    assert found.state == "refused" and found.reason == "the host did not answer"
    # Applied after all: the server asks for it to be undone.
    applied(conn, host, action_id, 500)
    assert actions.commands(conn, host, START)[0]["op"] == "revert"


def test_the_api_confirms_with_the_account_and_the_agent_gets_the_command(client, token, pool, kb, engine):
    with pool.connection() as conn:
        _, incident_id, proposal = copy_incident(conn, kb, engine, hosts.by_name(conn, "laptop"))
    url = f"/api/v1/hosts/laptop/incidents/{incident_id}/proposals/{proposal.id}"
    decided = client.post(url, json={"decision": "confirm"}).json()
    assert decided["state"] == "confirmed" and decided["decided_by"] == "admin"
    assert decided["action_state"] == "queued"
    body = batch(seq=1)
    answer = client.post("/api/v1/ingest", json=body, headers={"Authorization": f"Bearer {token}"}).json()
    [command] = answer["commands"]
    assert command["op"] == "apply" and command["target"] == WRITER
    body = batch(seq=2)
    body["actions"] = [{"id": command["id"], "at": 0, "event": "refused", "reason": "no actuator"}]
    answer = client.post("/api/v1/ingest", json=body, headers={"Authorization": f"Bearer {token}"}).json()
    assert answer["commands"] == []
    [row] = client.get("/api/v1/hosts/laptop/actions", params={"since": "2000-01-01T00:00:00Z"}).json()
    assert row["state"] == "refused" and row["reason"] == "no actuator" and row["cause"]
    revert = client.post(f"/api/v1/hosts/laptop/actions/{row['id']}", json={"op": "revert"})
    assert revert.status_code == 409


# Login (ADR 0064)


def test_nothing_is_read_without_a_session_but_the_agent_still_sends(anonymous, token):
    assert anonymous.get("/api/v1/hosts").status_code == 401
    assert anonymous.get("/api/v1/auth/me").status_code == 401
    sent = anonymous.post("/api/v1/ingest", json=batch(seq=1), headers={"Authorization": f"Bearer {token}"})
    assert sent.status_code == 200


def test_the_initial_password_must_be_changed_first(anonymous, pool):
    with pool.connection() as conn:
        initial = auth.initial_password(conn)
    signed = anonymous.post("/api/v1/auth/login", json={"name": "admin", "password": initial})
    assert signed.json() == {"name": "admin", "must_change": True}
    assert anonymous.get("/api/v1/hosts").status_code == 403
    short = anonymous.post("/api/v1/auth/password", json={"old": initial, "new": "short"})
    assert short.status_code == 422
    assert anonymous.post("/api/v1/auth/password", json={"old": initial, "new": PASSWORD}).status_code == 200
    assert anonymous.get("/api/v1/hosts").status_code == 200
    with pool.connection() as conn:
        assert auth.initial_password(conn) is None
    again = anonymous.post("/api/v1/auth/login", json={"name": "admin", "password": initial})
    assert again.status_code == 401


def test_failed_logins_lock_the_name_and_a_reset_ends_every_session(client, pool):
    for _ in range(auth.FAILURES):
        wrong = client.post("/api/v1/auth/login", json={"name": "admin", "password": "wrong"})
        assert wrong.status_code == 401
    locked = client.post("/api/v1/auth/login", json={"name": "admin", "password": PASSWORD})
    assert locked.status_code == 429
    assert client.get("/api/v1/hosts").status_code == 200
    with pool.connection() as conn:
        fresh = auth.reset(conn)
        assert auth.initial_password(conn) == fresh
    assert client.get("/api/v1/hosts").status_code == 401
    client.post("/api/v1/auth/logout")
