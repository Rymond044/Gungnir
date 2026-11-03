import { expect, it } from 'vitest'
import { entityName, isGroup } from './entity'

it('names a group by its unit', () => {
  expect(entityName('user.slice/user-1000.slice/user@1000.service/session.slice/kitty.scope')).toBe(
    'kitty.scope',
  )
  expect(entityName('system.slice')).toBe('system.slice')
})

it('unescapes systemd names and shortens container ids', () => {
  expect(entityName('app.slice/app-dbus\\x2d:1.2\\x2dorg.telegram.desktop.slice')).toBe(
    'app-dbus-:1.2-org.telegram.desktop.slice',
  )
  expect(entityName(`system.slice/docker-${'d156e8f6254a'.padEnd(64, '0')}.scope`)).toBe(
    'docker-d156e8f6254a.scope',
  )
})

it('keeps the host, devices and mount points readable', () => {
  expect(entityName('host')).toBe('хост')
  expect(entityName('nvme0n1')).toBe('nvme0n1')
  expect(entityName('/var/lib/waydroid/rootfs')).toBe('/var/lib/waydroid/rootfs')
  expect(isGroup('/boot')).toBe(false)
})
