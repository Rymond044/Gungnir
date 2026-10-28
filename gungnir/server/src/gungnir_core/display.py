"""Plain-text rendering of a host's state for the command line."""

from datetime import datetime

from gungnir_core.hosts import HostSummary
from gungnir_core.telemetry import SeriesInfo, SourceState

# A value older than this is marked with its age; the agent samples every 5 s.
STALE_SECONDS = 60


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


def render_status(
    host: HostSummary, sources: list[SourceState], series: list[SeriesInfo], now: datetime
) -> str:
    lines = []
    if host.last_seen_at is None:
        lines.append(f"{host.name}: no data received yet")
        return "\n".join(lines)

    started = host.agent_started_at.astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
    lines.append(f"Host {host.name}")
    lines.append(f"  agent {host.agent_version}, started {started}")
    lines.append(f"  last contact {format_age((now - host.last_seen_at).total_seconds())}")
    lines.append(f"  samples lost to buffer overflow: {host.dropped_samples}")

    lines.append("")
    lines.append("Signal sources")
    for s in sources:
        line = f"  {s.module:<8} {s.state:<10} {s.origin or '-'}"
        if s.state != "preferred" and s.reason:
            line += f"  ({s.reason})"
        lines.append(line)

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
    return "\n".join(lines)
