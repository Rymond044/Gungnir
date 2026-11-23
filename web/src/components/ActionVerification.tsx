import { Link } from 'react-router'
import type { Action } from '../api/types'
import { outcomes } from '../lib/actions'
import { dayClock, ms } from '../lib/format'
import { named, signalNames } from '../lib/names'
import { metricLink } from '../lib/records'
import Entity from './Entity'

// The verifier compares 30 s before application with the last 30 s of its window (ADR 0063).
const TAIL_MS = 30_000

function evidence(value: unknown): string[] | null {
  return Array.isArray(value) && value.every((item) => typeof item === 'string') ? [...new Set(value)] : null
}

export default function ActionVerification({
  action,
  host,
  offset,
  now,
}: {
  action: Action
  host: string
  offset: number | null
  now: number
}) {
  const verified = [...action.log].reverse().find((e) => e.event === 'verified')
  if (!verified && !action.outcome) return null
  const before = evidence(verified?.detail.before)
  const after = evidence(verified?.detail.after)
  return (
    <section aria-label="Проверка эффекта" style={{ margin: '10px 0' }}>
      <b>Проверка эффекта</b>
      {action.outcome && <div>{outcomes[action.outcome].title}</div>}
      <div className="muted">
        Сравнивается влияние на другие программы, без ожидания самой ограниченной цели.
      </div>
      {verified && before !== null && after !== null && action.applied_at ? (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '12px 24px', marginTop: 6 }}>
          {[
            { label: 'До применения', items: before, until: ms(action.applied_at) },
            { label: 'В конце проверки', items: after, until: ms(verified.at) },
          ].map(({ label, items, until }) => {
            const since = until - TAIL_MS
            const interval = { since: new Date(since).toISOString(), until: new Date(until).toISOString() }
            return (
              <div key={label} style={{ flex: '1 1 240px', minWidth: 0 }}>
                <b>{label}</b>
                <div className="muted">
                  {dayClock(since, offset, now)} — {dayClock(until, offset, now)} · 30 с
                </div>
                {items.length === 0 ? (
                  <div>Признаков деградации других программ не обнаружено.</div>
                ) : (
                  items.map((item) => {
                    const at = item.indexOf('@')
                    if (at < 0) return <div key={item}>{item}</div>
                    const signal = item.slice(0, at)
                    const entity = item.slice(at + 1)
                    return (
                      <div key={item}>
                        <Link to={metricLink(host, [{ signal, entity }], interval)}>
                          {named(signalNames, signal)}
                        </Link>{' '}
                        · <Entity entity={entity} />
                      </div>
                    )
                  })
                )}
                <Link to={`/hosts/${host}/audit?${new URLSearchParams(interval)}`}>Аудит интервала</Link>
              </div>
            )
          })}
        </div>
      ) : (
        <div className="muted">Подробные свидетельства этой проверки не сохранены.</div>
      )}
    </section>
  )
}
