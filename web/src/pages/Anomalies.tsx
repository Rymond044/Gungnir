import { Link, useParams, useSearchParams } from 'react-router'
import Entity from '../components/Entity'
import RecordChip from '../components/RecordChip'
import { useAnomalies, useOverview } from '../api/hooks'
import { Empty, Failure, Key, Loading, Panel, Segmented } from '../components/ui'
import ui from '../components/ui.module.css'
import { dayClock, duration, ms } from '../lib/format'
import { scoreText } from '../lib/records'
import { classNames, detectorName, kindNames, named, signalNames } from '../lib/names'
import { lastInterval, RANGES, rangeSeconds, type RangeKey } from '../lib/range'
import { useNow } from '../lib/useNow'

type Show = 'open' | 'all'
type Kind = 'any' | 'anomaly' | 'impact'

const DETECTORS = ['any', 'robust', 'threshold', 'iforest', 'growth', 'impact'] as const

export default function Anomalies() {
  const { host = '' } = useParams()
  const [params, setParams] = useSearchParams()
  const show = (params.get('show') as Show) ?? 'open'
  const kind = (params.get('kind') as Kind) ?? 'any'
  const range = (params.get('range') as RangeKey) ?? '24h'
  const detector = params.get('detector') ?? 'any'
  const now = useNow()
  const overview = useOverview(host)
  const offset = overview.data?.host.utc_offset_s ?? null

  const set = (key: string, value: string, fallback: string) => {
    const next = new URLSearchParams(params)
    if (value === fallback) next.delete(key)
    else next.set(key, value)
    setParams(next, { replace: true })
  }

  const records = useAnomalies(host, {
    ...(show === 'open' ? { open: true } : lastInterval(rangeSeconds(range), now)),
    kind: kind === 'any' ? undefined : kind,
    detector: detector === 'any' ? undefined : detector,
    limit: 1000,
  })

  return (
    <div className={ui.page}>
      <div className={ui.head}>
        <h1>Аномалии</h1>
        <span className="muted">
          записи детекции: необычное поведение (аномалия) и ожидание ресурсов программами (влияние)
        </span>
      </div>
      <div className={ui.head}>
        <Segmented<Show>
          label="Какие"
          options={[
            { value: 'open', label: 'Открытые' },
            { value: 'all', label: 'За период' },
          ]}
          value={show}
          onChange={(v) => set('show', v, 'open')}
        />
        {show === 'all' && (
          <Segmented<RangeKey>
            label="Период"
            options={RANGES.slice(1)}
            value={range}
            onChange={(v) => set('range', v, '24h')}
          />
        )}
        <Segmented<Kind>
          label="Вид"
          options={[
            { value: 'any', label: 'Все' },
            { value: 'anomaly', label: 'Аномалии' },
            { value: 'impact', label: 'Влияние' },
          ]}
          value={kind}
          onChange={(v) => set('kind', v, 'any')}
        />
        <label className="muted" style={{ display: 'inline-flex', gap: 8, alignItems: 'center' }}>
          детектор
          <select value={detector} onChange={(e) => set('detector', e.target.value, 'any')}>
            {DETECTORS.map((d) => (
              <option key={d} value={d}>
                {d === 'any' ? 'любой' : detectorName(d === 'robust' ? 'robust.mad' : d)}
              </option>
            ))}
          </select>
        </label>
      </div>

      <Panel
        aside={
          records.data
            ? records.data.length >= 1000
              ? 'показаны последние 1000 записей'
              : `${records.data.length} записей`
            : undefined
        }
      >
        {records.isPending ? (
          <Loading />
        ) : records.isError ? (
          <Failure error={records.error} />
        ) : records.data.length === 0 ? (
          <Empty>{show === 'open' ? 'Открытых записей нет.' : 'За период записей нет.'}</Empty>
        ) : (
          <div className={ui.scroll}>
            <table className={ui.fixed} style={{ minWidth: 980 }}>
              <colgroup>
                <col style={{ width: 110 }} />
                <col />
                <col style={{ width: 200 }} />
                <col style={{ width: 160 }} />
                <col style={{ width: 150 }} />
                <col style={{ width: 100 }} />
                <col style={{ width: 130 }} />
                <col style={{ width: 120 }} />
              </colgroup>
              <thead>
                <tr>
                  <th>вид</th>
                  <th>метрика</th>
                  <th>сущность</th>
                  <th>детектор</th>
                  <th>оценка</th>
                  <th>начало</th>
                  <th>длительность</th>
                  <th>тип нагрузки</th>
                </tr>
              </thead>
              <tbody>
                {records.data.map((r) => {
                  const end = r.ended_at ? ms(r.ended_at) : now
                  return (
                    <tr key={r.id} className={ui.tableRow}>
                      <td>
                        <RecordChip r={r} />
                      </td>
                      <td>
                        <Link to={`/hosts/${host}/anomalies/${r.id}`}>
                          {named(signalNames, r.signal)} {r.direction === 'up' ? '↑' : '↓'}
                        </Link>
                        <div>
                          <Key>{r.signal}</Key>
                        </div>
                      </td>
                      <td style={{ overflowWrap: 'break-word' }}>
                        <Entity entity={r.entity} />
                      </td>
                      <td>{detectorName(r.detector)}</td>
                      <td className="mono">{scoreText(r)}</td>
                      <td className="mono">{dayClock(ms(r.started_at), offset, now)}</td>
                      <td>
                        {duration((end - ms(r.started_at)) / 1000)}
                        {!r.ended_at && <span className="muted"> · открыта</span>}
                      </td>
                      <td className="muted">{named(classNames, r.workload_class ?? 'unknown')}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
      <span className="muted" style={{ fontSize: 13 }}>
        {kindNames.anomaly}: значение необычно для этого хоста в текущем типе нагрузки. {kindNames.impact}:
        программы ждут CPU, память или диск дольше допустимого; влияние открывает инцидент даже при ожидаемом
        поведении.
      </span>
    </div>
  )
}
