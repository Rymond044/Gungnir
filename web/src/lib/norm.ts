// The normal band of a series over time (ADR 0052): one stretch per span of
// workload class and power profile that has a baseline.

import type { Norm } from '../api/types'
import type { BandSegment, LimitLine } from '../components/Chart'
import { ms } from './format'
import { levelNames } from './names'

export function bandSegments(norm: Norm | undefined): BandSegment[] {
  if (!norm) return []
  const by = new Map(norm.bands.map((b) => [`${b.workload_class}|${b.power_profile}`, b]))
  return norm.spans.flatMap((s) => {
    const b = by.get(`${s.workload_class}|${s.power_profile}`)
    return b ? [{ from: ms(s.since), to: ms(s.until), low: b.low, high: b.high }] : []
  })
}

/** Fixed limits; the cold-start limit only while no baseline judges the series. */
export function limitLines(norm: Norm | undefined): LimitLine[] {
  if (!norm) return []
  const judged = bandSegments(norm).length > 0
  return norm.limits
    .filter((l) => !(judged && l.kind === 'cold start'))
    .map((l) => {
      const value = (l.above ?? l.below) as number
      if (l.kind === 'impact')
        return { value, label: `влияние: ${levelNames[l.level ?? ''] ?? l.level}`, color: 'var(--red)' }
      if (l.kind === 'threshold') return { value, label: 'порог', color: 'var(--ochre)' }
      return { value, label: 'холодный старт', color: 'var(--faint)' }
    })
}

/** Used memory is what people read; detection judges available memory, so
 * the same series is drawn turned over against the total. */
export function usedFrom(signal: string, memoryBytes: number | null | undefined): number | undefined {
  return signal === 'mem.available_bytes' && memoryBytes ? memoryBytes : undefined
}
