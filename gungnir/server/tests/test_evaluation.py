import numpy as np

from gungnir_core.detection.evaluation import Event, Rec, score, table

MIN = 60_000


def test_events_are_found_by_overlapping_records_of_expected_signals():
    events = [
        Event(10 * MIN, 12 * MIN, frozenset({"cpu.user_ratio"}), "cpu"),
        Event(30 * MIN, 32 * MIN, frozenset({"disk.write_bytes_per_s"}), "disk"),
        Event(50 * MIN, 52 * MIN, frozenset(), "any"),
    ]
    records = [
        Rec(10 * MIN + 15_000, 11 * MIN, "cpu.user_ratio"),
        # Overlaps but names another signal: not found.
        Rec(30 * MIN, 31 * MIN, "cpu.user_ratio"),
        # Already open when the phase starts: found, but not opened inside.
        Rec(45 * MIN, 51 * MIN, "mem.used_ratio"),
        # Outside every phase: a false alarm.
        Rec(70 * MIN, 71 * MIN, "cpu.user_ratio"),
    ]
    ticks = np.arange(0, 120 * MIN, 5000, dtype=np.int64)
    s = score(records, events, [(0, 5 * MIN)], ticks, 5000)
    assert (s.found, s.opened_inside, s.missed) == (2, 1, ["disk"])
    assert s.delays_s == [15.0, 0.0]
    # Records starting outside every phase: those at 45 and 70 min.
    assert s.false_alarms == 2
    assert abs(s.observed_h - 114 / 60) < 1e-9
    assert s.quiet_alarms == 0 and abs(s.quiet_h - 5 / 60) < 1e-9
    assert "| x | 2 / 3 | 1 | 0.67 |" in table([("x", s)])
