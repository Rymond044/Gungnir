import { fireEvent, render, screen } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import type { Shown } from '../lib/interval'
import RangePicker from './RangePicker'

it('picks an interval of its own on one calendar, start then end, with hours from lists', () => {
  const now = Date.UTC(2026, 10, 9, 15, 0)
  const setSpan = vi.fn()
  const shown: Shown = {
    interval: { since: new Date(now - 3600_000).toISOString(), until: new Date(now).toISOString() },
    live: true,
    range: '1h',
    setRange: vi.fn(),
    setSpan,
    changed: false,
    reset: vi.fn(),
  }
  render(<RangePicker shown={shown} offset={0} now={now} />)
  // The screen's own period: nothing to reset.
  expect(screen.queryByText(/Сбросить/)).not.toBeInTheDocument()
  fireEvent.click(screen.getByText('Свой интервал'))
  expect(screen.getByRole('dialog')).toBeInTheDocument()

  const day = (n: number) => screen.getAllByRole('button', { name: new RegExp(`^.*${n} ноября 2026`) })[0]
  fireEvent.click(day(7))
  expect(screen.getByText('Выберите день конца.')).toBeInTheDocument()
  fireEvent.click(day(8))
  fireEvent.change(screen.getByLabelText('с: час'), { target: { value: '14' } })
  fireEvent.change(screen.getByLabelText('с: минуты'), { target: { value: '0' } })
  fireEvent.change(screen.getByLabelText('по: час'), { target: { value: '16' } })
  fireEvent.change(screen.getByLabelText('по: минуты'), { target: { value: '30' } })
  fireEvent.click(screen.getByText('Показать'))
  expect(setSpan).toHaveBeenCalledWith(Date.UTC(2026, 10, 7, 14, 0), Date.UTC(2026, 10, 8, 16, 30))
})

it("resets an interval of its own to the screen's period", () => {
  const now = Date.UTC(2026, 10, 9, 15, 0)
  const reset = vi.fn()
  const shown: Shown = {
    interval: {
      since: new Date(now - 7200_000).toISOString(),
      until: new Date(now - 3600_000).toISOString(),
    },
    live: false,
    range: null,
    setRange: vi.fn(),
    setSpan: vi.fn(),
    changed: true,
    reset,
  }
  render(<RangePicker shown={shown} offset={0} now={now} />)
  fireEvent.click(screen.getByText(/Сбросить/))
  expect(reset).toHaveBeenCalled()
})
