import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, expect, it, vi } from 'vitest'
import { post } from '../api/client'
import Password from './Password'

const fixture = vi.hoisted(() => ({ mustChange: false }))
vi.mock('../api/hooks', () => ({ useMe: () => ({ data: { must_change: fixture.mustChange } }) }))
vi.mock('../api/client', async (original) => ({
  ...(await original<typeof import('../api/client')>()),
  post: vi.fn(),
}))
afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})
function page() {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>
        <Password />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}
it.each([false, true])('provides an exit only for optional password changes: %s', (mustChange) => {
  fixture.mustChange = mustChange
  page()
  if (mustChange) {
    expect(screen.getByText('Задайте свой пароль')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Отмена' })).not.toBeInTheDocument()
  } else {
    expect(screen.getByRole('link', { name: 'Отмена' })).toHaveAttribute('href', '/')
  }
})
it('does not submit mismatched password confirmation', () => {
  fixture.mustChange = false
  page()
  fireEvent.change(screen.getByLabelText('Текущий пароль'), { target: { value: 'test-old-value' } })
  fireEvent.change(screen.getByLabelText('Новый пароль'), { target: { value: 'test-new-value' } })
  fireEvent.change(screen.getByLabelText('Повтор'), { target: { value: 'different-value' } })
  expect(screen.getByText('Пароли не совпадают.')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Сменить' })).toBeDisabled()
  fireEvent.submit(document.querySelector('form')!)
  expect(post).not.toHaveBeenCalled()
})
