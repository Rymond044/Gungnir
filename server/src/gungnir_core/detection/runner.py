"""The detection loop: reads frames from a watermark, runs the detectors,
turns their evaluations into interval records and writes them.

The live loop keeps its watermark and the state of its detectors and open
records in `detection_state`, saved in the same transaction as the
records. A replay runs the same loop over stored data with a moving clock
and its own replay id; at time t its baselines and forests know only what
was built before t.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import psycopg
from psycopg.types.json import Jsonb

from gungnir_core import frame
from gungnir_core.baselines import Book, context_on, floor_hour
from gungnir_core.detection.detectors import BookBaselines, Context, Detector, Robust, impact, thresholds
from gungnir_core.detection.growth import Growth
from gungnir_core.detection.records import Closed, Opened, Tracker
from gungnir_core.hosts import Host
from gungnir_core.params import Params
from gungnir_core.telemetry import RAW_SAMPLES

# A live loop without a watermark starts this far back; history is the
# replay's job.
LIVE_START = timedelta(minutes=1)

# Medians of the context signals of a record's entity over its interval,
# up to `until` while it is open. Only disks have context signals so far.
CONTEXT_SQL = (
    "UPDATE anomalies a SET context = c.context FROM ("
    " SELECT a.id, jsonb_object_agg(cs.signal, m.median) AS context"
    " FROM anomalies a JOIN series s ON s.id = a.series_id"
    " JOIN series cs ON cs.host_id = s.host_id AND cs.entity = s.entity AND cs.signal = ANY(%(signals)s)"
    " CROSS JOIN LATERAL (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY r.value) AS median"
    f"  FROM {RAW_SAMPLES} r WHERE r.series_id = cs.id"
    "  AND r.ts >= a.started_at AND r.ts <= coalesce(a.ended_at, %(until)s)) m"
    " WHERE a.id = ANY(%(ids)s) AND m.median IS NOT NULL GROUP BY a.id"
    ") c WHERE a.id = c.id"
)


@dataclass
class Variant:
    """A named set of detectors, as compared in the evaluation (ADR 0027)."""

    name: str
    build: Callable[[psycopg.Connection, Host, Params], list[Detector]]


def _robust(per_class: bool, ewma: bool = True):
    def build(conn, host, params):
        return [Robust(BookBaselines(Book(conn, host, params, per_class)), ewma)]

    return build


def _forest(conn, host, params, store=False):
    from gungnir_core.detection.forest import ForestDetector

    return [ForestDetector(conn, host, params, store)]


def _with_forest(build_first):
    def build(conn, host, params):
        return [*build_first(conn, host, params), *_forest(conn, host, params)]

    return build


VARIANTS = {
    v.name: v
    for v in (
        Variant("thresholds", lambda conn, host, params: [thresholds(host.name)]),
        Variant("robust-host", _robust(per_class=False)),
        Variant("robust-classes", _robust(per_class=True)),
        Variant("iforest", _forest),
        Variant("robust-classes+iforest", _with_forest(_robust(per_class=True))),
        Variant("impact", lambda conn, host, params: [impact(host.name)]),
        Variant("growth", lambda conn, host, params: [Growth()]),
    )
}


def live_detectors(conn: psycopg.Connection, host: Host, params: Params) -> list[Detector]:
    """Every branch at once: the control thresholds, robust statistics with
    workload classes, the forest, impact, and the growth of group memory."""
    return [
        thresholds(host.name),
        *_robust(per_class=True)(conn, host, params),
        *_forest(conn, host, params, store=True),
        impact(host.name),
        Growth(),
    ]


@dataclass
class Runner:
    conn: psycopg.Connection
    host: Host
    params: Params
    detectors: list[Detector]
    replay_id: int | None = None
    at: datetime | None = None
    tracker: Tracker = None
    states: dict = field(default_factory=dict)
    written: int = 0

    def __post_init__(self) -> None:
        if self.tracker is None:
            self.tracker = Tracker(self.params)

    @classmethod
    def live(cls, conn: psycopg.Connection, host: Host, params: Params) -> "Runner":
        runner = cls(conn, host, params, live_detectors(conn, host, params))
        row = conn.execute("SELECT at, state FROM detection_state WHERE host_id = %s", (host.id,)).fetchone()
        if row:
            runner.at = row[0]
            runner.tracker = Tracker.from_json(params, row[1].get("tracker"))
            runner.states = row[1].get("detectors", {})
        return runner

    def run(self, until: datetime) -> int:
        """Judges every tick in [watermark, until) and moves the watermark
        there; returns the number of records written."""
        if self.at is None:
            self.at = until - LIVE_START
        lookback = timedelta(seconds=self.params.iforest_window * self.params.step_s)
        written = 0
        while self.at < until:
            # One step never crosses an hour, so it uses one baseline version.
            end = min(until, floor_hour(self.at) + timedelta(hours=1))
            for d in self.detectors:
                prepare = getattr(d, "prepare", None)
                if prepare:
                    prepare(self.at)
            f = frame.load(self.conn, self.host, self.at - lookback, end)
            start = int((f.ts < frame.to_ms(self.at)).sum())
            with self.conn.transaction():
                if start < f.ts.size:
                    written += self.step(f, start)
                self.at = end
                if self.replay_id is None:
                    self.save()
        self.written += written
        return written

    def step(self, f: frame.Frame, start: int) -> int:
        classes, powers, _ = context_on(self.conn, self.host, f, self.params.label_tail)
        events = []
        seen = set()
        for d in self.detectors:
            ctx = Context(f, start, classes, powers, self.params, self.states.setdefault(d.name, {}))
            for ev in d.evaluate(ctx):
                seen.add(ev.key)
                events += self.tracker.feed(ev)
        events += self.tracker.expire(seen, f.ts.size - start, int(f.ts[-1]))
        return self.write(events, classes[start:], powers[start:], int(f.ts[-1]))

    def write(self, events, classes, powers, until_ms: int) -> int:
        tokens: dict[int, list[int]] = {}
        touched: list[int] = []
        written = 0
        for ev in events:
            if isinstance(ev, Opened):
                episode = None
                if len(ev.records) > 1:
                    (episode,) = self.conn.execute("SELECT nextval('anomaly_episodes')").fetchone()
                ids = []
                for r in ev.records:
                    (rid,) = self.conn.execute(
                        "INSERT INTO anomalies (host_id, series_id, kind, detector, level, started_at,"
                        " direction, peak_score, value, workload_class, power_profile, baseline, episode,"
                        " replay_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
                        " RETURNING id",
                        (
                            self.host.id,
                            r.series_id,
                            ev.kind,
                            ev.detector,
                            ev.level,
                            frame.from_ms(ev.started_ms),
                            r.direction,
                            _finite(ev.score),
                            r.value,
                            classes[ev.index],
                            powers[ev.index],
                            Jsonb(r.baseline),
                            episode,
                            self.replay_id,
                        ),
                    ).fetchone()
                    ids.append(rid)
                tokens[ev.token] = ids
                self.tracker.set_ids(ev.token, ids)
                touched += ids
                written += len(ids)
            elif isinstance(ev, Closed):
                ids = ev.ids or tokens.get(ev.token, [])
                self.conn.execute(
                    "UPDATE anomalies SET ended_at = %s, peak_score = %s WHERE id = ANY(%s)",
                    (frame.from_ms(ev.ended_ms), _finite(ev.peak), ids),
                )
                touched += ids
        for track in self.tracker.open_tracks().values():
            current = track["open"]
            if current["ids"]:
                self.conn.execute(
                    "UPDATE anomalies SET peak_score = %s WHERE id = ANY(%s) AND peak_score < %s",
                    (_finite(current["peak"]), current["ids"], _finite(current["peak"])),
                )
                touched += current["ids"]
        if touched:
            self.conn.execute(
                CONTEXT_SQL,
                {"signals": list(frame.CONTEXT_SIGNALS), "ids": touched, "until": frame.from_ms(until_ms)},
            )
        return written

    def save(self) -> None:
        state = {"tracker": self.tracker.to_json(), "detectors": self.states}
        self.conn.execute(
            "INSERT INTO detection_state (host_id, at, state) VALUES (%s, %s, %s)"
            " ON CONFLICT (host_id) DO UPDATE SET at = EXCLUDED.at, state = EXCLUDED.state",
            (self.host.id, self.at, Jsonb(state)),
        )

    def close_open(self, at: datetime) -> None:
        """Ends the records still open, at the end of a replay."""
        for key in list(self.tracker.open_tracks()):
            track = self.tracker.tracks[key]
            current = track.pop("open")
            ended = max(at, frame.from_ms(current["started"]))
            self.conn.execute(
                "UPDATE anomalies SET ended_at = %s WHERE id = ANY(%s)", (ended, current["ids"])
            )


def _finite(x: float) -> float:
    # A score against a zero limit is infinite; JSON and comparisons need a number.
    return x if x < 1e300 else 1e300
