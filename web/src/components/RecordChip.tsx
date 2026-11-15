import type { RecordRow } from '../api/types'
import { Chip } from './ui'

export default function RecordChip({ r }: { r: RecordRow }) {
  if (r.kind === 'impact')
    return (
      <Chip tone="red" dot>
        вред
      </Chip>
    )
  if (r.expected) return <Chip tone="green">ожидаемая</Chip>
  return <Chip tone="ochre">аномалия</Chip>
}
