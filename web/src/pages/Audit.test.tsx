import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, expect, it, vi } from 'vitest'
import type { Mark } from '../components/Chart'
import Audit from './Audit'

vi.mock('../api/hooks', () => ({
  useOverview: () => ({ data: { host: { utc_offset_s: 0 }, devices: [], memory_bytes: null } }),
  useAudit: () => ({
    data: {
      data_since: null,
      stretches: [false, true].map((harm, i) => ({
        kind: 'unexplained',
        lane: 'cpu',
        start: 100 + i * 100,
        end: 150 + i * 100,
        harm,
        processes: [],
        actor: null,
        waiting: false,
        records: [],
        events: [],
        workload_class: null,
        groups: [],
        advice: [],
        near: null,
      })),
    },
  }),
}))
vi.mock('../components/SignalChart', () => ({
  default: ({ marks = [], onMarkClick }: { marks?: Mark[]; onMarkClick?: (key: string) => void }) => (
    <div>
      {marks.map((m) => (
        <button key={m.key} onClick={() => onMarkClick?.(m.key)}>
          Marker {m.label}
        </button>
      ))}
    </div>
  ),
}))
afterEach(cleanup)
it('keeps chart markers restricted to cards visible under the harm filter', () => {
  render(
    <MemoryRouter initialEntries={['/hosts/laptop/audit']}>
      <Routes>
        <Route path="/hosts/:host/audit" element={<Audit />} />
      </Routes>
    </MemoryRouter>,
  )
  expect(screen.getByRole('button', { name: 'Marker 1' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Marker 2' })).toBeInTheDocument()
  fireEvent.click(screen.getByLabelText('только то, что мешало'))
  expect(screen.queryByRole('button', { name: 'Marker 1' })).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Marker 2' })).toBeInTheDocument()
  const target = document.querySelector('article')!
  const scroll = vi.fn()
  target.scrollIntoView = scroll
  fireEvent.click(screen.getByRole('button', { name: 'Marker 2' }))
  expect(scroll).toHaveBeenCalled()
})
