import { useChart, useNorm, type Interval } from '../api/hooks'
import type { Point } from '../api/types'
import { ms } from '../lib/format'
import { bandSegments, limitLines } from '../lib/norm'
import Chart, { type Mark } from './Chart'
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
  interval: Interval
  offsetS: number | null
  format: (v: number) => string
  /** The series whose normal band and limits are drawn. */
  normOf?: { signal: string; entity?: string }
  height?: number
  marks?: Mark[]
  shade?: { from: number; to: number }
  minMax?: number
  ceiling?: number
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
  marks,
  shade,
  minMax,
  ceiling,
  live = true,
}: Props) {
  const keys = series.map((s) => `${s.signal}@${s.entity ?? 'host'}`)
  const chart = useChart(host, keys, interval, 600, live)
  const norm = useNorm(host, normOf?.signal ?? '', normOf?.entity ?? 'host', interval, !!normOf)
  const hasNorm = !!normOf && !norm.isError

  if (chart.isError) return <Failure error={chart.error} />
  const lines = series.map((s, i) => {
    const points = chart.data?.series[i]?.points ?? []
    return { key: keys[i], name: s.name, color: s.color, points: s.map ? points.map(s.map) : points }
  })
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
      limits={hasNorm ? limitLines(norm.data) : []}
      marks={marks}
      shade={shade}
      minMax={minMax}
      ceiling={ceiling}
    />
  )
}
