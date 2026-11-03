import { useState } from 'react'
import { Link, useParams } from 'react-router'
import { useAnomalies, useEvents, useOverview, useProcesses, type Interval } from '../api/hooks'
import type { Overview as OverviewData } from '../api/types'
import Entity from '../components/Entity'
import SignalChart, { type SeriesSpec } from '../components/SignalChart'
import { Chip, Empty, Failure, Key, Loading, Panel, Segmented } from '../components/ui'
import ui from '../components/ui.module.css'
import { clock, dayClock, duration, formatBytes, formatNumber, formatValue, ms, percent } from '../lib/format'
import { classNames, detectorName, kindNames, named, processStates, signalNames } from '../lib/names'
import { groupEvents, type EventGroup } from '../lib/events'
import { lastInterval, RANGES, rangeSeconds, type RangeKey } from '../lib/range'
import { metricLink } from '../lib/records'
import { useNow } from '../lib/useNow'
import styles from './Overview.module.css'

const SHOWN_OPEN = 5
const SHOWN_PROCESSES = 10
const SHOWN_EVENTS = 12

type Values = Map<string, number>

function valuesOf(o: OverviewData): Values {
  return new Map(o.latest.map((l) => [`${l.signal}@${l.entity}`, l.value]))
}

export default function Overview() {
  const { host = '' } = useParams()
  const overview = useOverview(host)
  const [range, setRange] = useState<RangeKey>('1h')
  const now = useNow()
  const interval = lastInterval(rangeSeconds(range), now)

  if (overview.isPending) return <Loading />
  if (overview.isError) return <Failure error={overview.error} />
  const o = overview.data
  const offset = o.host.utc_offset_s

  return (
    <div className={ui.page}>
      <div className={ui.head}>
        <h1>Обзор</h1>
        <span className="muted">/</span>
        <span>{host}</span>
        {o.workload_class && (
          <span className="muted">
            нагрузка: {named(classNames, o.workload_class)}
            {o.workload_since && ` с ${dayClock(ms(o.workload_since), offset, now)}`}
          </span>
        )}
      </div>

      <div className={ui.head}>
        <h2>Ресурсы</h2>
        <span style={{ marginLeft: 'auto' }}>
          <Segmented label="Период" options={RANGES.slice(0, 4)} value={range} onChange={setRange} />
        </span>
      </div>
      <Resources host={host} o={o} interval={interval} range={range} />

      <OpenStatus host={host} o={o} offset={offset} now={now} />

      <div className={ui.row}>
        <div style={{ flex: '3 1 560px', minWidth: 0 }}>
          <Processes host={host} offset={offset} />
        </div>
        <div style={{ flex: '2 1 340px', minWidth: 0 }}>
          <Events host={host} offset={offset} now={now} />
        </div>
      </div>
    </div>
  )
}

function OpenStatus({
  host,
  o,
  offset,
  now,
}: {
  host: string
  o: OverviewData
  offset: number | null
  now: number
}) {
  const open = useAnomalies(host, { open: true, limit: 200 })
  const { anomalies, impact, expected } = o.open
  const total = anomalies + impact
  const tone = impact > 0 ? styles.statusRed : ''
  const dot = impact > 0 ? 'var(--red)' : anomalies > 0 ? 'var(--ochre)' : 'var(--green)'
  const title =
    impact > 0
      ? `Влияние: программы ждут ресурсы (${impact})`
      : anomalies > 0
        ? 'Необычное поведение без влияния'
        : 'Всё спокойно'
  const sub =
    total === 0
      ? 'Открытых аномалий нет; программам хватает ресурсов.'
      : `Открыто: ${anomalies} ${plural(anomalies, 'аномалия', 'аномалии', 'аномалий')}` +
        (expected ? `, из них ожидаемых ${expected}` : '') +
        `, записей влияния ${impact}.`
  const rows = [...(open.data ?? [])].sort(
    (a, b) => (b.kind === 'impact' ? 1 : 0) - (a.kind === 'impact' ? 1 : 0) || b.peak_score - a.peak_score,
  )

  return (
    <Panel>
      <Link
        to={`/hosts/${host}/anomalies`}
        className={`${styles.status} ${tone}`}
        style={{ border: 0, padding: 0 }}
      >
        <span className={styles.statusDot} style={{ background: dot }} />
        <span className={styles.statusText}>
          <span style={{ fontWeight: 600 }}>{title}</span>
          <span className={styles.sub}>{sub}</span>
        </span>
        {total > 0 && <span className={ui.button}>Все аномалии</span>}
      </Link>
      {rows.length > 0 && (
        <div className={styles.openList}>
          {rows.slice(0, SHOWN_OPEN).map((r) => (
            <Link key={r.id} to={`/hosts/${host}/anomalies/${r.id}`} className={styles.openItem}>
              <span>
                <Chip tone={r.kind === 'impact' ? 'red' : r.expected ? 'green' : 'ochre'}>
                  {r.expected ? 'ожидаемая' : kindNames[r.kind]}
                </Chip>
              </span>
              <span style={{ minWidth: 0 }}>
                {named(signalNames, r.signal)} {r.direction === 'up' ? '↑' : '↓'} <Key>{r.signal}</Key>
                {r.entity !== 'host' && (
                  <>
                    {' · '}
                    <Entity entity={r.entity} />
                  </>
                )}
              </span>
              <span className="muted">{detectorName(r.detector)}</span>
              <span className="muted" title={`с ${clock(ms(r.started_at), offset)}`}>
                {duration((now - ms(r.started_at)) / 1000)}
              </span>
            </Link>
          ))}
          {rows.length > SHOWN_OPEN && (
            <Link to={`/hosts/${host}/anomalies`} className={styles.sub} style={{ paddingTop: 8 }}>
              ещё {rows.length - SHOWN_OPEN}
            </Link>
          )}
        </div>
      )}
    </Panel>
  )
}

function plural(n: number, one: string, few: string, many: string): string {
  const m10 = n % 10
  const m100 = n % 100
  if (m10 === 1 && m100 !== 11) return one
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few
  return many
}

/** A current value in the head of a card; a card of several lines names each. */
interface Reading {
  label?: string
  value: string
  red?: boolean
}

interface ResourceDef {
  title: string
  sub: string
  now: Reading[]
  series: SeriesSpec[]
  normOf?: { signal: string; entity?: string }
  format: (v: number) => string
  minMax?: number
  ceiling?: number
}

const INK = 'var(--ink)'
const BLUE = 'var(--blue)'
const OCHRE = 'var(--ochre)'
const RED = 'var(--red)'
const GRAY = 'var(--gray)'

function resources(o: OverviewData, v: Values): ResourceDef[] {
  const out: ResourceDef[] = []
  const idle = v.get('cpu.idle_ratio@host')
  const iowait = v.get('cpu.iowait_ratio@host')
  out.push({
    title: 'CPU',
    sub: 'все ядра',
    now: [{ value: idle === undefined ? '—' : percent(1 - idle - (iowait ?? 0)) }],
    series: [
      { signal: 'cpu.user_ratio', name: 'user', color: INK },
      { signal: 'cpu.system_ratio', name: 'system', color: BLUE },
      { signal: 'cpu.iowait_ratio', name: 'iowait', color: OCHRE },
    ],
    normOf: { signal: 'cpu.user_ratio' },
    format: (x) => formatValue('cpu.user_ratio', x),
    minMax: 0.05,
    ceiling: 1,
  })
  // Available memory, not used: it is what detection judges and what the
  // large view of the same metric shows.
  const total = o.memory_bytes
  const available = v.get('mem.available_bytes@host')
  out.push({
    title: 'Память',
    sub: total ? `доступно из ${formatBytes(total)}` : 'доступно',
    now: [{ value: available === undefined ? '—' : formatBytes(available) }],
    series: [
      { signal: 'mem.available_bytes', name: 'доступно', color: INK },
      { signal: 'mem.cached_bytes', name: 'страничный кэш', color: BLUE },
      { signal: 'mem.dirty_bytes', name: 'грязные страницы', color: OCHRE },
    ],
    normOf: { signal: 'mem.available_bytes' },
    format: formatBytes,
    ceiling: total ?? undefined,
  })
  const swapUsed = v.get('swap.used_bytes@host')
  out.push({
    title: 'Swap',
    sub: o.swap_bytes ? `из ${formatBytes(o.swap_bytes)}` : 'выключен или пуст',
    now: [{ value: swapUsed === undefined ? '—' : formatBytes(swapUsed) }],
    series: [{ signal: 'swap.used_bytes', name: 'занято', color: INK }],
    normOf: { signal: 'swap.used_bytes' },
    format: formatBytes,
    minMax: o.swap_bytes ? o.swap_bytes * 0.05 : 0,
  })
  for (const dev of o.devices) {
    const w = v.get(`disk.write_bytes_per_s@${dev}`)
    const r = v.get(`disk.read_bytes_per_s@${dev}`)
    out.push({
      title: 'Диск',
      sub: `${dev} · запись и чтение`,
      now: [
        { label: 'запись', value: formatValue('disk.write_bytes_per_s', w) },
        { label: 'чтение', value: formatValue('disk.read_bytes_per_s', r) },
      ],
      series: [
        { signal: 'disk.write_bytes_per_s', entity: dev, name: 'запись', color: INK },
        { signal: 'disk.read_bytes_per_s', entity: dev, name: 'чтение', color: BLUE },
      ],
      normOf: { signal: 'disk.write_bytes_per_s', entity: dev },
      format: (x) => formatValue('disk.write_bytes_per_s', x),
      minMax: 1024 * 1024,
    })
    out.push({
      title: 'Задержка диска',
      sub: `${dev} · await`,
      now: [
        { label: 'запись', value: formatValue('disk.write_await_ms', v.get(`disk.write_await_ms@${dev}`)) },
        { label: 'чтение', value: formatValue('disk.read_await_ms', v.get(`disk.read_await_ms@${dev}`)) },
      ],
      series: [
        { signal: 'disk.write_await_ms', entity: dev, name: 'запись', color: INK },
        { signal: 'disk.read_await_ms', entity: dev, name: 'чтение', color: BLUE },
      ],
      normOf: { signal: 'disk.write_await_ms', entity: dev },
      format: (x) => formatValue('disk.write_await_ms', x),
      minMax: 1,
    })
  }
  // Noticeable impact starts at 0.10 for every resource (knowledge base, limits).
  const pressure = (label: string, signal: string): Reading => {
    const x = v.get(`${signal}@host`)
    return { label, value: formatValue(signal, x), red: (x ?? 0) >= 0.1 }
  }
  out.push({
    title: 'Давление (PSI)',
    sub: 'some, доля времени с ожиданием',
    now: [
      pressure('I/O', 'io.pressure_some_ratio'),
      pressure('память', 'mem.pressure_some_ratio'),
      pressure('CPU', 'cpu.pressure_some_ratio'),
    ],
    series: [
      { signal: 'io.pressure_some_ratio', name: 'I/O', color: RED },
      { signal: 'mem.pressure_some_ratio', name: 'память', color: BLUE },
      { signal: 'cpu.pressure_some_ratio', name: 'CPU', color: GRAY },
    ],
    normOf: { signal: 'io.pressure_some_ratio' },
    format: (x) => formatValue('io.pressure_some_ratio', x),
    minMax: 0.12,
    ceiling: 1,
  })
  return out
}

function Resources({
  host,
  o,
  interval,
  range,
}: {
  host: string
  o: OverviewData
  interval: Interval
  range: RangeKey
}) {
  const v = valuesOf(o)
  const filesystems = o.filesystems.map((fs) => ({
    fs,
    used: v.get(`fs.used_ratio@${fs}`),
    free: v.get(`fs.available_bytes@${fs}`),
  }))
  return (
    <div className={ui.grid}>
      {resources(o, v).map((r) => (
        <Panel
          key={`${r.title}${r.sub}`}
          title={
            <Link
              to={metricLink(host, r.series, { range })}
              className={styles.titleLink}
              title="Открыть крупно"
            >
              {r.title} <span className={styles.sub}>{r.sub}</span>{' '}
              <span className={styles.expandIcon}>⤢</span>
            </Link>
          }
          aside={
            <span className={`${styles.readings} ${r.now.length > 1 ? styles.several : ''}`}>
              {r.now.map((n) => (
                <span key={n.label ?? ''} className={n.red ? styles.nowRed : undefined}>
                  {n.label && <span className={styles.sub}>{n.label} </span>}
                  <span className={styles.now}>{n.value}</span>
                </span>
              ))}
            </span>
          }
        >
          <SignalChart
            host={host}
            series={r.series}
            interval={interval}
            offsetS={o.host.utc_offset_s}
            format={r.format}
            normOf={r.normOf}
            minMax={r.minMax}
            ceiling={r.ceiling}
          />
        </Panel>
      ))}
      <Panel title="Файловые системы" aside="заполнение">
        {filesystems.length === 0 ? (
          <Empty>Нет данных о файловых системах.</Empty>
        ) : (
          <div className={styles.bars}>
            {filesystems.map(({ fs, used, free }) => (
              <FsRow key={fs} fs={fs} used={used} free={free} />
            ))}
          </div>
        )}
      </Panel>
    </div>
  )
}

function FsRow({ fs, used, free }: { fs: string; used?: number; free?: number }) {
  const share = used ?? 0
  return (
    <div className={styles.fs}>
      <span className={styles.fsName} title={fs}>
        {fs}
      </span>
      <span className="muted">
        {formatValue('fs.used_ratio', used)}
        {free !== undefined && `, свободно ${formatBytes(free)}`}
      </span>
      <span className={styles.bar}>
        <span style={{ width: `${share * 100}%`, background: share >= 0.9 ? 'var(--red)' : undefined }} />
      </span>
    </div>
  )
}

function Processes({ host, offset }: { host: string; offset: number | null }) {
  const processes = useProcesses(host)
  const rows = processes.data ?? []
  return (
    <Panel title="Процессы" aside={rows[0] ? `снимок ${clock(ms(rows[0].ts), offset)}` : undefined}>
      {processes.isError ? (
        <Failure error={processes.error} />
      ) : rows.length === 0 ? (
        <Empty>Снимка процессов за последнюю минуту нет.</Empty>
      ) : (
        <div className={ui.scroll}>
          <table>
            <thead>
              <tr>
                <th>pid</th>
                <th>команда</th>
                <th>группа</th>
                <th>ядра</th>
                <th>память</th>
                <th>состояние</th>
              </tr>
            </thead>
            <tbody>
              {rows.slice(0, SHOWN_PROCESSES).map((p) => (
                <tr key={p.pid}>
                  <td className="mono muted">{p.pid}</td>
                  <td>{p.comm}</td>
                  <td style={{ overflowWrap: 'anywhere' }}>
                    {p.unit || p.cgroup ? <Entity entity={(p.unit ?? p.cgroup)!} /> : '—'}
                  </td>
                  <td>{formatNumber(p.cpu_cores)}</td>
                  <td>{formatBytes(p.rss_bytes)}</td>
                  <td style={{ color: p.state === 'D' ? 'var(--red-text)' : undefined }}>
                    {processStates[p.state] ?? p.state}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  )
}

function Events({ host, offset, now }: { host: string; offset: number | null; now: number }) {
  const events = useEvents(host, lastInterval(86400, now), 1000)
  const groups = groupEvents([...(events.data ?? [])].reverse())
  return (
    <Panel title="События" aside="ядро и журнал, 24 ч">
      {events.isError ? (
        <Failure error={events.error} />
      ) : groups.length === 0 ? (
        <Empty>За сутки событий не было.</Empty>
      ) : (
        <div className={styles.events}>
          {groups.slice(0, SHOWN_EVENTS).map((g) => (
            <EventLine key={g.lines.join('\n')} group={g} offset={offset} now={now} />
          ))}
        </div>
      )}
    </Panel>
  )
}

function EventLine({ group, offset, now }: { group: EventGroup; offset: number | null; now: number }) {
  const [first, ...rest] = group.lines
  const earlier = group.times.slice(1)
  return (
    <>
      <span className="mono muted">{dayClock(group.times[0], offset, now)}</span>
      <span style={{ overflowWrap: 'anywhere' }}>
        {first}
        {rest.length > 0 && (
          <details className={styles.more}>
            <summary>
              ещё {rest.length} {plural(rest.length, 'строка', 'строки', 'строк')} в это же время
            </summary>
            {rest.map((l) => (
              <div key={l}>{l}</div>
            ))}
          </details>
        )}
        {earlier.length > 0 && (
          <span className={styles.repeat}>
            повторялось раньше: {earlier.map((t) => dayClock(t, offset, now)).join(', ')}
          </span>
        )}
      </span>
    </>
  )
}
