import assert from 'node:assert/strict'
import test from 'node:test'
import { setBasedPace } from './workoutPace.js'

const base = Date.parse('2026-11-19T12:00:00Z')
const occurrence = (exercise, count, round, seconds) => ({
  eventId: `${exercise}-${count}`, eventSequence: count, exercise, repetitions: count,
  round, timestamp: new Date(base + seconds * 1000).toISOString(),
})
const session = (exercise, count, events, setPaces) => ({
  sessionId: 'webcam-a', counts: { [exercise]: count }, recentEvents: events, setPaces,
})

test('first repetition waits, three consecutive reps measure, rest retains the set', () => {
  const first = [occurrence('pullup', 1, 1, 1)]
  assert.equal(setBasedPace(session('pullup', 0, []), 'pullup', base + 1000).status, 'waiting-for-repetitions')
  assert.equal(setBasedPace(session('pullup', 1, first), 'pullup', base + 1000).status, 'new-set-started')
  const three = [1, 2, 3].map((count) => occurrence('pullup', count, 1, count))
  const live = setBasedPace(session('pullup', 3, three), 'pullup', base + 4000)
  assert.equal(live.pace.secondsPerRep, 1)
  assert.equal(live.status, 'live-set-pace')
  const rested = setBasedPace(session('pullup', 3, three), 'pullup', base + 90_000)
  assert.equal(rested.pace.secondsPerRep, 1)
  assert.equal(rested.status, 'last-measured-set-pace')
  const interrupted = setBasedPace(session('pullup', 3, three), 'pullup', base + 4000, true)
  assert.equal(interrupted.pace.secondsPerRep, 1)
  assert.equal(interrupted.status, 'tracking-interrupted')
})

test('completed set survives other exercises and next set resets on its first rep', () => {
  const old = [1, 2, 3, 4, 5].map((count) => occurrence('pullup', count, 1, count))
  assert.equal(setBasedPace(session('pullup', 5, old), 'pullup', base + 6000).status, 'last-measured-set-pace')
  const next = [...old, occurrence('pullup', 6, 2, 60)]
  assert.equal(setBasedPace(session('pullup', 6, next), 'pullup', base + 61_000).status, 'new-set-started')
  assert.equal(setBasedPace(session('pullup', 6, next), 'pullup', base + 61_000).pace, null)
  next.push(occurrence('pullup', 7, 2, 62), occurrence('pullup', 8, 2, 64))
  assert.equal(setBasedPace(session('pullup', 8, next), 'pullup', base + 65_000).pace.secondsPerRep, 2)
})

test('bounded persisted summary recovers valid pace beyond the event tail', () => {
  const events = [8, 9, 10, 11, 12, 13, 14, 15].map((count) =>
    occurrence('squat', count, 1, count * 20))
  const saved = { squat: { round: 1, repetitions: 15,
    lastEventAt: events.at(-1).timestamp, setComplete: true,
    pace: { secondsPerRep: 1.25, repsPerMinute: 48, intervalCount: 4,
      measuredAt: new Date(base + 7000).toISOString() } } }
  const refreshed = structuredClone(session('squat', 15, events, saved))
  const reading = setBasedPace(refreshed, 'squat', base + 301_000)
  assert.equal(reading.pace.secondsPerRep, 1.25)
  assert.equal(reading.status, 'last-measured-set-pace')
  assert.equal(setBasedPace({ ...refreshed, counts: { squat: 16 } }, 'squat', base + 301_000).pace, null)
  assert.equal(setBasedPace({ ...refreshed, sessionId: null }, 'squat', base + 301_000).pace, null)
})

test('duplicates, ordering, gaps, and missing history do not manufacture pace', () => {
  const events = [occurrence('pushup', 3, 1, 3), occurrence('pushup', 1, 1, 1),
    occurrence('pushup', 2, 1, 2)]
  assert.equal(setBasedPace(session('pushup', 3, events), 'pushup', base + 4000).pace.secondsPerRep, 1)
  assert.equal(setBasedPace(session('pushup', 3, [...events, events[0]]), 'pushup', base + 4000).pace.secondsPerRep, 1)
  assert.equal(setBasedPace(session('pushup', 3, events.filter((item) => item.repetitions !== 2)), 'pushup', base + 4000).pace, null)
  assert.equal(setBasedPace(session('pushup', 3, events.map((item) => item.repetitions === 2
    ? { ...item, timestamp: null } : item)), 'pushup', base + 4000).pace, null)
  assert.equal(setBasedPace(session('pushup', 3, []), 'pushup', base + 4000).status, 'insufficient-data')
  const long = [occurrence('pushup', 1, 1, 1), occurrence('pushup', 2, 1, 2),
    occurrence('pushup', 3, 1, 30)]
  assert.equal(setBasedPace(session('pushup', 3, long), 'pushup', base + 31_000).pace, null)
})
