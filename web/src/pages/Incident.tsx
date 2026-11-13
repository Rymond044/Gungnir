import { Link, useParams } from 'react-router'
import { useAnomalies, useDecision, useIncident, useOverview } from '../api/hooks'
import type { Incident as IncidentData, IncidentHypothesis, Proposal } from '../api/types'
import Entity from '../components/Entity'
import Hypothesis from '../components/Hypothesis'
import RecordChip from '../components/RecordChip'
import { Chip, Empty, Failure, Key, Loading, Panel, Source, type Tone } from '../components/ui'
import ui from '../components/ui.module.css'
import { confidence, dayClock, duration, formatNumber, formatValue, ms, span } from '../lib/format'
import { actionNames, causeNames, classNames, likelihoodNames, named, signalNames } from '../lib/names'
import { useNow } from '../lib/useNow'

const STATES: Record<Proposal['state'], { label: string; tone: Tone }> = {
  proposed: { label: 'ждёт решения', tone: 'ochre' },
  confirmed: { label: 'подтверждено', tone: 'green' },
  rejected: { label: 'отклонено', tone: 'plain' },
  expired: { label: 'устарело', tone: 'plain' },
}
// Before and after the incident on the audit it opens.
const AUDIT_MARGIN_MS = 5 * 60_000

/**
 * One incident (ADR 0060): when someone was hurt, by what and by whom, what
 * the policy proposes and what the operator decided.
 */
export default function Incident() {
  const { host = '', id = '' } = useParams()
  const now = useNow()
  const overview = useOverview(host)
  const incident = useIncident(host, id)
  const offset = overview.data?.host.utc_offset_s ?? null

  if (incident.isPending) return <Loading />
  if (incident.isError) return <Failure error={incident.error} />
  const { summary: s, hypotheses } = incident.data
  const open = s.closed_at === null
  const start = ms(s.opened_at)
  const end = s.closed_at ? ms(s.closed_at) : now
  const audit = new URLSearchParams({
    since: new Date(start - AUDIT_MARGIN_MS).toISOString(),
    until: new Date(Math.min(now, end + AUDIT_MARGIN_MS)).toISOString(),
    harm: '1',
  })
  const harmful = hypotheses.filter((h) => h.harm && h.rank !== null)
  const other = hypotheses.filter((h) => !harmful.includes(h))

  return (
    <div className={ui.page}>
      <div className={ui.crumbs}>
        <Link to={`/hosts/${host}/incidents`}>Инциденты</Link>
        <span>/</span>
        <span>{s.id}</span>
      </div>
      <div className={ui.head}>
        <h1>Инцидент {s.id}</h1>
        {open ? (
          <Chip tone="red" dot>
            открыт
          </Chip>
        ) : (
          <Chip>закрыт</Chip>
        )}
        <span className="muted">
          {dayClock(start, offset, now)} — {open ? 'идёт сейчас' : dayClock(end, offset, now)} ·{' '}
          {duration((end - start) / 1000)}
          {s.workload_class && ` · нагрузка: ${named(classNames, s.workload_class)}`}
        </span>
        <Link
          to={`/hosts/${host}/audit?${audit}`}
          className={`${ui.button} ${ui.primary}`}
          style={{ marginLeft: 'auto' }}
        >
          Аудит этого времени
        </Link>
      </div>
      {open && (
        <span className="muted" style={{ fontSize: 13 }}>
          Закроется через две минуты после того, как вред прекратится; последний вред —{' '}
          {dayClock(ms(s.last_harm_at), offset, now)}.
        </span>
      )}

      <Proposals host={host} incident={incident.data} open={open} offset={offset} now={now} />

      <Panel title="Что мешало" aside={harmful.length > 0 ? 'гипотезы по рангу' : undefined}>
        {harmful.length === 0 ? (
          <Empty>
            Ни одна причина из базы знаний не объясняет вред; что было необычным и кто занимал ресурс — в
            аудите этого времени.
          </Empty>
        ) : (
          harmful.map((h, i) => (
            <Found key={h.id} h={h} first={i === 0} host={host} offset={offset} now={now} />
          ))
        )}
      </Panel>

      {other.length > 0 && (
        <Panel title="Что ещё было в это время" aside="без вреда или уже не подтверждается">
          {other.map((h) => (
            <Found key={h.id} h={h} host={host} offset={offset} now={now} />
          ))}
        </Panel>
      )}

      <Records host={host} incident={s.id} offset={offset} now={now} />
    </div>
  )
}

function Found({
  h,
  first = false,
  host,
  offset,
  now,
}: {
  h: IncidentHypothesis
  /** The best harmful hypothesis is shown unfolded. */
  first?: boolean
  host: string
  offset: number | null
  now: number
}) {
  return (
    <details open={first} style={{ marginBottom: 10 }}>
      <summary>
        {h.rank !== null ? `${h.rank}. ` : ''}
        <Link to={`/hosts/${host}/knowledge/${h.cause}`}>{named(causeNames, h.cause)}</Link>{' '}
        <Key>{h.cause}</Key> {h.culprit && <Entity entity={h.culprit} />} ·{' '}
        <span className="mono">{confidence(h.peak_confidence)}</span>
        {h.harm ? (
          <>
            {' '}
            <Chip tone="red">мешало</Chip>
          </>
        ) : (
          <>
            {' '}
            <Chip>без вреда</Chip>
          </>
        )}
        {h.expected && (
          <>
            {' '}
            <Chip tone="green">ожидаемо</Chip>
          </>
        )}
        {h.rank === null && <span className="muted"> · больше не подтверждается</span>}
        <span className="muted"> · с {dayClock(ms(h.first_seen), offset, now)}</span>
      </summary>
      <Hypothesis h={{ ...h.tree, actions: [] }} host={host} offset={offset} withActions={false} />
    </details>
  )
}

function Proposals({
  host,
  incident,
  open,
  offset,
  now,
}: {
  host: string
  incident: IncidentData
  open: boolean
  offset: number | null
  now: number
}) {
  const decide = useDecision(host, incident.summary.id)
  const { proposals, hypotheses } = incident
  const causeOf = new Map(hypotheses.map((h) => [h.id, named(causeNames, h.cause)]))
  if (proposals.length === 0) {
    return (
      <Panel title="Что предлагается">
        <Empty>
          Предложений нет: действия предлагаются только против виновника, который мешал другим, и никогда —
          против защищённых служб.
        </Empty>
      </Panel>
    )
  }
  return (
    <Panel title="Что предлагается" aside="по рангу гипотезы, затем по пользе и риску">
      <span className="muted" style={{ fontSize: 13 }}>
        Подтверждение записывает решение оператора. Применять действия будет исполнитель с автоматическим
        откатом; пока решение только записывается.
      </span>
      <table className={ui.fixed}>
        <colgroup>
          <col />
          <col style={{ width: 260 }} />
          <col style={{ width: 200 }} />
        </colgroup>
        <tbody>
          {proposals.map((p) => (
            <tr key={p.id} className={ui.tableRow}>
              <td>
                <div>
                  <b>{named(actionNames, p.action)}</b> <Key>{p.action}</Key> для <Entity entity={p.target} />
                </div>
                <div className="muted" style={{ fontSize: 13 }}>
                  {parameterText(p)} · против «{causeOf.get(p.hypothesis_id)}» · помогает{' '}
                  {likelihoodNames[p.effectiveness] ?? p.effectiveness}
                  <Source source={p.source} />
                </div>
              </td>
              <td className="muted" style={{ fontSize: 13 }}>
                {p.side_effects.length === 0
                  ? 'побочных эффектов не известно'
                  : p.side_effects
                      .map((e) => `${e.effect} (${likelihoodNames[e.likelihood] ?? e.likelihood})`)
                      .join('; ')}
                <div>откат через {span(p.rollback_after_s)}</div>
              </td>
              <td>
                {p.state === 'proposed' && open ? (
                  <div style={{ display: 'flex', gap: 6 }}>
                    <button
                      className={`${ui.button} ${ui.primary}`}
                      disabled={decide.isPending}
                      onClick={() => decide.mutate({ proposal: p.id, decision: 'confirm' })}
                    >
                      Подтвердить
                    </button>
                    <button
                      className={ui.button}
                      disabled={decide.isPending}
                      onClick={() => decide.mutate({ proposal: p.id, decision: 'reject' })}
                    >
                      Отклонить
                    </button>
                  </div>
                ) : (
                  <span>
                    <Chip tone={STATES[p.state].tone}>{STATES[p.state].label}</Chip>
                    {p.decided_at && (
                      <span className="muted" style={{ fontSize: 12.5 }}>
                        {' '}
                        {dayClock(ms(p.decided_at), offset, now)}
                      </span>
                    )}
                  </span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {decide.isError && <Failure error={decide.error} />}
    </Panel>
  )
}

/** The parameter in words: what the limit is and what it is half of. */
function parameterText(p: Proposal): string {
  const { signal, share, current, value } = p.parameter
  if (!signal) return `вес ×${formatNumber(share)} от текущего`
  const of = `${Math.round(share * 100)} % от «${named(signalNames, signal)}»`
  if (value === null || current === null) return `${of}; сколько это — исполнитель прочтёт перед применением`
  return `до ${formatValue(signal, value)}: ${of}, за последнюю минуту ${formatValue(signal, current)}`
}

function Records({
  host,
  incident,
  offset,
  now,
}: {
  host: string
  incident: number
  offset: number | null
  now: number
}) {
  const records = useAnomalies(host, { incident, limit: 500 })
  const rows = records.data ?? []
  return (
    <Panel title="Записи детекции" aside={`${rows.length}`}>
      {rows.length === 0 ? (
        <Empty>Записей нет.</Empty>
      ) : (
        <table className={ui.fixed}>
          <colgroup>
            <col style={{ width: 150 }} />
            <col style={{ width: 130 }} />
            <col />
            <col style={{ width: 120 }} />
          </colgroup>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id} className={ui.tableRow}>
                <td className="mono">
                  <Link to={`/hosts/${host}/anomalies/${r.id}`}>
                    {dayClock(ms(r.started_at), offset, now)}
                  </Link>
                </td>
                <td>
                  <RecordChip r={r} />
                </td>
                <td>
                  {named(signalNames, r.signal)} {r.direction === 'up' ? '↑' : '↓'} <Key>{r.signal}</Key>
                  {r.entity !== 'host' && (
                    <>
                      {' · '}
                      <Entity entity={r.entity} />
                    </>
                  )}
                </td>
                <td className="muted">
                  {r.ended_at ? duration((ms(r.ended_at) - ms(r.started_at)) / 1000) : 'открыта'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Panel>
  )
}
