import { expect, it } from 'vitest'
import type { Command } from '../api/types'
import { folded } from './audit'

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
