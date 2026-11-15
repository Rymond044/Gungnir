import { describe, expect, it } from 'vitest'
import { safeNext } from '../api/client'
import { limitText, reasonText } from './actions'

describe('actions', () => {
  it('names the actuator reasons in Russian and keeps what it does not know', () => {
    expect(reasonText('the timer ran out')).toBe('истёк таймер')
    expect(reasonText('reverted by admin')).toBe('откатил admin')
    expect(reasonText('verification failed; 1 file(s) changed by someone else were left as they were')).toBe(
      'проверка не прошла; 1 file(s) changed by someone else were left as they were',
    )
    expect(reasonText('the io controller is off for this group')).toBe(
      'the io controller is off for this group',
    )
  })

  it('tells a limit by its value or by its share', () => {
    expect(limitText('memory_high', null, 1.1)).toContain('×1,1')
    expect(limitText('io_max_wbps', 40e6, null)).toMatch(/^до /)
  })

  it('comes back only to a page of this application', () => {
    expect(safeNext('/hosts/laptop/incidents/3')).toBe('/hosts/laptop/incidents/3')
    expect(safeNext('//evil.example')).toBe('/')
    expect(safeNext('https://evil.example')).toBe('/')
    expect(safeNext(null)).toBe('/')
  })
})
