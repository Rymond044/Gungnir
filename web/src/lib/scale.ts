// Scales of the graphs: nice axis limits, time ticks in the host's local
// time, and polylines that break where samples are missing.

import type { Point } from '../api/types'

/** The smallest of 1, 2, 2.5, 5 times a power of ten that is at least x. */
export function niceCeil(x: number): number {
  if (!(x > 0)) return 1
  const p = 10 ** Math.floor(Math.log10(x))
  for (const m of [1, 2, 2.5, 5, 10]) if (m * p >= x * (1 - 1e-9)) return m * p
  return 10 * p
}

const STEPS_S = [60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200, 86400]

/** Tick times (ms) at round local times, about `count` of them. */
export function timeTicks(since: number, until: number, offsetS: number | null, count = 6): number[] {
  const span = (until - since) / 1000
  const step = (STEPS_S.find((s) => span / s <= count) ?? 86400) * 1000
  const shift = (offsetS ?? 0) * 1000
  const first = Math.ceil((since + shift) / step) * step - shift
  const out = []
  for (let t = first; t <= until; t += step) out.push(t)
  return out
}

/**
 * Round axis limits and the step between grid lines, about `count` steps
 * over [lo, hi]. With `zero` the axis starts at zero; otherwise it hugs the
 * data, so that a slow drift of a level fills the plot.
 */
export function niceAxis(
  lo: number,
  hi: number,
  zero: boolean,
  count = 4,
  binary = false,
): { min: number; max: number; step: number } {
  if (binary) {
    // Bytes: round in KiB, MiB or GiB, so the labels read 256 MiB, not 268 MB.
    const unit = 1024 ** Math.max(0, Math.floor(Math.log(Math.max(Math.abs(hi), 1)) / Math.log(1024)))
    const a = niceAxis(lo / unit, hi / unit, zero, count)
    return { min: a.min * unit, max: a.max * unit, step: a.step * unit }
  }
  const from = zero ? 0 : lo
  const span = hi - from
  if (!(span > 0)) {
    const v = Math.abs(hi) || 1
    return zero
      ? { min: 0, max: niceCeil(v), step: niceCeil(v) / count }
      : { min: hi - v * 0.05, max: hi + v * 0.05, step: v * 0.025 }
  }
  const step = niceCeil(span / count)
  const min = zero ? 0 : Math.floor(from / step + 1e-9) * step
  const max = Math.max(min + step, Math.ceil(hi / step - 1e-9) * step)
  return { min, max, step }
}

export interface Frame {
  since: number
  until: number
  yMin: number
  yMax: number
  width: number
  height: number
}

export function x(f: Frame, t: number): number {
  return ((t - f.since) / (f.until - f.since)) * f.width
}

export function y(f: Frame, v: number): number {
  const clamped = Math.max(f.yMin, Math.min(f.yMax, v))
  return f.height - ((clamped - f.yMin) / (f.yMax - f.yMin)) * f.height
}

/** Runs of points without a gap longer than `gapMs`. */
export function runs(points: Point[], gapMs: number): Point[][] {
  const out: Point[][] = []
  let current: Point[] = []
  for (const p of points) {
    const last = current[current.length - 1]
    if (last && p[0] - last[0] > gapMs) {
      out.push(current)
      current = []
    }
    current.push(p)
  }
  if (current.length) out.push(current)
  return out
}

/**
 * Stretches of [since, until] where no series has a sample for longer than
 * `gapMs`: the agent was stopped, the host slept or the network was down.
 */
export function missing(series: Point[][], since: number, until: number, gapMs: number): [number, number][] {
  const times = series
    .flatMap((points) => points.map((p) => p[0]))
    .filter((t) => t >= since && t <= until)
    .sort((a, b) => a - b)
  const out: [number, number][] = []
  let last = since
  for (const t of [...times, until]) {
    if (t - last > gapMs) out.push([last, t])
    last = Math.max(last, t)
  }
  return out
}

export function line(f: Frame, run: Point[], index = 1): string {
  return run.map((p) => `${x(f, p[0]).toFixed(1)},${y(f, p[index]).toFixed(1)}`).join(' ')
}

/** The min-max envelope of a run as a closed polygon. */
export function envelope(f: Frame, run: Point[]): string {
  const top = run.map((p) => `${x(f, p[0]).toFixed(1)},${y(f, p[3]).toFixed(1)}`)
  const bottom = [...run].reverse().map((p) => `${x(f, p[0]).toFixed(1)},${y(f, p[2]).toFixed(1)}`)
  return [...top, ...bottom].join(' ')
}

/** Index of the point nearest to time t, or -1. */
export function nearest(points: Point[], t: number): number {
  let lo = 0
  let hi = points.length - 1
  if (hi < 0) return -1
  while (lo < hi) {
    const mid = (lo + hi) >> 1
    if (points[mid][0] < t) lo = mid + 1
    else hi = mid
  }
  if (lo > 0 && t - points[lo - 1][0] < points[lo][0] - t) return lo - 1
  return lo
}
