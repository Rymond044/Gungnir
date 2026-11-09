import { useEffect, useRef, useState } from 'react'
import { DayPicker, type DateRange } from 'react-day-picker'
import { ru } from 'react-day-picker/locale'
import 'react-day-picker/style.css'
import { dayClock } from '../lib/format'
import { fromWall, toWall, type Shown, type Wall } from '../lib/interval'
import { RANGES, type RangeKey } from '../lib/range'
import { Segmented } from './ui'
import ui from './ui.module.css'
import styles from './RangePicker.module.css'

const HOURS = Array.from({ length: 24 }, (_, h) => h)
const MINUTES = Array.from({ length: 12 }, (_, i) => i * 5)
const DAY = 86_400_000

// The calendar knows days only; a day of the host's clock travels as a local
// date with the same year, month and day.
const asDate = (w: Wall) => new Date(w.year, w.month, w.day)
const onDay = (w: Wall, d: Date): Wall => ({
  ...w,
  year: d.getFullYear(),
  month: d.getMonth(),
  day: d.getDate(),
})

/**
 * Periods ending now, and an interval of one's own: one calendar where the
 * first click picks the start day and the second the end day, and the hour
 * and minute of each end from lists, in the host's clock, all by mouse.
 */
export default function RangePicker({
  shown,
  offset,
  now,
  ranges = RANGES.map((r) => r.value),
  maxDays = 7,
}: {
  shown: Shown
  offset: number | null
  now: number
  ranges?: RangeKey[]
  maxDays?: number
}) {
  const [open, setOpen] = useState(false)
  const box = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const close = (e: MouseEvent | KeyboardEvent) => {
      if (e instanceof KeyboardEvent ? e.key === 'Escape' : !box.current?.contains(e.target as Node))
        setOpen(false)
    }
    document.addEventListener('mousedown', close)
    document.addEventListener('keydown', close)
    return () => {
      document.removeEventListener('mousedown', close)
      document.removeEventListener('keydown', close)
    }
  }, [open])

  const since = Date.parse(shown.interval.since)
  const until = Date.parse(shown.interval.until)
  return (
    <div className={styles.picker} ref={box}>
      <Segmented<RangeKey | ''>
        label="Период"
        options={RANGES.filter((r) => ranges.includes(r.value))}
        value={shown.range ?? ''}
        onChange={(r) => r && shown.setRange(r)}
      />
      <button
        type="button"
        className={`${ui.button} ${shown.live ? '' : styles.active}`}
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        {shown.live ? 'Свой интервал' : `${dayClock(since, offset, now)} — ${dayClock(until, offset, now)}`}
      </button>
      {open && (
        <Panel
          since={since}
          until={until}
          offset={offset}
          now={now}
          maxDays={maxDays}
          onApply={(a, b) => {
            shown.setSpan(a, b)
            setOpen(false)
          }}
        />
      )}
    </div>
  )
}

function Panel({
  since,
  until,
  offset,
  now,
  maxDays,
  onApply,
}: {
  since: number
  until: number
  offset: number | null
  now: number
  maxDays: number
  onApply: (since: number, until: number) => void
}) {
  // Minutes in steps of five, as the lists offer them.
  const round = (w: Wall): Wall => ({ ...w, minute: Math.floor(w.minute / 5) * 5 })
  const [from, setFrom] = useState(() => round(toWall(since, offset)))
  const [to, setTo] = useState(() => round(toWall(until, offset)))
  // While only the start day is picked, the end waits for the second click.
  const [picking, setPicking] = useState(false)
  const a = fromWall(from, offset)
  const b = fromWall(to, offset)
  const problem = picking
    ? 'Выберите день конца.'
    : b <= a
      ? 'Конец должен быть позже начала.'
      : b - a > maxDays * DAY
        ? `Не больше ${maxDays} дней.`
        : a > now
          ? 'Начало ещё не наступило.'
          : null

  const today = toWall(now, offset)
  const pickDays = (_: DateRange | undefined, day: Date) => {
    // Each click on a finished range starts a new one: start, then end.
    if (!picking) {
      setFrom(onDay(from, day))
      setTo(onDay(to, day))
      setPicking(true)
      return
    }
    const first = asDate(from)
    const [start, end] = day < first ? [day, first] : [first, day]
    setFrom(onDay(from, start))
    setTo(onDay(to, end))
    setPicking(false)
  }
  const quick = (back: number) => {
    const start = toWall(fromWall({ ...today, hour: 0, minute: 0 }, offset) - back * DAY, offset)
    setFrom(start)
    setTo(back === 0 ? round(today) : { ...start, hour: 23, minute: 55 })
    setPicking(false)
  }

  return (
    <div className={styles.panel} role="dialog" aria-label="Свой интервал">
      <div className={styles.quick}>
        <button type="button" className={ui.button} onClick={() => quick(0)}>
          Сегодня
        </button>
        <button type="button" className={ui.button} onClick={() => quick(1)}>
          Вчера
        </button>
      </div>
      <DayPicker
        className={styles.calendar}
        mode="range"
        locale={ru}
        weekStartsOn={1}
        selected={{ from: asDate(from), to: picking ? undefined : asDate(to) }}
        onSelect={pickDays}
        defaultMonth={asDate(to)}
        disabled={{ after: asDate(today) }}
        endMonth={asDate(today)}
        showOutsideDays
      />
      <div className={styles.times}>
        <Time title="с" value={from} onChange={setFrom} />
        <span className="muted">—</span>
        <Time title="по" value={to} onChange={setTo} />
      </div>
      <div className={styles.foot}>
        {problem && <span className={styles.problem}>{problem}</span>}
        <button
          type="button"
          className={`${ui.button} ${ui.primary}`}
          disabled={!!problem}
          onClick={() => onApply(a, b)}
        >
          Показать
        </button>
      </div>
    </div>
  )
}

const pad = (n: number) => String(n).padStart(2, '0')

function Time({ title, value, onChange }: { title: string; value: Wall; onChange: (w: Wall) => void }) {
  return (
    <span className={styles.time}>
      <span className="muted">
        {title} {pad(value.day)}.{pad(value.month + 1)}
      </span>
      <select
        aria-label={`${title}: час`}
        value={value.hour}
        onChange={(e) => onChange({ ...value, hour: Number(e.target.value) })}
      >
        {HOURS.map((h) => (
          <option key={h} value={h}>
            {pad(h)}
          </option>
        ))}
      </select>
      :
      <select
        aria-label={`${title}: минуты`}
        value={value.minute}
        onChange={(e) => onChange({ ...value, minute: Number(e.target.value) })}
      >
        {MINUTES.map((m) => (
          <option key={m} value={m}>
            {pad(m)}
          </option>
        ))}
      </select>
    </span>
  )
}
