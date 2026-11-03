import { useMemo, useRef, useState } from 'react'
import type { Point } from '../api/types'
import { clock } from '../lib/format'
import { envelope, line, nearest, niceCeil, runs, timeTicks, x, y, type Frame } from '../lib/scale'
import styles from './Chart.module.css'

export interface Line {
  key: string
  name: string
  color: string
  points: Point[]
}

/** A stretch of the normal band, in one workload class and power profile. */
export interface BandSegment {
  from: number
  to: number
  low: number
  high: number
}

export interface LimitLine {
  value: number
  label: string
  color: string
}

export interface Mark {
  at: number
  label: string
}

interface Props {
  lines: Line[]
  since: number
  until: number
  bucketS: number
  offsetS: number | null
  format: (v: number) => string
  height?: number
  bands?: BandSegment[]
  limits?: LimitLine[]
  marks?: Mark[]
  shade?: { from: number; to: number }
  /** A floor for the axis, so a quiet series does not fill the plot with noise. */
  minMax?: number
  /** Ratios never exceed one. */
  ceiling?: number
}

const W = 1000

export default function Chart({
  lines,
  since,
  until,
  bucketS,
  offsetS,
  format,
  height = 110,
  bands = [],
  limits = [],
  marks = [],
  shade,
  minMax = 0,
  ceiling,
}: Props) {
  const plot = useRef<HTMLDivElement>(null)
  const [hover, setHover] = useState<number | null>(null)

  const { frame, shown, hidden } = useMemo(() => {
    const dataMax = Math.max(0, ...lines.flatMap((l) => l.points.map((p) => p[3])))
    const bandMax = Math.max(0, ...bands.filter((b) => b.to > since && b.from < until).map((b) => b.high))
    const base = Math.max(dataMax, bandMax, minMax)
    // A limit far above the data would flatten it; such limits are named below the plot.
    const shown = limits.filter((l) => l.value <= Math.max(base, 1e-12) * 2)
    const hidden = limits.filter((l) => !shown.includes(l))
    let yMax = niceCeil(Math.max(base, ...shown.map((l) => l.value)) * 1.08)
    if (ceiling !== undefined) yMax = Math.min(yMax, ceiling)
    const frame: Frame = { since, until, yMax, width: W, height }
    return { frame, shown, hidden }
  }, [lines, bands, limits, since, until, height, minMax, ceiling])

  const gap = Math.max(bucketS, 5) * 3000
  const ticks = timeTicks(since, until, offsetS)
  const pct = (t: number) => `${(((t - since) / (until - since)) * 100).toFixed(3)}%`

  function onMove(e: React.PointerEvent) {
    const box = plot.current?.getBoundingClientRect()
    if (!box) return
    const share = (e.clientX - box.left) / box.width
    setHover(since + Math.max(0, Math.min(1, share)) * (until - since))
  }

  const readout =
    hover === null
      ? null
      : lines.map((l) => {
          const i = nearest(l.points, hover)
          const p = i >= 0 && Math.abs(l.points[i][0] - hover) <= gap ? l.points[i] : null
          return { line: l, point: p }
        })
  const hoverLeft = hover === null ? 0 : (hover - since) / (until - since)

  return (
    <div className={styles.wrap}>
      <div
        ref={plot}
        className={styles.plot}
        style={{ height }}
        onPointerMove={onMove}
        onPointerLeave={() => setHover(null)}
      >
        {ticks.map((t) => (
          <span key={t} className={styles.tick} style={{ left: pct(t) }} />
        ))}
        {shade && (
          <span
            className={styles.shade}
            style={{
              left: pct(Math.max(since, shade.from)),
              width: `${((Math.min(until, shade.to) - Math.max(since, shade.from)) / (until - since)) * 100}%`,
            }}
          />
        )}
        <svg viewBox={`0 0 ${W} ${height}`} preserveAspectRatio="none" aria-hidden="true">
          {bands.map((b, i) => {
            const x0 = x(frame, Math.max(since, b.from))
            const x1 = x(frame, Math.min(until, b.to))
            if (x1 <= x0) return null
            const top = y(frame, b.high)
            return (
              <rect
                key={i}
                x={x0}
                y={top}
                width={x1 - x0}
                height={Math.max(0.5, y(frame, Math.max(0, b.low)) - top)}
                style={{ fill: 'var(--band)' }}
              />
            )
          })}
          {shown.map((l, i) => (
            <line
              key={i}
              x1={0}
              x2={W}
              y1={y(frame, l.value)}
              y2={y(frame, l.value)}
              strokeWidth={1}
              strokeDasharray="5 4"
              vectorEffect="non-scaling-stroke"
              style={{ stroke: l.color }}
            />
          ))}
          {lines.map((l) =>
            runs(l.points, gap).map((run, i) => (
              <g key={`${l.key}-${i}`}>
                {bucketS > 5 && run.length > 1 && (
                  <polygon points={envelope(frame, run)} style={{ fill: l.color, opacity: 0.12 }} />
                )}
                <polyline
                  points={run.length === 1 ? `${line(frame, run)} ${line(frame, run)}` : line(frame, run)}
                  fill="none"
                  strokeWidth={1.6}
                  strokeLinejoin="round"
                  vectorEffect="non-scaling-stroke"
                  style={{ stroke: l.color }}
                />
              </g>
            )),
          )}
        </svg>
        {marks.map((m) =>
          m.at >= since && m.at <= until ? (
            <span key={m.at} className={styles.mark} style={{ left: pct(m.at) }}>
              <span className={styles.markLabel}>{m.label}</span>
            </span>
          ) : null,
        )}
        <span className={styles.yMax}>{format(frame.yMax)}</span>
        {hover !== null && readout && (
          <>
            <span className={styles.cursor} style={{ left: `${hoverLeft * 100}%` }} />
            <div
              className={styles.tip}
              style={
                hoverLeft > 0.6
                  ? { right: `${(1 - hoverLeft) * 100 + 1}%` }
                  : { left: `${hoverLeft * 100 + 1}%` }
              }
            >
              <span>{clock(hover, offsetS)}</span>
              {readout.map(({ line: l, point }) => (
                <span key={l.key} className={styles.tipRow}>
                  <span className={styles.swatch} style={{ background: l.color }} />
                  {l.name}
                  <b>{point ? format(point[1]) : '—'}</b>
                </span>
              ))}
            </div>
          </>
        )}
      </div>
      <div className={styles.axis} aria-hidden="true">
        {ticks.map((t) => (
          <span key={t} style={{ left: pct(t) }}>
            {clock(t, offsetS, false)}
          </span>
        ))}
      </div>
      <div className={styles.legend}>
        {lines.map((l) => (
          <span key={l.key}>
            <span className={styles.swatch} style={{ background: l.color }} />
            {l.name}
          </span>
        ))}
        {bands.length > 0 && (
          <span>
            <span className={styles.swatch} style={{ background: 'var(--green)', opacity: 0.4, height: 8 }} />
            норма
          </span>
        )}
        {shown.map((l) => (
          <span key={l.label}>
            <span className={styles.swatch} style={{ borderTop: `1px dashed ${l.color}`, height: 0 }} />
            {l.label}
          </span>
        ))}
      </div>
      {hidden.length > 0 && (
        <span className={styles.note}>
          Выше графика: {hidden.map((l) => `${l.label} ${format(l.value)}`).join('; ')}
        </span>
      )}
    </div>
  )
}
