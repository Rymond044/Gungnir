import { expect, it } from 'vitest'
import { isStale } from './freshness'
it('requires each processing watermark to be recent', () => {
  const now = Date.parse('2026-11-23T12:00:00Z')
  expect(isStale('2026-11-23T11:59:50Z', now)).toBe(false)
  expect(isStale('2026-11-23T11:55:00Z', now)).toBe(true)
  expect(isStale(null, now)).toBe(true)
  expect(isStale(undefined, now)).toBe(true)
  expect(isStale('invalid', now)).toBe(true)
})
