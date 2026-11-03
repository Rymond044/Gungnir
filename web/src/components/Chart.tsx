import { useMemo, useRef, useState } from 'react'
import type { Point } from '../api/types'
import { clock } from '../lib/format'
import { envelope, line, missing, nearest, niceAxis, runs, timeTicks, x, y, type Frame } from '../lib/scale'
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

/** A record drawn as a strip along the top of the plot. */
export interface Interval {
  from: number
  to: number
  label: string
  impact?: boolean
  /** Still open: the strip reaches the right edge. */
  open?: boolean
  /** The record the page is about, drawn stronger than its neighbours. */
  current?: boolean
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
  intervals?: Interval[]
  /** A floor for the top of the axis, so a quiet series does not fill the plot with noise. */
  minMax?: number
  /** Ratios never exceed one. */
  ceiling?: number
  /** Levels: the axis hugs the data instead of starting at zero. */
  fitY?: boolean
  /** Bytes: grid lines at round binary units. */
  binary?: boolean
  /** Dragging across the plot selects a stretch of time to zoom into. */
  onSelect?: (from: number, to: number) => void
  /** False while the samples are still on their way: nothing is marked missing yet. */
  loaded?: boolean
}

const W = 1000
const STRIP = 7
// Shorter silences are the agent's batching and the detection lag.
const SILENCE_MS = 60_000

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
  intervals = [],
  minMax = 0,
  ceiling,
  fitY = false,
  binary = false,
  onSelect,
  loaded = true,
}: Props) {
  const plot = useRef<HTMLDivElement>(null)
  const [hover, setHover] = useState<number | null>(null)
  const [drag, setDrag] = useState<{ from: number; to: number } | null>(null)

  const { frame, ticks, shown, hidden } = useMemo(() => {
    const values = lines.flatMap((l) => l.points.filter((p) => p[0] >= since && p[0] <= until))
    const dataMax = Math.max(...values.map((p) => p[3]), -Infinity)
    const dataMin = Math.min(...values.map((p) => p[2]), Infinity)
    const visibleBands = bands.filter((b) => b.to > since && b.from < until)
    const bandMax = Math.max(...visibleBands.map((b) => b.high), -Infinity)
    const top = Math.max(dataMax, bandMax, minMax, 0)
    // A limit far beyond the data would flatten it; such limits are named below the plot.
    const reach = fitY ? Math.max((dataMax - dataMin) * 2, Math.abs(dataMax) * 0.1) : 0
    const shown = limits.filter((l) =>
      fitY ? l.value >= dataMin - reach && l.value <= dataMax + reach : l.value <= Math.max(top, 1e-12) * 2,
    )
    const hidden = limits.filter((l) => !shown.includes(l))
    const hi = Math.max(top, ...shown.map((l) => l.value))
    let lo = Math.min(dataMin, ...shown.map((l) => l.value))
    if (fitY && Number.isFinite(lo)) {
      // Keep the band in view where it is narrow enough to matter.
      const bandMin = Math.min(...visibleBands.map((b) => Math.max(0, b.low)), Infinity)
      if (bandMin >= lo - (hi - lo)) lo = Math.min(lo, bandMin)
    }
    const axis = niceAxis(Number.isFinite(lo) ? lo : 0, hi * 1.02 || 1, !fitY, 4, binary)
    let yMax = axis.max
    if (ceiling !== undefined) yMax = Math.min(yMax, ceiling)
    const frame: Frame = { since, until, yMin: axis.min, yMax, width: W, height }
    const ticks: number[] = []
    for (let v = axis.min + axis.step; v < yMax - axis.step * 1e-6; v += axis.step) ticks.push(v)
    return { frame, ticks, shown, hidden }
  }, [lines, bands, limits, since, until, height, minMax, ceiling, fitY, binary])

  const gap = Math.max(bucketS, 5) * 3000
  const timeMarks = timeTicks(since, until, offsetS)
  const share = (t: number) => (t - since) / (until - since)
  const pct = (t: number) => `${(share(t) * 100).toFixed(3)}%`
  const strips = intervals.filter((i) => i.to >= since && i.from <= until)
  const silent = loaded
    ? missing(
        lines.map((l) => l.points),
        since,
        until,
        Math.max(gap, SILENCE_MS),
      )
    : []
  const stripsH = strips.length ? STRIP + 4 : 0

  function timeAt(e: React.PointerEvent): number {
    const box = plot.current!.getBoundingClientRect()
    return since + Math.max(0, Math.min(1, (e.clientX - box.left) / box.width)) * (until - since)
  }

  function onDown(e: React.PointerEvent) {
    if (!onSelect || e.button !== 0) return
    const t = timeAt(e)
    setDrag({ from: t, to: t })
    plot.current?.setPointerCapture(e.pointerId)
  }

  function onMove(e: React.PointerEvent) {
    if (!plot.current) return
    const t = timeAt(e)
    setHover(t)
    if (drag) setDrag({ ...drag, to: t })
  }

  function onUp() {
    if (drag && onSelect && Math.abs(drag.to - drag.from) > (until - since) * 0.01) {
      onSelect(Math.min(drag.from, drag.to), Math.max(drag.from, drag.to))
    }
    setDrag(null)
  }

  const readout =
    hover === null
      ? null
      : lines.map((l) => {
          const i = nearest(l.points, hover)
          const p = i >= 0 && Math.abs(l.points[i][0] - hover) <= gap ? l.points[i] : null
          return { line: l, point: p }
        })
  const hoverLeft = hover === null ? 0 : share(hover)
  const hoverRecords = hover === null ? [] : strips.filter((s) => hover >= s.from && hover <= s.to)

  return (
    <div className={styles.wrap}>
      <div
        ref={plot}
        className={`${styles.plot} ${onSelect ? styles.selectable : ''}`}
        style={{ height: height + stripsH }}
        onPointerDown={onDown}
        onPointerMove={onMove}
        onPointerUp={onUp}
        onPointerLeave={() => {
          setHover(null)
          if (!drag) setDrag(null)
        }}
      >
        {strips.map((s, i) => {
          const from = Math.max(since, s.from)
          const to = Math.min(until, s.to)
          return (
            <span
              key={i}
              className={`${styles.strip} ${s.impact ? styles.stripImpact : ''} ${s.current ? styles.stripCurrent : ''}`}
              style={{ left: pct(from), width: `max(3px, ${(share(to) - share(from)) * 100}%)` }}
              title={s.label}
            />
          )
        })}
        <div className={styles.area} style={{ top: stripsH, height }}>
          {timeMarks.map((t) => (
            <span key={t} className={styles.tick} style={{ left: pct(t) }} />
          ))}
          {strips
            .filter((s) => s.current)
            .map((s, i) => (
              <span
                key={i}
                className={styles.shade}
                style={{
                  left: pct(Math.max(since, s.from)),
                  width: `${(share(Math.min(until, s.to)) - share(Math.max(since, s.from))) * 100}%`,
                }}
              />
            ))}
          <svg viewBox={`0 0 ${W} ${height}`} preserveAspectRatio="none" aria-hidden="true">
            {ticks.map((v) => (
              <line
                key={v}
                x1={0}
                x2={W}
                y1={y(frame, v)}
                y2={y(frame, v)}
                strokeWidth={1}
                vectorEffect="non-scaling-stroke"
                style={{ stroke: 'var(--grid-minor)' }}
              />
            ))}
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
                  height={Math.max(0.5, y(frame, Math.max(frame.yMin, b.low)) - top)}
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
                strokeWidth={1.2}
                strokeDasharray="6 4"
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
                  {run.length === 1 ? (
                    <circle
                      cx={x(frame, run[0][0])}
                      cy={y(frame, run[0][1])}
                      r={1.6}
                      vectorEffect="non-scaling-stroke"
                      style={{ fill: l.color }}
                    />
                  ) : (
                    <polyline
                      points={line(frame, run)}
                      fill="none"
                      strokeWidth={1.6}
                      strokeLinejoin="round"
                      vectorEffect="non-scaling-stroke"
                      style={{ stroke: l.color }}
                    />
                  )}
                </g>
              )),
            )}
          </svg>
          {silent.map(([from, to]) => (
            <span
              key={from}
              className={styles.missing}
              style={{ left: pct(from), width: `${(share(to) - share(from)) * 100}%` }}
              title={`нет данных: ${clock(from, offsetS)} — ${clock(to, offsetS)}`}
            >
              {share(to) - share(from) > 0.12 && 'нет данных'}
            </span>
          ))}
          {strips
            .filter((s) => s.current && s.from >= since)
            .map((s, i) => (
              <span key={i} className={styles.mark} style={{ left: pct(s.from) }} />
            ))}
          {[frame.yMin, ...ticks, frame.yMax].map((v, i, all) =>
            i === 0 && frame.yMin === 0 ? null : (
              <span
                key={v}
                className={styles.yLabel}
                style={
                  i === all.length - 1 ? { top: 2 } : { top: `calc(${(y(frame, v) / height) * 100}% - 8px)` }
                }
              >
                {format(v)}
              </span>
            ),
          )}
          {drag && (
            <span
              className={styles.selection}
              style={{
                left: pct(Math.min(drag.from, drag.to)),
                width: `${Math.abs(share(drag.to) - share(drag.from)) * 100}%`,
              }}
            />
          )}
          {hover !== null && readout && !drag && (
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
                {hoverRecords.map((r, i) => (
                  <span key={i} className={styles.tipRecord}>
                    {r.label}
                  </span>
                ))}
              </div>
            </>
          )}
        </div>
      </div>
      <div className={styles.axis} aria-hidden="true">
        {timeMarks.map((t) => (
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
            <span className={styles.swatch} style={{ borderTop: `1.5px dashed ${l.color}`, height: 0 }} />
            {l.label} {format(l.value)}
          </span>
        ))}
        {strips.length > 0 && (
          <span>
            <span className={styles.swatch} style={{ background: 'var(--ochre)', height: 6 }} />
            записи детекции
          </span>
        )}
        {silent.length > 0 && (
          <span>
            <span
              className={`${styles.swatch} ${styles.missing}`}
              style={{ position: 'static', height: 8 }}
            />
            нет данных
          </span>
        )}
      </div>
      {hidden.length > 0 && (
        <span className={styles.note}>
          Вне графика: {hidden.map((l) => `${l.label} ${format(l.value)}`).join('; ')}
        </span>
      )}
    </div>
  )
}
