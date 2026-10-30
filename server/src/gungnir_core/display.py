"""Plain-text rendering of a host's state for the command line."""

from datetime import UTC, datetime, timedelta, timezone
from fnmatch import fnmatchcase

from gungnir_core.evidence import HOST, Event, Item, Record, Value
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


def stamp(ts: datetime, zone: timezone) -> str:
    return ts.astimezone(zone).strftime("%Y-%m-%d %H:%M:%S")


def render_baselines(series: list, book, pattern: str | None) -> str:
    """Current baselines of a host: median and spread per series and class,
    with the power correction factors that apply."""
    by_id = {s.id: s for s in series}
    rows = []
    for (sid, cls), b in book.baselines.items():
        s = by_id.get(sid)
        if s is None or (pattern and not fnmatchcase(s.signal, pattern)):
            continue
        rows.append((s.signal, s.entity, cls, b))
    need, days = book.params.baseline_min_hours, book.params.baseline_days
    with_baseline = {cls for _sid, cls in book.baselines}
    waiting = [
        f"{cls} {hours:.1f} of {need:g} h"
        for cls, hours in sorted(book.collected.items())
        if cls not in with_baseline
    ]
    progress = f"waiting for samples in the last {days} days: " + ", ".join(waiting)
    if not rows:
        text = f"no baselines yet: a class needs {need:g} h of samples in the last {days} days"
        return text + ("\n" + progress if waiting else "")
    rows.sort(key=lambda r: (r[0], r[1], r[2]))
    lines = [
        f"Baselines as of {book.version:%Y-%m-%d %H:00} UTC (class, median, MAD, samples, entity, power)"
    ]
    if waiting:
        lines.append(progress)
    for signal, entity, cls, b in rows:
        factors = " ".join(f"{p} x{f:.2f}" for p, f in sorted(b.factors.items()) if abs(f - 1) > 0.005)
        lines.append(
            f"  {signal:<32} {cls:<12} {format_value(signal, b.median):>12}"
            f" {format_value(signal, b.mad):>12} {b.samples:>8}  {entity}  {factors}".rstrip()
        )
    return "\n".join(lines)


def format_span(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds} s"
    if seconds < 3600:
        return f"{seconds // 60} min {seconds % 60:02d} s"
    return f"{seconds // 3600} h {seconds % 3600 // 60:02d} min"


def against(signal: str, basis: dict) -> str:
    """What a record was judged against, in words."""
    kind = basis.get("basis")
    if kind == "baseline":
        text = (
            f"baseline of {basis.get('class')}: median {format_value(signal, basis['median'])},"
            f" MAD {format_value(signal, basis['mad'])}"
        )
        if abs(basis.get("factor", 1.0) - 1) > 0.005:
            text += f", power factor {basis['factor']:.2f} ({basis.get('power')})"
        return text
    if kind == "calibration":
        return (
            f"cold start: {basis['share']:.0%} of measured capacity {format_value(signal, basis['capacity'])}"
        )
    if kind == "group cold start":
        return f"cold start of a group: above {format_value(signal, basis['limit'])} ({basis['of']})"
    if kind == "threshold":
        if basis.get("above") is not None:
            return f"limit above {format_value(signal, basis['above'])}"
        return f"limit below {format_value(signal, basis['below'])}"
    if kind == "growth":
        hours = len(basis.get("hourly_minima", [])) - 1
        return (
            f"growth: hourly minima rose {hours} hours in a row by {format_value(signal, basis['rise'])}"
            f" (at least {format_value(signal, basis['needed'])})"
        )
    if kind == "forest":
        return f"forest of {basis.get('class')}: feature {basis.get('feature')}, z {basis.get('z', 0):.1f}"
    return "-"


def render_records(rows: list, now: datetime, zone: timezone, title: str) -> str:
    if not rows:
        return f"{title}: no records"
    still_open = sum(r.ended_at is None for r in rows)
    noun = "record" if len(rows) == 1 else "records"
    lines = [f"{title}: {len(rows)} {noun}, {still_open} open (newest first)", ""]
    for r in rows:
        start = r.started_at.astimezone(zone)
        fmt = "%H:%M:%S" if start.date() == now.astimezone(zone).date() else "%m-%d %H:%M:%S"
        end = r.ended_at.astimezone(zone).strftime("%H:%M:%S") if r.ended_at else "open"
        length = format_span(((r.ended_at or now) - r.started_at).total_seconds())
        what = r.detector + (f" {r.level}" if r.level else "")
        series = r.signal if r.entity == "host" else f"{r.signal} {r.entity.rsplit('/', 1)[-1]}"
        # A limit below which the value fell to zero gives an infinite ratio.
        score = f"{r.peak_score:.1f}" if r.peak_score < 1000 else ">999"
        arrow = "up" if r.direction == "up" else "down"
        lines.append(
            f"{start.strftime(fmt)} - {end:<8} {length:>13}  {r.kind:<7} {what:<17}"
            f" {series}  {arrow}, score {score}"
        )
        value = format_value(r.signal, r.value) if r.value is not None else "-"
        context = f"class {r.workload_class or '-'}, {r.power_profile or '-'}"
        episode = f", episode {r.episode}" if r.episode else ""
        lines.append(f"    value {value}; {context}; against {against(r.signal, r.baseline)}{episode}")
        if r.context:
            seen = ", ".join(f"{k} {format_value(k, v)}" for k, v in sorted(r.context.items()))
            lines.append(f"    during it: {seen}")
    return "\n".join(lines)


MARKS = {"matched": "+", "missing": "-", "pending": "?", "unobservable": "."}


def render_diagnosis(d, zone: timezone, title: str) -> str:
    """Ranked hypotheses with their derivation trees, then what nothing
    explains and the advisory; `d` is a reasoner Diagnosis."""

    def when(t: float) -> str:
        return datetime.fromtimestamp(t, zone).strftime("%H:%M:%S")

    def short(entity: str | None) -> str:
        return (entity.rsplit("/", 1)[-1] or entity) if entity else "-"

    def item(i: Item) -> str:
        if isinstance(i, Record):
            end = when(i.end) if i.end is not None else "open"
            where = "" if i.entity == HOST else f" {short(i.entity)}"
            return f"record {i.id}: {i.signal}{where} {i.direction}, {i.detector}, {when(i.start)}-{end}"
        if isinstance(i, Value):
            return f"value {i.signal} {format_value(i.signal, i.value)} on record {i.record or '-'}"
        if isinstance(i, Event):
            return f"event {i.type} at {when(i.at)}: {i.message[:80]}"
        return f"process {i.comm} [{i.pid}] state {i.state} in {short(i.group)} at {when(i.at)}"

    lines = [title, ""]
    if not d.hypotheses:
        lines.append("No hypothesis passes the cut-off.")
    for n, h in enumerate(d.hypotheses, 1):
        bound = ", ".join(
            f"{role} {short(value)}"
            for role, value in (
                ("culprit", h.binding.culprit),
                ("device", h.binding.device),
                ("filesystem", h.binding.filesystem),
            )
            if value
        )
        flag = "  [expected behavior]" if h.expected else ""
        head = f"{n}. {h.cause}  {h.confidence:.2f} (support {h.support:.2f})"
        lines.append("  ".join(x for x in (head, bound, flag.strip()) if x))
        if h.binding.culprit:
            lines.append(f"   culprit: {h.binding.culprit}")
        lines.append(f"   rule: {h.rule.source} [{h.rule.grade}]; windows from {when(h.anchor_at)}")
        for f in h.found:
            m = f.manifestation
            what = " | ".join(m.names) + (f" {m.direction}" if m.direction else "")
            if m.kind == "value":
                what += f" <= {m.at_most:g}" if m.at_most is not None else f" > {m.above:g}"
            window = "anchor" if m.anchor else f"[{m.window[0]}, {m.window[1]}] s"
            harm = ", harm" if m.harm else ""
            seen = item(f.item) if f.item else f.status
            lines.append(f"   {MARKS[f.status]} {m.strength:<14} {what} on {m.role}, {window}{harm}: {seen}")
        lines.append("")
    if d.unexplained:
        lines.append(f"Unexplained ({len(d.unexplained)}):")
        lines += [f"   {item(i)}" for i in d.unexplained]
        lines.append("")
    if d.expected:
        lines.append(f"Explained as expected behavior: records {', '.join(d.expected)}")
        lines.append("")
    for a in d.advisories:
        lines.append(f"Advisory ({a.resource}): {a.text}")
        lines.append(f"   source: {a.source}")
    return "\n".join(lines).rstrip()
