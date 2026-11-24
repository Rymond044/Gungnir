import { Link } from 'react-router'
import { useIncidentActions } from '../api/hooks'
import type { Incident } from '../api/types'
import { ms } from '../lib/format'
import { actionNames, causeNames, named } from '../lib/names'
import ActionSteps from './ActionSteps'
import { actionSteps } from '../lib/actionSteps'
import ActionVerification from './ActionVerification'
import Entity from './Entity'
import Steps, { type Step } from './Steps'
import { Empty, Failure, Loading, Panel } from './ui'

export default function IncidentSteps({
  host,
  incident,
  offset,
  now,
}: {
  host: string
  incident: Incident
  offset: number | null
  now: number
}) {
  const actions = useIncidentActions(host, String(incident.summary.id))
  if (actions.isPending)
    return (
      <Panel title="Ход инцидента">
        <Loading />
      </Panel>
    )
  if (actions.isError)
    return (
      <Panel title="Ход инцидента">
        <Failure error={actions.error} />
      </Panel>
    )
  const rows = actions.data ?? []
  const latest = rows.at(-1)
  const proposal =
    incident.proposals.find((p) => p.id === latest?.proposal_id) ??
    incident.proposals.find((p) => p.state === 'proposed') ??
    incident.proposals.at(-1)
  const hypothesis = proposal
    ? incident.hypotheses.find((h) => h.id === proposal.hypothesis_id)
    : incident.hypotheses.find(
        (h) => h.cause === incident.summary.cause && h.culprit === incident.summary.culprit,
      )
  const closed = incident.summary.closed_at !== null
  const pending = closed ? 'skipped' : 'waiting'
  const stages: Step[] = [
    {
      label: 'Impact',
      state: 'done',
      detail: 'Зафиксировано влияние на работу хоста',
      at: incident.summary.opened_at,
    },
    {
      label: 'Причина',
      state: hypothesis ? 'done' : pending,
      detail: hypothesis ? named(causeNames, hypothesis.cause) : 'Объяснение не найдено',
      at: hypothesis?.first_seen,
    },
    {
      label: 'Предложение',
      state: proposal ? 'done' : pending,
      detail: proposal ? named(actionNames, proposal.action) : 'Действие не предложено',
      at: proposal?.created_at,
    },
    ...(latest
      ? actionSteps(latest).slice(0, 3)
      : ([
          {
            label: 'Подтверждение',
            state:
              proposal?.state === 'confirmed'
                ? 'done'
                : proposal?.state === 'rejected' || proposal?.state === 'expired'
                  ? 'skipped'
                  : pending,
            detail:
              proposal?.state === 'confirmed'
                ? 'Решение записано'
                : proposal?.state === 'rejected'
                  ? 'Оператор отклонил'
                  : proposal?.state === 'expired'
                    ? 'Предложение устарело'
                    : 'Без решения оператора действие не применяется',
            at: proposal?.decided_at,
          },
          { label: 'Применение', state: pending, detail: 'Применение не зафиксировано' },
          { label: 'Проверка', state: pending, detail: 'Результат не получен' },
        ] satisfies Step[])),
  ]
  return (
    <Panel
      title="Ход инцидента"
      aside={latest ? `по последнему действию ${latest.id}` : 'от impact до проверки'}
    >
      <Steps label="Шаги инцидента" steps={stages} offset={offset} now={now} />
      <p className="muted">
        Закрытие инцидента означает, что impact прекратился. Эффект каждого действия и его откат показаны
        отдельно.
      </p>
      {rows.length === 0 ? (
        <Empty>Подтверждённых действий нет.</Empty>
      ) : (
        rows.map((a) => (
          <details key={a.id}>
            <summary>
              Действие {a.id}: {named(actionNames, a.action)} · <Entity entity={a.target} />
            </summary>
            <ActionSteps action={a} offset={offset} now={now} />
            <ActionVerification action={a} host={host} offset={offset} now={now} />
            <Link
              to={`/hosts/${host}/actions?${new URLSearchParams({ since: new Date(ms(a.confirmed_at) - 60_000).toISOString(), until: new Date(Math.max(ms(a.confirmed_at), ms(a.ended_at ?? a.verified_at ?? a.applied_at ?? a.confirmed_at)) + 60_000).toISOString() })}#action-${a.id}`}
            >
              Журнал действия {a.id}
            </Link>
          </details>
        ))
      )}
    </Panel>
  )
}
