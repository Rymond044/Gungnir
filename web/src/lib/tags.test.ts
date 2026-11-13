import { expect, it } from 'vitest'
import type { Mark } from '../components/Chart'
import { tagsOf } from './tags'

const HOUR = 3600_000
const mark = (n: number, at: number, tone: Mark['tone'] = 'gray'): Mark => ({
  key: `k${n}`,
  from: at,
  to: at + 60_000,
  label: String(n),
  tone,
  title: `эпизод ${n}`,
})

it('joins marks too close to tell apart into one tag that names them', () => {
  // A week: three stretches within a few minutes, one a day later.
  const week = 7 * 24 * HOUR
  const marks = [mark(1, 0), mark(2, 120_000, 'red'), mark(3, 300_000), mark(4, 24 * HOUR)]
  const [group, alone] = tagsOf(marks, 0, week)
  expect(group).toMatchObject({ key: 'k1', keys: ['k1', 'k2', 'k3'], label: '1–3 · 3', tone: 'red', row: 0 })
  expect(alone).toMatchObject({ key: 'k4', keys: ['k4'], label: '4', row: 0, end: false })
  // A group at the right edge ends at its mark, inside the graph.
  const [last] = tagsOf([mark(5, week - HOUR), mark(6, week - HOUR / 2)], 0, week)
  expect(last.end).toBe(true)
})

it('keeps marks apart when the graph is wide enough to show each', () => {
  const marks = [mark(1, 0), mark(2, 10 * 60_000)]
  expect(tagsOf(marks, 0, HOUR).map((t) => t.label)).toEqual(['1', '2'])
})
