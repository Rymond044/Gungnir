// Entities as the operator reads them. A control group path is long and
// mostly the same prefix; its last segment is the unit or scope that names
// the program. Systemd escapes some characters in unit names (`\x2d` is '-').

const CGROUP_SUFFIX = /\.(slice|service|scope|mount|socket|swap)$/
const CONTAINER_ID = /^(docker|libpod|crio|containerd)-([0-9a-f]{64})\.scope$/

export function isGroup(entity: string): boolean {
  return (entity.includes('/') && !entity.startsWith('/')) || CGROUP_SUFFIX.test(entity)
}

export function unescapeUnit(name: string): string {
  return name.replace(/\\x([0-9a-f]{2})/gi, (_, hex: string) => String.fromCharCode(parseInt(hex, 16)))
}

/** A short name: the host in words, a group by its unit, devices and mount points as they are. */
export function entityName(entity: string): string {
  if (entity === 'host') return 'хост'
  if (!isGroup(entity)) return entity
  const last = unescapeUnit(entity.split('/').pop() ?? entity)
  const container = CONTAINER_ID.exec(last)
  return container ? `${container[1]}-${container[2].slice(0, 12)}.scope` : last
}
