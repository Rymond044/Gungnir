import { Link, useParams } from 'react-router'
import { useAnomalies, useAnomaly, useOverview } from '../api/hooks'
import SignalChart from '../components/SignalChart'
import { Empty, Failure, Key, Loading, Panel } from '../components/ui'
import ui from '../components/ui.module.css'
import { against, scoreText } from '../lib/basis'
import { dayClock, duration, formatValue, ms } from '../lib/format'
import { classNames, detectorName, directionNames, named, signalNames } from '../lib/names'
import { useNow } from '../lib/useNow'
import RecordChip from '../components/RecordChip'

// The graph shows the record with room before it for what led up to it.
const BEFORE_MS = 10 * 60_000
const AFTER_MS = 5 * 60_000
// The interval handed to analysis: the windows of the rules reach about a
// minute around the leading symptom.
const ANALYSIS_MARGIN_MS = 60_000

export default function Anomaly() {
  const { host = '', id = '' } = useParams()
  const record = useAnomaly(host, id)
  const overview = useOverview(host)
  const now = useNow()
  const offset = overview.data?.host.utc_offset_s ?? null

  if (record.isPending) return <Loading />
  if (record.isError) return <Failure error={record.error} />
  const r = record.data
  const start = ms(r.started_at)
  const end = r.ended_at ? ms(r.ended_at) : now
  const since = start - BEFORE_MS
  const until = Math.min(now, end + AFTER_MS)
  const live = r.ended_at === null || until >= now - AFTER_MS
  const interval = {
    since: new Date(since).toISOString(),
    until: new Date(live ? Math.ceil(now / 10_000) * 10_000 : until).toISOString(),
  }
  const analysis = new URLSearchParams({
    since: new Date(start - ANALYSIS_MARGIN_MS).toISOString(),
    until: new Date(Math.min(now, end + ANALYSIS_MARGIN_MS)).toISOString(),
  })
  const fmt = (v: number) => formatValue(r.signal, v)

  return (
    <div className={ui.page}>
      <div className={ui.crumbs}>
        <Link to={`/hosts/${host}/anomalies`}>Аномалии</Link>
        <span>/</span>
        <span>{r.id}</span>
      </div>
      <div className={ui.head}>
        <h1>
          {named(signalNames, r.signal)}: {directionNames[r.direction]}
        </h1>
        <RecordChip r={r} />
        <span className="muted">
          {r.entity === 'host' ? 'хост' : <span className="mono">{r.entity}</span>} · с{' '}
          {dayClock(start, offset, now)} · {duration((end - start) / 1000)}
          {!r.ended_at && ' · открыта'}
        </span>
        <Link
          to={`/hosts/${host}/analysis?${analysis}`}
          className={`${ui.button} ${ui.primary}`}
          style={{ marginLeft: 'auto' }}
        >
          Анализ причин
        </Link>
      </div>

      <Panel title="Метрика вокруг записи" aside={<Key>{r.signal}</Key>}>
        <SignalChart
          host={host}
          series={[
            { signal: r.signal, entity: r.entity, name: named(signalNames, r.signal), color: 'var(--ink)' },
          ]}
          interval={interval}
          offsetS={offset}
          format={fmt}
          normOf={{ signal: r.signal, entity: r.entity }}
          height={180}
          shade={{ from: start, to: end }}
          marks={[{ at: start, label: `начало ${dayClock(start, offset, now)}` }]}
          ceiling={r.signal.endsWith('_ratio') ? 1 : undefined}
          live={live}
        />
      </Panel>

      <div className={ui.row}>
        <Panel title="Запись" className="">
          <div className={ui.facts}>
            <span>детектор</span>
            <span>
              {detectorName(r.detector)} <Key>{r.detector}</Key>
            </span>
            <span>оценка</span>
            <span className="mono">{scoreText(r.detector, r.peak_score)}</span>
            <span>значение при открытии</span>
            <span>{fmt(r.value ?? NaN)}</span>
            <span>с чем сравнивали</span>
            <span>{against(r.signal, r.baseline)}</span>
            <span>тип нагрузки</span>
            <span>{named(classNames, r.workload_class ?? 'unknown')}</span>
            <span>питание</span>
            <span className="mono">{r.power_profile ?? '—'}</span>
            <span>ожидаемое поведение</span>
            <span>
              {r.expected === null ? 'не оценивалось' : r.expected ? 'да, тип нагрузки объясняет' : 'нет'}
            </span>
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
        <table>
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
                <td className="mono">{scoreText(o.detector, o.peak_score)}</td>
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
