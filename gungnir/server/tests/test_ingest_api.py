from tests.test_wire_format import batch, sample


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_unknown_token_is_refused(client, token):
    response = client.post("/api/v1/ingest", json=batch(), headers=auth("wrong"))
    assert response.status_code == 401


def test_samples_are_stored_and_queryable(client, token):
    body = batch(samples=[sample(ts=1_761_000_000_000 + i * 5000, value=float(i)) for i in range(3)])
    response = client.post("/api/v1/ingest", json=body, headers=auth(token))
    assert response.json() == {"accepted": 3, "inserted": 3}

    rows = client.get(
        "/api/v1/hosts/laptop/samples",
        params={"signal": "disk.read_await_ms", "entity": "sda", "since": "2025-01-01T00:00:00Z"},
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
    assert response.json() == {"accepted": 1, "inserted": 0}


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
    assert host["dropped_samples"] == 7
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
