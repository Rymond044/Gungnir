import { entityName, isGroup } from '../lib/entity'

/** An entity by its short name; a control group keeps its full path in the tooltip. */
export default function Entity({ entity }: { entity: string }) {
  if (entity === 'host') return <span>хост</span>
  return (
    <span className="mono" title={isGroup(entity) ? entity : undefined}>
      {entityName(entity)}
    </span>
  )
}
