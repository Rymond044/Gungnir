"""Plain-text rendering of a host's state for the command line."""

from datetime import UTC, datetime, timedelta, timezone

from gungnir_core.hosts import HostSummary
from gungnir_core.telemetry import FactRow, ProcessRow, SeriesInfo, SourceState

# A value older than this is marked with its age; the agent samples every 5 s.
STALE_SECONDS = 60
SHOWN_PROCESSES = 8
# Control groups are many and sent sparsely, so they are listed on request.
GROUP_PREFIX = "cgroup."


def format_value(signal: str, value: float) -> str:
    """Formats a value by the unit suffix of its signal name."""
    if signal.endswith("_ratio"):
        return f"{value * 100:.1f} %"
    if signal.endswith("_bytes_per_s"):
        return f"{format_bytes(value)}/s"
    if signal.endswith("_bytes"):
        return format_bytes(value)
    if signal.endswith("_per_s"):
        return f"{value:.1f} /s"
    if signal.endswith("_ms"):
        return f"{value:.2f} ms"
    return f"{value:.0f}" if value == int(value) else f"{value:.2f}"


def format_bytes(value: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if abs(value) < 1024:
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TiB"


def format_age(seconds: float) -> str:
    if seconds < 90:
        return f"{seconds:.0f} s ago"
    if seconds < 90 * 60:
        return f"{seconds / 60:.0f} min ago"
    if seconds < 48 * 3600:
        return f"{seconds / 3600:.0f} h ago"
    return f"{seconds / 86400:.0f} d ago"


def host_zone(host: HostSummary) -> timezone:
    """The host's local time zone as a fixed offset; UTC until its agent reports one."""
    if host.utc_offset_s is None:
        return UTC
    return timezone(timedelta(seconds=host.utc_offset_s))


def render_status(
    host: HostSummary,
    sources: list[SourceState],
    series: list[SeriesInfo],
    now: datetime,
    facts: list[FactRow] = (),
    processes: list[ProcessRow] = (),
    show_groups: bool = False,
) -> str:
    lines = []
    if host.last_seen_at is None:
        lines.append(f"{host.name}: no data received yet")
        return "\n".join(lines)

    started = host.agent_started_at.astimezone(host_zone(host)).strftime("%Y-%m-%d %H:%M:%S %z")
    zone = "its local time" if host.utc_offset_s is not None else "UTC, the agent has not reported its offset"
    lines.append(f"Host {host.name} (times in {zone})")
    lines.append(f"  agent {host.agent_version}, started {started}")
    lines.append(f"  last contact {format_age((now - host.last_seen_at).total_seconds())}")
    lines.append(f"  rows lost to buffer and spool overflow: {host.dropped_rows}")

    lines.append("")
    lines.append("Signal sources")
    for s in sources:
        line = f"  {s.module:<13} {s.state:<10} {s.origin or '-'}"
        if s.state != "preferred" and s.reason:
            line += f"  ({s.reason})"
        lines.append(line)

    zone = host_zone(host)
    if facts:
        lines.append("")
        lines.append("Facts")
        for f in facts:
            age = (now - f.ts).total_seconds()
            lines.append(f"  {f.name:<16} {f.entity:<10} {f.value}  (since {format_age(age)})")

    if processes:
        lines.append("")
        lines.append(f"Top processes at {processes[0].ts.astimezone(zone).strftime('%H:%M:%S')}")
        lines.append(f"  {'pid':>7} {'command':<16} st {'cores':>6} {'memory':>10}  unit")
        for p in processes[:SHOWN_PROCESSES]:
            lines.append(
                f"  {p.pid:>7} {p.comm[:16]:<16} {p.state:<2} {p.cpu_cores:>6.2f}"
                f" {format_bytes(p.rss_bytes):>10}  {p.unit or '-'}"
            )

    groups = [s for s in series if s.signal.startswith(GROUP_PREFIX)]
    if not show_groups:
        series = [s for s in series if not s.signal.startswith(GROUP_PREFIX)]

    lines.append("")
    lines.append("Latest values")
    width = max((len(s.signal) for s in series), default=0)
    resource = None
    for s in series:
        if s.last_ts is None:
            continue
        current = s.signal.split(".", 1)[0]
        if resource is not None and current != resource:
            lines.append("")
        resource = current
        line = f"  {s.signal:<{width}}  {s.entity:<10} {format_value(s.signal, s.last_value):>12}"
        age = (now - s.last_ts).total_seconds()
        if age > STALE_SECONDS:
            line += f"  ({format_age(age)})"
        lines.append(line)
    if groups and not show_groups:
        entities = len({s.entity for s in groups})
        lines.append("")
        lines.append(
            f"  control groups: {len(groups)} series of {entities} groups, sparse (--groups lists them)"
        )
    return "\n".join(lines)
