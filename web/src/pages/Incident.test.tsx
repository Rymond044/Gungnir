import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, expect, it, vi } from 'vitest'
import Incident from './Incident'

const fixture = vi.hoisted(() => ({ closed: true }))
vi.mock('../api/hooks', () => ({
  useOverview: () => ({ data: { host: { utc_offset_s: 0 } } }),
  useAnomalies: () => ({ data: [] }),
  useDecision: () => ({}),
  useIncidentActions: () => ({ data: [] }),
  useIncident: () => ({
    data: {
      summary: {
        id: 269,
        opened_at: '2026-11-20T00:58:20Z',
        last_harm_at: '2026-11-20T00:58:40Z',
        closed_at: fixture.closed ? '2026-11-20T01:00:40Z' : null,
        cause: fixture.closed ? 'process_churn' : null,
        culprit: null,
        confidence: fixture.closed ? 0.84 : null,
      },
      hypotheses: [
        {
          id: 1,
          cause: 'process_churn',
          culprit: null,
          rank: null,
          harm: true,
          confidence: 0.3,
          peak_confidence: 0.84,
          first_seen: '2026-11-20T00:58:31Z',
          tree: {},
        },
      ],
      proposals: [],
    },
  }),
}))
vi.mock('../components/Hypothesis', () => ({ default: () => <span>Stored explanation</span> }))

afterEach(cleanup)

it.each([true, false])('keeps withdrawn harmful evidence in a closed incident, closed=%s', (closed) => {
  fixture.closed = closed
  render(
    <MemoryRouter initialEntries={['/hosts/laptop/incidents/269']}>
      <Routes>
        <Route path="/hosts/:host/incidents/:id" element={<Incident />} />
      </Routes>
    </MemoryRouter>,
  )
  if (closed) {
    expect(screen.queryByText(/Ни одна причина из базы знаний/)).not.toBeInTheDocument()
    expect(screen.queryByText('Что ещё было в это время')).not.toBeInTheDocument()
  } else {
    expect(screen.getByText(/Ни одна причина из базы знаний/)).toBeInTheDocument()
    expect(screen.getByText('Что ещё было в это время')).toBeInTheDocument()
  }
})
