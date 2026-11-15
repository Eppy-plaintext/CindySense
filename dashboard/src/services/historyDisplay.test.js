import test from 'node:test'
import assert from 'node:assert/strict'
import { comparableSessions, historyRows } from './historyDisplay.js'

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
