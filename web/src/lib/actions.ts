// Words of the actions screen and the incident's proposals (ADRs 0061-0063).
import type { ActionState, Outcome } from '../api/types'
import type { Tone } from '../components/ui'
import { formatNumber, formatValue } from './format'

export const actionStates: Record<ActionState, { label: string; tone: Tone }> = {
  queued: { label: 'ждёт исполнителя', tone: 'ochre' },
  applied: { label: 'применено', tone: 'blue' },
  kept: { label: 'оставлено', tone: 'green' },
  refused: { label: 'не применено', tone: 'red' },
  reverted: { label: 'откачено', tone: 'plain' },
}

export const outcomes: Record<Outcome, { label: string; tone: Tone; title: string }> = {
  worked: { label: 'помогло', tone: 'green', title: 'до действия другим мешали, к концу проверки — нет' },
  failed: {
    label: 'не помогло',
    tone: 'red',
    title: 'к концу проверки другим всё ещё мешали; действие откачено',
  },
  nothing_to_judge: {
    label: 'не с чем сравнить',
    tone: 'plain',
    title: 'вред прошёл ещё до действия: ни до, ни после никому не мешали',
  },
}

export const logEvents: Record<string, string> = {
  confirmed: 'подтверждено',
  applied: 'применено исполнителем',
  'applied late': 'применено, когда сервер уже перестал ждать',
  refused: 'исполнитель не применил',
  verified: 'проверено',
  'revert asked': 'запрошен откат',
  'keep asked': 'запрошено оставить',
  kept: 'оставлено до конца группы или перезагрузки',
  reverted: 'откачено',
}

const REASONS: Record<string, string> = {
  'the timer ran out': 'истёк таймер',
  'the actuator stopped': 'исполнитель остановлен',
  'the actuator restarted': 'исполнитель перезапустился',
  'the host rebooted': 'хост перезагружен',
  'its groups ended': 'группа завершилась',
  'verification failed': 'проверка не прошла',
  'the host did not answer': 'хост не ответил за пять минут',
  'nothing held under this id': 'у исполнителя этого действия уже нет',
}

/** Known reasons in Russian; the actuator's refusals stay as it wrote them. */
export function reasonText(reason: string | null | undefined): string {
  if (!reason) return ''
  const [first, ...rest] = reason.split('; ')
  const by = first.match(/^reverted by (.+)$/)
  const head = by ? `откатил ${by[1]}` : (REASONS[first] ?? first)
  return [head, ...rest].join('; ')
}

// The signal whose unit the value of each action is in.
const SIGNAL_OF: Record<string, string> = {
  io_max_rbps: 'cgroup.read_bytes_per_s',
  io_max_wbps: 'cgroup.write_bytes_per_s',
  io_max_riops: 'cgroup.read_ops_per_s',
  io_max_wiops: 'cgroup.write_ops_per_s',
  memory_high: 'cgroup.mem_bytes',
  cpu_max: 'cgroup.cpu_cores',
}

/** The limit the actuator is told: a value, or a share of what the group holds when applied. */
export function limitText(action: string, value: number | null, share: number | null): string {
  if (value !== null) {
    const signal = SIGNAL_OF[action]
    return signal ? `до ${formatValue(signal, value)}` : `${formatNumber(value)}`
  }
  if (share !== null) return `×${formatNumber(share)} от того, что у группы сейчас`
  return ''
}
