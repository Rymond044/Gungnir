"""HTTP API: ingest for agents, read endpoints for the interface and for checks."""

from collections.abc import Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Annotated

import psycopg
from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from psycopg_pool import ConnectionPool

from gungnir_server import (
    anomalies,
    audit,
    base,
    catalog,
    charts,
    gather,
    hosts,
    labels,
    maintenance,
    overview,
    params,
    pipeline,
    telemetry,
)
from gungnir_server.db import migrate, open_pool
from gungnir_server.evidence import inside
from gungnir_server.hosts import Host
from gungnir_server.inference import RuleEngine, as_dict
from gungnir_server.ingest import Batch

# A full batch of 10 000 samples is about 1 MB of JSON; 5 000 process rows
# add about 1.5 MB.
MAX_BODY = 4 * 1024 * 1024


def create_app(pool: ConnectionPool | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # A broken knowledge base stops the server here, never silently later.
        app.state.base = base.load()
        app.state.params = params.load()
        app.state.engine = RuleEngine(app.state.base, app.state.params)
        app.state.norms = charts.Norms(app.state.params)
        app.state.catalog = None
        own = pool is None
        app.state.pool = open_pool() if own else pool
        app.state.series = telemetry.SeriesIds()
        with app.state.pool.connection() as conn:
            migrate(conn)
            maintenance.ensure_partitions(conn, datetime.now(UTC))
        workers = [maintenance.Worker(app.state.pool), pipeline.Worker(app.state.pool)]
        for worker in workers:
            worker.start()
        yield
        for worker in workers:
            worker.stop()
        if own:
            app.state.pool.close()

    app = FastAPI(title="gungnIR server", version="0.1.0", lifespan=lifespan)

    @app.middleware("http")
    async def limit_body(request: Request, call_next):
        length = request.headers.get("content-length", "0")
        if not length.isdigit() or int(length) > MAX_BODY:
            return JSONResponse({"detail": "body too large"}, status_code=413)
        return await call_next(request)

    app.include_router(router, prefix="/api/v1")

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    return app


def connection(request: Request) -> Iterator[psycopg.Connection]:
    with request.app.state.pool.connection() as conn:
        yield conn


Conn = Annotated[psycopg.Connection, Depends(connection)]


def agent_host(conn: Conn, authorization: Annotated[str | None, Header()] = None) -> Host:
    scheme, _, token = (authorization or "").partition(" ")
    host = hosts.authenticate(conn, token) if scheme == "Bearer" and token else None
    if host is None:
        raise HTTPException(401, "unknown agent token", headers={"WWW-Authenticate": "Bearer"})
    return host


def named_host(conn: Conn, name: str) -> Host:
    host = hosts.by_name(conn, name)
    if host is None:
        raise HTTPException(404, f"no host '{name}'")
    return host


AgentHost = Annotated[Host, Depends(agent_host)]
NamedHost = Annotated[Host, Depends(named_host)]

router = APIRouter()


@router.post("/ingest")
def ingest(batch: Batch, request: Request, conn: Conn, host: AgentHost) -> telemetry.Stored:
    return telemetry.store(conn, request.app.state.series, host, batch, datetime.now(UTC))


@router.get("/hosts")
def list_hosts(conn: Conn) -> list[hosts.HostSummary]:
    return hosts.summaries(conn)


@router.get("/hosts/{name}/collectors")
def collectors(host: NamedHost, conn: Conn) -> list[telemetry.CollectorState]:
    return telemetry.collector_states(conn, host)


@router.get("/hosts/{name}/series")
def series(host: NamedHost, conn: Conn) -> list[telemetry.SeriesInfo]:
    return telemetry.series_of(conn, host)


@router.get("/hosts/{name}/samples")
def samples(
    host: NamedHost,
    conn: Conn,
    signal: str,
    entity: str = "host",
    since: datetime | None = None,
    until: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=100_000)] = 10_000,
) -> list[dict]:
    since, until = window(since, until, timedelta(hours=1))
    rows = telemetry.samples_of(conn, host, signal, entity, since, until, limit)
    return [{"ts": ts, "value": value} for ts, value in rows]


def window(since: datetime | None, until: datetime | None, default: timedelta) -> tuple[datetime, datetime]:
    until = until or datetime.now(UTC)
    return since or until - default, until


@router.get("/hosts/{name}/rollups")
def rollups(
    host: NamedHost,
    conn: Conn,
    signal: str,
    entity: str = "host",
    since: datetime | None = None,
    until: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=100_000)] = 10_000,
) -> list[dict]:
    """Minute aggregates; `sum / n` is the mean of the samples present."""
    since, until = window(since, until, timedelta(days=1))
    rows = telemetry.rollups_of(conn, host, signal, entity, since, until, limit)
    return [{"ts": ts, "min": lo, "max": hi, "sum": total, "n": n} for ts, lo, hi, total, n in rows]


@router.get("/hosts/{name}/events")
def events(
    host: NamedHost,
    conn: Conn,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=10_000)] = 1_000,
) -> list[telemetry.EventRow]:
    since, until = window(since, until, timedelta(days=1))
    return telemetry.events_of(conn, host, since, until, limit)


@router.get("/hosts/{name}/processes")
def processes(host: NamedHost, conn: Conn, at: datetime | None = None) -> list[telemetry.ProcessRow]:
    """The process snapshot nearest before `at` (default: now)."""
    return telemetry.processes_at(conn, host, at or datetime.now(UTC))


@router.get("/hosts/{name}/facts")
def facts(host: NamedHost, conn: Conn) -> list[telemetry.FactRow]:
    return telemetry.latest_facts(conn, host)


# Reads for the web interface (slice S7).

MAX_DIAGNOSIS = timedelta(hours=24)
MAX_AUDIT = timedelta(days=7)


@router.get("/hosts/{name}/overview")
def host_overview(host: NamedHost, conn: Conn) -> overview.Overview:
    return overview.of(conn, host, datetime.now(UTC))


@router.get("/hosts/{name}/chart")
def chart(
    host: NamedHost,
    conn: Conn,
    request: Request,
    series: Annotated[list[str], Query(description="signal@entity; entity defaults to host")],
    since: datetime | None = None,
    until: datetime | None = None,
    points: Annotated[int, Query(ge=2, le=2000)] = 600,
) -> dict:
    """Series bucketed to at most `points` points: [ms, mean, min, max]."""
    if len(series) > charts.MAX_SERIES:
        raise HTTPException(422, f"at most {charts.MAX_SERIES} series")
    since, until = window(since, until, timedelta(hours=1))
    if until <= since:
        raise HTTPException(422, "until must be after since")
    keys = [(s.partition("@")[0], s.partition("@")[2] or "host") for s in series]
    step = request.app.state.params.step_s
    bucket = charts.bucket_for(since, until, points, step)
    raw_since = datetime.now(UTC) - timedelta(days=maintenance.RAW_DAYS)
    found, bucket = charts.bucketed(conn, host, keys, since, until, bucket, step, raw_since)
    return {"since": since, "until": until, "bucket_s": bucket, "series": found}


@router.get("/hosts/{name}/norm")
def norm(
    host: NamedHost,
    conn: Conn,
    request: Request,
    signal: str,
    entity: str = "host",
    since: datetime | None = None,
    until: datetime | None = None,
) -> charts.Norm:
    """The band the robust detector judges the series normal in, for each
    workload class and power profile of the interval, and its fixed limits."""
    since, until = window(since, until, timedelta(hours=1))
    found = request.app.state.norms.of(conn, host, signal, entity, since, until, datetime.now(UTC))
    if found is None:
        raise HTTPException(404, f"no series {signal} of {entity}")
    return found


@router.get("/hosts/{name}/context")
def context(
    host: NamedHost, conn: Conn, since: datetime | None = None, until: datetime | None = None
) -> list[charts.Span]:
    """Workload class and power profile over the interval."""
    since, until = window(since, until, timedelta(hours=1))
    return charts.context_spans(conn, host, since, until)


@router.get("/hosts/{name}/anomalies")
def list_anomalies(
    host: NamedHost,
    conn: Conn,
    since: datetime | None = None,
    until: datetime | None = None,
    open: bool = False,
    kind: str | None = None,
    detector: str | None = None,
    signal: str | None = None,
    entity: str | None = None,
    replay: int | None = None,
    limit: Annotated[int, Query(ge=1, le=5_000)] = 500,
) -> list[anomalies.RecordRow]:
    """Records that overlap the interval, newest first."""
    since, until = window(since, until, timedelta(days=1))
    return anomalies.records_of(conn, host, since, replay, open, detector, limit, until, kind, signal, entity)


@router.get("/hosts/{name}/anomalies/{record_id}")
def one_anomaly(host: NamedHost, conn: Conn, record_id: int) -> anomalies.RecordRow:
    found = anomalies.record(conn, host, record_id)
    if found is None:
        raise HTTPException(404, f"no record {record_id}")
    return found


@router.get("/hosts/{name}/runs")
def runs(host: NamedHost, conn: Conn) -> list[dict]:
    """Labeled runs (calibration and scenarios) with their phases."""
    out: dict[str, dict] = {}
    for ph in labels.phases_of(conn, host):
        run = out.setdefault(ph.run, {"run": ph.run, "kind": ph.kind, "phases": []})
        run["phases"].append(
            {
                "phase": ph.phase,
                "since": ph.since,
                "until": ph.until,
                "normal": ph.normal,
                "expected": ph.expected,
                "scope": ph.scope,
            }
        )
    return sorted(out.values(), key=lambda r: r["phases"][0]["since"], reverse=True)


@router.get("/hosts/{name}/diagnosis")
def diagnosis(
    host: NamedHost,
    conn: Conn,
    request: Request,
    since: datetime | None = None,
    until: datetime | None = None,
    run: str | None = None,
    replay: int | None = None,
) -> list[dict]:
    """Ranked hypotheses of an interval, or of each phase of a labeled run
    that is not normal, as `gungnir-server diagnose` gives them; each
    hypothesis lists the actions the policy holds for its cause."""
    state = request.app.state
    if run:
        phases = [ph for ph in labels.phases_of(conn, host, run=run) if not ph.normal]
        if not phases:
            raise HTTPException(404, f"run '{run}' has no phase that is not normal")
        spans = [(ph.phase, ph.since, ph.until + state.params.label_tail) for ph in phases]
    else:
        since, until = window(since, until, timedelta(minutes=30))
        if until <= since or until - since > MAX_DIAGNOSIS:
            raise HTTPException(422, "the interval must be positive and at most 24 hours")
        spans = [("", since, until)]
    out = []
    for label, a, b in spans:
        evidence = gather.evidence(conn, host, state.base, a, b, replay)
        found = as_dict(state.engine.diagnose(evidence))
        # What the inference engine was given, so that an empty answer can be told
        # from an interval without evidence.
        found["evidence"] = {
            "records": sum(1 for r in evidence.records if inside(r, evidence)),
            "events": sum(1 for e in evidence.events if inside(e, evidence)),
            "processes": sum(1 for p in evidence.processes if inside(p, evidence)),
        }
        for h in found["hypotheses"] + found["near"]:
            h["actions"] = [
                {
                    "action": e.action,
                    "when": e.when,
                    "signal": e.signal,
                    "share": e.share,
                    "effectiveness": e.effectiveness,
                    "source": e.source,
                }
                for e in state.base.policy.for_cause(h["cause"])
            ]
        out.append({"label": label, "since": a, "until": b, **found})
    return out


@router.get("/hosts/{name}/audit")
def audit_of(
    host: NamedHost,
    conn: Conn,
    request: Request,
    since: datetime | None = None,
    until: datetime | None = None,
) -> dict:
    """What was unusual over an interval, what it was and who did it
    (ADRs 0056, 0057). `data_since` is the first snapshot of the host in the
    interval: a host switched on ten minutes ago has ten minutes to show."""
    state = request.app.state
    since, until = window(since, until, timedelta(hours=1))
    if until <= since or until - since > MAX_AUDIT:
        raise HTTPException(422, "the interval must be positive and at most 7 days")
    evidence = gather.evidence(conn, host, state.base, since, until)
    found = audit.audit(conn, state.base, evidence, state.engine.diagnose(evidence))
    (first,) = conn.execute(
        "SELECT min(ts) FROM process_samples WHERE host_id = %s AND ts >= %s AND ts <= %s",
        (host.id, since, until),
    ).fetchone()
    return {"since": since, "until": until, "data_since": first, **found}


@router.get("/knowledge")
def knowledge_base(request: Request, host: str | None = None) -> dict:
    """The whole knowledge base, with labels and provenance."""
    state = request.app.state
    if host:
        return catalog.document(state.base, host)
    if state.catalog is None:
        state.catalog = catalog.document(state.base)
    return state.catalog
