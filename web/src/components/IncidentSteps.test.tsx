import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, expect, it, vi } from 'vitest'
import type { Action, Incident } from '../api/types'
import IncidentSteps from './IncidentSteps'

const fixture = vi.hoisted(() => ({ actions: [] as Action[], pending: false, error: false }))
vi.mock('../api/hooks', () => ({
  useIncidentActions: () => ({
    data: fixture.actions,
    isPending: fixture.pending,
    isError: fixture.error,
    error: new Error('unavailable'),
  }),
}))
const incident: Incident = {
  summary: {
    id: 1,
    opened_at: '2026-11-15T10:00:00Z',
    closed_at: '2026-11-15T10:10:00Z',
    last_harm_at: '2026-11-15T10:08:00Z',
    workload_class: null,
    records: 1,
    cause: null,
    culprit: null,
    confidence: null,
    hypotheses: 0,
    waiting: 0,
  },
  hypotheses: [],
  proposals: [],
}
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
  state: 'reverted',
  outcome: 'worked',
  files: [],
  reason: 'the timer ran out',
  asked: null,
  confirmed_by: 'operator',
  confirmed_at: '2026-11-15T10:01:00Z',
  applied_at: '2026-11-15T10:01:10Z',
  verified_at: '2026-11-15T10:02:10Z',
  ended_at: '2026-11-15T10:31:10Z',
  revert_at: null,
  log: [],
}
function show() {
  render(
    <MemoryRouter>
      <IncidentSteps host="laptop" incident={incident} offset={0} now={Date.parse('2026-11-24T10:00:00Z')} />
    </MemoryRouter>,
  )
}
afterEach(() => {
  cleanup()
  fixture.actions = []
  fixture.pending = false
  fixture.error = false
})
it('does not turn a closed incident without an action into a successful intervention', () => {
  show()
  expect(screen.getByText('Подтверждённых действий нет.')).toBeInTheDocument()
  expect(within(screen.getByRole('list', { name: 'Шаги инцидента' })).getAllByRole('listitem')).toHaveLength(
    6,
  )
  expect(screen.queryByText('помогло')).not.toBeInTheDocument()
})
it('keeps interventions separate and links an old action with its exact history interval', () => {
  fixture.actions = [
    action,
    { ...action, id: 2, outcome: 'failed', state: 'applied', asked: 'revert', ended_at: null },
  ]
  show()
  const first = screen.getByText(/Действие 1:/)
  fireEvent.click(first)
  const firstSteps = screen.getByRole('list', { name: 'Шаги действия 1' })
  expect(within(firstSteps).getByText('помогло')).toBeInTheDocument()
  expect(within(firstSteps).queryByText('не помогло')).not.toBeInTheDocument()
  fireEvent.click(screen.getByText(/Действие 2:/))
  expect(
    within(screen.getByRole('list', { name: 'Шаги действия 2' })).getByText(
      'Откат запрошен; ждём исполнителя',
    ),
  ).toBeInTheDocument()
  const href = screen.getByRole('link', { name: 'Журнал действия 1' }).getAttribute('href')!
  const url = new URL(href, 'http://localhost')
  expect(url.searchParams.get('since')).toBe('2026-11-15T10:00:00.000Z')
  expect(url.hash).toBe('#action-1')
})
it.each(['pending', 'error'] as const)('does not claim missing actions when their request is %s', (key) => {
  fixture[key] = true
  show()
  expect(screen.queryByText('Подтверждённых действий нет.')).not.toBeInTheDocument()
  expect(screen.queryByRole('list', { name: 'Шаги инцидента' })).not.toBeInTheDocument()
})
