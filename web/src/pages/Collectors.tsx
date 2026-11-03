import { useParams } from 'react-router'
import { useOverview } from '../api/hooks'
import { Chip, Failure, Key, Loading, Panel } from '../components/ui'
import ui from '../components/ui.module.css'
import { age, dayClock, ms } from '../lib/format'
import { collectorNames, collectorStates, factNames, named } from '../lib/names'
import { useNow } from '../lib/useNow'
import Entity from '../components/Entity'

const TONE = { preferred: 'green', fallback: 'ochre', disabled: 'red' } as const

/** What the agent can see: each collector's source, and the facts of the host. */
export default function Collectors() {
  const { host = '' } = useParams()
  const overview = useOverview(host)
  const now = useNow()
  if (overview.isPending) return <Loading />
  if (overview.isError) return <Failure error={overview.error} />
  const o = overview.data
  const offset = o.host.utc_offset_s

  return (
    <div className={ui.page}>
      <div className={ui.head}>
        <h1>Коллекторы и факты</h1>
        <span className="muted">
          агент {o.host.agent_version ?? '—'}
          {o.host.agent_started_at && `, запущен ${dayClock(ms(o.host.agent_started_at), offset, now)}`}
          {o.host.dropped_rows > 0 && `, потеряно строк при переполнении буфера: ${o.host.dropped_rows}`}
        </span>
      </div>
      <Panel
        title="Коллекторы"
        aside="при старте агент выбирает основной источник, иначе запасной, иначе отключает коллектор"
      >
        <div className={ui.scroll}>
          <table>
            <thead>
              <tr>
                <th>коллектор</th>
                <th>состояние</th>
                <th>источник</th>
                <th>причина</th>
              </tr>
            </thead>
            <tbody>
              {o.collectors.map((c) => (
                <tr key={c.collector}>
                  <td>
                    {named(collectorNames, c.collector)} <Key>{c.collector}</Key>
                  </td>
                  <td>
                    <Chip tone={TONE[c.state]}>{collectorStates[c.state]}</Chip>
                  </td>
                  <td className="mono" style={{ overflowWrap: 'anywhere' }}>
                    {c.origin ?? '—'}
                  </td>
                  <td className="muted" style={{ overflowWrap: 'anywhere' }}>
                    {c.reason ?? ''}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
      <Panel title="Факты" aside="условия, которые проверяют правила базы знаний">
        <table>
          <thead>
            <tr>
              <th>факт</th>
              <th>сущность</th>
              <th>значение</th>
              <th>с</th>
            </tr>
          </thead>
          <tbody>
            {o.facts.map((f) => (
              <tr key={`${f.name}${f.entity}`}>
                <td>
                  {named(factNames, f.name)} <Key>{f.name}</Key>
                </td>
                <td>
                  <Entity entity={f.entity} />
                </td>
                <td className="mono">{f.value}</td>
                <td className="muted">{age((now - ms(f.ts)) / 1000)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>
    </div>
  )
}
