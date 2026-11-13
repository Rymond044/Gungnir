"""What the inference engine sees (slice S5): the records, attached values, events
and process symptoms of an interval, with the facts and the workload
class of each group. Built from a knowledge test case (`cases.evidence_of`)
or from the database (`gather.evidence`); times are seconds, from the
case's start or since the epoch.
"""

from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from functools import cached_property

HOST = "host"


@dataclass(frozen=True)
class Record:
    """An anomaly or impact record of one series."""

    id: str
    signal: str
    entity: str
    kind: str  # anomaly or impact
    detector: str
    direction: str
    start: float
    end: float | None = None
    level: str | None = None
    score: float | None = None
    # The host's workload class when the record opened.
    workload_class: str | None = None


@dataclass(frozen=True)
class Value:
    """A context value detection attached to a record (ADR 0040)."""

    signal: str
    entity: str
    value: float
    at: float
    record: str = ""


@dataclass(frozen=True)
class Event:
    """A kernel or journal event of a type the ontology knows."""

    id: str
    type: str
    at: float
    message: str = ""


@dataclass(frozen=True)
class SeenProcess:
    """A process of the snapshot that matches a process symptom (ADR 0038),
    at the first snapshot it was seen in."""

    symptom: str
    comm: str
    pid: int
    state: str
    # Its control group; None for a kernel thread.
    group: str | None
    at: float


Item = Record | Value | Event | SeenProcess


@dataclass(frozen=True)
class Evidence:
    records: tuple[Record, ...] = ()
    values: tuple[Value, ...] = ()
    events: tuple[Event, ...] = ()
    processes: tuple[SeenProcess, ...] = ()
    facts: dict[str, str] = field(default_factory=dict)
    # The workload class whose rules match a group; groups without one are
    # absent.
    group_class: dict[str, str] = field(default_factory=dict)
    # The interval asked about. Evidence may start earlier, by the longest
    # negative window; only what overlaps the interval can be unexplained.
    # A window that closes after `until` is still pending.
    since: float = float("-inf")
    until: float = float("inf")
    # Signals, event types and process symptoms the host does not provide.
    unobservable: frozenset[str] = frozenset()

    def between(self, kind: str, lo: float, hi: float) -> list["Item"]:
        """Items of a kind whose time lies in [lo, hi], earliest first. A
        week holds thousands of process snapshots; a symptom's window a few."""
        times, items = self._by_time[kind]
        return items[bisect_left(times, lo) : bisect_right(times, hi)]

    @cached_property
    def _by_time(self) -> dict[str, tuple[list[float], list["Item"]]]:
        out = {}
        for kind, items in (
            ("anomaly", self.records),
            ("value", self.values),
            ("event", self.events),
            ("process", self.processes),
        ):
            ordered = sorted(items, key=at)
            out[kind] = ([at(i) for i in ordered], ordered)
        return out


def at(item: Item) -> float:
    return item.start if isinstance(item, Record) else item.at


def inside(item: Item, ev: Evidence) -> bool:
    """The item overlaps the interval asked about."""
    end = item.end if isinstance(item, Record) else at(item)
    return end is None or end >= ev.since


def within(group: str, ancestor: str) -> bool:
    """A group is within itself and within each of its ancestors."""
    return group == ancestor or group.startswith(ancestor + "/")


def related(a: str, b: str) -> bool:
    """One group contains the other."""
    return within(a, b) or within(b, a)
