import { Link } from 'react-router'
import type { FoundSymptom, Hypothesis as H, PolicyAction } from '../api/types'
import { confidence, dayClock, formatNumber, symptomWindow } from '../lib/format'
import {
  actionNames,
  causeNames,
  likelihoodNames,
  named,
  roleNames,
  signalNames,
  statusNames,
  strengthNames,
  symptomNames,
} from '../lib/names'
import Evidence from './Evidence'
import styles from './Hypothesis.module.css'
import { Chip, Key, Source, type Tone } from './ui'
import Entity from './Entity'

const CHECK = 'M3 8.5l3 3 7-7'
const CROSS = 'M4 4l8 8M12 4l-8 8'
const CLOCK = 'M8 3.5v4.5l3 2M8 14.5a6.5 6.5 0 1 1 0-13 6.5 6.5 0 0 1 0 13z'
const NONE = 'M3.5 12.5l9-9M8 14.5a6.5 6.5 0 1 1 0-13 6.5 6.5 0 0 1 0 13z'

function StatusIcon({ s }: { s: FoundSymptom }) {
  const map = {
    matched: {
      d: CHECK,
      color: s.impact ? 'var(--red)' : s.strength === 'contradicting' ? 'var(--red)' : 'var(--ink)',
    },
    missing: { d: CROSS, color: 'var(--gray)' },
    pending: { d: CLOCK, color: 'var(--ochre)' },
    unobservable: { d: NONE, color: 'var(--gray)' },
    absent: { d: CHECK, color: 'var(--green)' },
  }[s.status]
  return (
    <svg
      className={styles.icon}
      width="16"
      height="16"
      viewBox="0 0 16 16"
      fill="none"
      strokeWidth="1.8"
      strokeLinecap="round"
      role="img"
      aria-label={statusNames[s.status]}
    >
      <path d={map.d} style={{ stroke: map.color }} />
    </svg>
  )
}

const STRENGTH_TONE: Record<string, Tone> = {
  characteristic: 'blue',
  typical: 'plain',
  possible: 'plain',
  contradicting: 'red',
}

export function Symptom({ s, host, offset }: { s: FoundSymptom; host: string; offset: number | null }) {
  const found = s.status === 'matched'
  return (
    <div className={styles.sym}>
      <StatusIcon s={s} />
      <div className={styles.symMain}>
        <div className={styles.symLine}>
          <span style={{ color: found || s.status === 'absent' ? 'var(--text)' : 'var(--faint)' }}>
            {symptomNames(s.kind, s.names)}
          </span>
          <Chip tone={STRENGTH_TONE[s.strength]}>{strengthNames[s.strength]}</Chip>
          {s.leading && <Chip tone="ochre">ведущий</Chip>}
          {s.impact && <Chip tone="red">влияние</Chip>}
        </div>
        <span className={styles.faint} style={{ fontSize: 12.5 }}>
          на: {roleNames[s.role] ?? s.role} · {symptomWindow(s.window)} · <Key>{s.names.join(' | ')}</Key>
        </span>
      </div>
      <div className={styles.evidence}>
        {s.evidence ? (
          <Evidence item={s.evidence} host={host} offset={offset} />
        ) : (
          <span className={styles.faint}>
            {statusNames[s.status]}
            {s.status === 'absent' && ' — противоречия нет'}
            {s.status === 'pending' && ' — окно выходит за конец интервала'}
            {s.status === 'unobservable' && ' — хост это не измеряет'}
          </span>
        )}
        <details>
          <summary>источник</summary>
          <Source source={s.source} />
        </details>
      </div>
    </div>
  )
}

export default function Hypothesis({
  h,
  rank,
  host,
  offset,
  withActions = true,
}: {
  h: H
  rank?: number
  host: string
  offset: number | null
  /** A cause that was not named proposes nothing. */
  withActions?: boolean
}) {
  const matched = h.symptoms.filter((s) => s.status === 'matched').length
  return (
    <div className={styles.card}>
      <div className={styles.top}>
        {rank !== undefined && <span className={styles.rank}>{rank}.</span>}
        <Link to={`/hosts/${host}/knowledge/${h.cause}`} className={styles.cause}>
          {named(causeNames, h.cause)}
        </Link>
        <Key>{h.cause}</Key>
        {h.expected && <Chip tone="green">ожидаемое поведение</Chip>}
        <span
          className={styles.conf}
          title={`опора до штрафа за отсутствующие симптомы: ${confidence(h.support)}`}
        >
          <span className="muted">уверенность</span>
          <span className={styles.confBar}>
            <span style={{ width: `${h.confidence * 100}%` }} />
          </span>
          <span className={styles.confNum}>{confidence(h.confidence)}</span>
        </span>
      </div>
      <div className={styles.binding}>
        <span>виновник</span>
        <span style={{ overflowWrap: 'anywhere' }}>{h.culprit ? <Entity entity={h.culprit} /> : '—'}</span>
        {h.device && (
          <>
            <span>устройство</span>
            <span className="mono">{h.device}</span>
          </>
        )}
        {h.filesystem && (
          <>
            <span>файловая система</span>
            <span className="mono">{h.filesystem}</span>
          </>
        )}
        <span>ведущий симптом</span>
        <span>{dayClock(h.leading_at * 1000, offset)}</span>
        <span>симптомы</span>
        <span>
          найдено {matched} из {h.symptoms.filter((s) => s.strength !== 'contradicting').length}
        </span>
      </div>
      <div className={styles.tree} aria-label="Дерево объяснения">
        {h.symptoms.map((s, i) => (
          <Symptom key={i} s={s} host={host} offset={offset} />
        ))}
      </div>
      <Source source={h.rule.source} grade={h.rule.grade} />
      {withActions && h.actions.length > 0 && <Actions actions={h.actions} />}
    </div>
  )
}

/** The actions the policy holds for a cause. */
export function Actions({
  actions,
  title = 'Действия по политике',
}: {
  actions: PolicyAction[]
  title?: string
}) {
  return (
    <div className={styles.actions}>
      <span style={{ fontWeight: 600 }}>{title}</span>
      {actions.map((a) => (
        <span key={a.action + (a.when ?? '')}>
          {named(actionNames, a.action)} <Key>{a.action}</Key>
          <span className="muted">
            {' '}
            ·{' '}
            {a.signal
              ? `до ${Math.round(a.share * 100)} % от текущего «${named(signalNames, a.signal)}» виновника`
              : `вес ×${formatNumber(a.share)}`}
            {a.when ? ` · если ${named(signalNames, a.when)}` : ''} · помогает{' '}
            {likelihoodNames[a.effectiveness] ?? a.effectiveness}
          </span>
        </span>
      ))}
    </div>
  )
}
