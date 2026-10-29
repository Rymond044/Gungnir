"""Labeled runs: phases of known loads or injected faults, kept as ground truth.

Scenario scripts (`calibrate.sh`, `overhead.sh`, the fault scenarios) write
one JSON object per phase to `scenarios/runs/<time>-<kind>.jsonl`:

    {"run": "20261029T120000Z-calibration", "kind": "calibration",
     "phase": "cpu-50", "start_ms": ..., "end_ms": ..., "normal": false,
     "expected": ["cpu.user_ratio"], "scope": "gungnir-cal-cpu-50",
     "command": "stress-ng --cpu 9 --timeout 60s"}

Import stores the phases and keeps the raw samples of the whole run past
retention. Phases not marked normal stay out of baselines and training, with
a tail after each phase for the load to fade.
"""

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Annotated

import psycopg
from pydantic import BaseModel, ConfigDict, Field, model_validator

from gungnir_core.hosts import Host
from gungnir_core.ingest import Ms, SignalName
from gungnir_core.telemetry import from_ms

Label = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$", max_length=128)]


class PhaseLabel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run: Label
    kind: Label
    phase: Label
    start_ms: Ms
    end_ms: Ms
    normal: bool = False
    expected: list[SignalName] = []
    scope: Annotated[str | None, Field(max_length=255)] = None
    command: Annotated[str | None, Field(max_length=2000)] = None

    @model_validator(mode="after")
    def ordered(self):
        if self.end_ms <= self.start_ms:
            raise ValueError("end_ms must be after start_ms")
        return self


def parse(lines: Iterable[str]) -> list[PhaseLabel]:
    phases = []
    for n, line in enumerate(lines, 1):
        if line.strip():
            try:
                phases.append(PhaseLabel.model_validate(json.loads(line)))
            except ValueError as err:
                raise ValueError(f"line {n}: {err}") from err
    return phases


def import_phases(conn: psycopg.Connection, host: Host, phases: list[PhaseLabel], tail: timedelta) -> int:
    with conn.transaction():
        for p in phases:
            conn.execute(
                "INSERT INTO labeled_runs (host_id, run, kind, phase, since, until, normal, expected,"
                " scope, command) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (host_id, run, phase) DO UPDATE SET kind = EXCLUDED.kind,"
                " since = EXCLUDED.since, until = EXCLUDED.until, normal = EXCLUDED.normal,"
                " expected = EXCLUDED.expected, scope = EXCLUDED.scope, command = EXCLUDED.command",
                (
                    host.id,
                    p.run,
                    p.kind,
                    p.phase,
                    from_ms(p.start_ms),
                    from_ms(p.end_ms),
                    p.normal,
                    p.expected,
                    p.scope,
                    p.command,
                ),
            )
        for run in sorted({p.run for p in phases}):
            reason = f"labeled run {run}"
            conn.execute("DELETE FROM kept_ranges WHERE host_id = %s AND reason = %s", (host.id, reason))
            conn.execute(
                "INSERT INTO kept_ranges (host_id, since, until, reason)"
                " SELECT host_id, min(since), max(until) + %s, %s FROM labeled_runs"
                " WHERE host_id = %s AND run = %s GROUP BY host_id",
                (tail, reason, host.id, run),
            )
    return len(phases)


@dataclass(frozen=True)
class Phase:
    run: str
    kind: str
    phase: str
    since: datetime
    until: datetime
    normal: bool
    expected: list[str]
    scope: str | None


def phases_of(
    conn: psycopg.Connection,
    host: Host,
    since: datetime | None = None,
    until: datetime | None = None,
    run: str | None = None,
) -> list[Phase]:
    rows = conn.execute(
        "SELECT run, kind, phase, since, until, normal, expected, scope FROM labeled_runs"
        " WHERE host_id = %(h)s AND (%(run)s::text IS NULL OR run = %(run)s)"
        " AND (%(until)s::timestamptz IS NULL OR since < %(until)s)"
        " AND (%(since)s::timestamptz IS NULL OR until > %(since)s - interval '1 hour')"
        " ORDER BY since",
        {"h": host.id, "run": run, "since": since, "until": until},
    ).fetchall()
    return [Phase(*row) for row in rows]


def excluded(phases: list[Phase], tail: timedelta) -> list[tuple[datetime, datetime]]:
    """Intervals kept out of baselines and training: phases not marked
    normal, each extended by the tail."""
    return [(p.since, p.until + tail) for p in phases if not p.normal]


def runs_of(conn: psycopg.Connection, host: Host) -> list[tuple]:
    return conn.execute(
        "SELECT run, kind, count(*), min(since), max(until) FROM labeled_runs WHERE host_id = %s"
        " GROUP BY run, kind ORDER BY min(since)",
        (host.id,),
    ).fetchall()
