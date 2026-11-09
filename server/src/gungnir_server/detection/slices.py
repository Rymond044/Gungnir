"""Who is behind a record (ADR 0057): the commands that used the record's
resource while it lasted, read from the process snapshots.

A record on a control group gets the processes inside the group; a record
on the host gets every process. Processes are folded into commands by their
process group: a build is `make` with its compilers, the next build another
group. Each command carries the average CPU it used over the snapshots of
the record and the most resident memory it held in one snapshot.
"""

from collections import defaultdict
from datetime import datetime, timedelta

import psycopg
from psycopg.types.json import Jsonb

# A record's commands are judged over its first half hour: who started it
# is known by then, and a record open for hours is not read again.
SPAN = timedelta(minutes=30)
# Commands kept per record.
TOP = 5
# Commands named under a process group.
NAMES = 3

# What orders the commands of a record, by the record's signal.
_ORDER = (
    (("cpu.", "cgroup.cpu_"), "cores"),
    (("mem.", "swap.", "cgroup.mem_"), "rss_bytes"),
)


def order_of(signal: str) -> str | None:
    """`cores` or `rss_bytes` for records of CPU and memory, else None."""
    return next((key for prefixes, key in _ORDER if signal.startswith(prefixes)), None)


def attach(conn: psycopg.Connection, host_id: int, ids: list[int], until: datetime) -> None:
    """Writes the commands of each record in `ids` still in its first half hour."""
    rows = conn.execute(
        "SELECT a.id, s.signal, s.entity, a.started_at, coalesce(a.ended_at, %s)"
        " FROM anomalies a JOIN series s ON s.id = a.series_id"
        " WHERE a.id = ANY(%s) AND a.started_at > %s",
        (until, ids, until - SPAN),
    ).fetchall()
    for rid, signal, entity, start, end in rows:
        key = order_of(signal)
        if key is None:
            continue
        found = commands(conn, host_id, entity, start, min(end, start + SPAN), key)
        conn.execute("UPDATE anomalies SET processes = %s WHERE id = %s", (Jsonb(found), rid))


def commands(
    conn: psycopg.Connection, host_id: int, entity: str, start: datetime, end: datetime, key: str
) -> list[dict]:
    """The top commands within `entity` (a group, or `host` for all) over [start, end]."""
    within = "" if entity == "host" else " AND (cgroup = %(g)s OR cgroup LIKE %(below)s)"
    rows = conn.execute(
        "SELECT ts, pid, pgid, comm, cpu_cores, rss_bytes, unit FROM process_samples"
        " WHERE host_id = %(h)s AND ts >= %(a)s AND ts <= %(b)s" + within,
        {"h": host_id, "a": start, "b": end, "g": entity, "below": _like(entity) + "/%"},
    ).fetchall()
    snapshots = conn.execute(
        "SELECT count(DISTINCT ts) FROM process_samples WHERE host_id = %s AND ts >= %s AND ts <= %s",
        (host_id, start, end),
    ).fetchone()[0]
    return fold(rows, snapshots, key)


def fold(rows, snapshots: int, key: str) -> list[dict]:
    """Rows of (ts, pid, pgid, comm, cores, rss, unit) folded into commands."""
    if not rows or not snapshots:
        return []
    groups: dict[tuple, dict] = {}
    rss_at: dict[tuple, dict] = defaultdict(lambda: defaultdict(int))
    for ts, pid, pgid, comm, cores, rss, unit in rows:
        # Kernel threads share group 0, and older agents sent none.
        g = ("group", pgid) if pgid else ("comm", comm)
        if g not in groups:
            groups[g] = {
                "pgid": pgid or None,
                "leader": None,
                "pids": set(),
                "names": {},
                "cpu": 0.0,
                "unit": unit,
            }
        c = groups[g]
        c["pids"].add(pid)
        c["cpu"] += cores
        names = c["names"].setdefault(comm, [set(), 0.0])
        names[0].add(pid)
        names[1] += cores
        if pgid and pid == pgid:
            c["leader"] = comm
        rss_at[g][ts] += rss
    out = []
    for g, c in groups.items():
        names = sorted(c["names"].items(), key=lambda kv: (-kv[1][1], -len(kv[1][0]), kv[0]))
        out.append(
            {
                "pgid": c["pgid"],
                "leader": c["leader"],
                "names": [[name, len(pids)] for name, (pids, _) in names[:NAMES]],
                "pids": len(c["pids"]),
                "cores": round(c["cpu"] / snapshots, 3),
                "rss_bytes": max(rss_at[g].values()),
                "unit": c["unit"],
            }
        )
    out.sort(key=lambda c: (-c[key], c["names"][0][0]))
    return [c for c in out if c[key] > 0][:TOP]


def _like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
