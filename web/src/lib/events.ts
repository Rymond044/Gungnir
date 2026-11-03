// Kernel and journal messages come in bursts: a driver that fails to resume
// writes the same handful of lines after every wake-up. A burst is the
// messages within a couple of seconds of each other; bursts with the same
// lines are one entry that remembers when it repeated.

import type { EventRow } from '../api/types'
import { ms } from './format'

const BURST_MS = 2000

export interface EventGroup {
  /** Start of each burst, newest first. */
  times: number[]
  /** The distinct lines of a burst, `unit: message` where the unit is known. */
  lines: string[]
}

/** Groups events given newest first. */
export function groupEvents(events: EventRow[]): EventGroup[] {
  const bursts: { at: number; lines: string[] }[] = []
  let last: number | null = null
  for (const e of events) {
    const t = ms(e.ts)
    const text = e.unit ? `${e.unit}: ${e.message}` : e.message
    const burst = bursts[bursts.length - 1]
    if (burst && last !== null && last - t <= BURST_MS) {
      burst.at = t
      if (!burst.lines.includes(text)) burst.lines.push(text)
    } else {
      bursts.push({ at: t, lines: [text] })
    }
    last = t
  }
  const groups = new Map<string, EventGroup>()
  for (const b of bursts) {
    const key = [...b.lines].sort().join('\n')
    const g = groups.get(key)
    if (g) g.times.push(b.at)
    else groups.set(key, { times: [b.at], lines: b.lines })
  }
  return [...groups.values()]
}
