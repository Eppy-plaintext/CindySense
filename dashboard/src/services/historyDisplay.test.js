import test from 'node:test'
import assert from 'node:assert/strict'
import { comparableSessions, formatHistoryDate, historyRows } from './historyDisplay.js'

test('history comparisons include verified compatible workouts only', () => {
  const data = { sessions: [
    { sessionId: 'full', comparable: true, performance: { exercises: { pushup: { paceSecondsPerRep: 2 } }, estimatedRestGaps: [] } },
    { sessionId: 'stopped', comparable: false, performance: null },
  ] }
  assert.deepEqual(comparableSessions(data).map((row) => row.sessionId), ['full'])
  assert.equal(historyRows(data)[0].pace, 2)
  assert.equal(historyRows(data)[1].restGaps, null)
  assert.deepEqual(historyRows({}), [])
})

test('history dates accept TinyIoT timestamps and hide invalid values', () => {
  const tinyiot = formatHistoryDate('20261115T153603,443', true)
  const iso = formatHistoryDate('2026-11-15T15:36:03.443Z', true)
  assert.equal(tinyiot, iso)
  assert.notEqual(tinyiot, 'Invalid Date')
  assert.equal(formatHistoryDate('broken', true), 'Date unknown')
  assert.equal(formatHistoryDate(null), 'Date unknown')
})
