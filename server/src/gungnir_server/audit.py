"""The audit of an interval (ADRs 0056, 0057): what was unusual, what it was
and who did it, as stretches an operator reads one by one.

The inference engine gives a hypothesis for each burst of a cause's leading
symptom; the audit joins the bursts of one cause and culprit that follow each
other closely and share a process group, so one build is one stretch and the
next build another. Host and device records no hypothesis uses that run
alongside such a stretch go with it as what the host saw meanwhile. The
rest become stretches by resource and time (ADR 0059): explained by
expected behavior; the activity of a program, when its own group's use was
unusual and no cause fits; or not explained, when only the host's or a
device's signals were. Each comes with the commands that used the
resource, the advice the policy holds and the cause that came closest. The
audit reads what detection attached to the records and changes nothing the
inference engine decided.

Harm is what opens an incident: an impact record, or a hypothesis whose
impact was matched. Waiting above its usual level but under the impact
levels is shown as such and is not harm, nor is an event on its own, such as
a unit that failed (ADR 0059).
"""

from dataclasses import dataclass, field

import psycopg

from gungnir_server.base import Base
from gungnir_server.evidence import HOST, Event, Evidence, Record, inside, related, within
from gungnir_server.inference import MATCHED, Diagnosis, Hypothesis, as_hypothesis, item_dict

# Bursts further apart than this are separate stretches.
GAP_S = 300.0
# Resources drawn on one lane of the interface.
LANES = {
    "cpu": "cpu",
    "pids": "cpu",
    "power": "cpu",
    "memory": "memory",
    "swap": "memory",
    "disk": "disk",
    "filesystem": "disk",
}
OTHER = "other"
# Commands shown per stretch.
TOP = 5
# Records no hypothesis uses join while they overlap or follow within a
# minute, so a stretch drawn on a graph covers a burst, not the quiet between
# bursts; a long record draws others in for five minutes at most, and a level
# that stays unusual all evening is not one event with everything meanwhile.
JOIN_S = 60.0
REACH_S = 300.0


@dataclass
class Stretch:
    kind: str  # explained, expected, activity or unexplained
    lane: str
    start: float
    end: float | None
    harm: bool
    records: list[Record] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
    hypotheses: list[Hypothesis] = field(default_factory=list)
    bursts: list[tuple[float, float | None]] = field(default_factory=list)
    # Until when a following record still joins it.
    reach: float = 0.0
    # Records no hypothesis uses that the host saw meanwhile.
    also: list[Record] = field(default_factory=list)


def audit(conn: psycopg.Connection, base: Base, ev: Evidence, d: Diagnosis) -> dict:
    ids = [int(r.id) for r in ev.records]
    commands = dict(conn.execute("SELECT id, processes FROM anomalies WHERE id = ANY(%s)", (ids,)).fetchall())
    slices = {str(k): v or [] for k, v in commands.items()}
    onto = base.ontology

    explained = _join(d.hypotheses, slices, onto)
    expected_ids = set(d.expected)
    leftover = [r for r in d.unexplained if isinstance(r, Record) and not _alongside(r, explained, onto)]
    leftover += [r for r in ev.records if r.id in expected_ids and inside(r, ev)]
    events = [e for e in d.unexplained if isinstance(e, Event)]
    rest = _cluster(leftover, events, expected_ids, onto)

    advice = {a.resource: a for a in d.advisories}
    out = []
    for s in explained:
        best = max(s.hypotheses, key=lambda h: h.confidence)
        out.append(
            {
                "kind": "explained",
                "lane": s.lane,
                "start": s.start,
                "end": s.end,
                "harm": s.harm,
                "expected": all(h.expected for h in s.hypotheses),
                "cause": best.cause,
                "culprit": best.binding.culprit,
                "device": best.binding.device,
                "filesystem": best.binding.filesystem,
                "confidence": round(best.confidence, 4),
                "bursts": s.bursts,
                "records": sorted({r.id for r in s.records}, key=int),
                "impact": [
                    item_dict(f.item) for f in best.with_status(MATCHED) if f.symptom.impact and f.item
                ],
                "processes": _commands(s.records, best.binding.culprit, slices),
                "also": [item_dict(r) for r in sorted(s.also, key=lambda r: r.start)],
                "hypothesis": as_hypothesis(best),
                "actions": _actions(base, best.cause) if s.harm else [],
            }
        )
    for s in rest:
        resources = {_resource(onto, i) for i in [*s.records, *s.events]}
        near = [
            h
            for h in d.near
            if s.lane in {LANES.get(x, OTHER) for x in onto.causes[h.cause].resources}
            and s.start - GAP_S <= h.leading_at <= (s.end or float("inf")) + GAP_S
        ]
        tips = [advice[x] for x in sorted(resources) if x in advice] if s.harm else []
        actor = _actor(s.records, onto)
        out.append(
            {
                "kind": "activity" if s.kind == "unexplained" and actor else s.kind,
                "lane": s.lane,
                "start": s.start,
                "end": s.end,
                "harm": s.harm,
                "waiting": any(_waits(r, onto) for r in s.records),
                "actor": actor,
                "records": [item_dict(r) for r in s.records],
                "events": [item_dict(e) for e in s.events],
                "workload_class": next((r.workload_class for r in s.records if r.workload_class), None),
                "processes": _commands(s.records, actor, slices),
                "groups": _groups(s.records, slices),
                "advice": [{"resource": a.resource, "text": a.text, "source": a.source} for a in tips],
                "near": {**as_hypothesis(near[0]), "reason": near[0].rejected} if near else None,
            }
        )
    return {"stretches": out}


def _join(hypotheses: tuple[Hypothesis, ...], slices: dict[str, list], onto) -> list[Stretch]:
    """Stretches of one cause and binding: bursts closer than GAP_S that share
    a process group are one stretch (one build, not two)."""
    by_key: dict[tuple, list[Hypothesis]] = {}
    for h in hypotheses:
        by_key.setdefault((h.cause, h.binding), []).append(h)
    out = []
    for (_, binding), found in by_key.items():
        lane = _lane_of(found[0], onto)
        current: Stretch | None = None
        for h in sorted(found, key=lambda h: h.leading_at):
            records = [f.item for f in h.with_status(MATCHED) if isinstance(f.item, Record)]
            start = min(r.start for r in records)
            end = None if any(r.end is None for r in records) else max(r.end for r in records)
            if current and _continues(current, start, records, binding.culprit, slices):
                current.hypotheses.append(h)
                current.records += records
                current.bursts.append((start, end))
                current.end = None if end is None or current.end is None else max(current.end, end)
                current.harm |= h.harm
                continue
            current = Stretch("explained", lane, start, end, h.harm, records, [], [h], [(start, end)])
            out.append(current)
    return out


def _continues(s: Stretch, start: float, records: list[Record], culprit: str | None, slices) -> bool:
    """A burst that overlaps the stretch continues it; one that follows
    within GAP_S continues it only when the same process group ran in both."""
    if s.end is None or start <= s.end:
        return True
    if start - s.end > GAP_S:
        return False
    before = {c["pgid"] for c in _commands(s.records, culprit, slices) if c["pgid"]}
    after = {c["pgid"] for c in _commands(records, culprit, slices) if c["pgid"]}
    return bool(before & after)


def _cluster(records: list[Record], events: list[Event], expected: set[str], onto) -> list[Stretch]:
    """Records and events no hypothesis uses, by lane and kind, joined while
    they overlap or follow within JOIN_S, a long record counting for REACH_S."""
    items = sorted([*records, *events], key=lambda i: i.start if isinstance(i, Record) else i.at)
    open_: dict[tuple[str, str], Stretch] = {}
    out = []
    for item in items:
        lane = LANES.get(_resource(onto, item), OTHER)
        kind = "expected" if isinstance(item, Record) and item.id in expected else "unexplained"
        start = item.start if isinstance(item, Record) else item.at
        end = item.end if isinstance(item, Record) else item.at
        reach = min(end if end is not None else float("inf"), start + REACH_S) + JOIN_S
        s = open_.get((lane, kind))
        if s and start <= s.reach:
            s.end = None if end is None or s.end is None else max(s.end, end)
            s.harm |= _hurts(item, onto)
            s.reach = max(s.reach, reach)
        else:
            s = open_[(lane, kind)] = Stretch(kind, lane, start, end, _hurts(item, onto), reach=reach)
            out.append(s)
        (s.records if isinstance(item, Record) else s.events).append(item)
    return out


def _alongside(r: Record, explained: list[Stretch], onto) -> bool:
    """Puts a record of the host or a device, or of a group the culprit is in
    or contains, with the explained stretch of its lane it runs alongside. An
    impact record stays on its own: it is harm no hypothesis explains."""
    if r.kind == "impact":
        return False
    lane = LANES.get(_resource(onto, r), OTHER)
    for s in explained:
        culprit = s.hypotheses[0].binding.culprit
        on_it = not r.signal.startswith("cgroup.") or (culprit is not None and related(r.entity, culprit))
        end = float("inf") if s.end is None else s.end
        if s.lane == lane and on_it and s.start - JOIN_S <= r.start <= end + JOIN_S:
            s.also.append(r)
            return True
    return False


def _actor(records: list[Record], onto) -> str | None:
    """The group whose own use was unusual: of a group and its ancestor the
    deepest, then the most unusual."""
    used = [r for r in records if _activity(r, onto)]
    groups = {r.entity for r in used}
    deepest = [r for r in used if not any(g != r.entity and within(g, r.entity) for g in groups)]
    return max(deepest, key=lambda r: r.score or 0.0).entity if deepest else None


def _activity(r: Record, onto) -> bool:
    """A rise of a group's own use of a resource, not of its waiting."""
    return (
        r.kind == "anomaly"
        and r.direction == "up"
        and r.signal.startswith("cgroup.")
        and not onto.signals[r.signal].impact
    )


def _hurts(item: Record | Event, onto) -> bool:
    """An impact record (ADR 0059)."""
    return isinstance(item, Record) and item.kind == "impact"


def _waits(r: Record, onto) -> bool:
    """Waiting above its usual level, under the impact levels."""
    return r.kind == "anomaly" and r.direction == "up" and onto.signals[r.signal].impact


def _lane_of(h: Hypothesis, onto) -> str:
    """The lane of the leading symptom's resource: swap I/O is seen on memory
    first, though it loads the disk too."""
    leading = h.rule.leading
    if leading.kind == "anomaly" and leading.names[0] in onto.signals:
        return LANES.get(onto.signals[leading.names[0]].resource, OTHER)
    return min(LANES.get(x, OTHER) for x in onto.causes[h.cause].resources)


def _resource(onto, item: Record | Event) -> str:
    if isinstance(item, Record):
        return onto.signals[item.signal].resource
    return onto.events[item.type].resource or OTHER


def _commands(records: list[Record], culprit: str | None, slices: dict[str, list]) -> list[dict]:
    """The commands of a stretch: from the culprit's records, else the host's,
    else any group's; a command seen in several records keeps its largest use."""
    pick = [r for r in records if culprit and r.entity == culprit]
    pick = pick or [r for r in records if r.entity == HOST and slices.get(r.id)]
    pick = pick or [r for r in records if slices.get(r.id)]
    merged: dict[tuple, dict] = {}
    for r in pick:
        for c in slices.get(r.id, []):
            key = ("group", c["pgid"]) if c["pgid"] else ("comm", c["names"][0][0])
            have = merged.get(key)
            if have is None or c["cores"] > have["cores"]:
                merged[key] = {**c, "rss_bytes": max(c["rss_bytes"], have["rss_bytes"] if have else 0)}
    return sorted(merged.values(), key=lambda c: (-c["cores"], -c["rss_bytes"]))[:TOP]


def _groups(records: list[Record], slices: dict[str, list]) -> list[dict]:
    """The control groups among the records, each with its own commands."""
    groups = sorted({r.entity for r in records if r.entity != HOST and r.signal.startswith("cgroup.")})
    return [
        {"group": g, "processes": _commands([r for r in records if r.entity == g], g, slices)} for g in groups
    ]


def _actions(base: Base, cause: str) -> list[dict]:
    return [
        {
            "action": e.action,
            "when": e.when,
            "signal": e.signal,
            "share": e.share,
            "effectiveness": e.effectiveness,
            "source": e.source,
        }
        for e in base.policy.for_cause(cause)
    ]
