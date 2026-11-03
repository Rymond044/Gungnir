// Reading the knowledge base: what a cause shares with others and what
// tells them apart, from the rules alone.

import type { Knowledge, RuleInfo, RuleSymptom } from '../api/types'

export const DOMAINS = ['disk', 'memory', 'cpu', 'cross'] as const

export function symptomKey(s: Pick<RuleSymptom, 'kind' | 'names'>): string {
  return `${s.kind}:${[...s.names].sort().join('|')}`
}

/** Each name a symptom may match, as a key of the graph. */
export function nameKeys(s: Pick<RuleSymptom, 'kind' | 'names'>): string[] {
  const kind = s.kind === 'value' ? 'anomaly' : s.kind
  return s.names.map((n) => `${kind}:${n}`)
}

export interface Similar {
  cause: string
  shared: string[]
  /** Names the other rule expects and this one does not. */
  onlyOther: string[]
  /** Names this rule expects and the other does not. */
  onlyThis: string[]
}

function names(rule: RuleInfo, contradicting = false): Set<string> {
  return new Set(
    rule.symptoms.filter((s) => (s.strength === 'contradicting') === contradicting).flatMap((s) => s.names),
  )
}

/** Causes whose rules share symptoms with this one, most shared first. */
export function similar(kb: Knowledge, cause: string): Similar[] {
  const rule = kb.rules.find((r) => r.cause === cause)
  if (!rule) return []
  const mine = names(rule)
  return kb.rules
    .filter((r) => r.cause !== cause)
    .map((r) => {
      const theirs = names(r)
      return {
        cause: r.cause,
        shared: [...mine].filter((n) => theirs.has(n)),
        onlyOther: [...theirs].filter((n) => !mine.has(n)),
        onlyThis: [...mine].filter((n) => !theirs.has(n)),
      }
    })
    .filter((s) => s.shared.length > 0)
    .sort((a, b) => b.shared.length - a.shared.length)
}

export function labelOf(
  kb: Knowledge | undefined,
  category: 'causes' | 'actions',
  name: string,
): string | null {
  return kb?.[category].find((x) => x.name === name)?.label ?? null
}
