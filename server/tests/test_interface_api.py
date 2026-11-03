from datetime import UTC, datetime, timedelta

from gungnir_server import charts
from tests.test_ingest_api import auth
from tests.test_wire_format import RECENT, batch, sample


def iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).isoformat()


def test_bucket_is_the_shortest_that_fits_the_points():
    now = datetime(2026, 11, 3, 12, tzinfo=UTC)
    assert charts.bucket_for(now - timedelta(minutes=15), now, 600, 5.0) == 5
    assert charts.bucket_for(now - timedelta(hours=1), now, 600, 5.0) == 10
    assert charts.bucket_for(now - timedelta(days=1), now, 600, 5.0) == 300


def test_a_sparse_rate_is_zero_between_samples_while_its_group_lives():
    minute = 60_000
    points = [[0, 1.0, 1.0, 1.0], [3 * minute, 2.0, 2.0, 2.0], [60 * minute, 3.0, 3.0, 3.0]]
    filled = charts.fill_zeros(points, minute)
    times = [p[0] for p in filled]
    assert times[:4] == [0, minute, 2 * minute, 3 * minute]
    assert filled[1][1] == 0.0
    # More than one full snapshot period after a sample the group is gone.
    assert 30 * minute not in times


def test_a_group_memory_holds_its_value_between_samples():
    minute = 60_000
    held = charts.carry([[0, 5.0, 5.0, 5.0], [3 * minute, 7.0, 7.0, 7.0]], minute)
    assert [p[1] for p in held] == [5.0, 5.0, 5.0, 7.0]


def test_overview_derives_memory_size_and_lists_devices(client, token):
    body = batch(
        samples=[
            sample(signal="mem.available_bytes", entity="host", value=4e9),
            sample(signal="mem.used_ratio", entity="host", value=0.75),
            sample(signal="disk.write_bytes_per_s", entity="nvme0n1", value=1e6),
        ]
    )
    client.post("/api/v1/ingest", json=body, headers=auth(token))
    found = client.get("/api/v1/hosts/laptop/overview").json()
    assert found["memory_bytes"] == 16e9
    assert found["devices"] == ["nvme0n1"]
    assert found["open"] == {"anomalies": 0, "impact": 0, "expected": 0}
    assert found["collectors"][0]["collector"] == "cpu"
    assert found["host"]["name"] == "laptop"


def test_chart_gives_mean_and_range_per_bucket(client, token):
    values = [1.0, 3.0, 2.0, 6.0]
    body = batch(
        samples=[
            sample(ts=RECENT + i * 5000, signal="cpu.user_ratio", entity="host", value=v)
            for i, v in enumerate(values)
        ]
    )
    client.post("/api/v1/ingest", json=body, headers=auth(token))
    found = client.get(
        "/api/v1/hosts/laptop/chart",
        params={
            "series": ["cpu.user_ratio", "disk.read_await_ms@sda"],
            "since": iso(RECENT),
            "until": iso(RECENT + 20_000),
            "points": 10,
        },
    ).json()
    assert found["bucket_s"] == 5
    user, await_ = found["series"]
    assert [p[1] for p in user["points"]] == values
    assert await_ == {"signal": "disk.read_await_ms", "entity": "sda", "points": []}

    wide = client.get(
        "/api/v1/hosts/laptop/chart",
        params={
            "series": ["cpu.user_ratio"],
            "since": iso(RECENT),
            "until": iso(RECENT + 20_000),
            "points": 2,
        },
    ).json()
    assert wide["bucket_s"] == 10
    assert [p[1:] for p in wide["series"][0]["points"]] == [[2.0, 1.0, 3.0], [4.0, 2.0, 6.0]]


def test_chart_refuses_too_many_series(client, token):
    response = client.get("/api/v1/hosts/laptop/chart", params={"series": [f"cpu.s{i}" for i in range(17)]})
    assert response.status_code == 422


def test_norm_of_a_series_without_baseline_has_spans_and_limits(client, token):
    client.post(
        "/api/v1/ingest",
        json=batch(samples=[sample(signal="disk.read_await_ms", entity="sda", value=1.0)]),
        headers=auth(token),
    )
    found = client.get(
        "/api/v1/hosts/laptop/norm",
        params={
            "signal": "disk.read_await_ms",
            "entity": "sda",
            "since": iso(RECENT - 60_000),
            "until": iso(RECENT),
        },
    ).json()
    assert found["bands"] == []
    assert found["spans"][0]["workload_class"] == "unknown"
    assert {(lim["kind"], lim["above"]) for lim in found["limits"]} == {("threshold", 20), ("impact", 50)}
    missing = client.get(
        "/api/v1/hosts/laptop/norm", params={"signal": "disk.read_await_ms", "entity": "sdz"}
    )
    assert missing.status_code == 404


def test_anomalies_list_and_a_missing_record(client, token):
    assert client.get("/api/v1/hosts/laptop/anomalies", params={"open": True}).json() == []
    assert client.get("/api/v1/hosts/laptop/anomalies/1").status_code == 404


def test_diagnosis_of_a_quiet_interval_and_a_bounded_window(client, token):
    (found,) = client.get("/api/v1/hosts/laptop/diagnosis").json()
    assert found["hypotheses"] == [] and found["unexplained"] == []
    assert found["evidence"] == {"records": 0, "events": 0, "processes": 0}
    assert found["near"] == []
    too_long = client.get(
        "/api/v1/hosts/laptop/diagnosis", params={"since": iso(RECENT - 2 * 86_400_000), "until": iso(RECENT)}
    )
    assert too_long.status_code == 422
    assert client.get("/api/v1/hosts/laptop/diagnosis", params={"run": "none"}).status_code == 404


def test_knowledge_lists_every_cause_with_its_rule_and_provenance(client):
    kb = client.get("/api/v1/knowledge").json()
    causes = {c["name"]: c for c in kb["causes"]}
    rules = {r["cause"] for r in kb["rules"]}
    assert rules <= set(causes)
    storm = causes["writeback_storm"]
    assert storm["label"] == "Writeback storm"
    assert storm["domain"] == "disk"
    assert storm["source"]
    assert all(s["source"] for r in kb["rules"] for s in r["symptoms"])
    assert all(e["source"] for e in kb["policy"])
    assert len(kb["tests"]) > 0
