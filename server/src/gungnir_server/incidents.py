"""Incidents and proposals (slice S6; ADRs 0002, 0015, 0060).

An incident opens on harm: an impact record, or a hypothesis whose impact
was matched (ADR 0056). Activity that hurt nobody stays in the audit. While
the incident is open every record of the host becomes its evidence; it
closes when no harm was seen for the quiet period. There is at most one open
incident per host, so causes spreading across resources stay in one
incident as separate hypotheses.

After each step the inference engine diagnoses the incident's interval. Each
cause and binding is one hypothesis row, updated while the incident is open
and frozen when it closes, so that a proposal and the operator's decision
refer to a stable id (ADR 0007). A harmful hypothesis with a culprit gets
the actions the policy holds for its cause, each with its parameter computed
from what the culprit consumed over the last minute. A proposal waits for
the operator; it expires when its hypothesis is no longer given or the
incident closes. Applying a confirmed one is the actuator's work (slice S8).

`advance` decides what happens to the incident from the harm it is shown and
reads no database, so the live loop and a replay can share it.
"""

import json
import logging
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta
from fnmatch import fnmatchcase

import psycopg
from psycopg.types.json import Jsonb

from gungnir_server import frame, gather
from gungnir_server.base import Base
from gungnir_server.evidence import Evidence, Record, inside
from gungnir_server.hosts import Host
from gungnir_server.inference import MATCHED, Diagnosis, Hypothesis, InferenceEngine, as_hypothesis
from gungnir_server.params import Params
from gungnir_server.policy import Entry
from gungnir_server.rules import CULPRIT_CLASS, STRENGTHS
from gungnir_server.telemetry import RAW_SAMPLES

log = logging.getLogger(__name__)

# The longest interval one diagnosis of an incident reads.
MAX_SPAN = timedelta(hours=24)


@dataclass(frozen=True)
class Harm:
    """An interval in which someone was hurt; end is None while it lasts."""

    start: float
    end: float | None


@dataclass(frozen=True)
class Current:
    opened: float
    last_harm: float


@dataclass(frozen=True)
class Step:
    """What became of the incident: still nothing, open (maybe new), or closed."""

    incident: Current | None
    opened: bool = False
    closed_at: float | None = None


def advance(
    current: Current | None, harm: list[Harm], until: float, after: float | None, quiet: float
) -> Step:
    """Opens an incident on harm that reaches past `after` (the end of the
    previous incident's harm), extends it while harm lasts, and closes it
    when none was seen for `quiet` seconds, at the end of that quiet."""

    def reach(h: Harm) -> float:
        return until if h.end is None else h.end

    seen = [h for h in harm if h.start <= until]
    opened = False
    if current is None:
        new = [h for h in seen if after is None or reach(h) > after]
        if not new:
            return Step(None)
        current = Current(min(h.start for h in new), max(reach(h) for h in new))
        opened = True
    else:
        last = max((reach(h) for h in seen if reach(h) >= current.opened), default=current.last_harm)
        current = Current(current.opened, max(current.last_harm, last))
    if until - current.last_harm >= quiet:
        return Step(current, opened, current.last_harm + quiet)
    return Step(current, opened)


def harm_of(d: Diagnosis, ev: Evidence) -> list[Harm]:
    """Impact records of the interval and the matched impact of harmful hypotheses."""
    items = {f.item for h in d.harmful() for f in h.with_status(MATCHED) if f.symptom.impact and f.item}
    items |= {r for r in ev.records if r.kind == "impact" and inside(r, ev)}
    return [
        Harm(i.start, i.end) if isinstance(i, Record) else Harm(i.at, i.at)
        for i in sorted(items, key=lambda i: i.start if isinstance(i, Record) else i.at)
    ]


class Manager:
    """Runs incidents of a host forward to where detection stands."""

    __name__ = "incidents"

    def __init__(self, base: Base, engine: InferenceEngine) -> None:
        self.base = base
        self.engine = engine

    def __call__(self, conn: psycopg.Connection, host: Host, p: Params, now: datetime) -> None:
        row = conn.execute("SELECT at FROM detection_state WHERE host_id = %s", (host.id,)).fetchone()
        if row is None:
            return
        until = row[0]
        row = conn.execute("SELECT at FROM incident_state WHERE host_id = %s", (host.id,)).fetchone()
        at = row[0] if row else until - p.incident_step
        while at < until:
            end = min(until, at + p.incident_step)
            # Each step is its own transaction: catching up on days of
            # detection must not hold one open for minutes.
            try:
                self.step(conn, host, p, at, end)
                conn.execute(
                    "INSERT INTO incident_state (host_id, at) VALUES (%s, %s)"
                    " ON CONFLICT (host_id) DO UPDATE SET at = EXCLUDED.at",
                    (host.id, end),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            at = end

    def step(self, conn: psycopg.Connection, host: Host, p: Params, since: datetime, until: datetime) -> None:
        quiet = p.incident_quiet.total_seconds()
        found = conn.execute(
            "SELECT id, opened_at, last_harm_at FROM incidents"
            " WHERE host_id = %s AND closed_at IS NULL AND replay_id IS NULL",
            (host.id,),
        ).fetchone()
        current = (found[0], Current(found[1].timestamp(), found[2].timestamp())) if found else None
        if current is None:
            ev = gather.evidence(conn, host, self.base, since, until)
            harm = harm_of(self.engine.diagnose(ev), ev)
            step = advance(None, harm, until.timestamp(), self._last_harm(conn, host), quiet)
            if step.incident is None:
                return
            current = (self._open(conn, host, step.incident, ev), step.incident)
        while current is not None:
            incident_id, state = current
            opened = frame.from_ms(round(state.opened * 1000))
            if until <= opened:
                # A step before the open incident began: nothing of it to judge.
                return
            ev = gather.evidence(conn, host, self.base, max(opened, until - MAX_SPAN), until)
            d = self.engine.diagnose(ev)
            harm = harm_of(d, ev)
            step = advance(state, harm, until.timestamp(), None, quiet)
            assert step.incident is not None
            seen_until = until if step.closed_at is None else _at(step.closed_at)
            conn.execute(
                "UPDATE incidents SET last_harm_at = %s WHERE id = %s",
                (_at(step.incident.last_harm), incident_id),
            )
            self._hypotheses(conn, incident_id, d, seen_until)
            self._proposals(conn, host, p, incident_id, d, ev, seen_until)
            conn.execute(
                "UPDATE anomalies SET incident_id = %s WHERE host_id = %s AND replay_id IS NULL"
                " AND incident_id IS NULL AND started_at <= %s AND (ended_at IS NULL OR ended_at >= %s)",
                (incident_id, host.id, seen_until, opened),
            )
            if step.closed_at is None:
                return
            conn.execute("UPDATE incidents SET closed_at = %s WHERE id = %s", (seen_until, incident_id))
            conn.execute(
                "UPDATE proposals SET state = 'expired' WHERE incident_id = %s AND state = 'proposed'",
                (incident_id,),
            )
            log.info("incident %d of %s closed", incident_id, host.name)
            # Harm after the quiet period opens the next incident in the same step.
            later = [h for h in harm if h.start > step.closed_at]
            nxt = advance(None, later, until.timestamp(), step.incident.last_harm, quiet)
            current = (self._open(conn, host, nxt.incident, ev), nxt.incident) if nxt.incident else None

    @staticmethod
    def _last_harm(conn: psycopg.Connection, host: Host) -> float | None:
        row = conn.execute(
            "SELECT max(last_harm_at) FROM incidents WHERE host_id = %s AND replay_id IS NULL", (host.id,)
        ).fetchone()
        return row[0].timestamp() if row and row[0] else None

    @staticmethod
    def _open(conn: psycopg.Connection, host: Host, state: Current, ev: Evidence) -> int:
        opened = _at(state.opened)
        cls = conn.execute(
            "SELECT value FROM facts WHERE host_id = %s AND name = 'workload.class' AND entity = 'host'"
            " AND ts <= %s ORDER BY ts DESC LIMIT 1",
            (host.id, opened),
        ).fetchone()
        (incident_id,) = conn.execute(
            "INSERT INTO incidents (host_id, opened_at, last_harm_at, workload_class)"
            " VALUES (%s, %s, %s, %s) RETURNING id",
            (host.id, opened, _at(state.last_harm), cls[0] if cls else None),
        ).fetchone()
        log.info("incident %d of %s opened at %s", incident_id, host.name, opened)
        return incident_id

    # Hypotheses

    def _hypotheses(self, conn: psycopg.Connection, incident_id: int, d: Diagnosis, at: datetime) -> None:
        """One row per cause and binding, the best of its bursts; rows the
        diagnosis no longer gives lose their rank."""
        best: dict[tuple, Hypothesis] = {}
        for h in d.hypotheses:
            best.setdefault(_key(h), h)
        conn.execute("UPDATE hypotheses SET rank = NULL WHERE incident_id = %s", (incident_id,))
        for rank, (key, h) in enumerate(best.items(), start=1):
            conn.execute(
                "INSERT INTO hypotheses (incident_id, cause, culprit, device, filesystem, first_seen,"
                " last_seen, confidence, peak_confidence, harm, expected, rank, tree)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (incident_id, cause, culprit, device, filesystem) DO UPDATE SET"
                " last_seen = EXCLUDED.last_seen, confidence = EXCLUDED.confidence,"
                " peak_confidence = greatest(hypotheses.peak_confidence, EXCLUDED.confidence),"
                " harm = hypotheses.harm OR EXCLUDED.harm, expected = EXCLUDED.expected,"
                " rank = EXCLUDED.rank, tree = EXCLUDED.tree",
                (
                    incident_id,
                    *key,
                    at,
                    at,
                    h.confidence,
                    h.confidence,
                    h.harm,
                    h.expected,
                    rank,
                    Jsonb(json.loads(json.dumps(as_hypothesis(h), default=str))),
                ),
            )

    # Proposals

    def _proposals(
        self,
        conn: psycopg.Connection,
        host: Host,
        p: Params,
        incident_id: int,
        d: Diagnosis,
        ev: Evidence,
        at: datetime,
    ) -> None:
        ids = dict(
            ((cause, culprit, device, fs), (hid, rank))
            for hid, cause, culprit, device, fs, rank in conn.execute(
                "SELECT id, cause, culprit, device, filesystem, rank FROM hypotheses WHERE incident_id = %s",
                (incident_id,),
            ).fetchall()
        )
        conn.execute(
            "UPDATE proposals SET state = 'expired' WHERE incident_id = %s AND state = 'proposed'"
            " AND hypothesis_id IN (SELECT id FROM hypotheses WHERE incident_id = %s AND rank IS NULL)",
            (incident_id, incident_id),
        )
        seen: set[tuple] = set()
        for h in d.harmful():
            key = _key(h)
            if key in seen or not h.binding.culprit or key not in ids:
                continue
            seen.add(key)
            hypothesis_id, rank = ids[key]
            if self._protected(conn, h):
                continue
            activity = latest_activity(h, at)
            for e in self.base.policy.for_cause(h.cause):
                if not applies(e, h, ev):
                    continue
                current = consumption(conn, host, p, h.binding.culprit, e.signal, activity)
                parameter = {
                    "signal": e.signal,
                    "share": e.share,
                    "current": current,
                    "value": None if current is None else current * e.share,
                }
                risk = _risk(e)
                order = (
                    rank * 100
                    + STRENGTHS.index(e.effectiveness) * 10
                    + (3 - STRENGTHS.index(risk) if risk else 0)
                )
                conn.execute(
                    "INSERT INTO proposals (incident_id, hypothesis_id, action, target, parameter,"
                    " effectiveness, risk, side_effects, rollback_after, source, rank, created_at)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
                    " ON CONFLICT (incident_id, target, action) DO NOTHING",
                    (
                        incident_id,
                        hypothesis_id,
                        e.action,
                        h.binding.culprit,
                        Jsonb(parameter),
                        e.effectiveness,
                        risk,
                        Jsonb([{"effect": s.effect, "likelihood": s.likelihood} for s in e.side_effects]),
                        p.rollback_after,
                        e.source,
                        order,
                        at,
                    ),
                )

    def _protected(self, conn: psycopg.Connection, h: Hypothesis) -> bool:
        """The culprit's own unit, or the command that used the most of its
        records, is one the ontology protects (ADR 0035). A slice is never a
        target: it holds every service below it, protected ones among them
        (ADR 0060)."""
        unit = (h.binding.culprit or "").rsplit("/", 1)[-1]
        if unit.endswith(".slice"):
            return True
        ids = [
            int(f.item.id)
            for f in h.with_status(MATCHED)
            if f.symptom.role == "culprit" and isinstance(f.item, Record) and f.item.id.isdigit()
        ]
        leaders = set()
        if ids:
            for (procs,) in conn.execute(
                "SELECT processes FROM anomalies WHERE id = ANY(%s)", (ids,)
            ).fetchall():
                for c in (procs or [])[:1]:
                    leaders.add(c.get("leader") or c["names"][0][0])
        for entity in self.base.ontology.protected.values():
            if any(fnmatchcase(unit, u) for u in entity.units):
                return True
            if any(fnmatchcase(n, c) for n in leaders for c in entity.commands):
                return True
        return False


def applies(e: Entry, h: Hypothesis, ev: Evidence) -> bool:
    """The entry's alternative matched on the culprit, and its conditions hold."""
    if e.when is not None:
        matched = {
            f.item.signal
            for f in h.with_status(MATCHED)
            if f.symptom.role == "culprit" and isinstance(f.item, Record)
        }
        if e.when not in matched:
            return False

    def value(name: str) -> str | None:
        if name == CULPRIT_CLASS:
            return ev.group_class.get(h.binding.culprit) if h.binding.culprit else None
        return ev.facts.get(name)

    if any(value(name) not in allowed for name, allowed in e.requires.items()):
        return False
    return not any(value(name) in banned for name, banned in e.excludes.items())


def latest_activity(h: Hypothesis, at: datetime) -> tuple[datetime, datetime] | None:
    """The interval of the culprit's latest matched record, until `at` while it lasts."""
    records = [
        f.item for f in h.with_status(MATCHED) if f.symptom.role == "culprit" and isinstance(f.item, Record)
    ]
    if not records:
        return None
    r = max(records, key=lambda r: r.start)
    end = at.timestamp() if r.end is None else min(r.end, at.timestamp())
    return _at(r.start), _at(max(r.start, end))


def consumption(
    conn: psycopg.Connection,
    host: Host,
    p: Params,
    group: str,
    signal: str | None,
    activity: tuple[datetime, datetime] | None,
) -> float | None:
    """What the group consumed of `signal` while it was active (ADR 0035):
    the median of its samples over the last `proposal_basis` of its latest
    matched record. None when it used none of it there: a limit of zero
    would stop the program, so the actuator reads the value before applying
    instead (slice S8)."""
    if signal is None or activity is None:
        return None
    start, end = activity
    since = max(start, end - p.proposal_basis)
    values = [
        v
        for (v,) in conn.execute(
            f"SELECT r.value FROM {RAW_SAMPLES} r JOIN series s ON s.id = r.series_id"
            " WHERE s.host_id = %s AND s.signal = %s AND s.entity = %s AND r.ts >= %s AND r.ts <= %s",
            (host.id, signal, group, since, end),
        ).fetchall()
        if v > 0
    ]
    return float(statistics.median(values)) if values else None


def _risk(e: Entry) -> str | None:
    """The likelihood of the most likely side effect."""
    found = [s.likelihood for s in e.side_effects]
    return min(found, key=STRENGTHS.index) if found else None


def _key(h: Hypothesis) -> tuple[str, str, str, str]:
    b = h.binding
    return (h.cause, b.culprit or "", b.device or "", b.filesystem or "")


def _at(seconds: float) -> datetime:
    return frame.from_ms(round(seconds * 1000))


# Reading and deciding, for the API


@dataclass(frozen=True)
class ProposalRow:
    id: int
    hypothesis_id: int
    action: str
    target: str
    parameter: dict
    effectiveness: str
    risk: str | None
    side_effects: list
    rollback_after_s: float
    source: str
    rank: int
    state: str
    created_at: datetime
    decided_at: datetime | None
    decided_by: str | None


@dataclass(frozen=True)
class HypothesisRow:
    id: int
    cause: str
    culprit: str | None
    device: str | None
    filesystem: str | None
    first_seen: datetime
    last_seen: datetime
    confidence: float
    peak_confidence: float
    harm: bool
    expected: bool
    rank: int | None
    tree: dict


@dataclass(frozen=True)
class IncidentSummary:
    id: int
    opened_at: datetime
    last_harm_at: datetime
    closed_at: datetime | None
    workload_class: str | None
    records: int
    # The best harmful hypothesis, if any.
    cause: str | None
    culprit: str | None
    confidence: float | None
    hypotheses: int
    waiting: int


@dataclass(frozen=True)
class Incident:
    summary: IncidentSummary
    hypotheses: list[HypothesisRow]
    proposals: list[ProposalRow]


SUMMARY = (
    "SELECT i.id, i.opened_at, i.last_harm_at, i.closed_at, i.workload_class,"
    " (SELECT count(*) FROM anomalies a WHERE a.incident_id = i.id),"
    " b.cause, nullif(b.culprit, ''), b.peak_confidence,"
    " (SELECT count(*) FROM hypotheses h WHERE h.incident_id = i.id),"
    " (SELECT count(*) FROM proposals p WHERE p.incident_id = i.id AND p.state = 'proposed')"
    " FROM incidents i LEFT JOIN LATERAL (SELECT cause, culprit, peak_confidence FROM hypotheses h"
    "  WHERE h.incident_id = i.id AND h.harm ORDER BY h.peak_confidence DESC LIMIT 1) b ON true"
)


def summaries(
    conn: psycopg.Connection, host: Host, since: datetime, until: datetime, limit: int = 200
) -> list[IncidentSummary]:
    """Live incidents that overlap [since, until), newest first."""
    rows = conn.execute(
        SUMMARY + " WHERE i.host_id = %s AND i.replay_id IS NULL AND i.opened_at < %s"
        " AND (i.closed_at IS NULL OR i.closed_at >= %s) ORDER BY i.opened_at DESC LIMIT %s",
        (host.id, until, since, limit),
    ).fetchall()
    return [IncidentSummary(*row) for row in rows]


def open_summary(conn: psycopg.Connection, host: Host) -> IncidentSummary | None:
    row = conn.execute(
        SUMMARY + " WHERE i.host_id = %s AND i.replay_id IS NULL AND i.closed_at IS NULL", (host.id,)
    ).fetchone()
    return IncidentSummary(*row) if row else None


def one(conn: psycopg.Connection, host: Host, incident_id: int) -> Incident | None:
    row = conn.execute(SUMMARY + " WHERE i.host_id = %s AND i.id = %s", (host.id, incident_id)).fetchone()
    if row is None:
        return None
    hyps = conn.execute(
        "SELECT id, cause, nullif(culprit, ''), nullif(device, ''), nullif(filesystem, ''), first_seen,"
        " last_seen, confidence, peak_confidence, harm, expected, rank, tree FROM hypotheses"
        " WHERE incident_id = %s ORDER BY harm DESC, rank NULLS LAST, peak_confidence DESC",
        (incident_id,),
    ).fetchall()
    props = conn.execute(
        "SELECT id, hypothesis_id, action, target, parameter, effectiveness, risk, side_effects,"
        " extract(epoch FROM rollback_after)::float, source, rank, state, created_at, decided_at, decided_by"
        " FROM proposals WHERE incident_id = %s ORDER BY rank, id",
        (incident_id,),
    ).fetchall()
    return Incident(
        IncidentSummary(*row), [HypothesisRow(*h) for h in hyps], [ProposalRow(*p) for p in props]
    )


class Refused(Exception):
    pass


DECISIONS = {"confirm": "confirmed", "reject": "rejected"}


def decide(
    conn: psycopg.Connection,
    host: Host,
    incident_id: int,
    proposal_id: int,
    decision: str,
    by: str,
    now: datetime,
) -> ProposalRow:
    """Records the operator's decision on a proposal still waiting for one.
    Confirming records it only; the actuator applies it from slice S8."""
    if decision not in DECISIONS:
        raise Refused(f"decision must be one of {', '.join(DECISIONS)}")
    row = conn.execute(
        "UPDATE proposals p SET state = %s, decided_at = %s, decided_by = %s FROM incidents i"
        " WHERE p.id = %s AND p.incident_id = %s AND i.id = p.incident_id AND i.host_id = %s"
        " AND p.state = 'proposed' RETURNING p.id",
        (DECISIONS[decision], now, by, proposal_id, incident_id, host.id),
    ).fetchone()
    if row is None:
        raise Refused("no proposal waiting for a decision")
    found = one(conn, host, incident_id)
    assert found is not None
    return next(p for p in found.proposals if p.id == proposal_id)
