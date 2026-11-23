import { Link, useParams } from 'react-router'
import { useActions, useAsk, useOverview } from '../api/hooks'
import type { Action } from '../api/types'
import Entity from '../components/Entity'
import ActionVerification from '../components/ActionVerification'
import RangePicker from '../components/RangePicker'
import { Chip, Empty, Failure, Key, Loading, Panel } from '../components/ui'
import ui from '../components/ui.module.css'
import { actionStates, limitText, logEvents, outcomes, reasonText } from '../lib/actions'
import { dayClock, ms, span } from '../lib/format'
import { useShownInterval } from '../lib/interval'
import { actionNames, causeNames, named } from '../lib/names'
import { useNow } from '../lib/useNow'

/**
 * What the actuator did on the operator's confirmation (ADRs 0061-0063): the
 * action, its verification and rollback, and every step with who took it.
 */
export default function Actions() {
  const { host = '' } = useParams()
  const now = useNow()
  const shown = useShownInterval('7d', now)
  const overview = useOverview(host)
  const actions = useActions(host, shown.interval, shown.live)
  const ask = useAsk(host)
  const offset = overview.data?.host.utc_offset_s ?? null

  return (
    <div className={ui.page}>
      <div className={ui.head}>
        <h1>Действия</h1>
        <span className="muted">что исполнитель изменил по подтверждению, помогло ли и когда откатит</span>
      </div>
      <div className={ui.head}>
        <RangePicker shown={shown} offset={offset} now={now} ranges={['24h', '7d', '30d']} />
      </div>
      <Panel>
        {actions.isPending ? (
          <Loading />
        ) : actions.isError ? (
          <Failure error={actions.error} />
        ) : actions.data.length === 0 ? (
          <Empty>
            За это время ничего не применялось. Действия появляются, когда оператор подтверждает предложение
            инцидента.
          </Empty>
        ) : (
          <table className={ui.fixed}>
            <colgroup>
              <col style={{ width: 150 }} />
              <col />
              <col style={{ width: 230 }} />
              <col style={{ width: 200 }} />
            </colgroup>
            <thead>
              <tr>
                <th>подтверждено</th>
                <th>действие</th>
                <th>состояние</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {actions.data.map((a) => (
                <Row
                  key={a.id}
                  a={a}
                  host={host}
                  offset={offset}
                  now={now}
                  busy={ask.isPending}
                  onAsk={(op) => ask.mutate({ action: a.id, op })}
                />
              ))}
            </tbody>
          </table>
        )}
        {ask.isError && <Failure error={ask.error} />}
      </Panel>
    </div>
  )
}

function Row({
  a,
  host,
  offset,
  now,
  busy,
  onAsk,
}: {
  a: Action
  host: string
  offset: number | null
  now: number
  busy: boolean
  onAsk: (op: 'revert' | 'keep') => void
}) {
  const live = a.state === 'applied' || a.state === 'kept'
  const canKeep = a.state === 'applied' && (a.outcome === 'worked' || a.outcome === 'nothing_to_judge')
  const verifyAt = a.applied_at ? ms(a.applied_at) + a.verify_after_s * 1000 : null
  return (
    <tr className={ui.tableRow}>
      <td className="mono">
        {dayClock(ms(a.confirmed_at), offset, now)}
        <div className="muted" style={{ fontSize: 12.5 }}>
          {a.confirmed_by}
        </div>
      </td>
      <td>
        <div>
          <b>{named(actionNames, a.action)}</b> <Key>{a.action}</Key> для <Entity entity={a.target} />
        </div>
        <div className="muted" style={{ fontSize: 13 }}>
          {limitText(a.action, a.value, a.share)} · против «{named(causeNames, a.cause)}» ·{' '}
          <Link to={`/hosts/${host}/incidents/${a.incident_id}`}>инцидент {a.incident_id}</Link>
        </div>
        <details style={{ fontSize: 13, marginTop: 4 }}>
          <summary className="muted">журнал</summary>
          <ActionVerification action={a} host={host} offset={offset} now={now} />
          {a.files && a.files.length > 0 && (
            <div style={{ margin: '4px 0' }}>
              {a.files.map((f, i) => (
                <div key={i} className="mono" style={{ fontSize: 12.5 }}>
                  {f.group}/{f.file}
                  {f.key ? ` ${f.key}` : ''}: {f.before} → {f.after}
                </div>
              ))}
            </div>
          )}
          {a.log.map((e, i) => (
            <div key={i}>
              <span className="mono">{dayClock(ms(e.at), offset, now)}</span> {logEvents[e.event] ?? e.event}
              <span className="muted">
                {' '}
                · {e.by}
                {typeof e.detail.reason === 'string' && ` · ${reasonText(e.detail.reason)}`}
                {typeof e.detail.outcome === 'string' &&
                  ` · ${outcomes[e.detail.outcome as keyof typeof outcomes]?.label ?? e.detail.outcome}`}
              </span>
            </div>
          ))}
        </details>
      </td>
      <td>
        <Chip tone={actionStates[a.state].tone}>{actionStates[a.state].label}</Chip>{' '}
        {a.outcome && (
          <span title={outcomes[a.outcome].title}>
            <Chip tone={outcomes[a.outcome].tone}>{outcomes[a.outcome].label}</Chip>
          </span>
        )}
        <div className="muted" style={{ fontSize: 12.5 }}>
          {a.state === 'applied' && !a.outcome && verifyAt !== null && (
            <div>проверка в {dayClock(verifyAt, offset, now)}</div>
          )}
          {a.state === 'applied' && a.revert_at && (
            <div>
              {ms(a.revert_at) > now
                ? `откат через ${span((ms(a.revert_at) - now) / 1000)}`
                : 'Срок отката истёк; ждём подтверждения исполнителя'}
            </div>
          )}
          {a.asked && live && <div>{a.asked === 'revert' ? 'откат запрошен' : 'запрошено оставить'}</div>}
          {(a.state === 'refused' || a.state === 'reverted') && a.reason && <div>{reasonText(a.reason)}</div>}
        </div>
      </td>
      <td>
        {live && (
          <div style={{ display: 'flex', gap: 6 }}>
            <button
              className={ui.button}
              disabled={busy || a.asked === 'revert'}
              onClick={() => onAsk('revert')}
            >
              Откатить
            </button>
            {canKeep && (
              <button
                className={ui.button}
                disabled={busy || a.asked === 'keep' || a.asked === 'revert'}
                title="не откатывать по таймеру: до завершения группы или перезагрузки"
                onClick={() => onAsk('keep')}
              >
                Оставить
              </button>
            )}
          </div>
        )}
      </td>
    </tr>
  )
}
