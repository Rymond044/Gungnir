import { describe, expect, it } from 'vitest'
import type { Point } from '../api/types'
import { nearest, niceCeil, runs, timeTicks } from './scale'

describe('niceCeil', () => {
  it('rounds up to 1, 2, 2.5 or 5 times a power of ten', () => {
    expect(niceCeil(0.37)).toBe(0.5)
    expect(niceCeil(1)).toBe(1)
    expect(niceCeil(2.1)).toBe(2.5)
    expect(niceCeil(186e6)).toBe(200e6)
    expect(niceCeil(0)).toBe(1)
  })
})

describe('timeTicks', () => {
  it('falls on round local times', () => {
    const offset = 3 * 3600
    const since = Date.UTC(2026, 10, 3, 10, 7)
    const ticks = timeTicks(since, since + 3600_000, offset, 6)
    expect(ticks.length).toBeGreaterThanOrEqual(4)
    for (const t of ticks) expect(((t / 1000 + offset) % 600) / 60).toBe(0)
  })
})

describe('runs', () => {
  it('breaks a line where samples are missing', () => {
    const pts: Point[] = [0, 5, 10, 60, 65].map((s) => [s * 1000, 1, 1, 1])
    expect(runs(pts, 15_000).map((r) => r.length)).toEqual([3, 2])
  })
})

describe('nearest', () => {
  it('finds the closest point', () => {
    const pts: Point[] = [0, 10, 20].map((s) => [s, 0, 0, 0])
    expect(nearest(pts, 14)).toBe(1)
    expect(nearest(pts, 16)).toBe(2)
    expect(nearest([], 3)).toBe(-1)
  })
})
