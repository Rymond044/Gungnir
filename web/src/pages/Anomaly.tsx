import { Link, useParams } from 'react-router'
import { useAnomalies, useAnomaly, useOverview } from '../api/hooks'
import type { RecordRow } from '../api/types'
import Entity from '../components/Entity'
import RecordChip from '../components/RecordChip'
import SignalChart from '../components/SignalChart'
import { Chip, Empty, Failure, Key, Loading, Panel } from '../components/ui'
import ui from '../components/ui.module.css'
import { against } from '../lib/basis'
import { dayClock, duration, formatValue, ms } from '../lib/format'
import { classNames, detectorName, directionNames, named, powerName, signalNames } from '../lib/names'
import { auditLink, isLevel, metricLink, recordInterval, recordLimit, scoreText } from '../lib/records'
import { useNow } from '../lib/useNow'

// The graph shows the record with room before it for what led up to it.
const BEFORE_MS = 10 * 60_000
const AFTER_MS = 5 * 60_000
const HOUR = 3600_000

/** What the graph must cover to show why the record opened. */
function windowOf(r: RecordRow, now: number): { since: number; until: number } {
  const start = ms(r.started_at)
  const end = r.ended_at ? ms(r.ended_at) : now
  if (r.detector === 'growth') {
    // Growth is judged on hourly minima over several hours (ADR 0042).
    const hours = r.baseline.hourly_minima?.length ?? 4
    return { since: start - (hours + 1) * HOUR, until: Math.min(now, end + HOUR) }
  }
  // A long record is shown whole, with a quarter of its length around it.
  const pad = (end - start) / 4
  return { since: start - Math.max(BEFORE_MS, pad), until: Math.min(now, end + Math.max(AFTER_MS, pad)) }
}

function explanation(r: RecordRow): string {
  switch (r.detector) {
    case 'robust.mad':
      return r.baseline.basis === 'baseline'
        ? 'Значение вышло за полосу нормы этого типа нагрузки и держалось там не меньше трёх замеров подряд.'
        : 'Нормы для этого типа нагрузки ещё нет; значение превысило предел холодного старта (пунктир).'
    case 'robust.ewma':
      return 'Сглаженное среднее (EWMA) медленно ушло от медианы нормы: это дрейф, а не всплеск, поэтому отдельные замеры могут оставаться в полосе.'
    case 'growth':
      return 'Минимум каждого часа (синие ступени) рос несколько часов подряд, каждый час заметно и без одного большого скачка: так выглядит утечка, а не всплеск и не разовая загрузка.'
    case 'threshold':
      return 'Значение пересекло фиксированный порог (пунктир).'
    case 'impact':
      return 'Программы ждали ресурс дольше допустимого уровня влияния (пунктир).'
    case 'iforest':
      return 'Isolation Forest счёл необычным сочетание нескольких метрик за последнюю минуту.'
    default:
      return ''
  }
}

export default function Anomaly() {
  const { host = '', id = '' } = useParams()
  const record = useAnomaly(host, id)
  const overview = useOverview(host)
  const now = useNow()
  const offset = overview.data?.host.utc_offset_s ?? null
  const r = record.data
  const w = r ? windowOf(r, now) : null
  const live = !!r && (r.ended_at === null || w!.until >= now - AFTER_MS)
  const interval = w
    ? {
        since: new Date(w.since).toISOString(),
        until: new Date(live ? Math.ceil(now / 10_000) * 10_000 : w.until).toISOString(),
      }
    : undefined
  const neighbours = useAnomalies(
    host,
    { ...interval, signal: r?.signal, entity: r?.entity, limit: 200 },
    !!r,
  )

  if (record.isPending) return <Loading />
  if (record.isError) return <Failure error={record.error} />
  if (!r || !interval) return null
  const start = ms(r.started_at)
  const end = r.ended_at ? ms(r.ended_at) : now
  const fmt = (v: number) => formatValue(r.signal, v)
  const limit = recordLimit(r)
  const others = (neighbours.data ?? []).filter((o) => o.id !== r.id)

  return (
    <div className={ui.page}>
      <div className={ui.crumbs}>
        <Link to={`/hosts/${host}/anomalies`}>Журнал аномалий</Link>
        <span>/</span>
        <span>{r.id}</span>
      </div>
      <div className={ui.head}>
        <h1>
          {named(signalNames, r.signal)}: {directionNames[r.direction]}
        </h1>
        <RecordChip r={r} />
        {r.ended_at ? (
          <Chip>закрыта</Chip>
        ) : (
          <Chip tone="ochre" dot>
            открыта
          </Chip>
        )}
        <span className="muted">
          <Entity entity={r.entity} /> · {dayClock(start, offset, now)}
          {r.ended_at ? ` — ${dayClock(end, offset, now)}` : ', всё ещё идёт'} ·{' '}
          {duration((end - start) / 1000)}
        </span>
        <Link to={auditLink(host, r)} className={`${ui.button} ${ui.primary}`} style={{ marginLeft: 'auto' }}>
          Что это было — аудит
        </Link>
      </div>

      <Panel
        title="Метрика вокруг записи"
        aside={
          <Link
            className={ui.expand}
            to={metricLink(host, [r], { since: interval.since, until: interval.until })}
          >
            <Key>{r.signal}</Key> ⤢ открыть крупно
          </Link>
        }
      >
        <span className="muted" style={{ fontSize: 13 }}>
          {explanation(r)}
        </span>
        <SignalChart
          host={host}
          series={[
            { signal: r.signal, entity: r.entity, name: named(signalNames, r.signal), color: 'var(--ink)' },
          ]}
          interval={interval}
          offsetS={offset}
          format={fmt}
          normOf={{ signal: r.signal, entity: r.entity }}
          extraLimits={limit ? [limit] : []}
          height={220}
          intervals={[
            ...others.map((o) => recordInterval(o, now, offset)),
            recordInterval(r, now, offset, true),
          ]}
          fitY={isLevel(r.signal)}
          ceiling={r.signal.endsWith('_ratio') ? 1 : undefined}
          live={live}
        />
      </Panel>

      <div className={ui.row}>
        <Panel title="Запись">
          <div className={ui.facts}>
            <span>детектор</span>
            <span>
              {detectorName(r.detector)} <Key>{r.detector}</Key>
            </span>
            <span>оценка</span>
            <span className="mono">{scoreText(r)}</span>
            <span>значение при открытии</span>
            <span>{fmt(r.value ?? NaN)}</span>
            <span>с чем сравнивали</span>
            <span>{against(r.signal, r.baseline)}</span>
            <span>тип нагрузки</span>
            <span>{named(classNames, r.workload_class ?? 'unknown')}</span>
            <span>питание</span>
            <span>{powerName(r.power_profile)}</span>
            <span>ожидаемое поведение</span>
            <span>
              {r.expected === null ? 'не оценивалось' : r.expected ? 'да, тип нагрузки объясняет' : 'нет'}
            </span>
            <span>закрывается</span>
            <span>после трёх замеров подряд в норме</span>
          </div>
        </Panel>
        {r.context && Object.keys(r.context).length > 0 && (
          <Panel title="Контекст" aside="медианы за интервал">
            <div className={ui.facts}>
              {Object.entries(r.context).map(([signal, value]) => (
                <Fact key={signal} signal={signal} value={value} />
              ))}
            </div>
          </Panel>
        )}
      </div>

      <Neighbours host={host} signal={r.signal} entity={r.entity} self={r.id} offset={offset} now={now} />
    </div>
  )
}

function Fact({ signal, value }: { signal: string; value: number }) {
  return (
    <>
      <span>
        {named(signalNames, signal)} <Key>{signal}</Key>
      </span>
      <span>{formatValue(signal, value)}</span>
    </>
  )
}

function Neighbours(props: {
  host: string
  signal: string
  entity: string
  self: number
  offset: number | null
  now: number
}) {
  const week = {
    since: new Date(props.now - 7 * 86400_000).toISOString(),
    until: new Date(props.now).toISOString(),
  }
  const others = useAnomalies(props.host, { ...week, signal: props.signal, entity: props.entity, limit: 50 })
  const rows = (others.data ?? []).filter((o) => o.id !== props.self)
  return (
    <Panel title="Другие записи этой метрики" aside="7 дней">
      {rows.length === 0 ? (
        <Empty>Других записей нет.</Empty>
      ) : (
        <table className={ui.fixed}>
          <colgroup>
            <col style={{ width: 150 }} />
            <col />
            <col style={{ width: 110 }} />
            <col style={{ width: 170 }} />
            <col style={{ width: 150 }} />
          </colgroup>
          <tbody>
            {rows.map((o) => (
              <tr key={o.id} className={ui.tableRow}>
                <td className="mono">
                  <Link to={`/hosts/${props.host}/anomalies/${o.id}`}>
                    {dayClock(ms(o.started_at), props.offset, props.now)}
                  </Link>
                </td>
                <td>{detectorName(o.detector)}</td>
                <td>{directionNames[o.direction]}</td>
                <td className="mono">{scoreText(o)}</td>
                <td className="muted">
                  {o.ended_at ? duration((ms(o.ended_at) - ms(o.started_at)) / 1000) : 'открыта'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Panel>
  )
}
