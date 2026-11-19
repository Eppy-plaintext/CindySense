import assert from 'node:assert/strict'
import test from 'node:test'
import { trackingStateForExercise } from './trackingQuality.js'

test('tracking diagnostics never cross sessions or treat missing data as stable', () => {
  const now = Date.parse('2026-11-19T11:40:00Z')
  const worker = { sessionId: 'new', updatedAt: new Date(now).toISOString(), trackingQuality: { pullup: { state: 'stable' },
    squat: { state: 'degraded' } } }
  assert.equal(trackingStateForExercise(worker, 'new', 'pullup', now), 'stable')
  assert.equal(trackingStateForExercise(worker, 'new', 'squat', now), 'degraded')
  assert.equal(trackingStateForExercise(worker, 'new', 'pushup', now), 'insufficient_evidence')
  assert.equal(trackingStateForExercise(worker, 'old', 'pullup', now), 'insufficient_evidence')
  assert.equal(trackingStateForExercise(null, 'new', 'pullup', now), 'insufficient_evidence')
  assert.equal(trackingStateForExercise({ ...worker, updatedAt: null }, 'new', 'pullup', now), 'insufficient_evidence')
  assert.equal(trackingStateForExercise(worker, 'new', 'pullup', now + 11_000), 'insufficient_evidence')
})
