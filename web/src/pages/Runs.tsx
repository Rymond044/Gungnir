import { useState } from 'react'
import { useDiagnosis, useRuns } from '../api/hooks'
import Hypothesis from '../components/Hypothesis'
import { Empty, Failure, Loading, Panel } from '../components/ui'

/**
 * Labeled scenario runs through the inference engine: what it names for each
 * phase that is not normal. A check of the engine on recorded load, kept
 * beside the knowledge tests rather than on the audit.
 */
export default function Runs({ host }: { host: string }) {
  const runs = useRuns(host)
  const scenarios = (runs.data ?? []).filter((r) => r.phases.some((p) => !p.normal))
  const [picked, setPicked] = useState<string | null>(null)
  const run = picked ?? scenarios[0]?.run ?? null
  const diagnosis = useDiagnosis(host, run ? { run } : null, false)

  return (
    <Panel title="Прогоны сценариев" aside="что вывод называет в каждой фазе размеченного прогона">
      {runs.isPending ? (
        <Loading />
      ) : scenarios.length === 0 ? (
        <Empty>Размеченных прогонов с нагрузкой нет.</Empty>
      ) : (
        <>
          <select value={run ?? ''} onChange={(e) => setPicked(e.target.value)} aria-label="Прогон">
            {scenarios.map((r) => (
              <option key={r.run} value={r.run}>
                {r.run} (
                {r.phases
                  .filter((p) => !p.normal)
                  .map((p) => p.phase)
                  .join(', ')}
                )
              </option>
            ))}
          </select>
          {diagnosis.isPending ? (
            <Loading />
          ) : diagnosis.isError ? (
            <Failure error={diagnosis.error} />
          ) : (
            diagnosis.data.map((d) => (
              <div key={d.label} style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                <b>Фаза {d.label}</b>
                {d.hypotheses.length === 0 ? (
                  <span className="muted">причина не названа</span>
                ) : (
                  d.hypotheses.map((h, i) => (
                    <Hypothesis
                      key={`${h.cause}${h.culprit}${h.leading_at}`}
                      h={h}
                      rank={i + 1}
                      host={host}
                      offset={null}
                    />
                  ))
                )}
              </div>
            ))
          )}
        </>
      )}
    </Panel>
  )
}
