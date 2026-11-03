// Values and times as the operator reads them. Values follow the unit suffix
// of the signal name, as the command line does (server display.py); times
// are shown in the host's own local time, like the CLI.

const nbsp = ' '

function trim(x: number, digits: number): string {
  return x.toLocaleString('ru-RU', { maximumFractionDigits: digits, minimumFractionDigits: 0 })
}

export function formatBytes(value: number): string {
  const units = ['Б', 'КиБ', 'МиБ', 'ГиБ', 'ТиБ']
  let v = value
  let i = 0
  while (Math.abs(v) >= 1024 && i < units.length - 1) {
    v /= 1024
    i += 1
  }
  return `${trim(v, i === 0 ? 0 : Math.abs(v) < 10 ? 2 : 1)}${nbsp}${units[i]}`
}

export function formatNumber(value: number): string {
  if (Number.isInteger(value)) return trim(value, 0)
  const a = Math.abs(value)
  return trim(value, a >= 100 ? 0 : a >= 10 ? 1 : a >= 1 ? 2 : 3)
}

export function formatValue(signal: string, value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  if (signal.endsWith('_ratio')) return `${trim(value * 100, Math.abs(value) < 0.1 ? 1 : 0)}${nbsp}%`
  if (signal.endsWith('_bytes_per_s')) return `${formatBytes(value)}/с`
  if (signal.endsWith('_bytes')) return formatBytes(value)
  if (signal.endsWith('_pages_per_s')) return `${formatNumber(value)}${nbsp}стр/с`
  if (signal.endsWith('_per_s')) return `${formatNumber(value)}/с`
  if (signal.endsWith('_ms')) return `${formatNumber(value)}${nbsp}мс`
  if (signal.endsWith('_cores')) return `${formatNumber(value)}${nbsp}ядра`
  if (signal.endsWith('_c')) return `${formatNumber(value)}${nbsp}°C`
  return formatNumber(value)
}

/** A short unit for an axis label, or '' when values carry their own. */
export function unitOf(signal: string): string {
  if (signal.endsWith('_ratio')) return '%'
  if (signal.endsWith('_bytes_per_s')) return 'байт/с'
  if (signal.endsWith('_bytes')) return 'байт'
  if (signal.endsWith('_ms')) return 'мс'
  if (signal.endsWith('_per_s')) return '1/с'
  if (signal.endsWith('_cores')) return 'ядра'
  return ''
}

// Times. The agent reports its UTC offset; without one the CLI and this
// interface both fall back to UTC.

export function shifted(ms: number, offsetS: number | null): Date {
  return new Date(ms + (offsetS ?? 0) * 1000)
}

function pad(n: number): string {
  return String(n).padStart(2, '0')
}

export function clock(ms: number, offsetS: number | null, seconds = true): string {
  const d = shifted(ms, offsetS)
  const hm = `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`
  return seconds ? `${hm}:${pad(d.getUTCSeconds())}` : hm
}

export function dayClock(ms: number, offsetS: number | null, nowMs = Date.now()): string {
  const d = shifted(ms, offsetS)
  const today = shifted(nowMs, offsetS)
  const sameDay =
    d.getUTCFullYear() === today.getUTCFullYear() &&
    d.getUTCMonth() === today.getUTCMonth() &&
    d.getUTCDate() === today.getUTCDate()
  const time = clock(ms, offsetS)
  return sameDay ? time : `${pad(d.getUTCDate())}.${pad(d.getUTCMonth() + 1)} ${time}`
}

export function ms(iso: string): number {
  return Date.parse(iso)
}

export function duration(seconds: number): string {
  const s = Math.max(0, Math.round(seconds))
  if (s < 60) return `${s}${nbsp}с`
  if (s < 3600) return `${Math.floor(s / 60)}${nbsp}мин ${pad(s % 60)}${nbsp}с`
  if (s < 86400) return `${Math.floor(s / 3600)}${nbsp}ч ${pad(Math.floor((s % 3600) / 60))}${nbsp}мин`
  return `${Math.floor(s / 86400)}${nbsp}д ${Math.floor((s % 86400) / 3600)}${nbsp}ч`
}

/** A stretch of seconds in its largest whole units: 30 с, 5 мин, 12 ч, 1 ч 30 мин. */
export function span(seconds: number): string {
  const s = Math.abs(Math.round(seconds))
  if (s < 60) return `${s}${nbsp}с`
  if (s < 3600) return s % 60 ? `${Math.floor(s / 60)}${nbsp}мин ${s % 60}${nbsp}с` : `${s / 60}${nbsp}мин`
  const h = Math.floor(s / 3600)
  const m = Math.round((s % 3600) / 60)
  return m ? `${h}${nbsp}ч ${m}${nbsp}мин` : `${h}${nbsp}ч`
}

/** A symptom's window, seconds around its leading symptom, in words. */
export function symptomWindow(w: [number, number] | null): string {
  if (!w) return 'ведущий симптом'
  const [from, to] = w
  if (from < 0 && to <= 0) return `за ${span(-from)}–${span(-to)} до ведущего`
  if (from < 0) return `от ${span(-from)} до ведущего до ${span(to)} после`
  if (from === 0) return `до ${span(to)} после ведущего`
  return `через ${span(from)}–${span(to)} после ведущего`
}

export function age(raw: number): string {
  // The browser's clock may run a little behind the host's.
  const seconds = Math.max(0, raw)
  if (seconds < 90) return `${Math.round(seconds)}${nbsp}с назад`
  if (seconds < 90 * 60) return `${Math.round(seconds / 60)}${nbsp}мин назад`
  if (seconds < 48 * 3600) return `${Math.round(seconds / 3600)}${nbsp}ч назад`
  return `${Math.round(seconds / 86400)}${nbsp}д назад`
}

export function percent(share: number): string {
  return `${Math.round(share * 100)}${nbsp}%`
}

/** A number with a fixed count of decimals and the Russian decimal comma. */
export function decimal(x: number, digits: number): string {
  return x.toLocaleString('ru-RU', { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

export function confidence(c: number): string {
  return decimal(c, 2)
}
