import { Link } from 'react-router'
import type { EvidenceItem } from '../api/types'
import { dayClock, formatValue } from '../lib/format'
import { eventNames, kindNames, named, processNames, processStates, signalNames } from '../lib/names'

/** One piece of evidence in words, with a link to its record. */
export default function Evidence({
  item,
  host,
  offset,
  withName = false,
}: {
  item: EvidenceItem
  host: string
  offset: number | null
  withName?: boolean
}) {
  switch (item.type) {
    case 'record':
      return (
        <span>
          {withName && `${named(signalNames, item.signal)} ${item.direction === 'up' ? '↑' : '↓'} `}
          <Link to={`/hosts/${host}/anomalies/${item.id}`}>
            {kindNames[item.kind] ?? item.kind} #{item.id}
          </Link>
          {' · '}
          {item.entity === 'host' ? 'хост' : <span className="mono">{item.entity}</span>}
          {' · '}с {dayClock(item.start * 1000, offset)}
        </span>
      )
    case 'value':
      return (
        <span>
          {withName && `${named(signalNames, item.signal)}: `}
          {formatValue(item.signal, item.value)} на <span className="mono">{item.entity}</span>, в{' '}
          <Link to={`/hosts/${host}/anomalies/${item.record}`}>записи #{item.record}</Link>
        </span>
      )
    case 'event':
      return (
        <span>
          {withName && `${named(eventNames, item.event_type)}: `}
          {dayClock(item.at * 1000, offset)} · {item.message}
        </span>
      )
    case 'process':
      return (
        <span>
          {withName && `${named(processNames, item.symptom)}: `}
          <span className="mono">
            {item.comm} {item.pid}
          </span>{' '}
          ({processStates[item.state] ?? item.state}
          {item.group ? `, ${item.group}` : ''}), с {dayClock(item.at * 1000, offset)}
        </span>
      )
  }
}
