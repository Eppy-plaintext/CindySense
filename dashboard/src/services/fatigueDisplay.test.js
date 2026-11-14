import assert from 'node:assert/strict'
import test from 'node:test'
import { fatigueRows, fatigueStateForSession } from './fatigueDisplay.js'

const ready = (sessionId = 'webcam-current') => ({ status: 'success', data: {
  selectedSessionId: sessionId, dataSufficient: true,
  results: [{ exercise: 'pullup', fatigueLevel: 'possible_fatigue', fatigueScore: 25.6, contributingSignals: ['pace_slowdown'] }],
} })

test('accepts only fatigue results for the active session', () => {
  assert.equal(fatigueStateForSession(ready('old-session'), 'webcam-current').status, 'loading')
  assert.equal(fatigueStateForSession(ready(), 'webcam-current').status, 'ready')
})

test('shows unknown for missing or insufficient data', () => {
  assert.equal(fatigueStateForSession({ status: 'success', data: { selectedSessionId: 'webcam-current', dataSufficient: false } }, 'webcam-current').status, 'unknown')
  assert.equal(fatigueStateForSession({ status: 'success', data: { selectedSessionId: 'webcam-current', dataSufficient: true, results: [] } }, null, false).status, 'unavailable')
})

test('never presents historical/test results as live fatigue', () => {
  const result = ready()
  result.data.historicalTest = true
  assert.equal(fatigueStateForSession(result, 'webcam-current').status, 'unavailable')
})

test('exposes API failures and normalizes exercise rows', () => {
  assert.equal(fatigueStateForSession({ status: 'error', error: 'offline' }, 'webcam-current').status, 'error')
  const rows = fatigueRows(ready().data.results)
  assert.deepEqual(rows.map((row) => row.label), ['Possible fatigue', 'Unknown', 'Unknown'])
  assert.deepEqual(rows[0].signals, ['pace_slowdown'])
})
