import assert from 'node:assert/strict'
import test from 'node:test'
import { canStartWebcamWorker, webcamWorkoutDisplay, webcamPersistenceStatus } from './webcamDisplay.js'

test('completed worker persistence is reported after dashboard reload', () => {
  assert.match(webcamPersistenceStatus({ state: 'stopped', completionReason: 'explicit_stop',
    summaryPublication: 'delivered', eventJournalPending: 0, summaryPending: 0 }), /stored/i)
  assert.match(webcamPersistenceStatus({ state: 'stopped', completionReason: 'explicit_stop',
    summaryPublication: 'pending', eventJournalPending: 2, summaryPending: 1 }), /pending/i)
  assert.match(webcamPersistenceStatus({ state: 'error', eventJournalFailed: 1,
    summaryFailed: 1 }), /failed/i)
  assert.match(webcamPersistenceStatus({ state: 'stopped', completionReason: 'explicit_stop',
    summaryPublication: 'created' }), /event status unavailable/i)
  assert.equal(webcamPersistenceStatus({ state: 'running' }), null)
})

test('a crashed worker can be restarted while an active worker cannot', () => {
  assert.equal(canStartWebcamWorker({ state: 'error' }), true)
  assert.equal(canStartWebcamWorker({ state: 'idle' }), true)
  assert.equal(canStartWebcamWorker({ state: 'running' }), false)
  assert.equal(canStartWebcamWorker(null), false)
})

test('worker state drives session timer and current round without stored event lag', () => {
  const live = webcamWorkoutDisplay({ state: 'running', sessionId: 'webcam-20261111T000000Z-12345678',
    counts: { pullup: 6, pushup: 10, squat: 15 }, completedRounds: 1, remainingSeconds: 1133.2 })
  assert.equal(live.sessionId, 'webcam-20261111T000000Z-12345678')
  assert.equal(live.secondsLeft, 1134)
  assert.equal(live.rounds, 1)
  assert.deepEqual(live.reps, { pullUps: 1, pushUps: 0, squats: 0 })
})

test('completed timer and invalid worker counts are handled', () => {
  const finished = webcamWorkoutDisplay({ state: 'completed', completionReason: 'timer',
    sessionId: 'webcam-20261111T000000Z-12345678', counts: { pullup: 5, pushup: 10, squat: 15 },
    completedRounds: 1, remainingSeconds: 0 })
  assert.equal(finished.secondsLeft, 0)
  assert.equal(finished.complete, true)
  const stopped = webcamWorkoutDisplay({ state: 'stopped', completionReason: 'explicit_stop',
    sessionId: 'webcam-20261111T000000Z-12345678', counts: { pullup: 5, pushup: 10, squat: 15 },
    completedRounds: 1, remainingSeconds: 1100 })
  assert.equal(stopped.complete, true)
  assert.equal(stopped.secondsLeft, 1100)
  assert.equal(webcamWorkoutDisplay({ ...finished, state: 'error' }), null)
  assert.equal(webcamWorkoutDisplay({ ...finished, counts: { pullup: -1, pushup: 10, squat: 15 } }), null)
})
