"""Host capacity from the calibration run (ADR 0024).

The calibration script only creates load; capacity is read from the
samples the agent sent during each phase, the same signals detection reads
later. For each signal the phases that should saturate it are listed; the
capacity is a high quantile of its samples there, after a warm-up, taken on
the entity that reached the most (the disk the load ran on).
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

import numpy as np
import psycopg

from gungnir_core.hosts import Host
from gungnir_core.labels import phases_of
from gungnir_core.params import Params
from gungnir_core.telemetry import RAW_SAMPLES

# Signal -> calibration phases that drive it to its limit. Latency has no
# such limit: a saturated sequential read answers faster than one stray read
# that wakes an idle disk, so await stays out and waits for a baseline.
SATURATING = {
    "cpu.user_ratio": ("cpu-100",),
    "disk.write_bytes_per_s": ("disk-seqwrite",),
    "disk.read_bytes_per_s": ("disk-seqread",),
    "disk.write_ops_per_s": ("disk-randwrite",),
    "disk.read_ops_per_s": ("disk-randread",),
}


@dataclass(frozen=True)
class Capacity:
    signal: str
    entity: str
    value: float
    phase: str


def derive(samples: dict[tuple[str, str, str], list[float]], quantile: float) -> list[Capacity]:
    """Capacities from samples keyed by (signal, entity, phase)."""
    best: dict[str, Capacity] = {}
    for (signal, entity, phase), values in samples.items():
        if phase not in SATURATING.get(signal, ()) or not values:
            continue
        value = float(np.quantile(values, quantile))
        if value > 0 and (signal not in best or value > best[signal].value):
            best[signal] = Capacity(signal, entity, value, phase)
    return sorted(best.values(), key=lambda c: c.signal)


def calibrate(conn: psycopg.Connection, host: Host, run: str, params: Params) -> list[Capacity]:
    phases = [p for p in phases_of(conn, host, run=run) if p.kind == "calibration"]
    if not phases:
        raise LookupError(f"no calibration run '{run}' for host '{host.name}'; import its labels first")
    samples: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    for p in phases:
        signals = [s for s, wanted in SATURATING.items() if p.phase in wanted]
        if not signals:
            continue
        rows = conn.execute(
            f"SELECT s.signal, s.entity, r.value FROM {RAW_SAMPLES} r JOIN series s ON s.id = r.series_id"
            " WHERE s.host_id = %s AND s.signal = ANY(%s) AND r.ts >= %s AND r.ts < %s",
            (host.id, signals, p.since + params.capacity_warmup, p.until),
        ).fetchall()
        for signal, entity, value in rows:
            samples[(signal, entity, p.phase)].append(value)
    found = derive(samples, params.capacity_quantile)
    # Known from the end of the run, so that a replay sees it only from then.
    measured_at = max(p.until for p in phases)
    with conn.transaction():
        conn.execute("DELETE FROM host_capacity WHERE host_id = %s", (host.id,))
        for c in found:
            conn.execute(
                "INSERT INTO host_capacity (host_id, signal, entity, value, run, phase, measured_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (host.id, c.signal, c.entity, c.value, run, c.phase, measured_at),
            )
    return found


def capacities(
    conn: psycopg.Connection, host: Host, at: datetime | None = None
) -> dict[tuple[str, str], float]:
    """Capacity by (signal, entity); with `at`, only what was measured by then."""
    rows = conn.execute(
        "SELECT signal, entity, value FROM host_capacity WHERE host_id = %s"
        " AND (%s::timestamptz IS NULL OR measured_at <= %s)",
        (host.id, at, at),
    ).fetchall()
    return {(signal, entity): value for signal, entity, value in rows}
