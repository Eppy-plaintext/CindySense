import assert from 'node:assert/strict'
import test from 'node:test'
import { hasNewCompletedWorkout, validateSavedRecovery, validateWeeklyPlan } from './weeklyPlanDisplay.js'

function planFixture() {
  const weekdays = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
  return {
    planId: 'a'.repeat(64), weekStart: '2026-11-09', timezone: 'Asia/Seoul',
    experienceLevel: 'beginner', trainingDays: [0, 2, 4],
    goal: { label: 'TEST goal', targetRounds: 7, durationSeconds: 1200 },
    completedWorkoutCount: 1, testGoal: true, limitedInformation: true,
    workoutProvenance: 'historical-test', recoveryRecommendations: ['Take an easy day.'],
    days: weekdays.map((weekday, index) => {
      const workout = [0, 2, 4].includes(index)
      return {
        weekday, date: `2026-11-${String(9 + index).padStart(2, '0')}`,
        type: workout ? 'workout' : 'recovery', suggestedRounds: workout ? 1 : 0,
        suggestedDurationSeconds: workout ? 300 : 0,
        exerciseVolume: workout ? { pullup: 5, pushup: 10, squat: 15 } : null,
        focus: workout ? 'Practice' : 'Rest', reason: 'Appropriate for this day.',
      }
    }),
  }
}

test('accepts a complete saved weekly plan and matching recovery record', () => {
  const plan = planFixture()
  assert.equal(validateWeeklyPlan(plan), null)
  assert.equal(validateSavedRecovery({ planId: plan.planId, weekStart: plan.weekStart,
    timezone: plan.timezone, items: plan.recoveryRecommendations }, plan), null)
})

test('rejects missing or invalid saved schedule values before rendering', () => {
  const plan = planFixture()
  plan.days[2].exerciseVolume.pushup = null
  assert.match(validateWeeklyPlan(plan), /workout targets/)
  plan.days[2].exerciseVolume.pushup = 10
  plan.days[6].date = '2026-11-14'
  assert.match(validateWeeklyPlan(plan), /invalid day/)
  assert.match(validateWeeklyPlan(null), /missing or invalid/)
})

test('rejects recovery content from another plan or with missing items', () => {
  const plan = planFixture()
  assert.match(validateSavedRecovery({ planId: 'b'.repeat(64), weekStart: plan.weekStart,
    timezone: plan.timezone, items: plan.recoveryRecommendations }, plan), /does not match/)
  assert.match(validateSavedRecovery({ planId: plan.planId, weekStart: plan.weekStart,
    timezone: plan.timezone, items: [] }, plan), /does not match/)
})

const webcamId = 'webcam-20261109T120000Z-a1b2c3d4'
const completed = (id = webcamId, source = 'webcam') => ({
  status: 'success', data: { available: true, workout: { workoutId: id, source } },
})

test('flags a new completed webcam workout and clears after that plan is saved', () => {
  const savedPlan = { planId: 'a'.repeat(64), sourceWorkoutId: '4-20261026T162428,4070040', completedWorkoutCount: 1 }
  assert.equal(hasNewCompletedWorkout(savedPlan, completed()), true)
  assert.equal(hasNewCompletedWorkout(savedPlan, completed(), {
    previousPlanId: savedPlan.planId, workoutId: webcamId,
  }), false)
  assert.equal(hasNewCompletedWorkout({ ...savedPlan, sourceWorkoutId: webcamId }, completed()), false)
  const noHistoryPlan = { planId: savedPlan.planId, sourceWorkoutId: null, completedWorkoutCount: 0 }
  assert.equal(hasNewCompletedWorkout(noHistoryPlan, completed()), true)
  assert.equal(hasNewCompletedWorkout(noHistoryPlan, completed(), {
    previousPlanId: noHistoryPlan.planId, workoutId: webcamId,
  }), false)
})

test('does not flag absent plans, workouts, or failed progress reads', () => {
  const savedPlan = { sourceWorkoutId: webcamId, completedWorkoutCount: 1 }
  assert.equal(hasNewCompletedWorkout(null, completed()), false)
  assert.equal(hasNewCompletedWorkout(savedPlan, { status: 'success', data: { available: false, workout: null } }), false)
  assert.equal(hasNewCompletedWorkout(savedPlan, { status: 'error', error: 'TinyIoT unavailable' }), false)
  assert.equal(hasNewCompletedWorkout(savedPlan, { status: 'loading' }), false)
})

test('ignores malformed identities, incomplete results, and test or unverified records', () => {
  const savedPlan = { sourceWorkoutId: '4-20261026T162428,4070040', completedWorkoutCount: 1 }
  for (const id of [null, '', 'webcam-test', 'webcam-20261109T120000Z-INVALID!']) {
    assert.equal(hasNewCompletedWorkout(savedPlan, completed(id)), false)
  }
  assert.equal(hasNewCompletedWorkout({ sourceWorkoutId: 'bad id', completedWorkoutCount: 1 }, completed()), false)
  assert.equal(hasNewCompletedWorkout({ sourceWorkoutId: null, completedWorkoutCount: 1 }, completed()), false)
  assert.equal(hasNewCompletedWorkout(savedPlan, completed(webcamId, 'simulator')), false)
  assert.equal(hasNewCompletedWorkout(savedPlan, completed('4-20261026T162428,4070040', null)), false)
  assert.equal(hasNewCompletedWorkout(savedPlan, { status: 'success', data: {
    available: true, workout: { workoutId: webcamId, source: 'webcam', status: 'running' },
  } }), false)
})
