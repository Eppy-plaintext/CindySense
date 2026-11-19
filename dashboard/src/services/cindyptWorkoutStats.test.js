import assert from 'node:assert/strict'
import test from 'node:test'
import { countSelectedFatigueAlerts, fatigueAlertValue, selectedAveragePaces, selectedWorkoutStats, workoutStatDisplay } from './cindyptWorkoutStats.js'

const selected = {
  sessionId: 'webcam-selected', summaryVerified: true, comparable: true,
  status: 'completed_20_minute', exerciseCounts: { pullup: 30, pushup: 57, squat: 75 },
}
const progress = { coachingWorkout: selected, workout: { workoutId: selected.sessionId }, detectorReliability: 'verified' }
const alert = (analysisId, sessionId = selected.sessionId, level = 'possible_fatigue') => ({
  analysisId, sessionId, level, dataQuality: { dataSufficient: true },
})

test('four statistics use only the selected verified workout', () => {
  const history = { complete: true, events: [alert('alert-1'), alert('alert-2', selected.sessionId, 'elevated_fatigue')] }
  assert.deepEqual(selectedWorkoutStats({ ...progress, latestWorkout: {
    sessionId: 'webcam-newer', exerciseCounts: { pullup: 0, pushup: 0, squat: 0 },
  } }, history), { pullups: 30, pushups: 57, squats: 75, fatigueAlerts: 2, status: null })
})

test('missing and unverified workouts never gain fabricated counts', () => {
  assert.deepEqual(selectedWorkoutStats({ coachingWorkout: null, workout: null }), {
    pullups: null, pushups: null, squats: null, fatigueAlerts: null, status: 'No verified workout selected.',
  })
  const unverified = selectedWorkoutStats({ ...progress,
    coachingWorkout: { ...selected, summaryVerified: false } })
  assert.equal(unverified.pullups, null)
  assert.equal(unverified.fatigueAlerts, null)
  assert.equal(unverified.status, 'Workout summary unverified.')
  assert.equal(selectedWorkoutStats({ coachingWorkout: null, workout: null,
    latestWorkout: { summaryVerified: false } }).status,
  'Latest workout summary unverified; no workout selected for coaching.')
  assert.equal(selectedWorkoutStats({ ...progress, coachingWorkout: {
    ...selected, comparable: false,
  } }).status, 'Short verified workout; not a full 20-minute result.')
  assert.equal(selectedWorkoutStats(progress).fatigueAlerts, null)
})

test('alerts require complete history and distinct qualifying analysis IDs from the selected session', () => {
  const events = [alert('alert-1'), alert('alert-1'), alert('alert-2', 'webcam-other'),
    alert('normal', selected.sessionId, 'normal'), alert('invalid-quality')]
  events[4].dataQuality.dataSufficient = false
  assert.equal(countSelectedFatigueAlerts({ complete: true, events }, selected.sessionId), 1)
  assert.equal(countSelectedFatigueAlerts({ complete: false, events }, selected.sessionId), null)
  assert.equal(countSelectedFatigueAlerts(null, selected.sessionId), null)
  assert.equal(countSelectedFatigueAlerts({ complete: true, events: [] }, selected.sessionId), 0)
  assert.equal(countSelectedFatigueAlerts({ complete: true, sessionId: 'webcam-other', events: [] }, selected.sessionId), null)
  assert.equal(countSelectedFatigueAlerts({ complete: true, events: [
    { ...alert('test'), synthetic: true }, { ...alert('historical'), historical: true },
  ] }, selected.sessionId), 0)
})

test('workout metric display keeps zero numeric and unavailable values neutral', () => {
  assert.deepEqual(workoutStatDisplay(30), { text: '30', numeric: true })
  assert.deepEqual(workoutStatDisplay(0), { text: '0', numeric: true })
  for (const value of [null, undefined, '', '  ', 'unavailable', -1, NaN]) {
    assert.deepEqual(workoutStatDisplay(value), { text: 'Not available', numeric: false })
  }
  assert.deepEqual(workoutStatDisplay('Tracking interrupted'), { text: 'Tracking interrupted', numeric: false })
})

test('unverified tracking never becomes zero alerts even with empty complete history', () => {
  const unverified = { ...progress, detectorReliability: 'detector_quality_unknown' }
  const stats = selectedWorkoutStats(unverified, { complete: true, events: [] })
  assert.equal(stats.fatigueAlerts, null)
  assert.equal(fatigueAlertValue(unverified, stats.fatigueAlerts), 'Not verified')
  assert.equal(fatigueAlertValue(unverified, 0), 'Not verified')
})

test('verified empty alert history is zero, but absent or incomplete history is unavailable', () => {
  const zero = selectedWorkoutStats(progress, { complete: true, events: [] }).fatigueAlerts
  assert.equal(zero, 0)
  assert.equal(fatigueAlertValue(progress, zero), 0)
  assert.equal(selectedWorkoutStats(progress).fatigueAlerts, null)
  assert.equal(fatigueAlertValue(progress, null), 'Not available')
  assert.equal(selectedWorkoutStats(progress, { complete: false, events: [alert('a')] }).fatigueAlerts, null)
  assert.equal(fatigueAlertValue(null, null), 'Not available')
})

test('selected workout averages stay session-scoped and preserve unavailable exercises', () => {
  const evidence = { ...progress, coachingWorkout: { ...selected, performance: { exercises: {
    pullup: { sessionAveragePace: { secondsPerRep: 2, acceptedIntervalCount: 8, coverage: 1 } },
    pushup: { sessionAveragePace: { secondsPerRep: null, acceptedIntervalCount: 1, coverage: 0.2 } },
  } } } }
  assert.equal(selectedAveragePaces(evidence).pullup.secondsPerRep, 2)
  assert.equal(selectedAveragePaces(evidence).pushup, null)
  assert.equal(selectedAveragePaces(evidence).squat, null)
  assert.equal(selectedAveragePaces({ ...evidence, workout: { workoutId: 'other' } }), null)
  assert.equal(selectedAveragePaces({ ...evidence, coachingWorkout: {
    ...evidence.coachingWorkout, summaryVerified: false,
  } }), null)
})
