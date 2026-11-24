import { expect, it } from 'vitest'
import type { Action } from '../api/types'
import { actionSteps } from './actionSteps'

const action: Action = {
  id: 1,
  proposal_id: 1,
  incident_id: 1,
  cause: 'cpu_hog',
  action: 'cpu_weight',
  target: 'worker.scope',
  value: null,
  share: 0.25,
  revert_after_s: 1800,
  verify_after_s: 60,
  state: 'applied',
  outcome: null,
  files: [],
  reason: null,
  asked: null,
  confirmed_by: 'operator',
  confirmed_at: '2026-11-24T10:00:00Z',
  applied_at: '2026-11-24T10:00:10Z',
  verified_at: null,
  ended_at: null,
  revert_at: '2026-11-24T10:30:10Z',
  log: [],
}

it('keeps a successful verification after an acknowledged timer rollback', () => {
  const steps = actionSteps({
    ...action,
    state: 'reverted',
    outcome: 'worked',
    verified_at: '2026-11-24T10:01:10Z',
    ended_at: '2026-11-24T10:30:10Z',
    reason: 'the timer ran out',
  })
  expect(steps[2]).toMatchObject({ state: 'done', detail: 'помогло', at: '2026-11-24T10:01:10Z' })
  expect(steps[3]).toMatchObject({ state: 'done', detail: 'Откат подтверждён: истёк таймер' })
})
it('does not complete rollback when verification failed and revert was only requested', () => {
  const steps = actionSteps({ ...action, outcome: 'failed', asked: 'revert' })
  expect(steps[2].state).toBe('failed')
  expect(steps[3]).toMatchObject({ state: 'waiting', detail: 'Откат запрошен; ждём исполнителя' })
})
it.each(['queued', 'refused'] as const)('does not invent application or verification after %s', (state) => {
  const steps = actionSteps({ ...action, state, applied_at: null, reason: 'protected group' })
  expect(steps[1].state).toBe(state === 'refused' ? 'failed' : 'waiting')
  expect(steps[2].state).toBe(state === 'refused' ? 'skipped' : 'waiting')
})
it('distinguishes nothing to judge from successful verification', () => {
  expect(actionSteps({ ...action, outcome: 'nothing_to_judge' })[2]).toMatchObject({
    state: 'skipped',
    detail: 'не с чем сравнить',
  })
})
it('does not invent verification when an action reverted before it', () => {
  expect(actionSteps({ ...action, state: 'reverted' })[2]).toMatchObject({
    state: 'skipped',
    detail: 'Результат проверки не получен',
  })
})
it('only completes keep after the actuator acknowledges it', () => {
  expect(actionSteps({ ...action, outcome: 'worked', asked: 'keep' })[3].state).toBe('waiting')
  expect(
    actionSteps({
      ...action,
      outcome: 'worked',
      state: 'kept',
      log: [{ event: 'kept', at: '2026-11-24T10:02:00Z', by: 'actuator', detail: {} }],
    })[3],
  ).toMatchObject({ state: 'done', at: '2026-11-24T10:02:00Z' })
})
