// What the audit page draws from the stretches of the server (ADRs 0056, 0057).

import type { Command } from '../api/types'

/**
 * Commands of one name in one unit read as one: postgres with its workers,
 * chromium with its renderers. Process groups only tell builds apart.
 */
export function folded(commands: Command[], byMemory: boolean): Command[] {
  const out = new Map<string, Command>()
  for (const c of commands) {
    const key = `${c.unit ?? ''}|${c.leader ?? c.names[0][0]}`
    const have = out.get(key)
    if (!have) {
      out.set(key, { ...c, names: c.names.map(([n, k]) => [n, k]) })
      continue
    }
    have.cores += c.cores
    have.rss_bytes += c.rss_bytes
    have.pids += c.pids
    for (const [n, k] of c.names) {
      const same = have.names.find(([m]) => m === n)
      if (same) same[1] += k
      else have.names.push([n, k])
    }
  }
  const amount = (c: Command) => (byMemory ? c.rss_bytes : c.cores)
  return [...out.values()].sort((a, b) => amount(b) - amount(a))
}
