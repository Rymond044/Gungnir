import type { Action } from '../api/types'
import type { Step } from '../components/Steps'
import { outcomes, reasonText } from './actions'

export function actionSteps(a: Action): Step[] {
  const ended = a.state === 'refused' || a.state === 'reverted'
  const applied = Boolean(a.applied_at)
  const last: Step =
    a.state === 'reverted'
      ? {
          label: 'Завершение',
          state: 'done',
          detail: `Откат подтверждён: ${reasonText(a.reason)}`,
          at: a.ended_at,
        }
      : a.state === 'kept'
        ? {
            label: 'Завершение',
            state: 'done',
            detail: 'Оставлено до конца группы или перезагрузки',
            at: [...a.log].reverse().find((e) => e.event === 'kept')?.at,
          }
        : {
            label: 'Завершение',
            state: a.state === 'refused' ? 'skipped' : 'waiting',
            detail:
              a.state === 'refused'
                ? 'Не применялось'
                : a.asked === 'revert'
                  ? 'Откат запрошен; ждём исполнителя'
                  : a.asked === 'keep'
                    ? 'Запрошено оставить; ждём исполнителя'
                    : 'Ожидается откат или закрепление',
          }
  return [
    { label: 'Подтверждение', state: 'done', detail: a.confirmed_by, at: a.confirmed_at },
    {
      label: 'Применение',
      state: applied ? 'done' : a.state === 'refused' ? 'failed' : 'waiting',
      detail: applied
        ? 'Исполнитель применил'
        : a.state === 'refused'
          ? reasonText(a.reason)
          : 'Ждём исполнителя',
      at: a.applied_at ?? (a.state === 'refused' ? a.ended_at : null),
    },
    {
      label: 'Проверка',
      state:
        a.outcome === 'worked'
          ? 'done'
          : a.outcome === 'failed'
            ? 'failed'
            : a.outcome === 'nothing_to_judge' || ended
              ? 'skipped'
              : 'waiting',
      detail: a.outcome
        ? outcomes[a.outcome].label
        : ended
          ? 'Результат проверки не получен'
          : applied
            ? 'Ожидается результат детекции'
            : 'После применения',
      at: a.verified_at,
    },
    last,
  ]
}
