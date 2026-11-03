import type { ReactNode } from 'react'
import { ApiError } from '../api/client'
import { gradeNames } from '../lib/names'
import styles from './ui.module.css'

export function Panel({
  title,
  aside,
  children,
  className,
}: {
  title?: ReactNode
  aside?: ReactNode
  children: ReactNode
  className?: string
}) {
  return (
    <section className={`${styles.panel} ${className ?? ''}`}>
      {(title || aside) && (
        <div className={styles.panelHead}>
          {title && <h2 className={styles.panelTitle}>{title}</h2>}
          {aside && <span className={styles.panelAside}>{aside}</span>}
        </div>
      )}
      {children}
    </section>
  )
}

export type Tone = 'red' | 'green' | 'ochre' | 'blue' | 'plain'

export function Chip({ tone = 'plain', dot, children }: { tone?: Tone; dot?: boolean; children: ReactNode }) {
  return (
    <span className={`${styles.chip} ${tone === 'plain' ? '' : styles[tone]}`}>
      {dot && <span className={styles.dot} />}
      {children}
    </span>
  )
}

/** The key of a signal, cause or entity as the code and the knowledge base name it. */
export function Key({ children }: { children: ReactNode }) {
  return <span className={styles.key}>{children}</span>
}

export function Source({ source, grade }: { source?: string | null; grade?: string | null }) {
  if (!source) return null
  return (
    <span className={styles.source}>
      Источник: {source}
      {grade ? ` · ${gradeNames[grade] ?? grade}` : ''}
    </span>
  )
}

export function Segmented<T extends string>({
  label,
  options,
  value,
  onChange,
}: {
  label: string
  options: { value: T; label: string }[]
  value: T
  onChange: (v: T) => void
}) {
  return (
    <div role="group" aria-label={label} className={styles.segmented}>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          aria-pressed={o.value === value}
          onClick={() => onChange(o.value)}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}

export function Loading() {
  return <div className={styles.state}>Загрузка…</div>
}

export function Failure({ error }: { error: unknown }) {
  const text =
    error instanceof ApiError
      ? `Сервер ответил ${error.status}: ${error.message}`
      : 'Сервер недоступен. Проверьте, что он запущен.'
  return (
    <div className={styles.error} role="alert">
      {text}
    </div>
  )
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className={styles.empty}>{children}</div>
}
