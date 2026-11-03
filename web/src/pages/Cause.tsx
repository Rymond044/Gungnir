import { Link, useParams } from 'react-router'
import { useKnowledge } from '../api/hooks'
import type { RuleSymptom } from '../api/types'
import { Chip, Empty, Failure, Key, Loading, Panel, Source, type Tone } from '../components/ui'
import ui from '../components/ui.module.css'
import { formatNumber, formatValue, symptomWindow } from '../lib/format'
import { similar } from '../lib/knowledge'
import {
  actionNames,
  anyName,
  causeNames,
  classNames,
  detectorName,
  directionNames,
  domainNames,
  factNames,
  gradeNames,
  kindNames,
  levelNames,
  likelihoodNames,
  named,
  resourceNames,
  roleNames,
  signalNames,
  strengthNames,
  symptomKindNames,
  symptomNames,
} from '../lib/names'

const TONE: Record<string, Tone> = {
  characteristic: 'blue',
  typical: 'plain',
  possible: 'plain',
  contradicting: 'red',
}

function conditionText(c: Record<string, string[]>): string {
  return Object.entries(c)
    .map(([k, v]) =>
      k === 'culprit_class'
        ? `тип нагрузки виновника: ${v.map((x) => named(classNames, x)).join(' или ')}`
        : `${named(factNames, k)} = ${v.join(' или ')}`,
    )
    .join('; ')
}

function details(s: RuleSymptom): string {
  const out: string[] = []
  if (s.direction) out.push(directionNames[s.direction])
  if (s.record !== 'any') out.push(`только ${kindNames[s.record]}`)
  if (s.level) out.push(`уровень ${levelNames[s.level]}`)
  if (s.detectors.length) out.push(`детекторы: ${s.detectors.map(detectorName).join(', ')}`)
  if (s.at_most !== null) out.push(`не больше ${formatValue(s.names[0], s.at_most)}`)
  if (s.above !== null) out.push(`больше ${formatValue(s.names[0], s.above)}`)
  return out.join(' · ')
}

/** The rule of one cause: its symptoms on a time axis around the leading one, conditions, actions. */
export default function Cause() {
  const { host = '', cause = '' } = useParams()
  const kb = useKnowledge(host)
  if (kb.isPending) return <Loading />
  if (kb.isError) return <Failure error={kb.error} />
  const k = kb.data
  const info = k.causes.find((c) => c.name === cause)
  if (!info) return <Empty>В базе знаний нет причины «{cause}».</Empty>
  const rule = k.rules.find((r) => r.cause === cause)
  const policy = k.policy.filter((p) => p.cause === cause)
  const advisories = k.advisories.filter((a) => a.cause === cause)
  const contra = k.contraindications.find((c) => c.cause === cause)
  const tests = k.tests.filter((t) => t.expect === cause || t.not.includes(cause))
  const close = similar(k, cause).slice(0, 5)

  const windows = rule?.symptoms.map((s) => s.window ?? [0, 0]) ?? []
  const lo = Math.min(0, ...windows.map((w) => w[0]))
  const hi = Math.max(0, ...windows.map((w) => w[1]), 1)
  const pos = (t: number) => `${((t - lo) / (hi - lo)) * 100}%`

  return (
    <div className={ui.page}>
      <div className={ui.crumbs}>
        <Link to={`/hosts/${host}/knowledge`}>База знаний</Link>
        <span>/</span>
        <span>{domainNames[info.domain ?? ''] ?? info.domain}</span>
      </div>
      <div className={ui.head}>
        <h1>{named(causeNames, cause, info.label)}</h1>
        <Key>{cause}</Key>
        <Chip tone={info.grade === 'documented' ? 'green' : 'ochre'}>
          {gradeNames[info.grade ?? ''] ?? info.grade}
        </Chip>
      </div>
      <Panel>
        <div className={ui.facts}>
          <span>суть</span>
          <span>{info.comment ?? info.label}</span>
          <span>ресурсы</span>
          <span>{info.resources.map((r) => named(resourceNames, r)).join(', ')}</span>
          <span>виновник</span>
          <span>{info.culprit ? <Key>{info.culprit}</Key> : 'нет: причина не в одной программе'}</span>
          {rule && Object.keys(rule.requires).length > 0 && (
            <>
              <span>требует</span>
              <span>{conditionText(rule.requires)}</span>
            </>
          )}
          {rule && Object.keys(rule.excludes).length > 0 && (
            <>
              <span>исключает</span>
              <span>{conditionText(rule.excludes)}</span>
            </>
          )}
        </div>
        <Source source={rule?.source ?? info.source} grade={rule?.grade ?? info.grade} />
      </Panel>

      {rule && (
        <Panel title="Симптомы" aside="окна во времени от начала ведущего симптома, секунды">
          <div className={ui.scroll}>
            <div
              style={{
                minWidth: 760,
                display: 'grid',
                gridTemplateColumns: 'minmax(0, 1.3fr) minmax(0, 1fr)',
                gap: 0,
              }}
            >
              <span />
              <div style={{ position: 'relative', height: 18, fontSize: 11, color: 'var(--faint)' }}>
                {[lo, 0, hi].map((t) => (
                  <span
                    key={t}
                    style={{
                      position: 'absolute',
                      left: pos(t),
                      transform: t === lo ? 'none' : t === hi ? 'translateX(-100%)' : 'translateX(-50%)',
                    }}
                  >
                    {t > 0 ? `+${t}` : t}
                  </span>
                ))}
              </div>
              {rule.symptoms.map((s, i) => (
                <SymptomRow key={i} s={s} pos={pos} />
              ))}
            </div>
          </div>
        </Panel>
      )}

      <div className={ui.grid}>
        <Panel title="Действия" aside="из политики">
          {policy.length === 0 ? (
            <Empty>Действия исполнителя здесь не помогают; есть только рекомендация.</Empty>
          ) : (
            policy.map((p, i) => (
              <div key={i} style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                <span>
                  {named(actionNames, p.action)} <Key>{p.action}</Key>
                </span>
                <span className="muted">
                  {p.signal
                    ? `до ${Math.round(p.share * 100)} % от «${named(signalNames, p.signal)}» виновника`
                    : `вес ×${formatNumber(p.share)}`}
                  {p.when ? ` · если ${named(signalNames, p.when)}` : ''} · помогает{' '}
                  {likelihoodNames[p.effectiveness] ?? p.effectiveness}
                </span>
                {p.side_effects.map((e) => (
                  <span key={e.effect} style={{ fontSize: 13 }}>
                    побочно: {e.effect} ({likelihoodNames[e.likelihood] ?? e.likelihood})
                  </span>
                ))}
                <Source source={p.source} />
              </div>
            ))
          )}
          {contra && (
            <span>Не предлагать: {contra.actions.map((a) => named(actionNames, a)).join(', ')}</span>
          )}
        </Panel>
        {advisories.length > 0 && (
          <Panel title="Рекомендация">
            {advisories.map((a, i) => (
              <div key={i} style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                <span>{a.text}</span>
                <Source source={a.source} />
              </div>
            ))}
          </Panel>
        )}
        <Panel title="Похожие причины" aside="общие симптомы и чем различить">
          {close.length === 0 ? (
            <Empty>С другими причинами симптомы не пересекаются.</Empty>
          ) : (
            close.map((c) => (
              <div key={c.cause} style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                <Link to={`/hosts/${host}/knowledge/${c.cause}`}>{named(causeNames, c.cause)}</Link>
                <span className="muted" style={{ fontSize: 13 }}>
                  общие: {c.shared.map(anyName).join(', ')}
                </span>
                {c.onlyThis.length > 0 && (
                  <span style={{ fontSize: 13 }}>только здесь: {c.onlyThis.map(anyName).join(', ')}</span>
                )}
              </div>
            ))
          )}
        </Panel>
        <Panel title="Тесты" aside={`${tests.length}`}>
          {tests.length === 0 ? (
            <Empty>Тестов с этой причиной нет.</Empty>
          ) : (
            tests.map((t) => (
              <span key={t.name}>
                {t.expect === cause ? '✓ должна быть названа: ' : '✕ не должна пройти порог: '}
                {t.name}
              </span>
            ))
          )}
        </Panel>
      </div>
    </div>
  )
}

function SymptomRow({ s, pos }: { s: RuleSymptom; pos: (t: number) => string }) {
  const w = s.window
  const contradicting = s.strength === 'contradicting'
  return (
    <>
      <div
        style={{
          padding: '8px 12px 8px 0',
          borderTop: '1px solid var(--divider)',
          display: 'flex',
          flexDirection: 'column',
          gap: 3,
        }}
      >
        <span style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 8px', alignItems: 'baseline' }}>
          <span>{symptomNames(s.kind, s.names)}</span>
          <Chip tone={TONE[s.strength]}>{strengthNames[s.strength]}</Chip>
          {s.leading && <Chip tone="ochre">ведущий</Chip>}
          {s.impact && <Chip tone="red">влияние</Chip>}
        </span>
        <span className="muted" style={{ fontSize: 12.5 }}>
          {symptomKindNames[s.kind]} на: {roleNames[s.role] ?? s.role} · {symptomWindow(w)}
          {details(s) && ` · ${details(s)}`}
        </span>
        <Key>{s.names.join(' | ')}</Key>
        <Source source={s.source} grade={s.grade} />
      </div>
      <div style={{ position: 'relative', borderTop: '1px solid var(--divider)', minHeight: 40 }}>
        <span
          style={{
            position: 'absolute',
            top: 0,
            bottom: 0,
            left: pos(0),
            borderLeft: '1px dashed var(--faint)',
          }}
        />
        {w ? (
          <span
            title={symptomWindow(w)}
            style={{
              position: 'absolute',
              top: 14,
              height: 10,
              left: pos(w[0]),
              width: `calc(${pos(w[1])} - ${pos(w[0])})`,
              minWidth: 3,
              borderRadius: 3,
              background: contradicting
                ? 'var(--red-tint)'
                : s.strength === 'characteristic'
                  ? 'var(--blue)'
                  : 'var(--gray)',
              border: contradicting ? '1px solid var(--red)' : undefined,
            }}
          />
        ) : (
          <span
            title="ведущий симптом: от него считаются окна"
            style={{
              position: 'absolute',
              top: 12,
              left: pos(0),
              width: 14,
              height: 14,
              marginLeft: -7,
              borderRadius: '50%',
              background: 'var(--ochre)',
            }}
          />
        )}
      </div>
    </>
  )
}
