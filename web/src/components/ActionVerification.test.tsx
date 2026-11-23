import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, expect, it } from 'vitest'
import type { Action } from '../api/types'
import ActionVerification from './ActionVerification'

const target = 'user.slice/user-1000.slice/user@1000.service/app.slice/hog.scope'
const victim = 'user.slice/user-1000.slice/user@1000.service/app.slice/victim.scope'
const applied = '2026-11-23T10:00:00Z'
const verified = '2026-11-23T10:01:00Z'

afterEach(cleanup)

function action(detail: Record<string, unknown>): Action {
  return {
    id: 1,
    proposal_id: 2,
    incident_id: 3,
    cause: 'cpu_hog',
    action: 'cpu_weight',
    target,
    value: null,
    share: 0.25,
    revert_after_s: 1800,
    verify_after_s: 60,
    state: 'applied',
    outcome: 'worked',
    files: null,
    reason: null,
    asked: null,
    confirmed_by: 'admin',
    confirmed_at: applied,
    applied_at: applied,
    verified_at: verified,
    ended_at: null,
    revert_at: null,
    log: [{ at: verified, event: 'verified', by: 'server', detail }],
  }
}

it('keeps evidence in expanded details and links the exact before/after windows', () => {
  render(
    <MemoryRouter>
      <details>
        <summary>журнал</summary>
        <ActionVerification
          action={action({ before: [`cgroup.cpu_pressure_some_ratio@${victim}`], after: [] })}
          host="laptop"
          offset={0}
          now={Date.parse(verified)}
        />
      </details>
    </MemoryRouter>,
  )
  expect(screen.getByText('Проверка эффекта')).not.toBeVisible()
  fireEvent.click(screen.getByText('журнал'))
  expect(screen.getByText('Проверка эффекта')).toBeVisible()
  const links = screen.getAllByRole('link')
  const metric = links.find((link) => link.getAttribute('href')?.includes('/metric?'))!
  const url = new URL(metric.getAttribute('href')!, 'http://localhost')
  expect(url.searchParams.get('s')).toBe(`cgroup.cpu_pressure_some_ratio@${victim}`)
  expect(url.searchParams.get('since')).toBe('2026-11-23T09:59:30.000Z')
  expect(url.searchParams.get('until')).toBe('2026-11-23T10:00:00.000Z')
  const after = new URL(
    screen.getAllByRole('link', { name: 'Аудит интервала' })[1].getAttribute('href')!,
    'http://localhost',
  )
  expect(after.searchParams.get('since')).toBe('2026-11-23T10:00:30.000Z')
  expect(after.searchParams.get('until')).toBe('2026-11-23T10:01:00.000Z')
  expect(screen.getAllByText('Признаков деградации других программ не обнаружено.')).toHaveLength(1)
})

it('does not interpret missing historical evidence as an absence of impact', () => {
  render(
    <MemoryRouter>
      <ActionVerification
        action={action({ outcome: 'worked' })}
        host="laptop"
        offset={0}
        now={Date.parse(verified)}
      />
    </MemoryRouter>,
  )
  expect(screen.getByText('Подробные свидетельства этой проверки не сохранены.')).toBeVisible()
  expect(screen.queryByText('Признаков деградации других программ не обнаружено.')).not.toBeInTheDocument()
})
