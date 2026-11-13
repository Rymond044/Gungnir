// Periods the screens offer, and the interval they mean now.

export type RangeKey = '15m' | '1h' | '6h' | '24h' | '7d' | '30d'

export const RANGES: { value: RangeKey; label: string; seconds: number }[] = [
  { value: '15m', label: '15 мин', seconds: 900 },
  { value: '1h', label: '1 ч', seconds: 3600 },
  { value: '6h', label: '6 ч', seconds: 6 * 3600 },
  { value: '24h', label: '24 ч', seconds: 86400 },
  { value: '7d', label: '7 дн', seconds: 7 * 86400 },
  { value: '30d', label: '30 дн', seconds: 30 * 86400 },
]

export function rangeSeconds(key: RangeKey): number {
  return RANGES.find((r) => r.value === key)?.seconds ?? 3600
}

/**
 * The interval ending now, with its end rounded to 10 s: queries made within
 * one polling period share a key and the cache, and the graph moves on with
 * each poll.
 */
export function lastInterval(seconds: number, now = Date.now()): { since: string; until: string } {
  const until = Math.ceil(now / 10_000) * 10_000
  return { since: new Date(until - seconds * 1000).toISOString(), until: new Date(until).toISOString() }
}
