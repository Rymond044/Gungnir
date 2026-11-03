import type { RecordRow } from '../api/types'
import { levelNames } from '../lib/names'
import { Chip } from './ui'

export default function RecordChip({ r }: { r: RecordRow }) {
  if (r.kind === 'impact')
    return (
      <Chip tone="red" dot>
        влияние{r.level ? `: ${levelNames[r.level]}` : ''}
      </Chip>
    )
  if (r.expected) return <Chip tone="green">ожидаемая</Chip>
  return <Chip tone="ochre">аномалия</Chip>
}
