import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, expect, it, vi } from 'vitest'
import Runs from './Runs'

const fixture = vi.hoisted(() => ({ failed: false }))
vi.mock('../api/hooks', () => ({
  useRuns: () => ({
    data: [],
    isPending: false,
    isError: fixture.failed,
    error: new Error('QA runs unavailable'),
  }),
  useDiagnosis: () => ({ data: [] }),
}))
afterEach(cleanup)
it.each([false, true])('distinguishes a failed run list from an empty list: %s', (failed) => {
  fixture.failed = failed
  render(
    <MemoryRouter>
      <Runs host="laptop" />
    </MemoryRouter>,
  )
  if (failed) {
    expect(screen.getByRole('alert')).toHaveTextContent('Сервер недоступен')
    expect(screen.queryByText('Размеченных прогонов с нагрузкой нет.')).not.toBeInTheDocument()
  } else {
    expect(screen.getByText('Размеченных прогонов с нагрузкой нет.')).toBeInTheDocument()
  }
})
