import { Link, useParams, useSearchParams } from 'react-router'
import { useAnomalies, useOverview } from '../api/hooks'
import RecordChip from '../components/RecordChip'
import SignalChart, { type SeriesSpec } from '../components/SignalChart'
import { usedFrom } from '../lib/norm'
import { Empty, Key, Loading, Panel, Segmented } from '../components/ui'
import ui from '../components/ui.module.css'
import { dayClock, duration, formatValue, ms } from '../lib/format'
import { detectorName, named, signalNames } from '../lib/names'
import { lastInterval, RANGES, rangeSeconds, type RangeKey } from '../lib/range'
import { isLevel, recordInterval, scoreText } from '../lib/records'
import { useNow } from '../lib/useNow'
import Entity from '../components/Entity'
import { entityName } from '../lib/entity'

const COLORS = ['var(--ink)', 'var(--blue)', 'var(--ochre)', 'var(--red)', 'var(--gray)', 'var(--green)']

function parse(key: string): { signal: string; entity: string } {
  const [signal, entity] = key.split('@')
  return { signal, entity: entity || 'host' }
}

/**
 * One or more series at a large scale: any period up to a week, a drag across
 * the plot zooms in, the records of detection on these series lie along the top.
 */
export default function Metric() {
  const { host = '' } = useParams()
  const [params, setParams] = useSearchParams()
  const now = useNow()
  const overview = useOverview(host)
  const offset = overview.data?.host.utc_offset_s ?? null

  const keys = params.getAll('s')
  const series = keys.map(parse)
  const main = series[0]
  const range = (params.get('range') as RangeKey) ?? '1h'
  const since = params.get('since')
  const until = params.get('until')
  const zoomed = !!since && !!until
  const interval = zoomed ? { since: since!, until: until! } : lastInterval(rangeSeconds(range), now)

  const records = useAnomalies(host, {
    ...interval,
    signal: main?.signal,
    entity: main?.entity,
    limit: 500,
  })

  if (!main) return <Empty>Не выбрана ни одна метрика.</Empty>

  const set = (next: Record<string, string | null>) => {
    const q = new URLSearchParams(params)
    Object.entries(next).forEach(([k, v]) => (v === null ? q.delete(k) : q.set(k, v)))
    setParams(q, { replace: false })
  }

  const memory = overview.data?.memory_bytes
  const specs: SeriesSpec[] = series.map((s, i) => {
    const mirror = usedFrom(s.signal, memory)
    const name = mirror ? 'Использованная память' : named(signalNames, s.signal)
    return {
      signal: s.signal,
      entity: s.entity,
      name: series.length > 1 && s.entity !== main.entity ? `${name} · ${entityName(s.entity)}` : name,
      color: COLORS[i % COLORS.length],
      mirror,
    }
  })
  const rows = records.data ?? []
  const level = isLevel(main.signal)

  return (
    <div className={ui.page}>
      <div className={ui.head}>
        <h1>{specs.map((s) => s.name).join(', ')}</h1>
        <span className="muted">
          <Entity entity={main.entity} />
        </span>
        <Key>{keys.join(', ')}</Key>
      </div>
      <div className={ui.head}>
        <Segmented<RangeKey>
          label="Период"
          options={RANGES}
          value={zoomed ? ('' as RangeKey) : range}
          onChange={(v) => set({ range: v, since: null, until: null })}
        />
        {zoomed && (
          <>
            <span className="muted">
              {dayClock(ms(since!), offset, now)} — {dayClock(ms(until!), offset, now)}
            </span>
            <button type="button" className={ui.button} onClick={() => set({ since: null, until: null })}>
              Сбросить масштаб
            </button>
          </>
        )}
        <span className="muted" style={{ marginLeft: 'auto', fontSize: 13 }}>
          выделите участок графика мышью, чтобы приблизить
        </span>
      </div>
      <Panel>
        {overview.isPending ? (
          <Loading />
        ) : (
          <SignalChart
            host={host}
            series={specs}
            interval={interval}
            offsetS={offset}
            format={(v) => formatValue(main.signal, v)}
            normOf={{ ...main, mirror: usedFrom(main.signal, memory) }}
            height={380}
            fitY={level}
            ceiling={main.signal.endsWith('_ratio') ? 1 : undefined}
            intervals={rows.map((r) => recordInterval(r, now, offset))}
            onSelect={(a, b) => set({ since: new Date(a).toISOString(), until: new Date(b).toISOString() })}
            live={!zoomed}
          />
        )}
      </Panel>
      <Panel title="Записи детекции на этой метрике" aside={`${rows.length} за период`}>
        {rows.length === 0 ? (
          <Empty>За период записей нет.</Empty>
        ) : (
          <table className={ui.fixed}>
            <colgroup>
              <col style={{ width: 130 }} />
              <col style={{ width: 150 }} />
              <col />
              <col style={{ width: 150 }} />
              <col style={{ width: 160 }} />
            </colgroup>
            <thead>
              <tr>
                <th>вид</th>
                <th>начало</th>
                <th>детектор</th>
                <th>оценка</th>
                <th>длительность</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id} className={ui.tableRow}>
                  <td>
                    <RecordChip r={r} />
                  </td>
                  <td className="mono">
                    <Link to={`/hosts/${host}/anomalies/${r.id}`}>
                      {dayClock(ms(r.started_at), offset, now)}
                    </Link>
                  </td>
                  <td>{detectorName(r.detector)}</td>
                  <td className="mono">{scoreText(r)}</td>
                  <td>{r.ended_at ? duration((ms(r.ended_at) - ms(r.started_at)) / 1000) : 'открыта'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>
    </div>
  )
}
