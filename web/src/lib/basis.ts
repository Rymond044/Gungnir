// What a record was judged against, in words (server display.against).

import type { Basis } from '../api/types'
import { decimal, formatValue } from './format'
import { classNames, named } from './names'

export function against(signal: string, b: Basis): string {
  const v = (x: number | undefined) => formatValue(signal, x ?? null)
  switch (b.basis) {
    case 'baseline': {
      let text = `норма типа «${named(classNames, b.class === '*' ? 'unknown' : b.class)}»: медиана ${v(b.median)}, MAD ${v(b.mad)}`
      if (b.factor !== undefined && Math.abs(b.factor - 1) > 0.005)
        text += `, поправка на питание ×${decimal(b.factor, 2)} (${b.power})`
      if (b.floor) text += ', разброс поднят до порога фонового сброса'
      return text
    }
    case 'calibration':
      return `холодный старт: ${Math.round((b.share ?? 0) * 100)} % ёмкости ${v(b.capacity)}, измеренной при калибровке`
    case 'cold start':
      return `холодный старт: выше ${v(b.limit)} (${b.of === 'spread floor' ? 'минимальный разброс' : b.of === 'background writeback threshold' ? 'порог фонового сброса' : b.of})`
    case 'group cold start':
      return `холодный старт группы: выше ${v(b.limit)} (${b.of})`
    case 'threshold':
      return b.above !== undefined ? `порог: выше ${v(b.above)}` : `порог: ниже ${v(b.below)}`
    case 'growth': {
      const hours = (b.hourly_minima?.length ?? 1) - 1
      return `рост: часовые минимумы росли ${hours} ч подряд, на ${v(b.rise)} (нужно не меньше ${v(b.needed)})`
    }
    case 'forest':
      return `Isolation Forest типа «${named(classNames, b.class)}»: признак ${b.feature}, z ${decimal(b.z ?? 0, 1)}`
    default:
      return '—'
  }
}
