import { expect, it } from 'vitest'
import type { Norm } from '../api/types'
import { bandSegments, hourSteps, limitLines } from './norm'

const norm: Norm = {
  signal: 'disk.write_bytes_per_s',
  entity: 'nvme0n1',
  spans: [
    {
      since: '2026-11-03T10:00:00Z',
      until: '2026-11-03T11:00:00Z',
      workload_class: 'idle',
      power_profile: 'eco/ac',
    },
    {
      since: '2026-11-03T11:00:00Z',
      until: '2026-11-03T12:00:00Z',
      workload_class: 'build',
      power_profile: 'eco/ac',
    },
  ],
  bands: [
    { workload_class: 'idle', power_profile: 'eco/ac', low: -1, high: 5, median: 2, mad: 0.5, samples: 9000 },
  ],
  limits: [{ kind: 'impact', above: 0.1, below: null, level: 'noticeable', source: 's' }],
  minima: [],
}

it('draws the minimum of each hour across its hour', () => {
  const minima = [
    { hour: '2026-11-03T10:00:00Z', value: 5e8 },
    { hour: '2026-11-03T11:00:00Z', value: 6e8 },
  ]
  expect(hourSteps({ ...norm, minima })).toEqual([
    { from: Date.parse('2026-11-03T10:00:00Z'), to: Date.parse('2026-11-03T11:00:00Z'), value: 5e8 },
    { from: Date.parse('2026-11-03T11:00:00Z'), to: Date.parse('2026-11-03T12:00:00Z'), value: 6e8 },
  ])
  expect(hourSteps(undefined)).toEqual([])
})

it('draws the band only where the class has a baseline', () => {
  expect(bandSegments(norm)).toEqual([
    { from: Date.parse('2026-11-03T10:00:00Z'), to: Date.parse('2026-11-03T11:00:00Z'), low: -1, high: 5 },
  ])
})

it('names limits by their kind and level', () => {
  expect(limitLines(norm)).toEqual([{ value: 0.1, label: 'влияние: заметное', color: 'var(--red)' }])
})

it('drops the cold-start limit once a baseline judges the series', () => {
  const cold = {
    ...norm,
    limits: [{ kind: 'cold start' as const, above: 9, below: null, level: null, source: 's' }],
  }
  expect(limitLines(cold)).toEqual([])
  expect(limitLines({ ...cold, bands: [] })).toEqual([
    { value: 9, label: 'холодный старт', color: 'var(--faint)' },
  ])
})
