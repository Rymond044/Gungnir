"""Actions: a confirmed proposal carried out by the host's actuator, its
verification and its rollback (slice S8; ADRs 0007, 0061-0063).

A confirmed proposal becomes an action in state `queued`. The server answers
each telemetry batch of the host with the commands it still has for it, and
repeats one until the actuator's log says what became of it: applied or
refused, later kept or reverted. When detection has passed the end of the
verification window, the action is judged by whether anyone but its target
is still hurt; one that failed is reverted at once. Every step goes to
`action_log` with who did it.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

import psycopg
from psycopg.types.json import Jsonb

from gungnir_server import frame, gather
from gungnir_server.base import Base
from gungnir_server.evidence import Record, inside, is_host_waiting
from gungnir_server.hosts import Host
from gungnir_server.inference import MATCHED, InferenceEngine
from gungnir_server.ingest import ActionEntry
from gungnir_server.params import Params

log = logging.getLogger(__name__)

# Actions whose value the actuator can take as a share of what the group
# holds now, when the server knows no use to compute it from.
READS_ITSELF = {"memory_high", "cpu_weight"}
# Harm in this long before the action and at the end of its window counts.
TAIL = timedelta(seconds=30)
# A queued action the host has not answered for this long is given up.
UNANSWERED = timedelta(minutes=5)


class Refused(Exception):
    pass


def queue(
    conn: psycopg.Connection, base: Base, p: Params, host: Host, proposal_id: int, by: str, now: datetime
) -> int:
    """The action of a proposal the operator confirmed, in the same
    transaction as the decision."""
    row = conn.execute(
        "SELECT pr.action, pr.target, pr.parameter, pr.rollback_after, h.cause FROM proposals pr"
        " JOIN hypotheses h ON h.id = pr.hypothesis_id WHERE pr.id = %s",
        (proposal_id,),
    ).fetchone()
    assert row is not None
    action, target, parameter, rollback_after, cause = row
    value, share = parameter.get("value"), parameter.get("share")
    if value is None and action not in READS_ITSELF:
        raise Refused("the culprit's use is not known, there is nothing to compute the limit from")
    (action_id,) = conn.execute(
        "INSERT INTO actions (proposal_id, host_id, action, target, value, share, revert_after,"
        " verify_after, confirmed_by, confirmed_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
        " RETURNING id",
        (
            proposal_id,
            host.id,
            action,
            target,
            value,
            None if value is not None else share,
            rollback_after,
            verify_after(base, p, cause),
            by,
            now,
        ),
    ).fetchone()
    note(conn, action_id, now, "confirmed", by, {"value": value, "share": share})
    return action_id


def verify_after(base: Base, p: Params, cause: str) -> timedelta:
    """The longest window of the cause's rule, the longest a symptom of it
    may follow its leading one, between `verify_min` and `verify_max`
    (ADR 0063)."""
    rule = base.rules.get(cause)
    longest = max((m.window[1] for m in rule.symptoms if m.window), default=0) if rule else 0
    return min(p.verify_max, max(p.verify_min, timedelta(seconds=longest)))


def note(conn: psycopg.Connection, action_id: int, at: datetime, event: str, by: str, detail: dict) -> None:
    conn.execute(
        "INSERT INTO action_log (action_id, at, event, by, detail) VALUES (%s, %s, %s, %s, %s)",
        (action_id, at, event, by, Jsonb(detail)),
    )


# The agent's side


def commands(conn: psycopg.Connection, host: Host, now: datetime) -> list[dict]:
    """What the host's actuator should still do, repeated until it answers."""
    conn.execute(
        "UPDATE actions SET state = 'refused', reason = 'the host did not answer', ended_at = %s"
        " WHERE host_id = %s AND state = 'queued' AND confirmed_at < %s",
        (now, host.id, now - UNANSWERED),
    )
    out = []
    for action_id, action, target, value, share, revert_after, state, asked, asked_reason in conn.execute(
        "SELECT id, action, target, value, share, extract(epoch FROM revert_after)::int, state, asked,"
        " asked_reason FROM actions WHERE host_id = %s"
        " AND (state IN ('queued', 'applied', 'kept') OR asked = 'revert') ORDER BY id",
        (host.id,),
    ).fetchall():
        if state == "queued":
            out.append(
                {
                    "id": action_id,
                    "op": "apply",
                    "action": action,
                    "target": target,
                    "value": value,
                    "share": share,
                    "revert_after_s": revert_after,
                }
            )
        elif asked == "revert":
            out.append({"id": action_id, "op": "revert", "reason": asked_reason or ""})
        elif asked == "keep" and state == "applied":
            out.append({"id": action_id, "op": "keep"})
    return out


def record(conn: psycopg.Connection, host: Host, entries: list[ActionEntry]) -> None:
    """The actuator's log as the agent delivered it. A step that does not
    follow from the action's state was seen already and changes nothing."""
    for e in entries:
        found = conn.execute(
            "SELECT state FROM actions WHERE id = %s AND host_id = %s FOR UPDATE", (e.id, host.id)
        ).fetchone()
        if found is None:
            continue
        (state,) = found
        at = frame.from_ms(e.at)
        detail = {k: v for k, v in {"reason": e.reason, "seq": e.seq}.items() if v is not None}
        if e.event == "applied" and state == "queued":
            files = [f.model_dump(exclude_none=True) for f in e.files]
            conn.execute(
                "UPDATE actions SET state = 'applied', applied_at = %s, files = %s WHERE id = %s",
                (at, Jsonb(files), e.id),
            )
            note(conn, e.id, at, "applied", "actuator", {**detail, "files": files})
        elif e.event == "applied" and state == "refused":
            # Applied after the server gave up waiting: undo it.
            conn.execute(
                "UPDATE actions SET asked = 'revert', asked_reason = 'applied after the server gave up'"
                " WHERE id = %s",
                (e.id,),
            )
            note(conn, e.id, at, "applied late", "actuator", detail)
        elif e.event == "refused" and state == "queued":
            conn.execute(
                "UPDATE actions SET state = 'refused', reason = %s, ended_at = %s WHERE id = %s",
                (e.reason, at, e.id),
            )
            note(conn, e.id, at, "refused", "actuator", detail)
        elif e.event == "kept" and state == "applied":
            conn.execute("UPDATE actions SET state = 'kept', asked = NULL WHERE id = %s", (e.id,))
            note(conn, e.id, at, "kept", "actuator", detail)
        elif e.event == "reverted" and state in ("applied", "kept", "refused"):
            conn.execute(
                "UPDATE actions SET state = 'reverted', reason = %s, ended_at = %s, asked = NULL"
                " WHERE id = %s",
                (e.reason, at, e.id),
            )
            note(conn, e.id, at, "reverted", "actuator", detail)


# The operator's side


def ask(conn: psycopg.Connection, host: Host, action_id: int, op: str, by: str, now: datetime) -> None:
    """Revert at once, or keep past the timer once verification found the
    action worked (or had nothing to judge)."""
    row = conn.execute(
        "SELECT state, outcome, asked FROM actions WHERE id = %s AND host_id = %s FOR UPDATE",
        (action_id, host.id),
    ).fetchone()
    if row is None:
        raise Refused(f"no action {action_id}")
    state, outcome, asked = row
    if op == "revert":
        if state not in ("applied", "kept"):
            raise Refused("only an applied action can be reverted")
        reason = f"reverted by {by}"
    elif op == "keep":
        if asked == "revert":
            raise Refused("an action cannot be kept while rollback is pending")
        if state != "applied":
            raise Refused("only an applied action can be kept")
        if outcome not in ("worked", "nothing_to_judge"):
            raise Refused("an action is kept only after verification found it did no harm")
        reason = None
    else:
        raise Refused("op must be revert or keep")
    conn.execute("UPDATE actions SET asked = %s, asked_reason = %s WHERE id = %s", (op, reason, action_id))
    note(conn, action_id, now, f"{op} asked", by, {})


# Verification (ADR 0063)


class Verifier:
    """Judges applied actions once detection has passed their window."""

    __name__ = "verify"

    def __init__(self, base: Base, engine: InferenceEngine) -> None:
        self.base = base
        self.engine = engine

    def __call__(self, conn: psycopg.Connection, host: Host, p: Params, now: datetime) -> None:
        row = conn.execute("SELECT at FROM detection_state WHERE host_id = %s", (host.id,)).fetchone()
        if row is None:
            return
        due = conn.execute(
            "SELECT id, target, applied_at, applied_at + verify_after FROM actions WHERE host_id = %s"
            " AND applied_at IS NOT NULL AND verified_at IS NULL AND applied_at + verify_after <= %s"
            # Reverted before its window ended, there is nothing to judge it by.
            " AND (ended_at IS NULL OR ended_at >= applied_at + verify_after) ORDER BY id",
            (host.id, row[0]),
        ).fetchall()
        for action_id, target, applied, end in due:
            before = self.hurt(conn, host, target, applied - TAIL, applied)
            after = self.hurt(conn, host, target, end - TAIL, end)
            outcome = "failed" if after else "worked" if before else "nothing_to_judge"
            conn.execute(
                "UPDATE actions SET outcome = %s, verified_at = %s WHERE id = %s", (outcome, end, action_id)
            )
            if outcome == "failed":
                conn.execute(
                    "UPDATE actions SET asked = 'revert', asked_reason = 'verification failed'"
                    " WHERE id = %s AND state IN ('applied', 'kept')",
                    (action_id,),
                )
            detail = {"outcome": outcome, "before": before, "after": after}
            note(conn, action_id, end, "verified", "server", detail)
            conn.commit()
            log.info("action %d on %s: %s", action_id, host.name, outcome)

    def hurt(
        self, conn: psycopg.Connection, host: Host, target: str, since: datetime, until: datetime
    ) -> list[str]:
        """Who other than the target and its children was hurt in the
        interval: impact records and the impact symptoms of harmful
        hypotheses (the harm of ADRs 0060, 0065). The host's own pressure is
        not harm: it counts the target's stalls too, which a limit causes on
        purpose, while the others' waiting shows in their own groups."""
        ev = gather.evidence(conn, host, self.base, since, until)
        d = self.engine.diagnose(ev)
        items = {f.item for h in d.harmful() for f in h.with_status(MATCHED) if f.symptom.impact}
        items |= {r for r in ev.records if r.kind == "impact"}
        return sorted(
            f"{r.signal}@{r.entity}"
            for r in items
            if isinstance(r, Record)
            and inside(r, ev)
            and r.entity != target
            and not r.entity.startswith(target + "/")
            and not is_host_waiting(r)
        )


# Reading, for the API


@dataclass(frozen=True)
class LogRow:
    at: datetime
    event: str
    by: str
    detail: dict


@dataclass(frozen=True)
class ActionRow:
    id: int
    proposal_id: int
    incident_id: int
    cause: str
    action: str
    target: str
    value: float | None
    share: float | None
    revert_after_s: float
    verify_after_s: float
    state: str
    outcome: str | None
    files: list | None
    reason: str | None
    asked: str | None
    confirmed_by: str
    confirmed_at: datetime
    applied_at: datetime | None
    verified_at: datetime | None
    ended_at: datetime | None
    # When the actuator's timer undoes it, while it is applied and not kept.
    revert_at: datetime | None
    log: list[LogRow]


COLUMNS = (
    "SELECT a.id, a.proposal_id, pr.incident_id, h.cause, a.action, a.target, a.value, a.share,"
    " extract(epoch FROM a.revert_after)::float, extract(epoch FROM a.verify_after)::float, a.state,"
    " a.outcome, a.files, a.reason, a.asked, a.confirmed_by, a.confirmed_at, a.applied_at, a.verified_at,"
    " a.ended_at, CASE WHEN a.state = 'applied' THEN a.applied_at + a.revert_after END"
    " FROM actions a JOIN proposals pr ON pr.id = a.proposal_id JOIN hypotheses h ON h.id = pr.hypothesis_id"
)


def history(
    conn: psycopg.Connection, host: Host, since: datetime, until: datetime, limit: int = 500
) -> list[ActionRow]:
    """Actions confirmed in the interval or still live, newest first."""
    rows = conn.execute(
        COLUMNS + " WHERE a.host_id = %s"
        " AND (a.confirmed_at >= %s OR a.state IN ('queued', 'applied', 'kept'))"
        " AND a.confirmed_at < %s ORDER BY a.confirmed_at DESC LIMIT %s",
        (host.id, since, until, limit),
    ).fetchall()
    return _with_log(conn, rows)


def for_incident(conn: psycopg.Connection, host: Host, incident_id: int) -> list[ActionRow]:
    rows = conn.execute(
        COLUMNS + " WHERE a.host_id = %s AND pr.incident_id = %s ORDER BY a.confirmed_at, a.id",
        (host.id, incident_id),
    ).fetchall()
    return _with_log(conn, rows)


def one(conn: psycopg.Connection, host: Host, action_id: int) -> ActionRow | None:
    rows = conn.execute(COLUMNS + " WHERE a.host_id = %s AND a.id = %s", (host.id, action_id)).fetchall()
    found = _with_log(conn, rows)
    return found[0] if found else None


def _with_log(conn: psycopg.Connection, rows: list) -> list[ActionRow]:
    ids = [r[0] for r in rows]
    logs: dict[int, list[LogRow]] = {i: [] for i in ids}
    for action_id, at, event, by, detail in conn.execute(
        "SELECT action_id, at, event, by, detail FROM action_log WHERE action_id = ANY(%s) ORDER BY at, id",
        (ids,),
    ).fetchall():
        logs[action_id].append(LogRow(at, event, by, detail))
    return [ActionRow(*r, log=logs[r[0]]) for r in rows]
