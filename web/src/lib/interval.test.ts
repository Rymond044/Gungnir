import { expect, it } from 'vitest'
import { fromWall, toWall } from './interval'

it('reads and writes times in the host clock', () => {
  const minsk = 3 * 3600
  const at = Date.UTC(2026, 10, 9, 11, 0)
  expect(toWall(at, minsk)).toEqual({ year: 2026, month: 10, day: 9, hour: 14, minute: 0 })
  expect(fromWall({ year: 2026, month: 10, day: 9, hour: 14, minute: 0 }, minsk)).toBe(at)
})
