// Records of detection as the graphs draw them, and the words for their score.

import type { RecordRow } from '../api/types'
import type { Interval, LimitLine } from '../components/Chart'
import { detectorName, kindNames } from './names'
import { dayClock, decimal, duration, ms } from './format'

export function seriesKey(s: { signal: string; entity?: string }): string {
  return `${s.signal}@${s.entity ?? 'host'}`
}

/**
 * Levels (memory, fullness, counts of processes) move slowly far from zero,
 * so their axis hugs the data; rates and shares of time start at zero.
 */
export function isLevel(signal: string): boolean {
  return (
    (signal.endsWith('_bytes') && !signal.endsWith('_per_s')) ||
    signal.endsWith('used_ratio') ||
    signal.startsWith('proc.') ||
    signal.endsWith('_c') ||
    signal === 'power.battery_ratio'
  )
}

export function recordInterval(r: RecordRow, now: number, offset: number | null, current = false): Interval {
  const from = ms(r.started_at)
  const to = r.ended_at ? ms(r.ended_at) : now
  return {
    from,
    to,
    open: r.ended_at === null,
    impact: r.kind === 'impact',
    current,
    label: `${kindNames[r.kind]} #${r.id}, ${detectorName(r.detector)}: ${dayClock(from, offset, now)}, ${
      r.ended_at ? duration((to - from) / 1000) : 'открыта'
    }`,
  }
}

/** The fixed limit a record was judged against, where it had one instead of a baseline. */
export function recordLimit(r: RecordRow): LimitLine | null {
  const b = r.baseline
  const cold = 'var(--faint)'
  switch (b.basis) {
    case 'calibration':
      return b.capacity !== undefined && b.share !== undefined
        ? { value: b.capacity * b.share, label: 'холодный старт', color: cold }
        : null
    case 'cold start':
    case 'group cold start':
      return b.limit !== undefined ? { value: b.limit, label: 'холодный старт', color: cold } : null
    case 'threshold': {
      const value = b.above ?? b.below
      return value !== undefined ? { value, label: 'порог', color: 'var(--ochre)' } : null
    }
    case 'growth': {
      // The rise that counts, from the first of the hours judged.
      const first = b.hourly_minima?.[0]
      return first !== undefined && b.needed !== undefined
        ? { value: first + b.needed, label: 'утечка от', color: 'var(--ochre)' }
        : null
    }
    default:
      return null
  }
}

/** What the score of a record means: |z| against a baseline, a share of a limit otherwise. */
export function scoreText(r: Pick<RecordRow, 'detector' | 'peak_score' | 'baseline'>): string {
  const score = r.peak_score
  const s = score > 999 ? '>999' : decimal(score, score < 10 ? 2 : 1)
  if (r.detector.startsWith('robust') && r.baseline.basis === 'baseline') return `|z| = ${s}`
  if (r.detector === 'iforest') return `×${s} порога леса`
  if (r.detector === 'growth') return `×${s} минимума роста`
  return `×${s} предела`
}

/** A link to the large view of these series (`pages/Metric.tsx`). */
export function metricLink(
  host: string,
  series: { signal: string; entity?: string }[],
  extra: Record<string, string> = {},
) {
  const q = new URLSearchParams()
  series.forEach((s) => q.append('s', seriesKey(s)))
  Object.entries(extra).forEach(([k, v]) => q.set(k, v))
  return `/hosts/${host}/metric?${q}`
}

// Room around a record on the audit, so its stretch is seen whole.
const AUDIT_MARGIN_MS = 10 * 60_000

/** The audit around a record, opened at the stretch that holds it. */
export function auditLink(
  host: string,
  r: { id: number | string; started_at: string; ended_at: string | null },
) {
  const q = new URLSearchParams({ record: String(r.id) })
  if (r.ended_at) {
    q.set('since', new Date(Date.parse(r.started_at) - AUDIT_MARGIN_MS).toISOString())
    q.set('until', new Date(Math.min(Date.now(), Date.parse(r.ended_at) + AUDIT_MARGIN_MS)).toISOString())
  } else if (Date.now() - Date.parse(r.started_at) > 3600_000) {
    q.set('range', Date.now() - Date.parse(r.started_at) > 6 * 3600_000 ? '24h' : '6h')
  }
  return `/hosts/${host}/audit?${q}`
}
