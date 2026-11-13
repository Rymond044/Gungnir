// Numbered tags over a graph, one per stretch of the audit or per group of
// stretches too close to tell apart (ADR 0056).

import type { Mark } from '../components/Chart'

// Marks closer than this share of the width share one tag.
const TAG_SHARE = 0.03

/** A number over the plot: one mark, or marks too close to tell apart. */
export interface Tag {
  key: string
  /** The marks it stands for, earliest first; a click goes to the first. */
  keys: string[]
  at: number
  label: string
  tone: Mark['tone']
  title: string
  row: number
  /** Near the right edge a tag ends at its mark instead of starting there. */
  end: boolean
}

const TONE_ORDER: Mark['tone'][] = ['red', 'gray', 'green']

/**
 * Tags of the marks: marks closer than a tag's width join one tag that names
 * the first and last number and how many there are, so a week of stretches
 * reads as a row of groups, not a column of numbers. A tag goes to the first
 * row where it keeps clear of the last one.
 */
export function tagsOf(marks: Mark[], since: number, until: number): Tag[] {
  const share = (m: Mark) => (Math.max(since, m.from) - since) / (until - since)
  const groups: Mark[][] = []
  for (const m of [...marks].sort((a, b) => a.from - b.from)) {
    const group = groups.at(-1)
    if (group && share(m) - share(group[0]) < TAG_SHARE) group.push(m)
    else groups.push([m])
  }
  const last: number[] = []
  return groups.map((g) => {
    const at = share(g[0])
    const many = g.length > 1
    const width = many ? TAG_SHARE * 2.5 : TAG_SHARE
    let row = last.findIndex((x) => at >= x)
    if (row === -1) row = last.length
    last[row] = at + width
    const tone = TONE_ORDER.find((t) => g.some((m) => m.tone === t)) ?? 'gray'
    return {
      key: g[0].key,
      keys: g.map((m) => m.key),
      at: g[0].from,
      label: many ? `${g[0].label}–${g.at(-1)!.label} · ${g.length}` : g[0].label,
      tone,
      title: many ? `${g.length} эпизодов: ${g.map((m) => m.label).join(', ')}` : g[0].title,
      row,
      end: at + width > 1,
    }
  })
}
