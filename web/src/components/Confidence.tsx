import { confidence } from '../lib/format'

export default function Confidence({
  value,
  historical = false,
}: {
  value: number | null
  historical?: boolean
}) {
  if (value === null) return null
  return (
    <span title="Сила поддержки гипотезы о причине и виновнике свидетельствами; не вероятность правильности диагноза">
      {historical ? 'максимальная уверенность' : 'уверенность'}{' '}
      <span className="mono">{confidence(value)}</span>
    </span>
  )
}
