from datetime import UTC, datetime

from tests.test_wire_format import RECENT, batch, event, process, sample


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_unknown_token_is_refused(client, token):
    response = client.post("/api/v1/ingest", json=batch(), headers=auth("wrong"))
    assert response.status_code == 401


def test_samples_are_stored_and_queryable(client, token):
    body = batch(samples=[sample(ts=RECENT + i * 5000, value=float(i)) for i in range(3)])
    response = client.post("/api/v1/ingest", json=body, headers=auth(token))
    assert response.json() == {"accepted": 3, "inserted": 3, "out_of_window": 0}

    rows = client.get(
        "/api/v1/hosts/laptop/samples",
        params={"signal": "disk.read_await_ms", "entity": "sda", "since": "2026-01-01T00:00:00Z"},
    ).json()
    assert [r["value"] for r in rows] == [0.0, 1.0, 2.0]

    series = client.get("/api/v1/hosts/laptop/series").json()
    assert series == [
        {"signal": "disk.read_await_ms", "entity": "sda", "last_ts": rows[-1]["ts"], "last_value": 2.0}
    ]


def test_resent_batch_is_harmless(client, token):
    body = batch()
    client.post("/api/v1/ingest", json=body, headers=auth(token))
    response = client.post("/api/v1/ingest", json=body, headers=auth(token))
    assert response.json() == {"accepted": 1, "inserted": 0, "out_of_window": 0}


def test_disabled_source_is_recorded_with_reason(client, token):
    sources = [
        {"module": "cpu", "state": "preferred", "origin": "/proc/stat"},
        {"module": "memory", "state": "fallback", "origin": "sysinfo(2)", "reason": "/proc/meminfo: gone"},
        {"module": "disk", "state": "disabled", "reason": "/sys/block: permission denied"},
    ]
    client.post("/api/v1/ingest", json=batch(sources=sources), headers=auth(token))
    states = {s["module"]: s for s in client.get("/api/v1/hosts/laptop/sources").json()}
    assert states["memory"]["state"] == "fallback"
    assert states["disk"]["state"] == "disabled"
    assert states["disk"]["reason"] == "/sys/block: permission denied"


def test_host_shows_agent_and_last_contact(client, token):
    client.post("/api/v1/ingest", json=batch(dropped=7), headers=auth(token))
    (host,) = client.get("/api/v1/hosts").json()
    assert host["name"] == "laptop"
    assert host["agent_version"] == "0.1.0"
    assert host["dropped_rows"] == 7
    assert host["utc_offset_s"] == 10800
    assert host["last_seen_at"] is not None


def test_malformed_batch_gets_422(client, token):
    response = client.post("/api/v1/ingest", json=batch(seq=0), headers=auth(token))
    assert response.status_code == 422


def test_agent_without_utc_offset_is_accepted_and_keeps_the_last_one(client, token):
    client.post("/api/v1/ingest", json=batch(), headers=auth(token))
    older_agent = batch(agent={"version": "0.0.9", "started_at": 1_761_000_000_000})
    assert client.post("/api/v1/ingest", json=older_agent, headers=auth(token)).status_code == 200
    (host,) = client.get("/api/v1/hosts").json()
    assert host["utc_offset_s"] == 10800


def test_rows_outside_retention_are_refused_not_failed(client, token):
    old = RECENT - 30 * 86_400_000
    body = batch(samples=[sample(ts=old), sample()], processes=[process(ts=old)])
    response = client.post("/api/v1/ingest", json=body, headers=auth(token))
    assert response.json() == {"accepted": 2, "inserted": 1, "out_of_window": 2}


def test_context_is_stored_and_queryable(client, token):
    body = batch(
        facts=[{"ts": RECENT, "name": "power.profile", "entity": "host", "value": "eco"}],
        processes=[process(), process(pid=1, comm="systemd", cpu_cores=0.0)],
        events=[event()],
    )
    client.post("/api/v1/ingest", json=body, headers=auth(token))
    client.post("/api/v1/ingest", json=body, headers=auth(token))

    (fact,) = client.get("/api/v1/hosts/laptop/facts").json()
    assert fact["value"] == "eco"
    at = datetime.fromtimestamp(RECENT / 1000 + 1, tz=UTC).isoformat()
    procs = client.get("/api/v1/hosts/laptop/processes", params={"at": at}).json()
    assert [p["comm"] for p in procs] == ["stress-ng", "systemd"]
    assert procs[0]["unit"] == "load.service"
    (stored,) = client.get("/api/v1/hosts/laptop/events").json()
    assert stored["message"].startswith("Out of memory")
