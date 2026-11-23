import { cleanup, render } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, expect, it, vi } from 'vitest'
import { useAnomalies, useChart, useNorm } from '../api/hooks'
import { metricLink, seriesKey } from '../lib/records'
import Metric from './Metric'

vi.mock('../api/hooks', () => ({
  useOverview: () => ({ data: { host: { utc_offset_s: 0 } } }),
  useAnomalies: vi.fn(() => ({ data: [] })),
  useChart: vi.fn(() => ({ data: { series: [], bucket_s: 5 } })),
  useNorm: vi.fn(() => ({})),
}))
vi.mock('../components/Chart', () => ({ default: () => null }))
vi.mock('../lib/useNow', () => ({ useNow: () => Date.parse('2026-11-23T10:40:00Z') }))

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

it.each([
  'host',
  'nvme0n1',
  'user.slice/user-1000.slice/user@1000.service/app.slice/app-org.chromium.Chromium.scope',
  'user.slice/user-1000.slice/user@1000.service/app.slice/app@worker.scope',
])('preserves series, records and norm for entity %s when opening a large graph', (entity) => {
  const series = {
    signal: 'cgroup.cpu_cores',
    entity,
  }
  const interval = { since: '2026-11-23T10:15:09.265Z', until: '2026-11-23T10:31:00.000Z' }
  render(
    <MemoryRouter initialEntries={[metricLink('laptop', [series], interval)]}>
      <Routes>
        <Route path="/hosts/:host/metric" element={<Metric />} />
      </Routes>
    </MemoryRouter>,
  )
  expect(useChart).toHaveBeenCalledWith('laptop', [seriesKey(series)], interval, 600, false)
  expect(useAnomalies).toHaveBeenCalledWith('laptop', { ...interval, ...series, limit: 500 })
  expect(useNorm).toHaveBeenCalledWith('laptop', series.signal, series.entity, interval, true)
})
