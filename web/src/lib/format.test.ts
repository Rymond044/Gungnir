import { expect, it } from 'vitest'
import { bytesOf, span as rawSpan, symptomWindow as rawWindow } from './format'

// Values carry no-break spaces between a number and its unit.
const plain = (s: string) => s.replace(/\u00a0/g, ' ')
const span = (s: number) => plain(rawSpan(s))
const symptomWindow = (w: [number, number] | null) => plain(rawWindow(w))

it('writes stretches of time in whole units', () => {
  expect(span(30)).toBe('30 с')
  expect(span(300)).toBe('5 мин')
  expect(span(43200)).toBe('12 ч')
  expect(span(5400)).toBe('1 ч 30 мин')
})

it('writes a window around the leading symptom in words', () => {
  expect(symptomWindow(null)).toBe('ведущий симптом')
  expect(symptomWindow([0, 43200])).toBe('до 12 ч после ведущего')
  expect(symptomWindow([-3600, 43200])).toBe('от 1 ч до ведущего до 12 ч после')
  expect(symptomWindow([60, 600])).toBe('через 1 мин–10 мин после ведущего')
})

it('writes used of total in the unit of the total', () => {
  const gib = 1024 ** 3
  expect(plain(bytesOf(7.81 * gib, 15.1 * gib))).toBe('7,81 / 15,1 ГиБ')
  expect(plain(bytesOf(300 * 1024 ** 2, 4 * gib))).toBe('0,29 / 4 ГиБ')
})
