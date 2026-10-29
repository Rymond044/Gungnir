import numpy as np

from gungnir_core.detection.records import Closed, Evaluation, Opened, RecordSpec, Tracker
from gungnir_core.params import Params

P = Params()


def ev(flags, start_ms=0, scores=None):
    flags = np.array(flags, dtype=bool)
    ticks = start_ms + 5000 * np.arange(flags.size, dtype=np.int64)
    scores = np.where(flags, 5.0, 1.0) if scores is None else np.array(scores, dtype=float)
    return Evaluation(
        "robust.mad|1",
        "robust.mad",
        "anomaly",
        ticks,
        flags,
        scores,
        lambda j: [RecordSpec(1, "up", {}, 0.0)],
    )


def test_three_in_a_row_open_and_three_normal_close():
    t = Tracker(P)
    events = t.feed(ev([0, 1, 1, 1, 1, 0, 1, 0, 0, 0, 0]))
    opened, closed = events
    assert isinstance(opened, Opened) and opened.started_ms == 5000 and opened.index == 1
    # The normal sample at index 5 is followed by a hit, so it does not end
    # the record; the one at index 7 does.
    assert isinstance(closed, Closed) and closed.ended_ms == 35000
    assert closed.token == opened.token
    assert t.tracks == {}


def test_two_hits_open_nothing():
    assert Tracker(P).feed(ev([1, 1, 0, 1, 1, 0])) == []


def test_state_carries_across_steps():
    t = Tracker(P)
    assert t.feed(ev([0, 1, 1])) == []
    t = Tracker.from_json(P, t.to_json())
    [opened] = t.feed(ev([1, 1], start_ms=15000))
    assert opened.started_ms == 5000
    t.set_ids(opened.token, [42])
    t = Tracker.from_json(P, t.to_json())
    [closed] = t.feed(ev([0, 0, 0], start_ms=25000))
    assert closed.ids == [42] and closed.ended_ms == 25000


def test_a_series_that_stops_sending_closes_its_record():
    t = Tracker(P)
    t.feed(ev([1, 1, 1]))
    [closed] = t.feed(ev([0] * P.stale_after, start_ms=15000, scores=[np.nan] * P.stale_after))
    assert closed.ended_ms == 15000


def test_a_key_no_longer_evaluated_expires():
    t = Tracker(P)
    t.feed(ev([1, 1, 1]))
    assert t.expire(set(), 5, 40000) == []
    [closed] = t.expire(set(), 10, 90000)
    assert closed.ended_ms == 15000
