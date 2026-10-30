"""Evidence of a host over an interval, read from the database (slice S5):
records, the values detection attached to them, events of the types the
ontology knows, process signs in the snapshots, context facts and the
workload class of each group.

The interval reaches back by the longest negative window of the rules, so
that a manifestation may precede the anchor it is counted from.
"""

from datetime import datetime, timedelta

import psycopg

from gungnir_core import telemetry, workload
from gungnir_core.base import Base
from gungnir_core.evidence import HOST, Event, Evidence, Record, Sign, Value
from gungnir_core.hosts import Host


def lookback(base: Base) -> timedelta:
    earliest = min(
        (m.window[0] for r in base.rules.values() for m in r.manifestations if m.window),
        default=0,
    )
    return timedelta(seconds=max(0, -earliest))


def evidence(
    conn: psycopg.Connection,
    host: Host,
    base: Base,
    since: datetime,
    until: datetime,
    replay_id: int | None = None,
) -> Evidence:
    onto = base.ontology
    early = since - lookback(base)
    records, values = _records(conn, host, early, until, replay_id, set(onto.signals))
    events = _events(conn, host, base, early, until)
    signs, snapshots = _signs(conn, host, base, early, until)

    facts = dict(
        conn.execute(
            "SELECT DISTINCT ON (name) name, value FROM facts WHERE host_id = %s AND ts <= %s"
            " ORDER BY name, ts DESC",
            (host.id, until),
        ).fetchall()
    )
    rules = workload.load_rules(host.name)
    groups = {r.entity for r in records if r.entity != HOST} | {s.group for s in signs if s.group}
    classes = {g: cls for g in groups if (cls := _class_of(rules, g))}

    seen = {
        row[0] for row in conn.execute("SELECT DISTINCT signal FROM series WHERE host_id = %s", (host.id,))
    }
    unobservable = set(onto.signals) - seen
    states = {s.module: s.state for s in telemetry.source_states(conn, host)}
    if states.get("journal") in (None, "disabled"):
        unobservable |= set(onto.events)
    if not snapshots:
        unobservable |= set(onto.signs)

    return Evidence(
        tuple(records),
        tuple(values),
        tuple(events),
        tuple(signs),
        {name: value for name, value in facts.items() if name in onto.facts},
        classes,
        since.timestamp(),
        until.timestamp(),
        frozenset(unobservable),
    )


def _records(conn, host, early, until, replay_id, known) -> tuple[list[Record], list[Value]]:
    rows = conn.execute(
        "SELECT a.id, s.signal, s.entity, a.kind, a.detector, a.direction, a.started_at, a.ended_at,"
        " a.level, a.peak_score, a.workload_class, a.context FROM anomalies a"
        " JOIN series s ON s.id = a.series_id"
        " WHERE a.host_id = %(h)s AND a.replay_id IS NOT DISTINCT FROM %(r)s"
        " AND a.started_at <= %(until)s AND (a.ended_at IS NULL OR a.ended_at >= %(early)s)"
        " ORDER BY a.started_at, a.id",
        {"h": host.id, "r": replay_id, "until": until, "early": early},
    ).fetchall()
    records, values = [], []
    for rid, signal, entity, kind, detector, direction, start, end, level, score, cls, context in rows:
        if signal not in known:
            continue
        record = Record(
            str(rid),
            signal,
            entity,
            kind,
            detector,
            direction,
            start.timestamp(),
            end.timestamp() if end else None,
            level,
            score,
            cls,
        )
        records.append(record)
        for name, value in (context or {}).items():
            if name in known:
                values.append(Value(name, entity, value, record.start, record.id))
    return records, values


def _events(conn, host, base: Base, early, until) -> list[Event]:
    rows = conn.execute(
        "SELECT id, ts, source, message_id, message FROM events"
        " WHERE host_id = %s AND ts >= %s AND ts <= %s ORDER BY ts, id",
        (host.id, early, until),
    ).fetchall()
    return [
        Event(str(eid), kind.name, ts.timestamp(), message)
        for eid, ts, source, message_id, message in rows
        for kind in base.ontology.events.values()
        if kind.matches(source, message_id, message)
    ]


def _signs(conn, host, base: Base, early, until) -> tuple[list[Sign], int]:
    """One sign for each stretch of snapshots a process shows it in, at the
    first snapshot of the stretch; also how many snapshots there were."""
    rows = conn.execute(
        "SELECT ts, pid, comm, state, cgroup FROM process_samples"
        " WHERE host_id = %s AND ts >= %s AND ts <= %s ORDER BY ts",
        (host.id, early, until),
    ).fetchall()
    signs: list[Sign] = []
    previous: set[tuple[str, int]] = set()
    current: set[tuple[str, int]] = set()
    last_ts = None
    snapshots = 0
    for ts, pid, comm, state, cgroup in rows:
        if ts != last_ts:
            previous, current, last_ts = current, set(), ts
            snapshots += 1
        for sign in base.ontology.signs.values():
            if sign.matches(comm, state):
                key = (sign.name, pid)
                if key not in previous and key not in current:
                    signs.append(Sign(sign.name, comm, pid, state, cgroup or None, ts.timestamp()))
                current.add(key)
    return signs, snapshots


def _class_of(rules: list[workload.Rule], group: str) -> str | None:
    """The class of the first workload rule, by priority, that matches the
    group's unit or path."""
    return next((r.cls for r in rules if r.matches_group(group)), None)
