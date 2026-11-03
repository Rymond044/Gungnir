import { useChart, useNorm, type Interval as Span } from '../api/hooks'
import type { Point } from '../api/types'
import { ms } from '../lib/format'
import { bandSegments, limitLines } from '../lib/norm'
import { seriesKey } from '../lib/records'
import Chart, { type Interval, type LimitLine } from './Chart'
import { Failure } from './ui'

export interface SeriesSpec {
  signal: string
  entity?: string
  name: string
  color: string
  /** Points transformed before drawing, e.g. a share of a total. */
  map?: (p: Point) => Point
}

interface Props {
  host: string
  series: SeriesSpec[]
  interval: Span
  offsetS: number | null
  format: (v: number) => string
  /** The series whose normal band and limits are drawn. */
  normOf?: { signal: string; entity?: string }
  height?: number
  intervals?: Interval[]
  /** Limits beyond those of the knowledge base, such as what a record was judged against. */
  extraLimits?: LimitLine[]
  minMax?: number
  ceiling?: number
  fitY?: boolean
  onSelect?: (from: number, to: number) => void
  live?: boolean
}

export default function SignalChart({
  host,
  series,
  interval,
  offsetS,
  format,
  normOf,
  height,
  intervals,
  extraLimits = [],
  minMax,
  ceiling,
  fitY,
  onSelect,
  live = true,
}: Props) {
  const keys = series.map(seriesKey)
  const chart = useChart(host, keys, interval, 600, live)
  const norm = useNorm(host, normOf?.signal ?? '', normOf?.entity ?? 'host', interval, !!normOf)
  const hasNorm = !!normOf && !norm.isError

  if (chart.isError) return <Failure error={chart.error} />
  const lines = series.map((s, i) => {
    const points = chart.data?.series[i]?.points ?? []
    return { key: keys[i], name: s.name, color: s.color, points: s.map ? points.map(s.map) : points }
  })
  const own = hasNorm ? limitLines(norm.data) : []
  const limits = [
    ...own,
    ...extraLimits.filter((e) => !own.some((o) => Math.abs(o.value - e.value) <= Math.abs(e.value) * 1e-6)),
  ]
  return (
    <Chart
      lines={lines}
      since={ms(interval.since)}
      until={ms(interval.until)}
      bucketS={chart.data?.bucket_s ?? 5}
      offsetS={offsetS}
      format={format}
      height={height}
      bands={hasNorm ? bandSegments(norm.data) : []}
      limits={limits}
      intervals={intervals}
      minMax={minMax}
      ceiling={ceiling}
      fitY={fitY}
      binary={series[0]?.signal.includes('_bytes') ?? false}
      onSelect={onSelect}
    />
  )
}
