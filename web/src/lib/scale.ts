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

export interface Frame {
  since: number
  until: number
  yMax: number
  width: number
  height: number
}

export function x(f: Frame, t: number): number {
  return ((t - f.since) / (f.until - f.since)) * f.width
}

export function y(f: Frame, v: number): number {
  const clamped = Math.max(0, Math.min(f.yMax, v))
  return f.height - (clamped / f.yMax) * f.height
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
