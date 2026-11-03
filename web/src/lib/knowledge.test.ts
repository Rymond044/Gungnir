import { expect, it } from 'vitest'
import type { Knowledge, RuleInfo, RuleSymptom } from '../api/types'
import { similar } from './knowledge'

function sym(names: string[], strength: RuleSymptom['strength'] = 'typical'): RuleSymptom {
  return {
    kind: 'anomaly',
    names,
    role: 'host',
    strength,
    leading: false,
    impact: false,
    window: null,
    direction: 'up',
    record: 'any',
    level: null,
    detectors: [],
    at_most: null,
    above: null,
    source: 's',
    grade: 'documented',
  }
}

function rule(cause: string, symptoms: RuleSymptom[]): RuleInfo {
  return { cause, file: 'disk', source: 's', grade: 'documented', requires: {}, excludes: {}, symptoms }
}

it('finds the causes that share symptoms and what tells them apart', () => {
  const kb = {
    rules: [
      rule('writeback_storm', [sym(['mem.dirty_bytes']), sym(['disk.write_bytes_per_s'])]),
      rule('sync_write_storm', [sym(['disk.write_bytes_per_s']), sym(['mem.dirty_bytes'], 'contradicting')]),
      rule('cpu_hog', [sym(['cgroup.cpu_cores'])]),
    ],
  } as unknown as Knowledge
  expect(similar(kb, 'writeback_storm')).toEqual([
    {
      cause: 'sync_write_storm',
      shared: ['disk.write_bytes_per_s'],
      onlyOther: [],
      onlyThis: ['mem.dirty_bytes'],
    },
  ])
})
