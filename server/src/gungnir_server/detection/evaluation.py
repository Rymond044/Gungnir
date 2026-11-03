"""Event-wise evaluation of records against labeled phases (ADR 0027).

A phase is found when a record of one of its expected signals overlaps it;
the delay runs from the phase start to the first such record. False alarms
are records that start outside every labeled phase, per hour of observed
time outside phases, and separately per hour of quiet intervals (normal
phases such as the calibration idle). No point-adjust: it lets even a
random detector look good (Kim et al., AAAI 2022).
"""

from dataclasses import dataclass, field

import numpy as np


@dataclass(frozen=True)
class Event:
    """A labeled phase in milliseconds, end including the tail."""

    start: int
    end: int
    expected: frozenset[str]
    name: str = ""


@dataclass(frozen=True)
class Rec:
    start: int
    end: int
    signal: str


@dataclass
class Score:
    events: int = 0
    found: int = 0
    # Found by a record that opened inside the phase, not one already open.
    opened_inside: int = 0
    delays_s: list[float] = field(default_factory=list)
    false_alarms: int = 0
    observed_h: float = 0.0
    quiet_alarms: int = 0
    quiet_h: float = 0.0
    missed: list[str] = field(default_factory=list)

    def add(self, other: "Score") -> "Score":
        return Score(
            self.events + other.events,
            self.found + other.found,
            self.opened_inside + other.opened_inside,
            self.delays_s + other.delays_s,
            self.false_alarms + other.false_alarms,
            self.observed_h + other.observed_h,
            self.quiet_alarms + other.quiet_alarms,
            self.quiet_h + other.quiet_h,
            self.missed + other.missed,
        )

    @property
    def recall(self) -> float:
        return self.found / self.events if self.events else float("nan")

    @property
    def median_delay_s(self) -> float:
        return float(np.median(self.delays_s)) if self.delays_s else float("nan")

    @property
    def false_per_h(self) -> float:
        return self.false_alarms / self.observed_h if self.observed_h else float("nan")

    @property
    def quiet_per_h(self) -> float:
        return self.quiet_alarms / self.quiet_h if self.quiet_h else float("nan")


def score(
    records: list[Rec],
    events: list[Event],
    quiet: list[tuple[int, int]],
    ticks: np.ndarray,
    step_ms: int,
) -> Score:
    """`ticks` are the times with data in the evaluated span; observed time
    is counted from them, so gaps (host asleep) count as nothing."""
    s = Score(events=len(events))
    for e in events:
        hits = [r.start for r in records if r.start < e.end and r.end > e.start and _expected(r, e)]
        if hits:
            s.found += 1
            s.delays_s.append(max(0, min(hits) - e.start) / 1000)
            s.opened_inside += any(h >= e.start for h in hits)
        else:
            s.missed.append(e.name)
    in_event = _inside(ticks, [(e.start, e.end) for e in events])
    in_quiet = _inside(ticks, quiet)
    s.observed_h = float((~in_event).sum()) * step_ms / 3.6e6
    s.quiet_h = float(in_quiet.sum()) * step_ms / 3.6e6
    starts = np.array([r.start for r in records], dtype=np.int64)
    s.false_alarms = int((~_inside(starts, [(e.start, e.end) for e in events])).sum())
    s.quiet_alarms = int(_inside(starts, quiet).sum())
    return s


def _expected(r: Rec, e: Event) -> bool:
    # A phase without expected signals (a fault whose symptoms are unknown)
    # is found by any record.
    return not e.expected or r.signal in e.expected


def _inside(ts: np.ndarray, intervals: list[tuple[int, int]]) -> np.ndarray:
    mask = np.zeros(ts.shape, dtype=bool)
    for a, b in intervals:
        mask |= (ts >= a) & (ts < b)
    return mask


def table(rows: list[tuple[str, Score]], unit_s: float = 1.0, unit: str = "s") -> str:
    """A Markdown table of scores, one row per variant."""
    lines = [
        f"| variant | phases found | opened inside | recall | median delay, {unit} | false alarms / h"
        " | on quiet / h |",
        "|---|---|---|---|---|---|---|",
    ]
    for name, s in rows:
        lines.append(
            f"| {name} | {s.found} / {s.events} | {s.opened_inside} | {_f(s.recall, 2)}"
            f" | {_f(s.median_delay_s / unit_s, 1)}"
            f" | {_f(s.false_per_h, 2)} | {_f(s.quiet_per_h, 2)} |"
        )
    return "\n".join(lines)


def _f(x: float, digits: int) -> str:
    return "-" if x != x else f"{x:.{digits}f}"
