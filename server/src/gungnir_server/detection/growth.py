"""Growth of a control group's memory over hours: the slow leak that the
robust branch misses, since groups have wide floors and no EWMA (ADR 0032;
docs/research/memory.md MG3).

A process's resident memory may only grow while its caches fill (Gregg p.
320), so the level says little; monitoring memory over time shows a leak
(Gregg p. 326). The detector keeps the minimum of each hour per group in
its own state and judges each tick against the completed hours: the last
`growth_hours` minima each rose by at least the spread floor of group
memory, by at least `growth_min_share` of the first and `growth_min_bytes`
in total, and no hour brought more than `growth_max_step_share` of the
rise (ADR 0058): a leak is steady, a program that loaded a file once is a
step. The hours must follow each other: those on both sides of a sleep say
nothing about a rise. Minima ignore the swings of caches and short peaks. Slices are
skipped: they only sum their children.
"""

from itertools import pairwise

import numpy as np

from gungnir_server.detection.detectors import Context
from gungnir_server.detection.records import Evaluation, RecordSpec
from gungnir_server.params import by_pattern

SIGNAL = "cgroup.mem_bytes"
HOUR_MS = 3_600_000


class Growth:
    name = "growth"

    def evaluate(self, ctx: Context) -> list[Evaluation]:
        p = ctx.params
        f = ctx.frame
        ticks = ctx.ticks
        hours = (ticks // HOUR_MS) * HOUR_MS
        state = ctx.state.setdefault("minima", {})
        keep = p.growth_hours + 2
        seen = set()
        out = []
        for i in f.rows_of(SIGNAL):
            meta = f.series[i]
            # A slice only sums its children, whose own records name the grower.
            if meta.entity.endswith(".slice"):
                continue
            key = str(meta.id)
            seen.add(key)
            x = f.values[i, ctx.start :]
            minima: dict[str, float] = state.setdefault(key, {})
            flags = np.zeros(ticks.size, dtype=bool)
            scores = np.full(ticks.size, np.nan)
            bases: list[dict | None] = [None] * ticks.size
            # The verdict depends only on completed hours, so it changes on the hour.
            judged_hour, verdict = None, None
            for j in range(ticks.size):
                if np.isnan(x[j]):
                    continue
                hour = str(int(hours[j]))
                minima[hour] = min(minima.get(hour, x[j]), float(x[j]))
                if hours[j] != judged_hour:
                    done = sorted((int(h), v) for h, v in minima.items() if int(h) < hours[j])
                    last = done[-(p.growth_hours + 1) :]
                    # Hours on both sides of a sleep are not a rise in a row.
                    following = all(b - a == HOUR_MS for (a, _), (b, _) in pairwise(last))
                    verdict = judge([v for _, v in last], p) if following else None
                    judged_hour = hours[j]
                if verdict is not None:
                    flags[j], scores[j], bases[j] = verdict
            for h in sorted(minima, key=int)[:-keep]:
                del minima[h]
            out.append(
                Evaluation(
                    f"growth|{meta.id}",
                    self.name,
                    "anomaly",
                    ticks,
                    flags,
                    scores,
                    lambda j, m=meta, x=x, b=bases: [RecordSpec(m.id, "up", b[j] or {}, float(x[j]))],
                )
            )
        for key in [k for k in state if k not in seen]:
            del state[key]
        return out


def judge(minima: list[float], p) -> tuple[bool, float, dict] | None:
    """Verdict on consecutive hourly minima, oldest first: None until there
    are enough hours. The score is the rise against the smallest rise that
    counts."""
    if len(minima) < p.growth_hours + 1:
        return None
    steps = [b - a for a, b in pairwise(minima)]
    rise = minima[-1] - minima[0]
    needed = max(p.growth_min_bytes, p.growth_min_share * minima[0])
    step = by_pattern(p.floors, SIGNAL) or 0.0
    steady = min(steps) >= step and max(steps) <= p.growth_max_step_share * rise
    basis = {
        "basis": "growth",
        "hourly_minima": minima,
        "rise": rise,
        "needed": needed,
        "min_step": step,
        "max_step_share": p.growth_max_step_share,
    }
    return steady and rise >= needed, rise / needed, basis
