import { useEffect, useRef, useState } from 'react'
import { dayClock } from '../lib/format'
import { fromWall, monthGrid, toWall, type Shown, type Wall } from '../lib/interval'
import { RANGES, type RangeKey } from '../lib/range'
import { Segmented } from './ui'
import ui from './ui.module.css'
import styles from './RangePicker.module.css'

const MONTHS = [
  'январь',
  'февраль',
  'март',
  'апрель',
  'май',
  'июнь',
  'июль',
  'август',
  'сентябрь',
  'октябрь',
  'ноябрь',
  'декабрь',
]
const WEEKDAYS = ['пн', 'вт', 'ср', 'чт', 'пт', 'сб', 'вс']
const HOURS = Array.from({ length: 24 }, (_, h) => h)
const MINUTES = Array.from({ length: 12 }, (_, i) => i * 5)
const DAY = 86_400_000

/**
 * Periods ending now, and a stretch of one's own: a day from a calendar and
 * an hour and minute from lists, in the host's clock, all by mouse.
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
  const a = fromWall(from, offset)
  const b = fromWall(to, offset)
  const problem =
    b <= a
      ? 'Конец должен быть позже начала.'
      : b - a > maxDays * DAY
        ? `Не больше ${maxDays} дней.`
        : a > now
          ? 'Начало ещё не наступило.'
          : null

  const today = toWall(now, offset)
  const day = (back: number): [Wall, Wall] => {
    const start = toWall(fromWall({ ...today, hour: 0, minute: 0 }, offset) - back * DAY, offset)
    const end = back === 0 ? round(today) : { ...start, hour: 23, minute: 55 }
    return [start, end]
  }
  return (
    <div className={styles.panel} role="dialog" aria-label="Свой интервал">
      <div className={styles.quick}>
        {[
          ['Сегодня', 0],
          ['Вчера', 1],
        ].map(([label, back]) => (
          <button
            key={label}
            type="button"
            className={ui.button}
            onClick={() => {
              const [s, e] = day(back as number)
              setFrom(s)
              setTo(e)
            }}
          >
            {label}
          </button>
        ))}
      </div>
      <div className={styles.ends}>
        <End title="От" value={from} onChange={setFrom} today={today} />
        <End title="До" value={to} onChange={setTo} today={today} />
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

function End({
  title,
  value,
  onChange,
  today,
}: {
  title: string
  value: Wall
  onChange: (w: Wall) => void
  today: Wall
}) {
  const [page, setPage] = useState({ year: value.year, month: value.month })
  const shift = (n: number) => {
    const m = page.month + n
    setPage({ year: page.year + Math.floor(m / 12), month: ((m % 12) + 12) % 12 })
  }
  const future = (d: number) =>
    page.year > today.year ||
    (page.year === today.year && (page.month > today.month || (page.month === today.month && d > today.day)))
  return (
    <div className={styles.end}>
      <div className={styles.endHead}>
        <b>{title}</b>
        <span className="muted">
          {String(value.day).padStart(2, '0')}.{String(value.month + 1).padStart(2, '0')}.{value.year}
        </span>
      </div>
      <div className={styles.month}>
        <button type="button" onClick={() => shift(-1)} aria-label="Предыдущий месяц">
          ‹
        </button>
        <span>
          {MONTHS[page.month]} {page.year}
        </span>
        <button type="button" onClick={() => shift(1)} aria-label="Следующий месяц">
          ›
        </button>
      </div>
      <table className={styles.calendar}>
        <thead>
          <tr>
            {WEEKDAYS.map((d) => (
              <th key={d}>{d}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {monthGrid(page.year, page.month).map((week, i) => (
            <tr key={i}>
              {week.map((d, j) => (
                <td key={j}>
                  {d && (
                    <button
                      type="button"
                      disabled={future(d)}
                      aria-pressed={value.year === page.year && value.month === page.month && value.day === d}
                      className={
                        today.year === page.year && today.month === page.month && today.day === d
                          ? styles.today
                          : undefined
                      }
                      onClick={() => onChange({ ...value, year: page.year, month: page.month, day: d })}
                    >
                      {d}
                    </button>
                  )}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      <div className={styles.time}>
        <select
          aria-label={`${title}: час`}
          value={value.hour}
          onChange={(e) => onChange({ ...value, hour: Number(e.target.value) })}
        >
          {HOURS.map((h) => (
            <option key={h} value={h}>
              {String(h).padStart(2, '0')}
            </option>
          ))}
        </select>
        <span>:</span>
        <select
          aria-label={`${title}: минуты`}
          value={value.minute}
          onChange={(e) => onChange({ ...value, minute: Number(e.target.value) })}
        >
          {MINUTES.map((m) => (
            <option key={m} value={m}>
              {String(m).padStart(2, '0')}
            </option>
          ))}
        </select>
      </div>
    </div>
  )
}
