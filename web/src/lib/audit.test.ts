import { expect, it } from 'vitest'
import type { Command, Stretch } from '../api/types'
import { folded, packed } from './audit'

const cmd = (name: string, pgid: number, cores: number, rss: number, unit = 'db.scope'): Command => ({
  pgid,
  leader: name,
  names: [[name, 1]],
  pids: 1,
  cores,
  rss_bytes: rss,
  unit,
})

it('reads the processes of one name in one unit as one command', () => {
  const out = folded(
    [cmd('postgres', 1, 0.1, 100), cmd('postgres', 2, 0.1, 300), cmd('chromium', 3, 0.5, 50)],
    true,
  )
  expect(out.map((c) => [c.leader, c.rss_bytes, c.pids])).toEqual([
    ['postgres', 400, 2],
    ['chromium', 50, 1],
  ])
  expect(folded([cmd('postgres', 1, 0.1, 100), cmd('chromium', 3, 0.5, 50)], false)[0].leader).toBe(
    'chromium',
  )
})

it('puts overlapping stretches on rows of their own', () => {
  const s = (start: number, end: number | null) => ({ s: { start, end } as Stretch, id: `${start}` })
  const rows = packed([s(0, 100), s(10, 20), s(30, 40), s(200, 300)], 1_000_000)
  expect([...rows.entries()]).toEqual([
    ['0', 1],
    ['10', 2],
    ['30', 2],
    ['200', 1],
  ])
})
