"""Replays of the detection loop over stored data, and their evaluation
against the host's labeled runs (ADR 0027)."""

import dataclasses
from datetime import datetime

import numpy as np
import psycopg
from psycopg.types.json import Jsonb

from gungnir_core import frame
from gungnir_core.detection import evaluation
from gungnir_core.detection.runner import VARIANTS, Runner
from gungnir_core.hosts import Host
from gungnir_core.labels import phases_of
from gungnir_core.params import Params
from gungnir_core.telemetry import RAW_SAMPLES

# Variants compared in S3; impact is reported on its own.
COMPARED = ("thresholds", "robust-host", "robust-classes", "iforest", "robust-classes+iforest")


def stored_span(conn: psycopg.Connection, host: Host) -> tuple[datetime | None, datetime | None]:
    return conn.execute(
        f"SELECT min(r.ts), max(r.ts) FROM {RAW_SAMPLES} r JOIN series s ON s.id = r.series_id"
        " WHERE s.host_id = %s AND s.signal = 'cpu.idle_ratio'",
        (host.id,),
    ).fetchone()


def replay(
    conn: psycopg.Connection, host: Host, variant: str, since: datetime, until: datetime, params: Params
) -> tuple[int, int]:
    """Runs one variant over [since, until); returns the replay id and the
    number of records written."""
    (replay_id,) = conn.execute(
        "INSERT INTO replays (host_id, variant, since, until, params)"
        " VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (host.id, variant, since, until, Jsonb(_jsonable(params))),
    ).fetchone()
    detectors = VARIANTS[variant].build(conn, host, params)
    runner = Runner(conn, host, params, detectors, replay_id=replay_id, at=since)
    written = runner.run(until)
    runner.close_open(until)
    conn.commit()
    return replay_id, written


def _jsonable(params: Params) -> dict:
    fields = dataclasses.asdict(params).items()
    return {k: (v.total_seconds() if hasattr(v, "total_seconds") else v) for k, v in fields}


def score_replay(conn: psycopg.Connection, host: Host, replay_id: int, params: Params) -> evaluation.Score:
    since, until, variant = conn.execute(
        "SELECT since, until, variant FROM replays WHERE id = %s", (replay_id,)
    ).fetchone()
    kind = "impact" if variant == "impact" else "anomaly"
    rows = conn.execute(
        "SELECT a.started_at, a.ended_at, s.signal FROM anomalies a JOIN series s ON s.id = a.series_id"
        " WHERE a.replay_id = %s AND a.kind = %s",
        (replay_id, kind),
    ).fetchall()
    records = [evaluation.Rec(frame.to_ms(a), frame.to_ms(b or until), sig) for a, b, sig in rows]
    phases = [p for p in phases_of(conn, host, since, until) if p.since < until and p.until > since]
    tail = params.label_tail
    events = [
        evaluation.Event(
            frame.to_ms(p.since), frame.to_ms(p.until + tail), frozenset(p.expected), f"{p.run}/{p.phase}"
        )
        for p in phases
        if not p.normal
    ]
    quiet = [(frame.to_ms(p.since), frame.to_ms(p.until)) for p in phases if p.normal]
    ticks = np.array(
        [
            frame.to_ms(ts)
            for (ts,) in conn.execute(
                f"SELECT r.ts FROM {RAW_SAMPLES} r JOIN series s ON s.id = r.series_id"
                " WHERE s.host_id = %s AND s.signal = 'cpu.idle_ratio' AND r.ts >= %s AND r.ts < %s",
                (host.id, since, until),
            )
        ],
        dtype=np.int64,
    )
    return evaluation.score(records, events, quiet, ticks, int(params.step_s * 1000))


def latest_replays(conn: psycopg.Connection, host: Host) -> dict[str, int]:
    rows = conn.execute(
        "SELECT DISTINCT ON (variant) variant, id FROM replays WHERE host_id = %s ORDER BY variant, id DESC",
        (host.id,),
    ).fetchall()
    return dict(rows)
