import { expect, it } from 'vitest'
import { fromWall, monthGrid, toWall } from './interval'

it('lays out a month from Monday', () => {
  // November 2026 starts on a Sunday.
  const weeks = monthGrid(2026, 10)
  expect(weeks[0]).toEqual([null, null, null, null, null, null, 1])
  expect(weeks.at(-1)).toEqual([30, null, null, null, null, null, null])
})

it('reads and writes times in the host clock', () => {
  const minsk = 3 * 3600
  const at = Date.UTC(2026, 10, 9, 11, 0)
  expect(toWall(at, minsk)).toEqual({ year: 2026, month: 10, day: 9, hour: 14, minute: 0 })
  expect(fromWall({ year: 2026, month: 10, day: 9, hour: 14, minute: 0 }, minsk)).toBe(at)
})
