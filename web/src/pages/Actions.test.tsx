import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, expect, it, vi } from 'vitest'
import Actions from './Actions'

const fixture = vi.hoisted(() => ({ asked: null as string | null, revertAt: null as string | null }))
vi.mock('../api/hooks', () => ({
  useOverview: () => ({ data: { host: { utc_offset_s: 0 } } }),
  useAsk: () => ({ mutate: vi.fn(), isPending: false }),
  useActions: () => ({
    data: [
      {
        id: 1,
        incident_id: 1,
        action: 'cpu_weight',
        cause: 'cpu_hog',
        target: 'test.scope',
        value: null,
        share: 0.25,
        state: 'applied',
        outcome: 'worked',
        asked: fixture.asked,
        confirmed_at: '2026-11-23T10:00:00Z',
        confirmed_by: 'qa',
        applied_at: '2026-11-23T10:00:01Z',
        verify_after_s: 60,
        revert_at: fixture.revertAt,
        log: [],
        files: [],
      },
    ],
  }),
}))
vi.mock('../lib/useNow', () => ({ useNow: () => Date.parse('2026-11-23T10:30:00Z') }))
afterEach(() => {
  cleanup()
  fixture.asked = null
  fixture.revertAt = null
})
it.each([null, 'keep', 'revert'])('never enables Keep while rollback is pending: %s', (asked) => {
  fixture.asked = asked
  render(
    <MemoryRouter initialEntries={['/hosts/laptop/actions']}>
      <Routes>
        <Route path="/hosts/:host/actions" element={<Actions />} />
      </Routes>
    </MemoryRouter>,
  )
  expect(screen.getByRole('button', { name: 'Оставить' })).toHaveProperty('disabled', asked !== null)
  expect(screen.getByRole('button', { name: 'Откатить' })).toHaveProperty('disabled', asked === 'revert')
})

it.each([
  ['2026-11-23T10:00:00Z', true],
  ['2026-11-23T11:00:00Z', false],
] as const)('shows an overdue timer without claiming a future rollback: %s', (revertAt, overdue) => {
  fixture.revertAt = revertAt
  render(
    <MemoryRouter initialEntries={['/hosts/laptop/actions']}>
      <Routes>
        <Route path="/hosts/:host/actions" element={<Actions />} />
      </Routes>
    </MemoryRouter>,
  )
  if (overdue) {
    expect(screen.getByText('Срок отката истёк; ждём подтверждения исполнителя')).toBeInTheDocument()
    expect(screen.queryByText(/откат через/)).not.toBeInTheDocument()
  } else {
    expect(screen.getByText(/откат через/)).toBeInTheDocument()
    expect(screen.queryByText(/Срок отката истёк/)).not.toBeInTheDocument()
  }
})
