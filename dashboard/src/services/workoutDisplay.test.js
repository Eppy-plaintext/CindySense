import assert from 'node:assert/strict'
import test from 'node:test'
import { acceptSnapshot, liveDisplay } from './workoutDisplay.js'

const started = Date.parse('2026-11-06T12:00:00Z')
const snapshot = (counts, overrides = {}) => ({ sources: { webcam: {
  sessionId: 'webcam-20261106T120000Z-12345678', counts,
  startAt: '2026-11-06T12:00:00Z', lastEventAt: '2026-11-06T12:00:05Z',
  observedExercises: ['pullup', 'pushup', 'squat'], startInferred: false,
  ...overrides,
} } })

test('first five squats appear in the main tracker with zero completed rounds', () => {
  const shown = liveDisplay(snapshot({ pullup: 0, pushup: 0, squat: 5 }), 'webcam', started + 6000)
  assert.deepEqual(shown.reps, { pullUps: 0, pushUps: 0, squats: 5 })
  assert.equal(shown.rounds, 0)
  assert.equal(shown.secondsLeft, 1194)
})

test('one Cindy round requires all three targets; extra repetitions stay in the next round', () => {
  const shown = liveDisplay(snapshot({ pullup: 7, pushup: 12, squat: 18 }), 'webcam', started + 10000)
  assert.equal(shown.rounds, 1)
  assert.deepEqual(shown.reps, { pullUps: 2, pushUps: 2, squats: 3 })
})

test('timer expires from session start and stale source is identified', () => {
  const shown = liveDisplay(snapshot({ pullup: 5, pushup: 10, squat: 15 }), 'webcam', started + 1201000)
  assert.equal(shown.secondsLeft, 0)
  assert.equal(shown.complete, true)
  assert.equal(shown.stale, true)
})

test('sources are separate and missing sessions remain empty', () => {
  assert.equal(liveDisplay(snapshot({ pullup: 0, pushup: 0, squat: 5 }), 'mpu6050', started), null)
  assert.equal(liveDisplay(null, 'webcam', started), null)
})

test('late older HTTP response and prior session cannot overwrite a newer snapshot', () => {
  const current = { ...snapshot({ pullup: 0, pushup: 10, squat: 0 }),
    retrievedAt: '2026-11-06T12:10:02Z', snapshotRevision: 8 }
  const olderResponse = { ...snapshot({ pullup: 0, pushup: 5, squat: 0 }),
    retrievedAt: '2026-11-06T12:10:01Z', snapshotRevision: 7 }
  assert.equal(acceptSnapshot(current, olderResponse), false)
  assert.equal(acceptSnapshot(current, { ...olderResponse,
    retrievedAt: '2026-11-06T12:10:03Z', snapshotRevision: 9,
    sources: { webcam: { ...olderResponse.sources.webcam,
      startAt: '2026-11-06T11:00:00Z' } } }), false)
  assert.equal(acceptSnapshot(current, { ...current,
    retrievedAt: '2026-11-06T12:10:03Z', snapshotRevision: 9 }), true)
})
