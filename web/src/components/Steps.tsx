import type { ReactNode } from 'react'
import { dayClock, ms } from '../lib/format'
import styles from './Steps.module.css'

export interface Step {
  label: string
  state: 'done' | 'waiting' | 'failed' | 'skipped'
  detail: ReactNode
  at?: string | null
}

export default function Steps({
  label,
  steps,
  offset,
  now,
}: {
  label: string
  steps: Step[]
  offset: number | null
  now: number
}) {
  return (
    <ol className={styles.steps} aria-label={label}>
      {steps.map((step, i) => (
        <li key={step.label} className={styles[step.state]}>
          <span className={styles.marker} aria-hidden="true">
            {step.state === 'done'
              ? '✓'
              : step.state === 'failed'
                ? '×'
                : step.state === 'skipped'
                  ? '−'
                  : i + 1}
          </span>
          <b>{step.label}</b>
          <span className={styles.detail}>{step.detail}</span>
          {step.at && <time dateTime={step.at}>{dayClock(ms(step.at), offset, now)}</time>}
        </li>
      ))}
    </ol>
  )
}
