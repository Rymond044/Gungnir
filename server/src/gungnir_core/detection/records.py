"""Interval records from per-sample conditions (ADR 0021).

A detector evaluates its condition on every tick of a series (or, for the
forest, every window). A record opens when the condition holds on
`open_after` evaluations in a row and starts at the first of them; it is
extended while the condition holds and closes after `close_after` normal
evaluations, ending where the condition stopped. A series that stops
sending closes its record after `stale_after` evaluations without data.

The tracker's state is plain JSON, saved with every step of the live loop.
"""

from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

from gungnir_core.params import Params


@dataclass(frozen=True)
class RecordSpec:
    """One record to write when an interval opens. The forest names several
    series for one interval; every other detector names the series it
    judged."""

    series_id: int
    direction: str
    baseline: dict
    value: float | None = None


@dataclass
class Evaluation:
    """A detector's verdicts on one key (detector and series) over the ticks
    of a frame. NaN scores mark ticks that were not evaluated."""

    key: str
    detector: str
    kind: str
    ticks: np.ndarray  # int64 ms
    flags: np.ndarray  # bool
    scores: np.ndarray  # float64, |score| of the condition
    # What to record when an interval opens at tick index i.
    name: Callable[[int], list[RecordSpec]]
    level: str | None = None
    open_after: int | None = None


@dataclass(frozen=True)
class Opened:
    # Ties the interval's records to its later close, also within one step.
    token: int
    key: str
    detector: str
    kind: str
    level: str | None
    started_ms: int
    index: int
    score: float
    records: list[RecordSpec]


@dataclass(frozen=True)
class Closed:
    token: int
    key: str
    ended_ms: int
    peak: float
    # Records written in an earlier step; empty if opened in this one.
    ids: list[int]


Event = Opened | Closed


@dataclass
class Tracker:
    params: Params
    tracks: dict[str, dict] = field(default_factory=dict)
    next_token: int = 1

    def to_json(self) -> dict:
        return {"tracks": self.tracks, "next": self.next_token}

    @classmethod
    def from_json(cls, params: Params, data: dict | None) -> "Tracker":
        data = data or {}
        return cls(params, dict(data.get("tracks", {})), data.get("next", 1))

    def open_tracks(self) -> dict[str, dict]:
        return {k: t for k, t in self.tracks.items() if t.get("open")}

    def feed(self, ev: Evaluation) -> list[Event]:
        p = self.params
        track = self.tracks.get(ev.key)
        valid = ~np.isnan(ev.scores)
        if track is None or (not track.get("open") and track.get("hits", 0) == 0):
            # Fast path: nothing open, nothing building, nothing new.
            if not (ev.flags & valid).any():
                if track is not None:
                    del self.tracks[ev.key]
                return []
            track = self.tracks.setdefault(ev.key, {"hits": 0})
        open_after = ev.open_after or p.open_after
        step_ms = int(p.step_s * 1000)
        events: list[Event] = []
        for i in range(ev.ticks.size):
            t = int(ev.ticks[i])
            current = track.get("open")
            if not valid[i]:
                track["hits"] = 0
                if current:
                    current["missing"] = current.get("missing", 0) + 1
                    if current["missing"] >= p.stale_after:
                        events.append(self._close(ev.key, track, current["last_hit"] + step_ms))
                continue
            score = float(ev.scores[i])
            if ev.flags[i]:
                if current:
                    current.update(last_hit=t, normals=0, missing=0, peak=max(current["peak"], score))
                    current.pop("ending", None)
                    continue
                if track["hits"] == 0:
                    track["first"], track["first_index"], track["peak"] = t, i, score
                track["hits"] += 1
                track["peak"] = max(track["peak"], score)
                if track["hits"] >= open_after:
                    first_index = max(track["first_index"], 0)
                    token = self.next_token
                    self.next_token += 1
                    track["open"] = {
                        "token": token,
                        "started": track["first"],
                        "last_hit": t,
                        "peak": track["peak"],
                        "ids": [],
                    }
                    track["hits"] = 0
                    events.append(
                        Opened(
                            token,
                            ev.key,
                            ev.detector,
                            ev.kind,
                            ev.level,
                            track["first"],
                            first_index,
                            track["peak"],
                            ev.name(first_index),
                        )
                    )
            else:
                track["hits"] = 0
                if current:
                    current["missing"] = 0
                    current["normals"] = current.get("normals", 0) + 1
                    current.setdefault("ending", t)
                    if current["normals"] >= p.close_after:
                        events.append(self._close(ev.key, track, current["ending"]))
        # Tick indices refer to this frame only.
        if track.get("hits"):
            track["first_index"] = -1
        if not track.get("open") and not track.get("hits"):
            self.tracks.pop(ev.key, None)
        return events

    def _close(self, key: str, track: dict, ended_ms: int) -> Closed:
        current = track.pop("open")
        ended = max(ended_ms, current["started"])
        return Closed(current["token"], key, ended, current["peak"], current["ids"])

    def expire(self, seen: set[str], ticks: int, last_ms: int) -> list[Event]:
        """Keys a step did not evaluate at all (a group that ended, a series
        no longer checked): their open records age by the step's ticks."""
        events: list[Event] = []
        step_ms = int(self.params.step_s * 1000)
        for key in [k for k in self.tracks if k not in seen]:
            track = self.tracks[key]
            current = track.get("open")
            if not current:
                del self.tracks[key]
                continue
            current["missing"] = current.get("missing", 0) + ticks
            if current["missing"] >= self.params.stale_after:
                events.append(self._close(key, track, min(current["last_hit"] + step_ms, last_ms)))
                del self.tracks[key]
        return events

    def set_ids(self, token: int, ids: list[int]) -> None:
        """Remembers the written records of an interval that is still open."""
        for track in self.tracks.values():
            current = track.get("open")
            if current and current["token"] == token:
                current["ids"] = ids
