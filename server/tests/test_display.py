from datetime import UTC, datetime, timedelta

import pytest

from gungnir_core.display import format_value, render_status
from gungnir_core.hosts import HostSummary
from gungnir_core.telemetry import SeriesInfo, SourceState


@pytest.mark.parametrize(
    ("signal", "value", "text"),
    [
        ("cpu.iowait_ratio", 0.0234, "2.3 %"),
        ("disk.write_bytes_per_s", 5 * 1024 * 1024, "5.0 MiB/s"),
        ("mem.available_bytes", 8.5 * 1024**3, "8.5 GiB"),
        ("swap.used_bytes", 512, "512 B"),
        ("cpu.context_switches_per_s", 3952.4, "3952.4 /s"),
        ("disk.read_await_ms", 0.904, "0.90 ms"),
        ("cpu.runnable_tasks", 3.0, "3"),
        ("disk.queue_depth", 0.615, "0.61"),
    ],
)
def test_values_are_shown_in_their_units(signal, value, text):
    assert format_value(signal, value) == text


def test_status_marks_fallbacks_and_stale_values():
    now = datetime(2026, 10, 28, 16, 0, tzinfo=UTC)
    host = HostSummary("laptop", now, now - timedelta(seconds=4), "0.1.0", now - timedelta(hours=1), 10800, 0)
    sources = [
        SourceState("cpu", "preferred", "/proc/stat", None, now),
        SourceState("memory", "fallback", "sysinfo(2)", "/proc/meminfo: gone", now),
    ]
    series = [
        SeriesInfo("cpu.idle_ratio", "host", now - timedelta(seconds=4), 0.98),
        SeriesInfo("disk.read_await_ms", "sda", now - timedelta(minutes=10), 1.5),
    ]
    text = render_status(host, sources, series, now)
    assert "started 2026-10-28 18:00:00 +0300" in text
    assert "last contact 4 s ago" in text
    assert "fallback   sysinfo(2)  (/proc/meminfo: gone)" in text
    assert "98.0 %" in text
    assert "(10 min ago)" in text
