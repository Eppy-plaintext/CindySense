import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { formatDashboardDate } from './dashboardText.js'

test('dashboard date follows the supplied current date', () => {
  assert.equal(formatDashboardDate(new Date(2026, 10, 19)), 'THURSDAY, NOVEMBER 19, 2026')
  assert.equal(formatDashboardDate(new Date(2026, 10, 20)), 'FRIDAY, NOVEMBER 20, 2026')
})

test('dashboard shell uses guest identity and contains no corrupted glyphs', () => {
  const source = readFileSync(new URL('../App.jsx', import.meta.url), 'utf8')
  assert.match(source, /Guest User/)
  assert.match(source, /Guest Mode/)
  assert.match(source, /Welcome to CindySense/)
  assert.match(source, /formatDashboardDate\(now\)/)
  assert.doesNotMatch(source, /Jordan|CrossFit athlete|\bJD\b|â|Â|Ã|�/)
})
