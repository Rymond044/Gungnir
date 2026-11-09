import { useEffect, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router'
import { useAudit, useOverview } from '../api/hooks'
import type { Command, ExplainedStretch, Lane, OpenStretch, Stretch } from '../api/types'
import Entity from '../components/Entity'
import Evidence from '../components/Evidence'
import Hypothesis, { Actions } from '../components/Hypothesis'
import RangePicker from '../components/RangePicker'
import type { Mark } from '../components/Chart'
import SignalChart, { type SeriesSpec } from '../components/SignalChart'
import { Chip, Empty, Failure, Key, Loading, Panel, Segmented, Source } from '../components/ui'
import ui from '../components/ui.module.css'
import { confidence, dayClock, formatBytes, formatNumber, formatValue } from '../lib/format'
import { folded } from '../lib/audit'
import { useShownInterval } from '../lib/interval'
import { causeNames, classNames, named, signalNames, symptomNames } from '../lib/names'
import { entityName } from '../lib/entity'
import { usedFrom } from '../lib/norm'
import { metricLink } from '../lib/records'
import { useNow } from '../lib/useNow'
import styles from './Audit.module.css'

const LANES: { lane: Exclude<Lane, 'other'>; title: string }[] = [
  { lane: 'cpu', title: 'CPU' },
  { lane: 'memory', title: 'Память' },
  { lane: 'disk', title: 'Диск' },
]
const LANE_WORDS: Record<Lane, string> = { cpu: 'CPU', memory: 'память', disk: 'диск', other: 'прочее' }
// Context around a stretch on its own graph.
const MARGIN_MS = 120_000

type LaneFilter = Lane | 'all'

/**
 * What was unusual over an interval, what it was and who did it (ADRs 0056,
 * 0057): graphs of the host with a lane of stretches under each, then the
 * stretches that hurt the others and those that did not, newest first.
 */
export default function Audit() {
  const { host = '' } = useParams()
  const [params, setParams] = useSearchParams()
  const now = useNow()
  const shown = useShownInterval('1h', now)
  const overview = useOverview(host)
  const audit = useAudit(host, shown.interval, shown.live)
  const [hovered, setHovered] = useState<string | null>(null)
  const offset = overview.data?.host.utc_offset_s ?? null

  const lane = (params.get('lane') as LaneFilter) ?? 'all'
  const harmOnly = params.get('harm') === '1'
  const set = (key: string, value: string | null) => {
    const q = new URLSearchParams(params)
    if (value === null) q.delete(key)
    else q.set(key, value)
    setParams(q, { replace: true })
  }

  // A host switched on ten minutes ago has ten minutes to show.
  const first = audit.data?.data_since ? Date.parse(audit.data.data_since) : null
  const since = Math.max(Date.parse(shown.interval.since), first ?? 0)
  const span = { since: new Date(since).toISOString(), until: shown.interval.until }

  // Numbered from the earliest, as the graphs read left to right.
  const all = [...(audit.data?.stretches ?? [])]
    .sort((a, b) => a.start - b.start)
    .map((s, i) => ({ s, id: stretchId(s), n: i + 1 }))

  // Opened from a record: show the stretch that holds it.
  const record = params.get('record')
  const target = record ? all.find(({ s }) => holds(s, record))?.id : undefined
  useEffect(() => {
    if (!target) return
    setHovered(target)
    document.getElementById(target)?.scrollIntoView({ block: 'start' })
  }, [target])
  const picked = all.filter(({ s }) => (lane === 'all' || s.lane === lane) && (!harmOnly || s.harm))
  const newest = (a: { s: Stretch }, b: { s: Stretch }) => b.s.start - a.s.start
  const hurt = picked.filter(({ s }) => s.harm).sort(newest)
  const quiet = picked.filter(({ s }) => !s.harm).sort(newest)

  return (
    <div className={ui.page}>
      <div className={ui.head}>
        <h1>Аудит</h1>
        <span className="muted">что было необычным, что это было и кто это сделал</span>
      </div>
      <div className={ui.head}>
        <RangePicker shown={shown} offset={offset} now={now} ranges={['15m', '1h', '6h', '24h', '7d']} />
        {audit.isFetching && <span className="muted">считаю…</span>}
      </div>

      {overview.data && (
        <Panel>
          <div className={styles.lanes}>
            {LANES.map(({ lane: l, title }) => (
              <LaneChart
                key={l}
                lane={l}
                title={title}
                host={host}
                device={overview.data.devices[0]}
                memory={overview.data.memory_bytes}
                span={span}
                live={shown.live}
                offset={offset}
                onSelect={shown.setSpan}
                stretches={all.filter(({ s }) => s.lane === l)}
                hovered={hovered}
                onHover={setHovered}
                now={now}
              />
            ))}
            <div className={styles.legend}>
              <span>
                Номер и заливка — эпизод из списка ниже: наведите, чтобы подсветить его, нажмите, чтобы
                перейти.
              </span>
              <span>
                <i className={`${styles.swatch} ${styles.red}`} /> мешало другим
              </span>
              <span>
                <i className={`${styles.swatch} ${styles.plain}`} /> никому не мешало
              </span>
              <span>
                <i className={`${styles.swatch} ${styles.green}`} /> ожидаемое поведение
              </span>
              <span>
                <i className={styles.long} /> длиннее половины графика
              </span>
            </div>
          </div>
        </Panel>
      )}

      <div className={ui.head}>
        <Segmented<LaneFilter>
          label="Ресурс"
          options={[
            { value: 'all', label: 'Все' },
            ...LANES.map((l) => ({ value: l.lane as LaneFilter, label: l.title })),
          ]}
          value={lane}
          onChange={(v) => set('lane', v === 'all' ? null : v)}
        />
        <label className={styles.toggle}>
          <input
            type="checkbox"
            checked={harmOnly}
            onChange={(e) => set('harm', e.target.checked ? '1' : null)}
          />
          только то, что мешало
        </label>
      </div>

      {audit.isPending ? (
        <Loading />
      ) : audit.isError ? (
        <Failure error={audit.error} />
      ) : picked.length === 0 ? (
        <Panel>
          <Empty>
            {all.length === 0
              ? 'За интервал ничего необычного: хост в норме.'
              : 'Под выбранные фильтры ничего не попало.'}
          </Empty>
        </Panel>
      ) : (
        <>
          {[
            { title: 'Мешало другим', items: hurt },
            { title: 'Никому не мешало', items: quiet },
          ]
            .filter((g) => g.items.length > 0)
            .map((g) => (
              <section key={g.title} className={styles.group}>
                <h2>
                  {g.title} <span className="muted">{g.items.length}</span>
                </h2>
                {g.items.map(({ s, id, n }) => (
                  <Card
                    key={id}
                    id={id}
                    n={n}
                    s={s}
                    host={host}
                    offset={offset}
                    now={now}
                    memory={overview.data?.memory_bytes ?? null}
                    lit={hovered === id}
                    onHover={setHovered}
                  />
                ))}
              </section>
            ))}
        </>
      )}
    </div>
  )
}

function holds(s: Stretch, record: string): boolean {
  return s.kind === 'explained' ? s.records.includes(record) : s.records.some((r) => r.id === record)
}

function stretchId(s: Stretch): string {
  return `${s.kind}-${s.lane}-${Math.round(s.start)}${s.kind === 'explained' ? `-${s.cause}` : ''}`
}

function toneOf(s: Stretch): 'red' | 'green' | 'plain' {
  return s.harm ? 'red' : s.kind === 'expected' || (s.kind === 'explained' && s.expected) ? 'green' : 'plain'
}

// Graphs of the host

const LANE_SERIES: Record<Exclude<Lane, 'other'>, (device: string, memory: number | null) => Graph> = {
  cpu: () => ({
    series: [
      { signal: 'cpu.user_ratio', name: 'user', color: 'var(--ink)' },
      { signal: 'cpu.system_ratio', name: 'system', color: 'var(--blue)' },
    ],
    normOf: { signal: 'cpu.user_ratio' },
    format: (v) => formatValue('cpu.user_ratio', v),
    ceiling: 1,
    minMax: 0.05,
  }),
  memory: (_, memory) => {
    const mirror = usedFrom('mem.available_bytes', memory)
    return {
      series: [{ signal: 'mem.available_bytes', name: 'использовано', color: 'var(--ink)', mirror }],
      normOf: { signal: 'mem.available_bytes', mirror },
      format: formatBytes,
      ceiling: memory ?? undefined,
    }
  },
  disk: (device) => ({
    series: [{ signal: 'disk.util_ratio', entity: device, name: 'занят', color: 'var(--ink)' }],
    normOf: { signal: 'disk.util_ratio', entity: device },
    format: (v) => formatValue('disk.util_ratio', v),
    ceiling: 1,
    minMax: 0.05,
  }),
}

interface Graph {
  series: SeriesSpec[]
  normOf: { signal: string; entity?: string; mirror?: number }
  format: (v: number) => string
  ceiling?: number
  minMax?: number
}

function LaneChart({
  lane,
  title,
  host,
  device,
  memory,
  span,
  live,
  offset,
  onSelect,
  stretches,
  hovered,
  onHover,
  now,
}: {
  lane: Exclude<Lane, 'other'>
  title: string
  host: string
  device: string | undefined
  memory: number | null
  span: { since: string; until: string }
  live: boolean
  offset: number | null
  onSelect: (a: number, b: number) => void
  stretches: { s: Stretch; id: string; n: number }[]
  hovered: string | null
  onHover: (id: string | null) => void
  now: number
}) {
  const g = LANE_SERIES[lane](device ?? '', memory)
  const tone = { red: 'red', green: 'green', plain: 'gray' } as const
  const marks: Mark[] = stretches.map(({ s, id, n }) => ({
    key: id,
    from: s.start * 1000,
    to: s.end === null ? now : s.end * 1000,
    label: String(n),
    tone: tone[toneOf(s)],
    title: `${n}. ${titleOf(s)}, ${dayClock(s.start * 1000, offset, now)}`,
    lit: hovered === id,
  }))
  return (
    <div className={styles.lane}>
      <Link
        to={metricLink(host, g.series, live ? {} : span)}
        className={styles.laneTitle}
        title="Открыть крупно"
      >
        {title}
      </Link>
      <div>
        <SignalChart
          host={host}
          series={g.series}
          interval={span}
          offsetS={offset}
          format={g.format}
          normOf={g.normOf}
          height={70}
          ceiling={g.ceiling}
          minMax={g.minMax}
          onSelect={onSelect}
          live={live}
          marks={marks}
          withLimits={false}
          onMark={onHover}
          onMarkClick={(id) =>
            document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
          }
        />
      </div>
    </div>
  )
}

// Stretches

function titleOf(s: Stretch): string {
  if (s.kind === 'explained') return named(causeNames, s.cause)
  if (s.kind === 'expected') return `Ожидаемое поведение: ${LANE_WORDS[s.lane]}`
  return `Не объяснено: ${LANE_WORDS[s.lane]}`
}

function certainty(c: number): string {
  return c >= 0.7 ? 'уверенно' : c >= 0.5 ? 'вероятно' : 'предположительно'
}

function Card({
  id,
  n,
  s,
  host,
  offset,
  now,
  memory,
  lit,
  onHover,
}: {
  id: string
  n: number
  s: Stretch
  host: string
  offset: number | null
  now: number
  memory: number | null
  lit: boolean
  onHover: (id: string | null) => void
}) {
  const tone = toneOf(s)
  return (
    <article
      id={id}
      className={`${styles.card} ${styles[`card_${tone}`]} ${lit ? styles.cardLit : ''}`}
      onMouseEnter={() => onHover(id)}
      onMouseLeave={() => onHover(null)}
    >
      <header className={styles.cardHead}>
        <span className={`${styles.badge} ${styles[tone]}`} title="номер эпизода на графиках">
          {n}
        </span>
        <span className={styles.when}>
          {dayClock(s.start * 1000, offset, now)} —{' '}
          {s.end === null ? <b>идёт сейчас</b> : dayClock(s.end * 1000, offset, now)}
        </span>
        <Chip>{LANE_WORDS[s.lane]}</Chip>
        <Chip tone={tone}>{s.harm ? 'мешало' : tone === 'green' ? 'ожидаемо' : 'без вреда'}</Chip>
        <span className={styles.cardTitle}>
          {s.kind === 'explained' ? (
            <>
              <Link to={`/hosts/${host}/knowledge/${s.cause}`}>{titleOf(s)}</Link> <Key>{s.cause}</Key>
            </>
          ) : (
            titleOf(s)
          )}
        </span>
        {s.kind === 'explained' && (
          <span className={styles.certainty} title="уверенность механизма вывода">
            {certainty(s.confidence)} <span className={styles.number}>{confidence(s.confidence)}</span>
          </span>
        )}
      </header>
      {s.kind === 'explained' ? (
        <Explained s={s} host={host} offset={offset} now={now} memory={memory} />
      ) : (
        <Open s={s} host={host} offset={offset} now={now} memory={memory} />
      )}
    </article>
  )
}

function Explained({
  s,
  host,
  offset,
  now,
  memory,
}: {
  s: ExplainedStretch
  host: string
  offset: number | null
  now: number
  memory: number | null
}) {
  const leading = s.hypothesis.symptoms.find((x) => x.leading && x.evidence?.type === 'record')?.evidence
  return (
    <>
      <Row label="Кто">
        {s.culprit ? (
          <Entity entity={s.culprit} />
        ) : (
          <span className="muted">виновник не назван: причина на хосте</span>
        )}
        {s.device && <span className="mono"> · {s.device}</span>}
        <Commands commands={s.processes} lane={s.lane} />
      </Row>
      {leading && leading.type === 'record' && (
        <StretchChart
          host={host}
          signal={leading.signal}
          entity={leading.entity}
          s={s}
          bursts={s.bursts}
          offset={offset}
          now={now}
          memory={memory}
        />
      )}
      <Row label="Мешало ли">
        {s.harm ? (
          <span>
            {s.impact.map((item, i) => (
              <span key={i}>
                {i > 0 && '; '}
                <Evidence item={item} host={host} offset={offset} withName />
              </span>
            ))}
          </span>
        ) : (
          <span className="muted">никому не мешало: программы не ждали ресурса дольше обычного</span>
        )}
      </Row>
      {s.harm && s.actions.length > 0 && (
        <Row label="Что делать">
          <Actions actions={s.actions} title="" />
        </Row>
      )}
      <details className={styles.why}>
        <summary>Почему механизм вывода так решил</summary>
        <Hypothesis h={{ ...s.hypothesis, actions: [] }} host={host} offset={offset} withActions={false} />
      </details>
    </>
  )
}

function Open({
  s,
  host,
  offset,
  now,
  memory,
}: {
  s: OpenStretch
  host: string
  offset: number | null
  now: number
  memory: number | null
}) {
  // The record to draw: impact first, then the most unusual.
  const main = [...s.records].sort(
    (a, b) => Number(b.kind === 'impact') - Number(a.kind === 'impact') || (b.score ?? 0) - (a.score ?? 0),
  )[0]
  const hurtBy = s.records.filter((r) => r.kind === 'impact')
  return (
    <>
      <Row label="Кто">
        {s.processes.length === 0 && s.groups.length === 0 ? (
          <span className="muted">
            {s.lane === 'disk'
              ? 'по процессам ввод-вывод не виден без привилегий; группы — в записях ниже'
              : 'снимков процессов за это время нет'}
          </span>
        ) : (
          <>
            <Commands commands={s.processes} lane={s.lane} />
            {s.groups.length > 0 && (
              <span className={styles.groups}>
                группы:{' '}
                {s.groups.map((g, i) => (
                  <span key={g.group}>
                    {i > 0 && ', '}
                    <Entity entity={g.group} />
                  </span>
                ))}
              </span>
            )}
          </>
        )}
        {s.kind === 'expected' && s.workload_class && (
          <span className="muted">класс нагрузки: {named(classNames, s.workload_class)}</span>
        )}
      </Row>
      {main && (
        <StretchChart
          host={host}
          signal={main.signal}
          entity={main.entity}
          s={s}
          bursts={s.records.map((r) => [r.start, r.end ?? null])}
          offset={offset}
          now={now}
          memory={memory}
        />
      )}
      <Row label="Что необычно">
        <span>
          {s.records.map((r, i) => (
            <span key={r.id}>
              {i > 0 && '; '}
              <Evidence item={r} host={host} offset={offset} withName />
            </span>
          ))}
          {s.events.map((e, i) => (
            <span key={i}>
              {'; '}
              <Evidence item={e} host={host} offset={offset} withName />
            </span>
          ))}
        </span>
      </Row>
      <Row label="Мешало ли">
        {s.harm ? (
          <span>
            {hurtBy.length > 0
              ? `записи влияния: ${hurtBy.map((r) => named(signalNames, r.signal)).join(', ')}`
              : 'программы ждали ресурса дольше обычного'}
          </span>
        ) : (
          <span className="muted">никому не мешало</span>
        )}
      </Row>
      {s.advice.length > 0 && (
        <Row label="Что проверить">
          {s.advice.map((a, i) => (
            <span key={i} className={styles.advice}>
              {a.text}
              <Source source={a.source} />
            </span>
          ))}
        </Row>
      )}
      {s.near && (
        <details className={styles.why}>
          <summary>
            Ближе всего: {named(causeNames, s.near.cause)} — {nearReason(s.near)}
          </summary>
          <Hypothesis h={{ ...s.near, actions: [] }} host={host} offset={offset} withActions={false} />
        </details>
      )}
    </>
  )
}

function nearReason(h: NonNullable<OpenStretch['near']>): string {
  const missing = h.symptoms
    .filter((x) => x.status === 'missing' && x.strength === 'characteristic' && !x.impact)
    .map((x) => symptomNames(x.kind, x.names))
  switch (h.reason) {
    case 'characteristic missing':
      return `не хватило обязательного симптома: ${missing.join(', ')}`
    case 'contradicted':
      return 'нашёлся противоречащий симптом'
    case 'only impact':
      return 'видно только влияние, без признаков самой причины'
    case 'no impact':
      return 'без ведущего симптома и без влияния'
    case 'below cut-off':
      return `уверенность ${confidence(h.confidence)} ниже порога`
    default:
      return h.reason
  }
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className={styles.row}>
      <span className={styles.rowLabel}>{label}</span>
      <div className={styles.rowBody}>{children}</div>
    </div>
  )
}

/** The commands behind a stretch, with bars of their share. */
function Commands({ commands: raw, lane }: { commands: Command[]; lane: Lane }) {
  if (raw.length === 0) return null
  const byMemory = lane === 'memory'
  const commands = folded(raw, byMemory)
  const amount = (c: Command) => (byMemory ? c.rss_bytes : c.cores)
  const most = Math.max(...commands.map(amount), 1e-9)
  return (
    <div className={styles.commands}>
      {commands.map((c, i) => {
        const [top] = c.names
        const names = c.names.map(([n, k]) => (k > 1 ? `${n} ×${k}` : n)).join(', ')
        return (
          <div key={i} className={styles.command}>
            <span className={styles.commandName} title={c.pgid ? `группа процессов ${c.pgid}` : undefined}>
              {c.leader && c.leader !== top[0] ? (
                <>
                  <b>{c.leader}</b> → {names}
                </>
              ) : (
                <b>{names}</b>
              )}
              {c.unit && <span className="muted"> · {entityName(c.unit)}</span>}
            </span>
            <span className={styles.commandBar}>
              <span style={{ width: `${(amount(c) / most) * 100}%` }} />
            </span>
            <span className={styles.commandValue}>
              {byMemory ? formatBytes(c.rss_bytes) : `${formatNumber(c.cores)} ядра`}
            </span>
          </div>
        )
      })}
    </div>
  )
}

function StretchChart({
  host,
  signal,
  entity,
  s,
  bursts,
  offset,
  now,
  memory,
}: {
  host: string
  signal: string
  entity: string
  s: Stretch
  bursts: [number, number | null][]
  offset: number | null
  now: number
  memory: number | null
}) {
  const end = s.end === null ? now : s.end * 1000
  // As much before and after as the stretch itself lasts, at least two minutes.
  const pad = Math.max(MARGIN_MS, end - s.start * 1000)
  const until = Math.min(now, end + pad)
  const since = s.start * 1000 - pad
  const mirror = usedFrom(signal, memory)
  return (
    <div className={styles.chart}>
      <SignalChart
        host={host}
        series={[
          {
            signal,
            entity,
            name: mirror ? 'использовано' : named(signalNames, signal),
            color: 'var(--ink)',
            mirror,
          },
        ]}
        interval={{ since: new Date(since).toISOString(), until: new Date(until).toISOString() }}
        offsetS={offset}
        format={(v) => formatValue(signal, v)}
        normOf={{ signal, entity, mirror }}
        height={110}
        intervals={bursts.map(([a, b]) => ({
          from: a * 1000,
          to: b === null ? now : b * 1000,
          label: 'всплеск',
          impact: s.harm,
          current: true,
        }))}
        live={s.end === null}
      />
    </div>
  )
}
