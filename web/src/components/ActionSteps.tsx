import type { Action } from '../api/types'
import { actionSteps } from '../lib/actionSteps'
import Steps from './Steps'

export default function ActionSteps({
  action,
  offset,
  now,
}: {
  action: Action
  offset: number | null
  now: number
}) {
  return <Steps label={`Шаги действия ${action.id}`} steps={actionSteps(action)} offset={offset} now={now} />
}
