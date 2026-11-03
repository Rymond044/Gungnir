import * as Tabs from '@radix-ui/react-tabs'
import { lazy, Suspense, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router'
import { useKnowledge } from '../api/hooks'
import type { Knowledge as KB } from '../api/types'
import { Chip, Empty, Failure, Key, Loading, Panel, Source } from '../components/ui'
import ui from '../components/ui.module.css'
import { formatNumber, formatValue } from '../lib/format'
import { DOMAINS } from '../lib/knowledge'
import {
  actionNames,
  causeNames,
  classNames,
  directionNames,
  domainNames,
  eventNames,
  factNames,
  gradeNames,
  levelNames,
  likelihoodNames,
  named,
  processNames,
  protectedNames,
  resourceNames,
  roleNames,
  signalNames,
} from '../lib/names'
import styles from './Knowledge.module.css'

// React Flow is the largest dependency; only the graph tab loads it.
const Graph = lazy(() => import('./KnowledgeGraph'))

const TABS = [
  ['causes', 'Причины'],
  ['graph', 'Граф'],
  ['signals', 'Метрики'],
  ['events', 'События и процессы'],
  ['actions', 'Действия'],
  ['expected', 'Ожидаемое поведение'],
  ['limits', 'Пороги'],
  ['tests', 'Тесты'],
] as const

type Tab = (typeof TABS)[number][0]

export default function Knowledge() {
  const { host = '' } = useParams()
  const [params, setParams] = useSearchParams()
  const tab = (params.get('tab') as Tab) ?? 'causes'
  const kb = useKnowledge(host)

  if (kb.isPending) return <Loading />
  if (kb.isError) return <Failure error={kb.error} />
  const k = kb.data
  const counts: Record<Tab, number> = {
    causes: k.causes.length,
    graph: k.rules.length,
    signals: k.signals.length,
    events: k.events.length + k.processes.length,
    actions: k.policy.length,
    expected: k.expected.length + k.workload_rules.length,
    limits: k.limits.length,
    tests: k.tests.length,
  }

  return (
    <div className={ui.page}>
      <div className={ui.head}>
        <h1>База знаний</h1>
        <span className="muted">
          онтология, правила, политика действий; у каждого элемента указан источник
        </span>
      </div>
      <Tabs.Root
        value={tab}
        onValueChange={(v) => setParams(v === 'causes' ? {} : { tab: v }, { replace: true })}
      >
        <Tabs.List className={styles.tabs} aria-label="Разделы базы знаний">
          {TABS.map(([value, label]) => (
            <Tabs.Trigger key={value} value={value} className={styles.tab}>
              {label}
              <span className={styles.count}>{counts[value]}</span>
            </Tabs.Trigger>
          ))}
        </Tabs.List>
        <div style={{ paddingTop: 16 }}>
          <Tabs.Content value="causes">
            <Causes k={k} host={host} />
          </Tabs.Content>
          <Tabs.Content value="graph">
            <Suspense fallback={<Loading />}>
              <Graph k={k} host={host} />
            </Suspense>
          </Tabs.Content>
          <Tabs.Content value="signals">
            <Signals k={k} />
          </Tabs.Content>
          <Tabs.Content value="events">
            <Events k={k} />
          </Tabs.Content>
          <Tabs.Content value="actions">
            <Actions k={k} host={host} />
          </Tabs.Content>
          <Tabs.Content value="expected">
            <Expected k={k} />
          </Tabs.Content>
          <Tabs.Content value="limits">
            <Limits k={k} />
          </Tabs.Content>
          <Tabs.Content value="tests">
            <Tests k={k} host={host} />
          </Tabs.Content>
        </div>
      </Tabs.Root>
    </div>
  )
}

function gradeTone(grade: string | null) {
  return grade === 'documented'
    ? 'green'
    : grade === 'measured'
      ? 'blue'
      : grade === 'unverified'
        ? 'ochre'
        : 'plain'
}

function Causes({ k, host }: { k: KB; host: string }) {
  const [q, setQ] = useState('')
  const text = q.trim().toLowerCase()
  const match = (name: string, label: string | null, comment: string | null) =>
    !text ||
    [name, label ?? '', comment ?? '', causeNames[name] ?? ''].some((s) => s.toLowerCase().includes(text))
  return (
    <div className={ui.page}>
      <input
        className={styles.search}
        type="search"
        placeholder="Найти причину"
        aria-label="Найти причину"
        value={q}
        onChange={(e) => setQ(e.target.value)}
      />
      {DOMAINS.map((domain) => {
        const causes = k.causes.filter((c) => c.domain === domain && match(c.name, c.label, c.comment))
        if (causes.length === 0) return null
        return (
          <section key={domain} className={ui.page} style={{ gap: 10 }}>
            <h2>{domainNames[domain]}</h2>
            <div className={styles.causes}>
              {causes.map((c) => {
                const rule = k.rules.find((r) => r.cause === c.name)
                const actions = k.policy.filter((p) => p.cause === c.name)
                return (
                  <Link key={c.name} to={`/hosts/${host}/knowledge/${c.name}`} className={styles.causeCard}>
                    <span className={styles.causeName}>{named(causeNames, c.name, c.label)}</span>
                    <Key>{c.name}</Key>
                    <span className="muted" style={{ fontSize: 13 }}>
                      {c.comment}
                    </span>
                    <span className={styles.meta}>
                      {c.resources.map((r) => (
                        <Chip key={r}>{named(resourceNames, r)}</Chip>
                      ))}
                      {rule && <Chip>{rule.symptoms.length} симптомов</Chip>}
                      <Chip tone={actions.length ? 'blue' : 'plain'}>
                        {actions.length ? `${actions.length} действ.` : 'только рекомендация'}
                      </Chip>
                      <Chip tone={gradeTone(c.grade)}>{gradeNames[c.grade ?? ''] ?? c.grade}</Chip>
                    </span>
                  </Link>
                )
              })}
            </div>
          </section>
        )
      })}
    </div>
  )
}

function Signals({ k }: { k: KB }) {
  const [q, setQ] = useState('')
  const text = q.trim().toLowerCase()
  const rows = k.signals.filter(
    (s) =>
      !text ||
      [s.name, signalNames[s.name] ?? '', s.comment ?? ''].some((x) => x.toLowerCase().includes(text)),
  )
  return (
    <div className={ui.page}>
      <input
        className={styles.search}
        type="search"
        placeholder="Найти метрику"
        aria-label="Найти метрику"
        value={q}
        onChange={(e) => setQ(e.target.value)}
      />
      <Panel>
        <div className={ui.scroll}>
          <table style={{ minWidth: 860 }}>
            <thead>
              <tr>
                <th>метрика</th>
                <th>ресурс</th>
                <th>на чём</th>
                <th>единица</th>
                <th>смысл и источник</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((s) => (
                <tr key={s.name}>
                  <td>
                    {named(signalNames, s.name)}
                    <div>
                      <Key>{s.name}</Key>
                    </div>
                    {s.impact && <Chip tone="red">измеряет влияние</Chip>}
                  </td>
                  <td>
                    {named(resourceNames, s.resource)}
                    {s.reflects.length > 0 && (
                      <div className="muted" style={{ fontSize: 12 }}>
                        отражает: {s.reflects.map((r) => named(resourceNames, r)).join(', ')}
                      </div>
                    )}
                  </td>
                  <td className="mono">{s.entity}</td>
                  <td className="mono">{s.unit}</td>
                  <td>
                    {s.comment && <div>{s.comment}</div>}
                    <Source source={s.source} grade={s.grade} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  )
}

function Events({ k }: { k: KB }) {
  return (
    <div className={ui.page}>
      <Panel title="Типы событий" aside="ядро и журнал systemd">
        <table>
          <tbody>
            {k.events.map((e) => (
              <tr key={e.name}>
                <td>
                  {named(eventNames, e.name, e.label)}
                  <div>
                    <Key>{e.name}</Key>
                  </div>
                </td>
                <td className="muted">{e.emitted_by}</td>
                <td className="mono" style={{ overflowWrap: 'anywhere' }}>
                  {e.message_id ?? e.pattern}
                </td>
                <td>
                  {e.comment && <div>{e.comment}</div>}
                  <Source source={e.source} grade={e.grade} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>
      <Panel title="Симптомы процессов" aside="ищутся в снимках процессов">
        <table>
          <tbody>
            {k.processes.map((p) => (
              <tr key={p.name}>
                <td>
                  {named(processNames, p.name, p.label)}
                  <div>
                    <Key>{p.name}</Key>
                  </div>
                </td>
                <td className="mono">
                  {p.pattern}
                  {p.state ? ` · ${p.state}` : ''}
                </td>
                <td>
                  {p.comment && <div>{p.comment}</div>}
                  <Source source={p.source} grade={p.grade} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>
      <Panel title="Факты" aside="условия правил">
        <table>
          <tbody>
            {k.facts.map((f) => (
              <tr key={f.name}>
                <td>
                  {named(factNames, f.name)}
                  <div>
                    <Key>{f.name}</Key>
                  </div>
                </td>
                <td className="mono">{f.values.join(', ')}</td>
                <td>
                  {f.comment && <div>{f.comment}</div>}
                  <Source source={f.source} grade={f.grade} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>
    </div>
  )
}

function Actions({ k, host }: { k: KB; host: string }) {
  return (
    <div className={ui.page}>
      <Panel
        title="Политика"
        aside="какое действие лечит какую причину; параметр — доля текущего потребления виновника"
      >
        <div className={ui.scroll}>
          <table style={{ minWidth: 860 }}>
            <thead>
              <tr>
                <th>причина</th>
                <th>действие</th>
                <th>параметр</th>
                <th>помогает</th>
                <th>побочные эффекты</th>
              </tr>
            </thead>
            <tbody>
              {k.policy.map((p, i) => (
                <tr key={i}>
                  <td>
                    <Link to={`/hosts/${host}/knowledge/${p.cause}`}>{named(causeNames, p.cause)}</Link>
                  </td>
                  <td>
                    {named(actionNames, p.action)}
                    <div>
                      <Key>{p.action}</Key>
                    </div>
                  </td>
                  <td>
                    {p.signal
                      ? `${Math.round(p.share * 100)} % от ${named(signalNames, p.signal)}`
                      : `вес ×${formatNumber(p.share)}`}
                    {p.when && <div className="muted">если {named(signalNames, p.when)}</div>}
                  </td>
                  <td>{likelihoodNames[p.effectiveness] ?? p.effectiveness}</td>
                  <td>
                    {p.side_effects.map((s) => (
                      <div key={s.effect}>
                        {s.effect}{' '}
                        <span className="muted">({likelihoodNames[s.likelihood] ?? s.likelihood})</span>
                      </div>
                    ))}
                    <Source source={p.source} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
      <div className={ui.grid}>
        <Panel title="Типы действий исполнителя">
          <table>
            <tbody>
              {k.actions.map((a) => (
                <tr key={a.name}>
                  <td>
                    {named(actionNames, a.name, a.label)}
                    <div>
                      <Key>{a.name}</Key>
                    </div>
                  </td>
                  <td className="muted">
                    {named(resourceNames, a.resource)}
                    {a.proportional && ', вес'}
                  </td>
                  <td>
                    {a.comment && <div>{a.comment}</div>}
                    <Source source={a.source} grade={a.grade} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
        <Panel title="Противопоказания" aside="действия, которые не предлагаются для причины">
          {k.contraindications.map((c) => (
            <div key={c.cause}>
              <Link to={`/hosts/${host}/knowledge/${c.cause}`}>{named(causeNames, c.cause)}</Link>:{' '}
              {c.actions.map((a) => named(actionNames, a)).join(', ')}
            </div>
          ))}
        </Panel>
        <Panel title="Защищённые сущности" aside="исполнитель их не трогает">
          <table>
            <tbody>
              {k.protected.map((p) => (
                <tr key={p.name}>
                  <td>{named(protectedNames, p.name, p.label)}</td>
                  <td className="mono" style={{ overflowWrap: 'anywhere' }}>
                    {[...p.units, ...p.commands].join(', ')}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      </div>
    </div>
  )
}

function Expected({ k }: { k: KB }) {
  return (
    <div className={ui.page}>
      <Panel
        title="Ожидаемое поведение"
        aside="необычное, которое объясняет тип нагрузки; влияние никогда не ожидаемое"
      >
        <table>
          <tbody>
            {k.expected.map((e, i) => (
              <tr key={i}>
                <td>{named(classNames, e.class)}</td>
                <td>
                  {e.signals.map((s) => named(signalNames, s)).join(', ')}: {directionNames[e.direction]}
                  {e.members && <span className="muted"> · только у групп этого типа</span>}
                </td>
                <td>
                  <Source source={e.source} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>
      <Panel title="Типы нагрузки" aside="правила, по которым хост относят к типу">
        <table>
          <tbody>
            {k.workload_rules.map((r, i) => (
              <tr key={i}>
                <td>
                  {named(classNames, r.class)} <span className="muted">· приоритет {r.priority}</span>
                </td>
                <td className="mono" style={{ overflowWrap: 'anywhere' }}>
                  {[...r.command, ...r.unit, ...r.group].join(', ')}
                </td>
                <td>
                  <Source source={r.source} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>
    </div>
  )
}

function Limits({ k }: { k: KB }) {
  return (
    <Panel title="Пороги и уровни влияния" aside="фиксированные пределы детекции">
      <table>
        <thead>
          <tr>
            <th>вид</th>
            <th>метрика</th>
            <th>предел</th>
            <th>источник</th>
          </tr>
        </thead>
        <tbody>
          {k.limits.map((l, i) => (
            <tr key={i}>
              <td>
                {l.kind === 'impact' ? (
                  <Chip tone="red">влияние: {levelNames[l.level ?? ''] ?? l.level}</Chip>
                ) : (
                  <Chip tone="ochre">порог</Chip>
                )}
              </td>
              <td>
                {named(signalNames, l.signal)}
                <div>
                  <Key>{l.signal}</Key>
                </div>
              </td>
              <td>
                {l.above !== null
                  ? `выше ${formatValue(l.signal, l.above)}`
                  : `ниже ${formatValue(l.signal, l.below)}`}
              </td>
              <td>
                <Source source={l.source} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Panel>
  )
}

function Tests({ k, host }: { k: KB; host: string }) {
  if (k.tests.length === 0) return <Empty>Тестов нет.</Empty>
  return (
    <Panel
      title="Тесты базы знаний"
      aside="синтетические свидетельства и причина, которую должен назвать вывод"
    >
      <table>
        <thead>
          <tr>
            <th>случай</th>
            <th>ожидается</th>
            <th>свидетельства</th>
          </tr>
        </thead>
        <tbody>
          {k.tests.map((t) => (
            <tr key={t.name}>
              <td>
                {t.name}
                <div className="muted">{domainNames[t.domain] ?? t.domain}</div>
              </td>
              <td>
                {t.expect ? (
                  <Link to={`/hosts/${host}/knowledge/${t.expect}`}>{named(causeNames, t.expect)}</Link>
                ) : (
                  'ни одной причины'
                )}
                {t.within > 1 && <span className="muted"> · в первых {t.within}</span>}
                {t.not.length > 0 && (
                  <div className="muted">
                    не выше порога: {t.not.map((c) => named(causeNames, c)).join(', ')}
                  </div>
                )}
              </td>
              <td style={{ fontSize: 13 }}>
                {t.items.map((it, i) => (
                  <div key={i}>
                    {it.at} с:{' '}
                    {named(
                      it.kind === 'event' ? eventNames : it.kind === 'process' ? processNames : signalNames,
                      it.name,
                    )}
                    {it.direction ? ` ${it.direction === 'up' ? '↑' : '↓'}` : ''} ·{' '}
                    {roleNames[it.role] ?? it.role}
                  </div>
                ))}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Panel>
  )
}
