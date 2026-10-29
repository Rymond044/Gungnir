"""Detectors: pure functions from a frame of samples to evaluations.

A detector reads only the frame, the context of each tick (workload class,
power profile) and what it is judged against; reading the database and the
clock is the runner's job. So the live loop, a replay and the public
dataset run the same code, and a detector can be replaced behind the
`Detector` interface (NFR-3).
"""

from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from typing import Protocol

import numpy as np

from gungnir_core import knowledge
from gungnir_core.baselines import Book
from gungnir_core.detection.records import Evaluation, RecordSpec
from gungnir_core.frame import Frame, SeriesMeta, is_group
from gungnir_core.params import Params, by_pattern

# MAD of a normal distribution times this is its standard deviation.
MAD_TO_SIGMA = 1.4826


@dataclass
class Context:
    frame: Frame
    # Ticks before this index are history (windows, EWMA); only later ticks
    # are judged.
    start: int
    classes: np.ndarray
    powers: np.ndarray
    params: Params
    # Each detector's own state between steps, kept as JSON by the runner.
    state: dict = field(default_factory=dict)

    @property
    def ticks(self) -> np.ndarray:
        return self.frame.ts[self.start :]


class Detector(Protocol):
    name: str

    def evaluate(self, ctx: Context) -> list[Evaluation]: ...


def direction(sign: float) -> str:
    return "down" if sign < 0 else "up"


# Thresholds and impact: fixed limits from knowledge files.


@dataclass(frozen=True)
class Limit:
    signal: str
    above: float | None
    below: float | None
    source: str
    level: str | None = None
    samples: int | None = None


def limits_from(items: list[dict]) -> list[Limit]:
    out = []
    for item in items:
        above, below = item.get("above"), item.get("below")
        if (above is None) == (below is None):
            raise knowledge.KnowledgeError(f"{item.get('signal')}: give exactly one of above or below")
        out.append(
            Limit(
                item["signal"],
                above,
                below,
                item["provenance"]["source"],
                item.get("level"),
                item.get("samples"),
            )
        )
    return out


def load_limits(table: str, filename: str, host: str | None = None) -> list[Limit]:
    """Limits from a knowledge file, with host-local entries replacing those
    of the same signal and level."""
    shipped = limits_from(knowledge.entries(knowledge.directory() / filename, table))
    limits = {(lim.signal, lim.level): lim for lim in shipped}
    if host:
        for lim in limits_from(knowledge.entries(knowledge.local_file(host), table)):
            limits[(lim.signal, lim.level)] = lim
    return list(limits.values())


def judge_limit(lim: Limit, x: np.ndarray) -> tuple[np.ndarray, np.ndarray, str]:
    with np.errstate(divide="ignore", invalid="ignore"):
        if lim.above is not None:
            return x > lim.above, x / lim.above, "up"
        return x < lim.below, np.where(x > 0, lim.below / x, np.inf), "down"


class LimitDetector:
    def __init__(self, name: str, kind: str, limits: list[Limit]) -> None:
        self.name, self.kind = name, kind
        self.by_signal: dict[str, list[Limit]] = {}
        for lim in limits:
            self.by_signal.setdefault(lim.signal, []).append(lim)

    def evaluate(self, ctx: Context) -> list[Evaluation]:
        out = []
        f = ctx.frame
        for i, meta in enumerate(f.series):
            for lim in self.by_signal.get(meta.signal, ()):
                x = f.values[i, ctx.start :]
                flags, scores, d = judge_limit(lim, x)
                scores = np.where(np.isnan(x), np.nan, scores)
                basis = {"basis": "threshold", "above": lim.above, "below": lim.below, "source": lim.source}
                detector = self.name
                key = f"{detector}.{lim.level}|{meta.id}" if lim.level else f"{detector}|{meta.id}"
                out.append(
                    Evaluation(
                        key,
                        detector,
                        self.kind,
                        ctx.ticks,
                        flags & ~np.isnan(x),
                        scores,
                        lambda j, m=meta, x=x, b=basis, d=d: [RecordSpec(m.id, d, b, float(x[j]))],
                        level=lim.level,
                        open_after=lim.samples,
                    )
                )
        return out


def thresholds(host: str | None = None) -> LimitDetector:
    return LimitDetector("threshold", "anomaly", load_limits("threshold", "static_thresholds.toml", host))


def impact(host: str | None = None) -> LimitDetector:
    return LimitDetector("impact", "impact", load_limits("impact", "impact.toml", host))


# Robust statistics against a baseline.


class Baselines(Protocol):
    def lookup(self, meta: SeriesMeta, cls: str, power: str) -> tuple[float, float, dict] | None:
        """Median and spread floor-applied for a series in a class and
        power profile, with what to record about them."""

    def cold(self, meta: SeriesMeta) -> tuple[float, dict] | None:
        """A cold-start upper limit from measured capacity."""


class BookBaselines:
    """Baselines from hourly histograms, with the power correction factor
    and spread floors applied; capacity for cold start (ADR 0022, 0024)."""

    def __init__(self, book: Book) -> None:
        self.book = book

    def prepare(self, t) -> None:
        self.book.at(t)

    def lookup(self, meta, cls, power):
        b = self.book.get(meta.id, cls)
        if b is None:
            return None
        factor = b.factor(power)
        median = b.median * factor
        mad = max(b.mad * factor, self.book.floor(meta.signal, meta.entity, median))
        return (
            median,
            mad,
            {
                "basis": "baseline",
                "median": median,
                "mad": mad,
                "class": cls if self.book.per_class else "*",
                "power": power,
                "factor": factor,
                "samples": b.samples,
                "version": self.book.version.isoformat(),
            },
        )

    def cold(self, meta):
        cap = self.book.capacity.get((meta.signal, meta.entity))
        share = by_pattern(self.book.params.cold_start_share, meta.signal)
        if cap is None or share is None:
            return None
        return cap * share, {"basis": "calibration", "capacity": cap, "share": share}


def per_tick(
    baselines: Baselines, meta: SeriesMeta, classes: np.ndarray, powers: np.ndarray
) -> tuple[np.ndarray, np.ndarray, list]:
    """Median and spread at each tick (NaN without a baseline) and the
    record basis of each tick."""
    n = classes.size
    median, mad = np.full(n, np.nan), np.full(n, np.nan)
    basis: list[dict | None] = [None] * n
    cache: dict[tuple[str, str], tuple | None] = {}
    for j in range(n):
        combo = (classes[j], powers[j])
        if combo not in cache:
            cache[combo] = baselines.lookup(meta, *combo)
        found = cache[combo]
        if found is not None:
            median[j], mad[j], basis[j] = found
    return median, mad, basis


class Robust:
    """z = (x - median) / (1.4826 * MAD') against the baseline of the tick's
    class; an EWMA of the series against the same median catches slow
    drift. Without a baseline, a dense series with a measured capacity is
    judged against `capacity * share` (cold start)."""

    name = "robust"

    def __init__(self, baselines: Baselines, ewma: bool = True) -> None:
        self.baselines = baselines
        self.ewma = ewma

    def prepare(self, t) -> None:
        prepare = getattr(self.baselines, "prepare", None)
        if prepare:
            prepare(t)

    def evaluate(self, ctx: Context) -> list[Evaluation]:
        p = ctx.params
        f = ctx.frame
        classes, powers = ctx.classes[ctx.start :], ctx.powers[ctx.start :]
        ewma_state = ctx.state.setdefault("ewma", {})
        out = []
        for i, meta in enumerate(f.series):
            x = f.values[i, ctx.start :]
            median, mad, basis = per_tick(self.baselines, meta, classes, powers)
            has_baseline = ~np.isnan(median)
            cold = None if is_group(meta.signal) else self.baselines.cold(meta)
            if not has_baseline.any() and cold is None:
                ewma_state.pop(str(meta.id), None)
                continue
            with np.errstate(divide="ignore", invalid="ignore"):
                z = (x - median) / (MAD_TO_SIGMA * mad)
            scores = np.abs(z)
            flags = scores > p.robust_k
            signs = np.sign(z)
            if cold is not None:
                limit, cold_basis = cold
                use = ~has_baseline & ~np.isnan(x)
                scores = np.where(use, x / limit, scores)
                flags = np.where(use, x > limit, flags)
                signs = np.where(use, 1.0, signs)
                basis = [b if b is not None else cold_basis for b in basis]
            scores = np.where(np.isnan(x), np.nan, scores)
            out.append(
                self._evaluation(
                    f"robust.mad|{meta.id}", "robust.mad", ctx, meta, x, flags, scores, signs, basis
                )
            )
            drifts = any(fnmatchcase(meta.signal, pat) for pat in p.ewma_signals)
            if self.ewma and drifts and has_baseline.any():
                e, last = ewma(x, p.ewma_lambda, ewma_state.get(str(meta.id)))
                ewma_state[str(meta.id)] = last
                width = MAD_TO_SIGMA * mad * np.sqrt(p.ewma_lambda / (2 - p.ewma_lambda))
                with np.errstate(divide="ignore", invalid="ignore"):
                    ze = (e - median) / width
                escores = np.where(has_baseline & ~np.isnan(x), np.abs(ze), np.nan)
                out.append(
                    self._evaluation(
                        f"robust.ewma|{meta.id}",
                        "robust.ewma",
                        ctx,
                        meta,
                        e,
                        escores > p.ewma_l,
                        escores,
                        np.sign(ze),
                        basis,
                    )
                )
        return out

    @staticmethod
    def _evaluation(key, detector, ctx, meta, x, flags, scores, signs, basis) -> Evaluation:
        return Evaluation(
            key,
            detector,
            "anomaly",
            ctx.ticks,
            np.nan_to_num(flags, nan=0).astype(bool),
            scores,
            lambda j: [RecordSpec(meta.id, direction(signs[j]), basis[j] or {}, float(x[j]))],
        )


def ewma(x: np.ndarray, lam: float, start: float | None) -> tuple[np.ndarray, float | None]:
    """Exponentially weighted moving average; gaps hold the last value."""
    out = np.full(x.shape, np.nan)
    e = start
    for j, v in enumerate(x):
        if np.isnan(v):
            continue
        e = v if e is None else lam * v + (1 - lam) * e
        out[j] = e
    return out, (None if e is None else float(e))
