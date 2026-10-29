"""Workload class of a host from its process snapshots and group I/O (ADR 0023).

Each snapshot gets a raw class from the rules; a new class is accepted only
after it held for `class_accept_samples` snapshots in a row, and is then
backdated to its first one, so baselines get the first seconds in the right
class. The accepted class is stored as the fact `workload.class`.

The classifier sits behind `Classifier`; S4 moves the rules into the
knowledge base without changing the runner.
"""

import json
from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from fnmatch import fnmatchcase
from typing import Protocol

import psycopg

from gungnir_core import knowledge
from gungnir_core.hosts import Host
from gungnir_core.labels import excluded, phases_of
from gungnir_core.params import Params

CLASSES = ("idle", "interactive", "build", "backup", "maintenance")
RULED = ("build", "backup", "maintenance")
FACT = "workload.class"
RULE_TABLE = "workload_rule"
GLOBAL_FILE = "workload_classes.toml"
# Operator rules win over the shipped ones.
OPERATOR_PRIORITY = 100
# Classify at most this much per call, so a long backlog commits in steps.
CHUNK = timedelta(hours=1)


@dataclass(frozen=True)
class Rule:
    cls: str
    priority: int
    command: tuple[str, ...] = ()
    unit: tuple[str, ...] = ()
    group: tuple[str, ...] = ()
    source: str = ""

    def matches_process(self, comm: str, unit: str | None, cgroup: str | None) -> bool:
        return (
            _any(self.command, comm)
            or (unit is not None and _any(self.unit, unit))
            or (cgroup is not None and _any(self.group, cgroup))
        )

    def matches_group(self, path: str) -> bool:
        return _any(self.unit, path.rsplit("/", 1)[-1]) or _any(self.group, path)


def _any(patterns: tuple[str, ...], name: str) -> bool:
    return any(fnmatchcase(name, p) for p in patterns)


def rules_from(items: list[dict], where: str) -> list[Rule]:
    rules = []
    for i, item in enumerate(items, 1):
        cls = item.get("class")
        if cls not in RULED:
            raise knowledge.KnowledgeError(f"{where} #{i}: class must be one of {', '.join(RULED)}")
        matchers = {k: tuple(item.get(k, ())) for k in ("command", "unit", "group")}
        if not any(matchers.values()):
            raise knowledge.KnowledgeError(f"{where} #{i}: no command, unit or group to match")
        rules.append(Rule(cls, int(item.get("priority", 0)), **matchers, source=item["provenance"]["source"]))
    return rules


def load_rules(host: str | None = None) -> list[Rule]:
    path = knowledge.directory() / GLOBAL_FILE
    rules = rules_from(knowledge.entries(path, RULE_TABLE), str(path))
    if host:
        local = knowledge.local_file(host)
        rules += rules_from(knowledge.entries(local, RULE_TABLE), str(local))
    return sorted(rules, key=lambda r: -r.priority)


@dataclass(frozen=True)
class Proc:
    comm: str
    cpu_cores: float
    unit: str | None
    cgroup: str | None


@dataclass
class Snapshot:
    ts: datetime
    processes: list[Proc] = field(default_factory=list)
    # Bytes per second read plus written, per control group.
    group_io: dict[str, float] = field(default_factory=dict)
    host_io: float = 0.0
    # Share of all CPUs busy (neither idle nor waiting for I/O).
    host_cpu: float = 0.0


def io_share(io: float, snap: Snapshot, params: Params) -> float:
    """A group's share of host disk I/O. On a nearly idle disk any trickle
    is a large share, so the share counts only above the idle level."""
    if snap.host_io < params.idle_io_bytes_per_s:
        return 0.0
    return io / snap.host_io


class Classifier(Protocol):
    def classify(self, snap: Snapshot) -> str: ...


class RuleClassifier:
    def __init__(self, rules: list[Rule], params: Params) -> None:
        self.rules = sorted(rules, key=lambda r: -r.priority)
        self.params = params

    def counting(self, snap: Snapshot) -> list[Rule]:
        """Rules whose processes or groups take a noticeable share."""
        p = self.params
        found = []
        for rule in self.rules:
            cores = sum(x.cpu_cores for x in snap.processes if rule.matches_process(x.comm, x.unit, x.cgroup))
            io = sum(v for g, v in snap.group_io.items() if rule.matches_group(g))
            if cores >= p.noticeable_cores or io_share(io, snap, p) >= p.noticeable_io_share:
                found.append(rule)
        return found

    def classify(self, snap: Snapshot) -> str:
        counting = self.counting(snap)
        if counting:
            return counting[0].cls
        if snap.host_cpu < self.params.idle_cpu and snap.host_io < self.params.idle_io_bytes_per_s:
            return "idle"
        return "interactive"


@dataclass(frozen=True)
class State:
    at: datetime | None = None
    current: str | None = None
    candidate: str | None = None
    candidate_since: datetime | None = None
    candidate_n: int = 0
    last_ts: datetime | None = None

    @property
    def settled(self) -> datetime | None:
        """Classes before this time will not change any more."""
        if self.candidate_since is not None and self.at is not None:
            return min(self.at, self.candidate_since)
        return self.at


def accept(state: State, ts: datetime, cls: str, params: Params) -> tuple[State, tuple[str, datetime] | None]:
    """Feeds one raw class; returns the new state and an accepted (class, since)."""
    # After a gap in the snapshots (agent stopped, host asleep) the run of
    # equal classes starts over.
    gap = state.last_ts is not None and ts - state.last_ts > timedelta(seconds=3 * params.step_s)
    state = replace(state, at=ts, last_ts=ts)
    if cls == state.current:
        return replace(state, candidate=None, candidate_since=None, candidate_n=0), None
    if cls == state.candidate and not gap:
        state = replace(state, candidate_n=state.candidate_n + 1)
    else:
        state = replace(state, candidate=cls, candidate_since=ts, candidate_n=1)
    if state.candidate_n >= params.class_accept_samples:
        since = state.candidate_since
        return replace(state, current=cls, candidate=None, candidate_since=None, candidate_n=0), (cls, since)
    return state, None


SNAPSHOT_SIGNALS = (
    "cgroup.read_bytes_per_s",
    "cgroup.write_bytes_per_s",
    "disk.read_bytes_per_s",
    "disk.write_bytes_per_s",
    "cpu.idle_ratio",
    "cpu.iowait_ratio",
)


def snapshots(
    conn: psycopg.Connection, host: Host, after: datetime | None, until: datetime
) -> list[Snapshot]:
    """Snapshots with times in (after, until], oldest first."""
    rows = conn.execute(
        "SELECT ts, comm, cpu_cores, unit, cgroup FROM process_samples WHERE host_id = %s"
        " AND (%s::timestamptz IS NULL OR ts > %s) AND ts <= %s ORDER BY ts",
        (host.id, after, after, until),
    ).fetchall()
    snaps: dict[datetime, Snapshot] = {}
    for ts, comm, cores, unit, cgroup in rows:
        snaps.setdefault(ts, Snapshot(ts)).processes.append(Proc(comm, cores, unit, cgroup))
    if not snaps:
        return []
    first = min(snaps)
    samples = conn.execute(
        "SELECT r.ts, s.signal, s.entity, r.value FROM samples r JOIN series s ON s.id = r.series_id"
        " WHERE s.host_id = %s AND s.signal = ANY(%s) AND r.ts >= %s AND r.ts <= %s",
        (host.id, list(SNAPSHOT_SIGNALS), first, until),
    ).fetchall()
    idle: dict[datetime, float] = defaultdict(float)
    for ts, signal, entity, value in samples:
        snap = snaps.get(ts)
        if snap is None:
            continue
        if signal.startswith("cgroup."):
            # Sparse: a group missing at a tick did no I/O (ADR 0017).
            snap.group_io[entity] = snap.group_io.get(entity, 0.0) + value
        elif signal.startswith("disk."):
            snap.host_io += value
        else:
            idle[ts] += value
    for ts, snap in snaps.items():
        snap.host_cpu = max(0.0, 1.0 - idle[ts]) if ts in idle else 1.0
    return [snaps[ts] for ts in sorted(snaps)]


def load_state(conn: psycopg.Connection, host: Host) -> State:
    row = conn.execute(
        "SELECT at, current, candidate, candidate_since, candidate_n FROM workload_state WHERE host_id = %s",
        (host.id,),
    ).fetchone()
    return State(*row, last_ts=row[0]) if row else State()


def save_state(conn: psycopg.Connection, host: Host, state: State) -> None:
    conn.execute(
        "INSERT INTO workload_state (host_id, at, current, candidate, candidate_since, candidate_n)"
        " VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (host_id) DO UPDATE SET at = EXCLUDED.at,"
        " current = EXCLUDED.current, candidate = EXCLUDED.candidate,"
        " candidate_since = EXCLUDED.candidate_since, candidate_n = EXCLUDED.candidate_n",
        (host.id, state.at, state.current, state.candidate, state.candidate_since, state.candidate_n),
    )


def run(
    conn: psycopg.Connection, host: Host, classifier: Classifier, params: Params, until: datetime
) -> tuple[State, int]:
    """Classifies the snapshots not seen yet, up to `until`. On the first run
    this starts at the oldest stored snapshot, so collected data get classes.
    Returns the state and the number of classes accepted."""
    state = load_state(conn, host)
    accepted = 0
    while True:
        start = state.at
        if start is None:
            (start,) = conn.execute(
                "SELECT min(ts) - interval '1 ms' FROM process_samples WHERE host_id = %s", (host.id,)
            ).fetchone()
            if start is None:
                return state, accepted
        end = min(until, start + CHUNK)
        snaps = snapshots(conn, host, start, end)
        with conn.transaction():
            for snap in snaps:
                state, new = accept(state, snap.ts, classifier.classify(snap), params)
                if new:
                    conn.execute(
                        "INSERT INTO facts (host_id, name, entity, ts, value) VALUES (%s, %s, 'host', %s, %s)"
                        " ON CONFLICT DO NOTHING",
                        (host.id, FACT, new[1], new[0]),
                    )
                    accepted += 1
            if not snaps:
                state = replace(state, at=end)
            save_state(conn, host, state)
        if end >= until:
            return state, accepted


def settled(conn: psycopg.Connection, host: Host) -> datetime | None:
    return load_state(conn, host).settled


@dataclass(frozen=True)
class Unmatched:
    kind: str
    name: str
    noticeable: int
    peak: float
    example: str | None


def unmatched(
    conn: psycopg.Connection,
    host: Host,
    classifier: RuleClassifier,
    params: Params,
    since: datetime,
    until: datetime,
) -> list[Unmatched]:
    """Processes and groups that took a noticeable share outside labeled
    phases and that no rule covers: candidates for an operator rule."""
    skip = excluded(phases_of(conn, host, since, until), params.label_tail)
    rules = classifier.rules
    found: dict[tuple[str, str], list] = {}
    for snap in snapshots(conn, host, since, until):
        if any(a <= snap.ts < b for a, b in skip):
            continue
        by_command: dict[str, list] = defaultdict(lambda: [0.0, None])
        for x in snap.processes:
            if not any(r.matches_process(x.comm, x.unit, x.cgroup) for r in rules):
                by_command[x.comm][0] += x.cpu_cores
                by_command[x.comm][1] = x.unit
        for comm, (cores, unit) in by_command.items():
            if cores >= params.noticeable_cores:
                entry = found.setdefault(("command", comm), [0, 0.0, unit])
                entry[0] += 1
                entry[1] = max(entry[1], cores)
        for group, io in snap.group_io.items():
            # Top-level slices only sum their children.
            if "/" not in group or any(r.matches_group(group) for r in rules):
                continue
            share = io_share(io, snap, params)
            if share >= params.noticeable_io_share:
                entry = found.setdefault(("unit", group.rsplit("/", 1)[-1]), [0, 0.0, group])
                entry[0] += 1
                entry[1] = max(entry[1], share)
    items = [Unmatched(kind, name, n, peak, ex) for (kind, name), (n, peak, ex) in found.items()]
    return sorted(items, key=lambda u: -u.noticeable)


def assign(host: str, cls: str, command: str | None, unit: str | None, today: date) -> str:
    """Appends an operator rule to the host-local file; returns its path."""
    if cls not in RULED:
        raise ValueError(f"class must be one of {', '.join(RULED)}")
    if bool(command) == bool(unit):
        raise ValueError("give exactly one of a command or a unit")
    path = knowledge.local_file(host)
    path.parent.mkdir(parents=True, exist_ok=True)
    matcher = f"command = [{json.dumps(command)}]" if command else f"unit = [{json.dumps(unit)}]"
    source = json.dumps(f"operator, {host}, {today.isoformat()}")
    entry = (
        f"\n[[{RULE_TABLE}]]\nclass = {json.dumps(cls)}\npriority = {OPERATOR_PRIORITY}\n{matcher}\n"
        f'provenance = {{ source = {source}, grade = "operator" }}\n'
    )
    with open(path, "a") as f:
        f.write(entry)
    # Refuse to leave a file behind that would not load.
    load_rules(host)
    return str(path)
