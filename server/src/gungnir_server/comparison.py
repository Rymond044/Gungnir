"""Thresholds, statistics only and the hybrid compared by what the operator
is alerted to (slice S9, ADR 0008).

All three come from one replay of every live branch. Thresholds alert on the
records of the control thresholds, statistics on those of the robust branch,
the forest and the growth check; records closer than the quiet period join
one alert, as an incident joins harm. The hybrid alerts with incidents, which
open only on harm and carry hypotheses, so it alone is scored on the cause,
the culprit and the proposal.

A fault phase (`harm` true) is found when an alert overlaps it with its
tail; a phase that hurts nobody (`harm` false) should raise none, and an
alert that starts inside it counts against the approach. Alerts that start
outside every labeled phase are counted per hour of data outside phases: an
upper bound of false alerts, since the host's own life between scenarios
may hurt for real.
"""

from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import psycopg

from gungnir_server.hosts import Host
from gungnir_server.labels import Phase, phases_of
from gungnir_server.params import Params
from gungnir_server.telemetry import RAW_SAMPLES

APPROACHES = ("thresholds", "statistics", "hybrid")


@dataclass(frozen=True)
class Alert:
    start: float
    end: float
    incident: int | None = None


@dataclass(frozen=True)
class Window:
    """A scored phase in seconds, end including the tail."""

    name: str
    start: float
    end: float
    harm: bool
    cause: str | None
    scope: str | None


@dataclass(frozen=True)
class Suspect:
    """A harmful hypothesis of an incident."""

    incident: int
    cause: str
    culprit: str | None
    first_seen: float
    last_seen: float
    confidence: float


@dataclass
class Score:
    faults: int = 0
    found: int = 0
    opened_inside: int = 0
    delays_s: list[float] = field(default_factory=list)
    harmless: int = 0
    alerted_harmless: int = 0
    false_alerts: int = 0
    observed_h: float = 0.0
    quiet_alerts: int = 0
    quiet_h: float = 0.0
    missed: list[str] = field(default_factory=list)
    # The hybrid only.
    top1: int = 0
    top3: int = 0
    culprit: int = 0
    proposed: int = 0
    wrong: list[str] = field(default_factory=list)

    @property
    def recall(self) -> float:
        return self.found / self.faults if self.faults else float("nan")

    @property
    def median_delay_s(self) -> float:
        return float(np.median(self.delays_s)) if self.delays_s else float("nan")

    @property
    def false_per_h(self) -> float:
        return self.false_alerts / self.observed_h if self.observed_h else float("nan")

    @property
    def quiet_per_h(self) -> float:
        return self.quiet_alerts / self.quiet_h if self.quiet_h else float("nan")


def join(records: list[tuple[float, float]], quiet: float) -> list[Alert]:
    """Records closer than `quiet` to the end of the alert join it."""
    alerts: list[Alert] = []
    for start, end in sorted(records):
        if alerts and start - alerts[-1].end < quiet:
            last = alerts[-1]
            alerts[-1] = Alert(last.start, max(last.end, end))
        else:
            alerts.append(Alert(start, end))
    return alerts


def score(
    alerts: list[Alert],
    windows: list[Window],
    labeled: list[tuple[float, float]],
    quiet: list[tuple[float, float]],
    ticks: np.ndarray,
    step_s: float,
) -> Score:
    """`labeled` are every labeled phase with its tail, scored or not; `ticks`
    the times with data, so that a host asleep counts as nothing."""
    s = Score()
    for w in windows:
        overlapping = [a for a in alerts if a.start < w.end and a.end > w.start]
        if not w.harm:
            s.harmless += 1
            s.alerted_harmless += any(w.start <= a.start < w.end for a in overlapping)
            continue
        s.faults += 1
        if not overlapping:
            s.missed.append(w.name)
            continue
        s.found += 1
        first = min(a.start for a in overlapping)
        s.delays_s.append(max(0.0, first - w.start))
        s.opened_inside += any(a.start >= w.start for a in overlapping)
    starts = np.array([a.start for a in alerts])
    s.false_alerts = int((~_inside(starts, labeled)).sum())
    s.quiet_alerts = int(_inside(starts, quiet).sum())
    s.observed_h = float((~_inside(ticks, labeled)).sum()) * step_s / 3600
    s.quiet_h = float(_inside(ticks, quiet).sum()) * step_s / 3600
    return s


def rank_causes(w: Window, incidents: set[int], suspects: list[Suspect], late: float) -> list[Suspect]:
    """The harmful hypotheses that first appeared during the phase in the
    incidents overlapping it, best first, one per cause; `late` is how long
    after the phase a diagnosis may first show it (one step). One incident
    may span two phases close together; a hypothesis of the earlier one is
    not the later phase's answer."""
    seen = sorted(
        (h for h in suspects if h.incident in incidents and w.start <= h.first_seen <= w.end + late),
        key=lambda h: -h.confidence,
    )
    ranked: dict[str, Suspect] = {}
    for h in seen:
        ranked.setdefault(h.cause, h)
    return list(ranked.values())


def is_culprit(culprit: str | None, scope: str | None) -> bool:
    """A culprit is a control group path; the label names the unit."""
    return bool(culprit and scope) and culprit.rsplit("/", 1)[-1] == scope


def score_causes(
    s: Score,
    alerts: list[Alert],
    windows: list[Window],
    suspects: list[Suspect],
    proposals: dict[int, set[str]],
    late: float = 0.0,
) -> None:
    """Top-1 and top-3 of the cause, the culprit of the right cause, and a
    proposal against that culprit, over the found fault phases with a cause."""
    for w in windows:
        if not w.harm or w.cause is None:
            continue
        ids = {a.incident for a in alerts if a.start < w.end and a.end > w.start and a.incident}
        ranked = rank_causes(w, ids, suspects, late)
        causes = [h.cause for h in ranked]
        if w.cause in causes[:1]:
            s.top1 += 1
        else:
            s.wrong.append(f"{w.name}: {', '.join(causes[:3]) or 'no hypothesis'}")
        if w.cause in causes[:3]:
            s.top3 += 1
        right = next((h for h in ranked if h.cause == w.cause), None)
        if right and (w.scope is None or is_culprit(right.culprit, w.scope)):
            s.culprit += 1
        if right and w.scope and any(is_culprit(t, w.scope) for i in ids for t in proposals.get(i, ())):
            s.proposed += 1


@dataclass(frozen=True)
class Comparison:
    replay_id: int
    since: datetime
    until: datetime
    scores: dict[str, Score]
    with_cause: int
    # Of them, those whose label names a culprit.
    with_culprit: int


def compare(conn: psycopg.Connection, host: Host, replay_id: int, p: Params) -> Comparison:
    since, until = conn.execute("SELECT since, until FROM replays WHERE id = %s", (replay_id,)).fetchone()
    quiet_s = p.incident_quiet.total_seconds()
    tail = p.label_tail
    phases = [ph for ph in phases_of(conn, host, since, until) if ph.since < until and ph.until > since]
    windows = [_window(ph, tail.total_seconds()) for ph in phases if ph.harm is not None]
    labeled = [(ph.since.timestamp(), (ph.until + tail).timestamp()) for ph in phases]
    quiet = [(ph.since.timestamp(), ph.until.timestamp()) for ph in phases if ph.normal]
    ticks = np.array(
        [
            ts.timestamp()
            for (ts,) in conn.execute(
                f"SELECT r.ts FROM {RAW_SAMPLES} r JOIN series s ON s.id = r.series_id"
                " WHERE s.host_id = %s AND s.signal = 'cpu.idle_ratio' AND r.ts >= %s AND r.ts < %s",
                (host.id, since, until),
            )
        ]
    )

    def records(where: str) -> list[tuple[float, float]]:
        rows = conn.execute(
            "SELECT started_at, coalesce(ended_at, %s) FROM anomalies"
            f" WHERE replay_id = %s AND kind = 'anomaly' AND {where}",
            (until, replay_id),
        ).fetchall()
        return [(a.timestamp(), b.timestamp()) for a, b in rows]

    incident_alerts = [
        Alert(a.timestamp(), b.timestamp(), i)
        for i, a, b in conn.execute(
            "SELECT id, opened_at, last_harm_at FROM incidents WHERE replay_id = %s ORDER BY opened_at",
            (replay_id,),
        ).fetchall()
    ]
    alerts = {
        "thresholds": join(records("detector = 'threshold'"), quiet_s),
        "statistics": join(records("detector <> 'threshold'"), quiet_s),
        "hybrid": incident_alerts,
    }
    scores = {name: score(found, windows, labeled, quiet, ticks, p.step_s) for name, found in alerts.items()}
    suspects = [
        Suspect(i, cause, culprit or None, a.timestamp(), b.timestamp(), c)
        for i, cause, culprit, a, b, c in conn.execute(
            "SELECT h.incident_id, h.cause, h.culprit, h.first_seen, h.last_seen, h.peak_confidence"
            " FROM hypotheses h JOIN incidents i ON i.id = h.incident_id WHERE i.replay_id = %s AND h.harm"
            # Withdrawn once its pending symptoms did not come: not the answer.
            " AND h.rank IS NOT NULL",
            (replay_id,),
        ).fetchall()
    ]
    proposals: dict[int, set[str]] = {}
    for i, target in conn.execute(
        "SELECT p.incident_id, p.target FROM proposals p JOIN incidents i ON i.id = p.incident_id"
        " WHERE i.replay_id = %s",
        (replay_id,),
    ).fetchall():
        proposals.setdefault(i, set()).add(target)
    late = p.replay_incident_step.total_seconds()
    score_causes(scores["hybrid"], incident_alerts, windows, suspects, proposals, late)
    with_cause = sum(1 for w in windows if w.harm and w.cause)
    with_culprit = sum(1 for w in windows if w.harm and w.cause and w.scope)
    return Comparison(replay_id, since, until, scores, with_cause, with_culprit)


def _window(ph: Phase, tail_s: float) -> Window:
    return Window(
        f"{ph.run}/{ph.phase}",
        ph.since.timestamp(),
        ph.until.timestamp() + tail_s,
        bool(ph.harm),
        ph.cause,
        ph.scope,
    )


def _inside(ts: np.ndarray, intervals: list[tuple[float, float]]) -> np.ndarray:
    mask = np.zeros(ts.shape, dtype=bool)
    for a, b in intervals:
        mask |= (ts >= a) & (ts < b)
    return mask


def table(c: Comparison) -> str:
    """Markdown: detection by approach, then the hybrid's causes."""
    lines = [
        "| approach | faults found | opened inside | recall | median delay, s"
        " | alerts on harmless loads | false alerts / h | on quiet / h |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for name in APPROACHES:
        s = c.scores[name]
        lines.append(
            f"| {name} | {s.found} / {s.faults} | {s.opened_inside} | {_f(s.recall, 2)}"
            f" | {_f(s.median_delay_s, 0)} | {s.alerted_harmless} / {s.harmless}"
            f" | {_f(s.false_per_h, 2)} | {_f(s.quiet_per_h, 2)} |"
        )
    h = c.scores["hybrid"]
    n = c.with_cause
    lines += [
        "",
        "| faults with a cause | cause top-1 | cause top-3 | culprit right | proposal for the culprit |",
        "|---|---|---|---|---|",
        f"| {n} | {_share(h.top1, n)} | {_share(h.top3, n)} | {_share(h.culprit, n)}"
        f" | {_share(h.proposed, c.with_culprit)} |",
    ]
    return "\n".join(lines)


def _share(k: int, n: int) -> str:
    return f"{k} / {n} ({k / n:.2f})" if n else "-"


def _f(x: float, digits: int) -> str:
    return "-" if x != x else f"{x:.{digits}f}"
