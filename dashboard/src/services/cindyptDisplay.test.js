import assert from 'node:assert/strict'
import test from 'node:test'
import { formatCindyDuration, isTestGoal, recommendationMatchesProgress } from './cindyptDisplay.js'

test('integration goal stays visibly marked as test data', () => {
  assert.equal(isTestGoal({ label: 'TEST CindyPT integration 2026-11-07' }), true)
  assert.equal(isTestGoal({ label: null }), false)
})

test('recommendation only matches the same completed workout and goal', () => {
  const goal = { label: null, targetRounds: 7, durationSeconds: 1200 }
  const progress = { available: true, goal, workout: { workoutId: 'completed-1' } }
  const advice = { goal, sourceWorkoutId: 'completed-1' }
  assert.equal(recommendationMatchesProgress(advice, progress), true)
  assert.equal(recommendationMatchesProgress(advice, { ...progress, available: false }), false)
  assert.equal(recommendationMatchesProgress(advice, { ...progress, workout: { workoutId: 'running-2' } }), false)
  assert.equal(recommendationMatchesProgress({ ...advice, goal: { ...goal, label: 'TEST old' } }, progress), false)
})

test('duration preserves seconds without inventing a rounded workout time', () => {
  assert.equal(formatCindyDuration(1200), '20 min')
  assert.equal(formatCindyDuration(1190), '19m 50s')
  assert.equal(formatCindyDuration(null), 'Unknown')
})
