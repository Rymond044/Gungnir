import { Link, useParams } from 'react-router'
import { useIncidents, useOverview } from '../api/hooks'
import Entity from '../components/Entity'
import RangePicker from '../components/RangePicker'
import { Chip, Empty, Failure, Key, Loading, Panel } from '../components/ui'
import ui from '../components/ui.module.css'
import { confidence, dayClock, duration, ms } from '../lib/format'
import { useShownInterval } from '../lib/interval'
import { causeNames, named } from '../lib/names'
import { useNow } from '../lib/useNow'

/**
 * Incidents of the host, newest first (ADR 0060): stretches of time in which
 * someone was hurt, each with its best cause and the proposals waiting for a
 * decision.
 */
export default function Incidents() {
  const { host = '' } = useParams()
  const now = useNow()
  const shown = useShownInterval('7d', now)
  const overview = useOverview(host)
  const incidents = useIncidents(host, shown.interval, shown.live)
  const offset = overview.data?.host.utc_offset_s ?? null

  return (
    <div className={ui.page}>
      <div className={ui.head}>
        <h1>Инциденты</h1>
        <span className="muted">когда программам мешали и что с этим делать</span>
      </div>
      <div className={ui.head}>
        <RangePicker shown={shown} offset={offset} now={now} ranges={['24h', '7d', '30d']} />
      </div>
      <Panel>
        {incidents.isPending ? (
          <Loading />
        ) : incidents.isError ? (
          <Failure error={incidents.error} />
        ) : incidents.data.length === 0 ? (
          <Empty>
            За это время никому не мешали. Необычная активность без вреда видна в аудите, инцидента она не
            открывает.
          </Empty>
        ) : (
          <table className={ui.fixed}>
            <colgroup>
              <col style={{ width: 70 }} />
              <col style={{ width: 210 }} />
              <col style={{ width: 110 }} />
              <col />
              <col style={{ width: 120 }} />
              <col style={{ width: 150 }} />
            </colgroup>
            <thead>
              <tr>
                <th>№</th>
                <th>когда</th>
                <th>длилось</th>
                <th>причина и виновник</th>
                <th>уверенность</th>
                <th>предложения</th>
              </tr>
            </thead>
            <tbody>
              {incidents.data.map((i) => {
                const end = i.closed_at ? ms(i.closed_at) : now
                return (
                  <tr key={i.id} className={ui.tableRow}>
                    <td className="mono">
                      <Link to={`/hosts/${host}/incidents/${i.id}`}>{i.id}</Link>
                    </td>
                    <td className="mono">
                      <Link to={`/hosts/${host}/incidents/${i.id}`}>
                        {dayClock(ms(i.opened_at), offset, now)}
                      </Link>{' '}
                      {i.closed_at === null && (
                        <Chip tone="red" dot>
                          открыт
                        </Chip>
                      )}
                    </td>
                    <td className="muted">{duration((end - ms(i.opened_at)) / 1000)}</td>
                    <td>
                      {i.cause ? (
                        <>
                          {named(causeNames, i.cause)} <Key>{i.cause}</Key>
                          {i.culprit && (
                            <>
                              {' · '}
                              <Entity entity={i.culprit} />
                            </>
                          )}
                        </>
                      ) : (
                        <span className="muted">причина не найдена, есть записи влияния</span>
                      )}
                    </td>
                    <td className="mono">{i.confidence === null ? '—' : confidence(i.confidence)}</td>
                    <td>
                      {i.waiting > 0 ? (
                        <Chip tone="ochre">ждут решения: {i.waiting}</Chip>
                      ) : (
                        <span className="muted">—</span>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </Panel>
    </div>
  )
}
