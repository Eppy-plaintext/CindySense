import assert from 'node:assert/strict'
import test from 'node:test'
import { applyLiveEvent, EMPTY_LIVE_WORKOUTS, mergeLiveEventsIntoSnapshot } from './liveWorkout.js'

const event = (exercise, repetitions, eventId, extras = {}) => ({
  eventId, sessionId: 'webcam-session-1', source: 'webcam', exercise,
  repetitions, round: 1, timestamp: '2026-11-06T12:00:00Z', ...extras,
})

test('first squat is one squat and zero completed Cindy rounds', () => {
  const state = applyLiveEvent(EMPTY_LIVE_WORKOUTS, event('squat', 1, 'one'))
  assert.deepEqual(state.webcam.counts, { pullUps: 0, pushUps: 0, squats: 1 })
  assert.equal(state.webcam.completedRounds, 0)
  assert.equal(applyLiveEvent(state, event('squat', 1, 'one')), state)
  assert.equal(applyLiveEvent(state, event('squat', 2, 'one')), state)
})

test('absolute counts absorb missed polls and require all Cindy targets', () => {
  let state = EMPTY_LIVE_WORKOUTS
  state = applyLiveEvent(state, event('pullup', 5, 'pull'))
  state = applyLiveEvent(state, event('pushup', 10, 'push'))
  assert.equal(state.webcam.completedRounds, 0)
  state = applyLiveEvent(state, event('squat', 15, 'squat'))
  assert.equal(state.webcam.completedRounds, 1)
  state = applyLiveEvent(state, event('squat', 14, 'older'))
  assert.equal(state.webcam.counts.squats, 15)
  assert.equal(state.webcam.completedRounds, 1)
})

test('ESP32 and webcam counts stay separate', () => {
  let state = applyLiveEvent(EMPTY_LIVE_WORKOUTS, event('squat', 1, 'webcam'))
  state = applyLiveEvent(state, event('squat', 1, 'esp', { source: 'mpu6050', sessionId: 'esp-session' }))
  assert.equal(state.webcam.counts.squats, 1)
  assert.equal(state.mpu6050.counts.squats, 1)
})

test('newer session resets its source; old session cannot overwrite it', () => {
  let state = applyLiveEvent(EMPTY_LIVE_WORKOUTS, event('squat', 15, 'old'))
  state = applyLiveEvent(state, event('pullup', 1, 'new', {
    sessionId: 'webcam-session-2', timestamp: '2026-11-06T12:01:00Z',
  }))
  assert.deepEqual(state.webcam.counts, { pullUps: 1, pushUps: 0, squats: 0 })
  assert.equal(applyLiveEvent(state, event('squat', 16, 'late-old')), state)
})

test('events without a valid source or identity do not change counts', () => {
  const invalid = event('squat', 1, 'one', { source: 'unknown' })
  assert.equal(applyLiveEvent(EMPTY_LIVE_WORKOUTS, invalid), EMPTY_LIVE_WORKOUTS)
  assert.equal(applyLiveEvent(EMPTY_LIVE_WORKOUTS, event('squat', 1, '')), EMPTY_LIVE_WORKOUTS)
})

test('out-of-order events from one session still advance another exercise', () => {
  let state = applyLiveEvent(EMPTY_LIVE_WORKOUTS, event('squat', 2, 's2', { timestamp: '2026-11-06T12:00:02Z' }))
  state = applyLiveEvent(state, event('pushup', 1, 'p1', { timestamp: '2026-11-06T12:00:01Z' }))
  assert.deepEqual(state.webcam.counts, { pullUps: 0, pushUps: 1, squats: 2 })
})

test('duplicate SSE event and stale absolute count do not change state', () => {
  const first = applyLiveEvent(EMPTY_LIVE_WORKOUTS, event('squat', 2, 's2'))
  assert.equal(applyLiveEvent(first, event('squat', 2, 's2')), first)
  assert.equal(applyLiveEvent(first, event('squat', 1, 'older')), first)
})

test('SSE counts appear immediately while snapshot metadata remains authoritative', () => {
  const base = { retrievedAt: '2026-11-06T12:00:05Z', snapshotRevision: 4, sources: { webcam: {
    sessionId: 'webcam-session-1', counts: { pullup: 0, pushup: 0, squat: 1 },
    completedRounds: 0, startAt: '2026-11-06T12:00:00Z', lastEventAt: '2026-11-06T12:00:05Z',
  } } }
  const state = applyLiveEvent(EMPTY_LIVE_WORKOUTS, event('squat', 2, 'two', { timestamp: '2026-11-06T12:00:06Z' }))
  const shown = mergeLiveEventsIntoSnapshot(base, state)
  assert.equal(shown.sources.webcam.counts.squat, 2)
  assert.equal(shown.sources.webcam.startAt, base.sources.webcam.startAt)
})
