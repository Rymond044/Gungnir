// The interval a screen shows, kept in the address: a period ending now
// (`range`) or a fixed stretch (`since`, `until`), so a link opens the same
// view. Times are picked in the host's clock, as every screen shows them.

import { useSearchParams } from 'react-router'
import { lastInterval, rangeSeconds, type RangeKey } from './range'

export interface Shown {
  interval: { since: string; until: string }
  /** A period ending now, which moves on with every poll. */
  live: boolean
  /** The period, when one is chosen. */
  range: RangeKey | null
  setRange: (range: RangeKey) => void
  setSpan: (since: number, until: number) => void
  /** Something other than the screen's own period is shown. */
  changed: boolean
  /** Back to the screen's own period ending now. */
  reset: () => void
}

export function useShownInterval(fallback: RangeKey, now: number): Shown {
  const [params, setParams] = useSearchParams()
  const since = params.get('since')
  const until = params.get('until')
  const fixed = !!since && !!until
  const range = fixed ? null : ((params.get('range') as RangeKey | null) ?? fallback)
  const update = (next: Record<string, string | null>) => {
    const q = new URLSearchParams(params)
    Object.entries(next).forEach(([k, v]) => (v === null ? q.delete(k) : q.set(k, v)))
    setParams(q, { replace: false })
  }
  return {
    interval: fixed ? { since: since!, until: until! } : lastInterval(rangeSeconds(range!), now),
    live: !fixed,
    range,
    setRange: (r) => update({ range: r === fallback ? null : r, since: null, until: null }),
    setSpan: (a, b) =>
      update({ range: null, since: new Date(a).toISOString(), until: new Date(b).toISOString() }),
    changed: fixed || range !== fallback,
    reset: () => update({ range: null, since: null, until: null }),
  }
}

/** A moment in the host's clock. */
export interface Wall {
  year: number
  month: number // 0-11
  day: number
  hour: number
  minute: number
}

export function toWall(ms: number, offsetS: number | null): Wall {
  const d = new Date(ms + (offsetS ?? 0) * 1000)
  return {
    year: d.getUTCFullYear(),
    month: d.getUTCMonth(),
    day: d.getUTCDate(),
    hour: d.getUTCHours(),
    minute: d.getUTCMinutes(),
  }
}

export function fromWall(w: Wall, offsetS: number | null): number {
  return Date.UTC(w.year, w.month, w.day, w.hour, w.minute) - (offsetS ?? 0) * 1000
}
