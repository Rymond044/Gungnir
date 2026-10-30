"""HTTP API: ingest for agents, read endpoints for the interface and for checks."""

from collections.abc import Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Annotated

import psycopg
from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from psycopg_pool import ConnectionPool

from gungnir_core import hosts, maintenance, pipeline, telemetry
from gungnir_core.db import migrate, open_pool
from gungnir_core.hosts import Host
from gungnir_core.ingest import Batch

# A full batch of 10 000 samples is about 1 MB of JSON; 5 000 process rows
# add about 1.5 MB.
MAX_BODY = 4 * 1024 * 1024


def create_app(pool: ConnectionPool | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
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

    app = FastAPI(title="gungnIR core", version="0.1.0", lifespan=lifespan)

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


@router.get("/hosts/{name}/sources")
def sources(host: NamedHost, conn: Conn) -> list[telemetry.SourceState]:
    return telemetry.source_states(conn, host)


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
