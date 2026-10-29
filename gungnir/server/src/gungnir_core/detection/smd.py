"""The robust branch and the forest on the Server Machine Dataset (ADR 0027).

The dataset (Su et al., KDD 2019) holds 28 machines, each with 38 metrics
at one-minute steps, normalised to [0, 1]: a train split, a test split with
a label per step, and for each labeled anomaly the metrics that show it.
The detectors run unchanged over its arrays: the baseline of a metric is
the median and MAD of its train split, the forest is trained on the train
split's windows. A labeled anomaly is an event whose expected signals are
its interpretation metrics.
"""

import dataclasses
import re
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from gungnir_core.detection import evaluation, forest
from gungnir_core.detection.detectors import Context, Robust
from gungnir_core.detection.records import Opened, Tracker
from gungnir_core.frame import Frame, SeriesMeta
from gungnir_core.params import Params

STEP_MS = 60_000
CLASS = "smd"
VARIANTS = ("robust", "iforest", "robust+iforest")


def smd_params(base: Params) -> Params:
    # One-minute steps: three samples are three minutes.
    return dataclasses.replace(base, step_s=60.0)


def machines(root: Path) -> list[str]:
    return sorted(p.stem for p in (root / "train").glob("machine-*.txt"))


def name_of(d: int) -> str:
    return f"smd.metric_{d:02d}"


def load(root: Path, machine: str) -> tuple[np.ndarray, np.ndarray, list[evaluation.Event]]:
    train = np.loadtxt(root / "train" / f"{machine}.txt", delimiter=",")
    test = np.loadtxt(root / "test" / f"{machine}.txt", delimiter=",")
    labels = np.loadtxt(root / "test_label" / f"{machine}.txt", delimiter=",").astype(bool)
    events = []
    interpretation = root / "interpretation_label" / f"{machine}.txt"
    for line in interpretation.read_text().splitlines():
        m = re.fullmatch(r"(\d+)-(\d+):([\d,]*)", line.strip())
        if not m:
            continue
        a, b = int(m[1]), int(m[2])
        dims = frozenset(name_of(int(d)) for d in m[3].split(",") if d)
        events.append(evaluation.Event(a * STEP_MS, (b + 1) * STEP_MS, dims, f"{machine}:{a}-{b}"))
    # Labeled steps without an interpretation line: events of any metric.
    covered = np.zeros(labels.size, dtype=bool)
    for e in events:
        covered[e.start // STEP_MS : e.end // STEP_MS] = True
    rest = labels & ~covered
    edges = np.flatnonzero(np.diff(np.r_[0, rest.astype(np.int8), 0]))
    for a, b in zip(edges[::2], edges[1::2], strict=True):
        events.append(evaluation.Event(a * STEP_MS, b * STEP_MS, frozenset(), f"{machine}:{a}-{b - 1}"))
    return train, test, sorted(events, key=lambda e: e.start)


class TrainBaselines:
    """The baseline of a metric is the median and MAD of its train split,
    with the ratio floor (the data are normalised to [0, 1])."""

    def __init__(self, train: np.ndarray, series: list[SeriesMeta], floor: float) -> None:
        median = np.median(train, axis=0)
        mad = np.maximum(np.median(np.abs(train - median), axis=0), floor)
        self.by_id = {s.id: (float(median[i]), float(mad[i])) for i, s in enumerate(series)}

    def lookup(self, meta, cls, power):
        median, mad = self.by_id[meta.id]
        return median, mad, {"basis": "baseline", "median": median, "mad": mad}

    def cold(self, meta):
        return None


def frame_of(values: np.ndarray, series: list[SeriesMeta]) -> Frame:
    return Frame(np.arange(values.shape[0], dtype=np.int64) * STEP_MS, series, values.T.astype(float))


def run_machine(root: Path, machine: str, base: Params) -> dict[str, evaluation.Score]:
    params = smd_params(base)
    train, test, events = load(root, machine)
    series = [SeriesMeta(d, name_of(d), "host") for d in range(1, train.shape[1] + 1)]
    signal_of = {s.id: s.signal for s in series}
    test_frame = frame_of(test, series)

    robust = Robust(TrainBaselines(train, series, params.floors["*_ratio"]), ewma=False)
    signals = {s.signal: "max" for s in series}
    windows, _, _ = forest.window_features(frame_of(train, series), params, signals)
    windows = windows[~np.isnan(windows).any(axis=1)]
    trees = forest.ForestDetector(None, None, params, store=False, signals=signals)
    model = forest.train(windows, CLASS, datetime(2019, 1, 1, tzinfo=UTC), params)
    trees.models = {CLASS: model} if model else {}

    n = test_frame.ts.size
    ctx_args = (test_frame, 0, np.full(n, CLASS, dtype=object), np.full(n, "-", dtype=object), params)
    records = {
        "robust": _records(robust, ctx_args, signal_of),
        "iforest": _records(trees, ctx_args, signal_of),
    }
    records["robust+iforest"] = records["robust"] + records["iforest"]
    return {v: evaluation.score(records[v], events, [], test_frame.ts, STEP_MS) for v in VARIANTS}


def _records(detector, ctx_args, signal_of) -> list[evaluation.Rec]:
    tracker = Tracker(ctx_args[-1])
    ctx = Context(*ctx_args)
    out, open_recs = [], {}
    last = int(ctx.frame.ts[-1]) + STEP_MS
    for ev in detector.evaluate(ctx):
        for e in tracker.feed(ev):
            if isinstance(e, Opened):
                open_recs[e.token] = [(e.started_ms, signal_of[r.series_id]) for r in e.records]
            else:
                out += [evaluation.Rec(s, e.ended_ms, sig) for s, sig in open_recs.pop(e.token)]
    # Still open at the end of the test split.
    for recs in open_recs.values():
        out += [evaluation.Rec(s, last, sig) for s, sig in recs]
    return out


def run(root: Path, base: Params, only: list[str] | None = None) -> tuple[str, dict[str, evaluation.Score]]:
    """Every machine; returns a Markdown report and the summed scores."""
    total = {v: evaluation.Score() for v in VARIANTS}
    per_machine = []
    for machine in only or machines(root):
        scores = run_machine(root, machine, base)
        per_machine.append((machine, scores))
        for v in VARIANTS:
            total[v] = total[v].add(scores[v])
    lines = ["## All machines", "", evaluation.table([(v, total[v]) for v in VARIANTS], 60, "min"), ""]
    lines += ["## Per machine", "", "| machine | " + " | ".join(f"{v} found, FA/h" for v in VARIANTS) + " |"]
    lines.append("|---" * (len(VARIANTS) + 1) + "|")
    for machine, scores in per_machine:
        cells = [f"{scores[v].found}/{scores[v].events}, {scores[v].false_per_h:.2f}" for v in VARIANTS]
        lines.append(f"| {machine} | " + " | ".join(cells) + " |")
    return "\n".join(lines), total
