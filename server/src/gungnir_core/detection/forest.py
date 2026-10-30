"""Isolation Forest over a sliding minute window of host series (ADR 0026).

Features are the mean and the maximum over the window of about fifteen host
series, rates and bytes on a log scale. The last 60 s of raw samples are
scored every 10 s. One forest per workload class is trained daily on the
minute rollups of the seven days before, without labeled phases; a rollup
minute gives the same features from its sum, count and maximum. A window is
anomalous above the 99.9th percentile of the training scores.

The forest only says that a window is odd, so each interval names the
three signals whose features lie furthest from the training windows (robust
z), one record each, sharing an episode.
"""

import logging
import pickle
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np
import psycopg
from numpy.lib.stride_tricks import sliding_window_view
from sklearn.ensemble import IsolationForest

from gungnir_core import frame
from gungnir_core.baselines import UNKNOWN
from gungnir_core.detection.detectors import MAD_TO_SIGMA, Context, direction
from gungnir_core.detection.records import Evaluation, RecordSpec
from gungnir_core.hosts import Host
from gungnir_core.labels import excluded, phases_of
from gungnir_core.params import Params

log = logging.getLogger(__name__)

# Host signals and how several entities (disks) combine into one.
SIGNALS = {
    "cpu.user_ratio": "max",
    "cpu.system_ratio": "max",
    "cpu.iowait_ratio": "max",
    "cpu.pressure_some_ratio": "max",
    "mem.available_bytes": "sum",
    "mem.pressure_some_ratio": "max",
    "mem.pressure_full_ratio": "max",
    "mem.major_faults_per_s": "sum",
    "disk.read_bytes_per_s": "sum",
    "disk.write_bytes_per_s": "sum",
    "disk.read_ops_per_s": "sum",
    "disk.write_ops_per_s": "sum",
    "disk.read_await_ms": "max",
    "disk.write_await_ms": "max",
    "disk.util_ratio": "max",
    "io.pressure_some_ratio": "max",
    "io.pressure_full_ratio": "max",
}
NAMES = list(SIGNALS)
# A window needs at least half its samples.
MIN_FILL = 0.5


def logged(signal: str) -> bool:
    return signal.endswith(("_bytes", "_per_s"))


def feature_names(names: list[str]) -> list[str]:
    return [f"{s}:{stat}" for s in names for stat in ("mean", "max")]


def to_features(mean: np.ndarray, peak: np.ndarray, names: list[str] = NAMES) -> np.ndarray:
    """Feature matrix (windows, features) from per-signal mean and maximum
    arrays of shape (windows, signals)."""
    out = np.empty((mean.shape[0], 2 * len(names)))
    for k, s in enumerate(names):
        m, p = mean[:, k], peak[:, k]
        if logged(s):
            m, p = np.log1p(np.maximum(m, 0)), np.log1p(np.maximum(p, 0))
        out[:, 2 * k], out[:, 2 * k + 1] = m, p
    return out


@dataclass
class Model:
    cls: str
    trained_at: datetime
    forest: IsolationForest
    threshold: float
    median: np.ndarray
    mad: np.ndarray
    windows: int

    def score(self, x: np.ndarray) -> np.ndarray:
        return -self.forest.score_samples(x)

    def z(self, x: np.ndarray) -> np.ndarray:
        with np.errstate(divide="ignore", invalid="ignore"):
            return (x - self.median) / (MAD_TO_SIGMA * self.mad)


def train(windows: np.ndarray, cls: str, at: datetime, params: Params, seed: int = 0) -> Model | None:
    if windows.shape[0] < params.iforest_min_minutes:
        return None
    forest = IsolationForest(
        n_estimators=params.iforest_trees,
        max_samples=min(params.iforest_subsample, windows.shape[0]),
        random_state=seed,
    ).fit(windows)
    scores = -forest.score_samples(windows)
    median = np.median(windows, axis=0)
    mad = np.median(np.abs(windows - median), axis=0)
    # A feature that never moved in training: any change is far away, but
    # not infinitely.
    mad = np.maximum(mad, 1e-3 * np.maximum(np.abs(median), 1.0))
    return Model(
        cls, at, forest, float(np.quantile(scores, params.iforest_quantile)), median, mad, windows.shape[0]
    )


def training_windows(
    conn: psycopg.Connection, host: Host, since: datetime, until: datetime, params: Params
) -> dict[str, np.ndarray]:
    """Minute windows by workload class from the rollups in [since, until),
    without labeled phases."""
    rows = conn.execute(
        "SELECT r.ts, s.signal,"
        " CASE WHEN s.signal = ANY(%(sums)s) THEN sum(r.sum / r.n) ELSE max(r.sum / r.n) END,"
        " CASE WHEN s.signal = ANY(%(sums)s) THEN sum(r.max) ELSE max(r.max) END"
        " FROM samples_1m r JOIN series s ON s.id = r.series_id"
        " WHERE s.host_id = %(h)s AND s.signal = ANY(%(signals)s) AND r.ts >= %(since)s AND r.ts < %(until)s"
        " GROUP BY r.ts, s.signal",
        {
            "h": host.id,
            "signals": NAMES,
            "sums": [s for s, how in SIGNALS.items() if how == "sum"],
            "since": since,
            "until": until,
        },
    ).fetchall()
    if not rows:
        return {}
    minutes = sorted({ts for ts, *_ in rows})
    index = {ts: i for i, ts in enumerate(minutes)}
    col = {s: k for k, s in enumerate(NAMES)}
    mean = np.full((len(minutes), len(NAMES)), np.nan)
    peak = np.full_like(mean, np.nan)
    for ts, signal, m, p in rows:
        mean[index[ts], col[signal]], peak[index[ts], col[signal]] = m, p
    grid = np.array([frame.to_ms(ts) for ts in minutes], dtype=np.int64)
    classes = frame.labels_on(grid, frame.fact_spans(conn, host, "workload.class", since, until), UNKNOWN)
    skip = excluded(phases_of(conn, host, since, until), params.label_tail)
    # A minute overlapping a labeled phase is out.
    skip_mask = frame.inside(grid, skip) | frame.inside(grid + 59_999, skip)
    complete = ~np.isnan(mean).any(axis=1)
    x = to_features(mean, peak)
    out = {}
    for cls in set(classes) - {UNKNOWN}:
        use = (classes == cls) & complete & ~skip_mask
        if use.any():
            out[cls] = x[use]
    return out


def window_features(
    f: frame.Frame, params: Params, signals: dict[str, str] = SIGNALS
) -> tuple[np.ndarray, np.ndarray, dict]:
    """Features of the window ending at each tick (NaN rows where the window
    is too empty), the per-tick value of each signal, and per signal the
    frame rows of its entities."""
    names = list(signals)
    n = f.ts.size
    w = params.iforest_window
    rows_by_signal = {s: f.rows_of(s) for s in names}
    per_tick = np.full((n, len(names)), np.nan)
    for k, s in enumerate(names):
        rows = rows_by_signal[s]
        if not rows:
            continue
        block = f.values[rows]
        with np.errstate(all="ignore"):
            per_tick[:, k] = np.nansum(block, axis=0) if signals[s] == "sum" else np.nanmax(block, axis=0)
        per_tick[np.isnan(block).all(axis=0), k] = np.nan
    feats = np.full((n, 2 * len(names)), np.nan)
    if n >= w:
        win = sliding_window_view(per_tick, w, axis=0)  # (n - w + 1, signals, w)
        filled = (~np.isnan(win)).sum(axis=2) >= MIN_FILL * w
        with np.errstate(all="ignore"):
            mean = np.where(filled, np.nanmean(win, axis=2), np.nan)
            peak = np.where(filled, np.nanmax(win, axis=2), np.nan)
        feats[w - 1 :] = to_features(mean, peak, names)
    return feats, per_tick, rows_by_signal


class ForestDetector:
    name = "iforest"

    def __init__(
        self,
        conn: psycopg.Connection | None,
        host: Host | None,
        params: Params,
        store: bool = True,
        signals: dict[str, str] = SIGNALS,
    ) -> None:
        self.conn, self.host, self.params, self.store = conn, host, params, store
        self.signals = signals
        self.names = list(signals)
        self.features = feature_names(self.names)
        self.day: datetime | None = None
        self.models: dict[str, Model] = {}

    def prepare(self, t: datetime) -> None:
        if self.conn is None:
            # Models given from outside (the public dataset).
            return
        day = t.replace(hour=0, minute=0, second=0, microsecond=0)
        if day == self.day:
            return
        self.day = day
        self.models = self._stored(day) if self.store else {}
        if not self.models:
            since = day - timedelta(days=self.params.baseline_days)
            for cls, windows in training_windows(self.conn, self.host, since, day, self.params).items():
                model = train(windows, cls, day, self.params)
                if model:
                    self.models[cls] = model
                    if self.store:
                        self._save(model)
            if self.models:
                log.info("forests of %s for %s: %s", self.host.name, day.date(), sorted(self.models))

    def _stored(self, day: datetime) -> dict[str, Model]:
        rows = self.conn.execute(
            "SELECT model FROM forests WHERE host_id = %s AND trained_at = %s", (self.host.id, day)
        ).fetchall()
        return {m.cls: m for m in (pickle.loads(row[0]) for row in rows)}

    def _save(self, model: Model) -> None:
        self.conn.execute(
            "INSERT INTO forests (host_id, class, trained_at, windows, threshold, model)"
            " VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
            (self.host.id, model.cls, model.trained_at, model.windows, model.threshold, pickle.dumps(model)),
        )
        self.conn.execute(
            "DELETE FROM forests WHERE host_id = %s AND trained_at < %s",
            (self.host.id, model.trained_at - timedelta(days=self.params.baseline_days)),
        )

    def evaluate(self, ctx: Context) -> list[Evaluation]:
        if not self.models:
            return []
        p = ctx.params
        f = ctx.frame
        step_ms = int(p.step_s * 1000)
        # Scored every `iforest_every` ticks, aligned to the clock so that the
        # live loop and a replay score the same windows.
        ticks = np.arange(ctx.start, f.ts.size)
        ticks = ticks[(f.ts[ticks] // step_ms) % p.iforest_every == 0]
        if ticks.size == 0:
            return []
        feats, per_tick, rows_by_signal = window_features(f, p, self.signals)
        scores = np.full(ticks.size, np.nan)
        thresholds = np.full(ticks.size, np.nan)
        for cls, model in self.models.items():
            use = (ctx.classes[ticks] == cls) & ~np.isnan(feats[ticks]).any(axis=1)
            if use.any():
                scores[use] = model.score(feats[ticks[use]])
                thresholds[use] = model.threshold
        with np.errstate(invalid="ignore"):
            ratio = scores / thresholds
        flags = np.nan_to_num(ratio > 1.0, nan=0).astype(bool)

        def name(j: int) -> list[RecordSpec]:
            i = ticks[j]
            model = self.models[ctx.classes[i]]
            z = model.z(feats[i])
            specs, used = [], set()
            for k in np.argsort(-np.abs(np.nan_to_num(z))):
                signal = self.features[k].split(":")[0]
                if signal in used:
                    continue
                rows = rows_by_signal[signal]
                if not rows:
                    continue
                window = f.values[rows, max(0, i - p.iforest_window + 1) : i + 1]
                with np.errstate(all="ignore"):
                    entity_row = rows[
                        int(np.nanargmax(np.nan_to_num(np.nanmean(window, axis=1), nan=-np.inf)))
                    ]
                basis = {
                    "basis": "forest",
                    "feature": self.features[k],
                    "z": float(z[k]),
                    "score": float(scores[j]),
                    "threshold": model.threshold,
                    "class": model.cls,
                    "trained_at": model.trained_at.isoformat(),
                }
                specs.append(
                    RecordSpec(
                        f.series[entity_row].id,
                        direction(z[k]),
                        basis,
                        float(per_tick[i, self.names.index(signal)]),
                    )
                )
                used.add(signal)
                if len(specs) == p.iforest_named:
                    break
            return specs

        return [
            Evaluation(
                "iforest",
                "iforest",
                "anomaly",
                f.ts[ticks],
                flags,
                np.where(np.isnan(scores), np.nan, ratio),
                name,
            )
        ]
