import { useParams, useSearchParams } from 'react-router'
import { useDiagnosis, useKnowledge, useOverview, useRuns, type DiagnosisRequest } from '../api/hooks'
import type { Diagnosis, EvidenceItem, SignalInfo } from '../api/types'
import Evidence from '../components/Evidence'
import Hypothesis from '../components/Hypothesis'
import { Empty, Failure, Key, Loading, Panel, Segmented, Source } from '../components/ui'
import ui from '../components/ui.module.css'
import { confidence, dayClock, ms } from '../lib/format'
import { lastInterval } from '../lib/range'
import { causeNames, named, resourceNames, symptomNames } from '../lib/names'
import { useNow } from '../lib/useNow'
import styles from './Analysis.module.css'

type Mode = 'last' | 'interval' | 'run'
const MINUTES = ['15', '30', '60'] as const
type Minutes = (typeof MINUTES)[number]

/**
 * Ranked hypotheses of an interval from the inference engine: the last
 * minutes (live), an interval around a record, or each phase of a labeled
 * scenario run.
 */
export default function Analysis() {
  const { host = '' } = useParams()
  const [params, setParams] = useSearchParams()
  const now = useNow()
  const overview = useOverview(host)
  const runs = useRuns(host)
  const kb = useKnowledge(host)
  const signals = new Map((kb.data?.signals ?? []).map((s) => [s.name, s]))
  const offset = overview.data?.host.utc_offset_s ?? null

  const since = params.get('since')
  const until = params.get('until')
  const run = params.get('run')
  const mode: Mode = run ? 'run' : since && until ? 'interval' : 'last'
  const minutes = (params.get('minutes') as Minutes) ?? '30'

  const request: DiagnosisRequest | null =
    mode === 'run'
      ? { run: run! }
      : mode === 'interval'
        ? { since: since!, until: until! }
        : lastInterval(Number(minutes) * 60, now)
  const diagnosis = useDiagnosis(host, request, mode === 'last')

  const choose = (next: Record<string, string>) => setParams(new URLSearchParams(next), { replace: true })
  const scenarioRuns = (runs.data ?? []).filter((r) => r.phases.some((p) => !p.normal))

  return (
    <div className={ui.page}>
      <div className={ui.head}>
        <h1>Анализ причин</h1>
        <span className="muted">механизм вывода по свидетельствам интервала и базе знаний</span>
      </div>
      <div className={ui.head}>
        <Segmented<Mode>
          label="Что анализировать"
          options={[
            { value: 'last', label: 'Последние минуты' },
            { value: 'run', label: 'Прогон сценария' },
            ...(mode === 'interval' ? [{ value: 'interval' as Mode, label: 'Интервал' }] : []),
          ]}
          value={mode}
          onChange={(m) => {
            if (m === 'last') choose({})
            if (m === 'run' && scenarioRuns[0]) choose({ run: scenarioRuns[0].run })
          }}
        />
        {mode === 'last' && (
          <Segmented<Minutes>
            label="Минуты"
            options={MINUTES.map((m) => ({ value: m, label: `${m} мин` }))}
            value={minutes}
            onChange={(m) => choose(m === '30' ? {} : { minutes: m })}
          />
        )}
        {mode === 'run' && (
          <select value={run ?? ''} onChange={(e) => choose({ run: e.target.value })} aria-label="Прогон">
            {scenarioRuns.map((r) => (
              <option key={r.run} value={r.run}>
                {r.run} (
                {r.phases
                  .filter((p) => !p.normal)
                  .map((p) => p.phase)
                  .join(', ')}
                )
              </option>
            ))}
          </select>
        )}
        {diagnosis.isFetching && <span className="muted">считаю…</span>}
      </div>

      {diagnosis.isPending ? (
        <Loading />
      ) : diagnosis.isError ? (
        <Failure error={diagnosis.error} />
      ) : (
        // Keyed by place, not by time: the live interval moves with every poll,
        // and a new key would remount the span and close what the operator opened.
        diagnosis.data.map((d, i) => (
          <Span key={`${i}-${d.label}`} d={d} host={host} offset={offset} now={now} signals={signals} />
        ))
      )}
    </div>
  )
}

function Span({
  d,
  host,
  offset,
  now,
  signals,
}: {
  d: Diagnosis
  host: string
  offset: number | null
  now: number
  signals: Map<string, SignalInfo>
}) {
  const title = d.label ? `Фаза ${d.label}` : 'Интервал'
  const e = d.evidence
  return (
    <div className={ui.page}>
      <div className={ui.head}>
        <h2>{title}</h2>
        <span className="muted">
          {dayClock(ms(d.since), offset, now)} — {dayClock(ms(d.until), offset, now)}
        </span>
      </div>
      <Panel>
        <span>
          Механизм вывода получил из базы{' '}
          <b>
            {e.records} {plural(e.records, 'запись', 'записи', 'записей')} детекции
          </b>
          , <b>{e.events} событий</b> ядра и журнала известных базе знаний типов и{' '}
          <b>{e.processes} симптомов процессов</b> за интервал и сопоставил их с правилами базы знаний:{' '}
          <b>
            {d.hypotheses.length} {plural(d.hypotheses.length, 'гипотеза', 'гипотезы', 'гипотез')}
          </b>
          , {d.unexplained.length} без объяснения.
        </span>
      </Panel>
      {d.hypotheses.length === 0 ? (
        <Panel>
          <Empty>
            {d.unexplained.length === 0
              ? 'Свидетельств нет: хост в норме.'
              : d.near.length > 0
                ? 'Причина не названа: ни одно правило не сошлось полностью. Ниже — какие причины были близко и чего им не хватило.'
                : 'Причина не названа: необычные записи есть, но ни одно правило не узнаёт в них свой ведущий симптом.'}
          </Empty>
        </Panel>
      ) : (
        d.hypotheses.map((h, i) => (
          <Panel key={`${h.cause}${h.culprit}`}>
            <Hypothesis h={h} rank={i + 1} host={host} offset={offset} />
          </Panel>
        ))
      )}
      {d.near.length > 0 && (
        <Panel title="Почти подошли" aside="ведущий симптом найден, но правило не сошлось">
          {d.near.map((h) => (
            <details key={`${h.cause}${h.culprit}`} className={styles.near}>
              <summary>
                <b>{named(causeNames, h.cause)}</b> <Key>{h.cause}</Key> — {reasonText(h)}
              </summary>
              <Hypothesis h={h} host={host} offset={offset} withActions={false} />
            </details>
          ))}
        </Panel>
      )}
      {d.advisories.length > 0 && (
        <Panel title="Рекомендации" aside="влияние есть, а причина не найдена">
          {d.advisories.map((a, i) => {
            const why = a.resource ? impactOn(a.resource, d.unexplained, signals) : []
            return (
              <div key={i} style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                <span style={{ fontWeight: 500 }}>
                  {a.cause ? named(causeNames, a.cause) : named(resourceNames, a.resource)}
                </span>
                <span>{a.text}</span>
                {why.length > 0 && (
                  <span className="muted" style={{ fontSize: 13 }}>
                    Влияние:{' '}
                    {why.slice(0, SHOWN_WHY).map((item, j) => (
                      <span key={j}>
                        {j > 0 && '; '}
                        <Evidence item={item} host={host} offset={offset} withName />
                      </span>
                    ))}
                    {why.length > SHOWN_WHY && `; ещё ${why.length - SHOWN_WHY}`}
                  </span>
                )}
                <Source source={a.source} />
              </div>
            )
          })}
        </Panel>
      )}
      {(d.unexplained.length > 0 || d.expected.length > 0) && (
        <Panel title="Без объяснения" aside={`${d.unexplained.length} свидетельств`}>
          {d.expected.length > 0 && (
            <span className="muted">
              Ожидаемое поведение объясняет записи: {d.expected.map((id) => `#${id}`).join(', ')}.
            </span>
          )}
          {d.unexplained.length > 0 && (
            <details>
              <summary>Записи и события, которые не использует ни одна гипотеза</summary>
              <ul
                style={{
                  margin: '8px 0 0',
                  paddingLeft: 18,
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 4,
                }}
              >
                {d.unexplained.map((item, i) => (
                  <li key={i}>
                    <Evidence item={item} host={host} offset={offset} withName />
                  </li>
                ))}
              </ul>
            </details>
          )}
        </Panel>
      )}
    </div>
  )
}

const SHOWN_WHY = 3

/**
 * The records that raised an advisory: impact, or an anomaly of a signal that
 * measures impact, on the advisory's resource (server inference._advisories).
 */
function impactOn(resource: string, items: EvidenceItem[], signals: Map<string, SignalInfo>): EvidenceItem[] {
  return items.filter((item) => {
    if (item.type !== 'record') return false
    const s = signals.get(item.signal)
    return s?.resource === resource && (item.kind === 'impact' || s.impact)
  })
}

function plural(n: number, one: string, few: string, many: string): string {
  const m10 = n % 10
  const m100 = n % 100
  if (m10 === 1 && m100 !== 11) return one
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few
  return many
}

// Why a cause whose leading symptom was seen is not named (ADR 0055).
function reasonText(h: Diagnosis['near'][number]): string {
  const missing = h.symptoms
    .filter((s) => s.status === 'missing' && s.strength === 'characteristic')
    .map((s) => symptomNames(s.kind, s.names))
  switch (h.reason) {
    case 'no impact':
      return 'её симптомы влияния не найдены: необычно, но никому не мешает'
    case 'characteristic missing':
      return `нет обязательного симптома: ${missing.join(', ')}`
    case 'contradicted':
      return 'найден противоречащий симптом, причина исключена'
    case 'only impact':
      return 'видно только влияние, без признаков самой причины'
    case 'below cut-off':
      return `уверенность ${confidence(h.confidence)} ниже порога ${confidence(0.3)}`
    default:
      return h.reason
  }
}
