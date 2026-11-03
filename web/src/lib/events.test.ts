import { expect, it } from 'vitest'
import type { EventRow } from '../api/types'
import { groupEvents } from './events'

function event(ts: string, message: string): EventRow {
  return { ts, source: 'kernel', priority: 3, unit: null, message_id: null, message }
}

it('folds repeated bursts into one entry with every time', () => {
  const groups = groupEvents([
    event('2026-11-03T20:00:00Z', 'gpu timeout'),
    event('2026-11-03T19:12:23Z', 'resume failed'),
    event('2026-11-03T19:12:22Z', 'write failed'),
    event('2026-11-03T19:12:22Z', 'resume failed'),
    event('2026-11-03T16:18:19Z', 'write failed'),
    event('2026-11-03T16:18:19Z', 'resume failed'),
  ])
  expect(groups.map((g) => g.lines)).toEqual([['gpu timeout'], ['resume failed', 'write failed']])
  expect(groups[1].times).toEqual([Date.parse('2026-11-03T19:12:22Z'), Date.parse('2026-11-03T16:18:19Z')])
})
