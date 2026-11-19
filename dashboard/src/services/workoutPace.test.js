import assert from 'node:assert/strict'
import test from 'node:test'
import { exercisePace, exercisePerformance, PERFORMANCE_CONFIG } from './workoutPace.js'

const base = Date.parse('2026-11-19T10:00:00Z')
const event = (exercise, count, seconds, extra = {}) => ({
  eventId: `${exercise}-${count}`, eventSequence: count, exercise, repetitions: count,
  round: 1, timestamp: new Date(base + seconds * 1000).toISOString(), ...extra,
})
const pace = (events, exercise, now = base + 20_000) => exercisePace({ recentEvents: events }, exercise, now)

test('rolling pace uses occurrence timestamps, independently for each exercise', () => {
  const events = [1, 2, 3, 4].map((count) => event('pullup', count, count * 2))
    .concat([1, 2, 3].map((count) => event('pushup', count, count)))
    .concat([1, 2, 3].map((count) => event('squat', count, count * 3)))
  assert.deepEqual(pace(events, 'pullup'), { secondsPerRep: 2, repsPerMinute: 30, intervalCount: 3 })
  assert.equal(pace(events, 'pushup').secondsPerRep, 1)
  assert.equal(pace(events, 'squat').secondsPerRep, 3)
  assert.equal(pace([], 'squat'), null)
  assert.equal(pace([event('squat', 1, 1)], 'squat'), null)
})

test('active five-round snapshot restores all three paces after refresh', () => {
  const recentEvents = []
  const counts = { pullup: 25, pushup: 50, squat: 75 }
  for (const [exercise, target, lastSequence] of [
    ['pullup', 5, 125], ['pushup', 10, 135], ['squat', 15, 150],
  ]) {
    for (let offset = 7; offset >= 0; offset -= 1) {
      const repetitions = counts[exercise] - offset
      const eventSequence = lastSequence - offset
      recentEvents.push({ eventId: `${exercise}-${repetitions}`, eventSequence,
        exercise, repetitions, round: 5,
        timestamp: new Date(base + eventSequence * 1000).toISOString() })
    }
    assert.equal(target * 5, counts[exercise])
  }
  const response = { sources: { webcam: { sessionId: 'webcam-20261119T113037Z-b106d783',
    eventCount: 150, counts, recentEvents } } }
  const refreshed = structuredClone(response).sources.webcam
  for (const exercise of Object.keys(counts)) {
    assert.equal(exercisePace(refreshed, exercise, base + 151_000)?.secondsPerRep, 1)
  }
  assert.equal(exercisePace({ ...refreshed, recentEvents: undefined }, 'pullup', base + 151_000), null)
})

test('missing timestamps and duplicate deliveries never supply an interval', () => {
  const events = [event('squat', 1, 1), event('squat', 2, 2), event('squat', 3, 3)]
  assert.equal(pace([...events, events[2]], 'squat').intervalCount, 2)
  assert.equal(pace([events[0], { ...events[1], timestamp: null }, events[2]], 'squat'), null)
  assert.equal(pace([events[0], events[2]], 'squat'), null)
})

test('out-of-order delivery sorts by event sequence, never arrival time', () => {
  const events = [event('pushup', 3, 6), event('pushup', 1, 2), event('pushup', 2, 4)]
  assert.equal(pace(events, 'pushup').secondsPerRep, 2)
})

test('round boundaries and session replacement cannot mix intervals', () => {
  const old = [1, 2, 3].map((count) => event('pullup', count, count))
  const fresh = [event('pullup', 6, 10, { round: 2 })]
  assert.equal(pace([...old, ...fresh], 'pullup'), null)
  assert.equal(exercisePace({ recentEvents: fresh }, 'pullup', base + 12_000), null)
})

test('long rests, stale sessions, and delayed publication do not imply live pace', () => {
  const burst = [event('squat', 1, 1), event('squat', 2, 2), event('squat', 3, 3)]
  assert.equal(pace(burst, 'squat', base + 40_000), null)
  assert.equal(pace([...burst, event('squat', 4, 45)], 'squat', base + 46_000), null)
  assert.equal(pace([...burst, event('squat', 4, 45), event('squat', 5, 46), event('squat', 6, 47)], 'squat', base + 48_000)?.secondsPerRep, 1)
  // A network-delayed event is evaluated at its occurrence, not its arrival.
  assert.equal(pace(burst, 'squat', base + 29_000)?.secondsPerRep, 1)
})

test('refresh recovers from persisted event tail, while ahead-of-snapshot counts wait', () => {
  const persisted = { counts: { pushup: 3 }, recentEvents: [1, 2, 3].map((count) => event('pushup', count, count * 2)) }
  assert.equal(exercisePace(persisted, 'pushup', base + 8_000)?.secondsPerRep, 2)
  assert.equal(exercisePace({ ...persisted, counts: { pushup: 4 } }, 'pushup', base + 8_000), null)
})

const performanceSession = (exercise, earlySeconds = 2, recentSeconds = 2, extra = {}) => {
  const first = [1, 2, 3, 4, 5].map((count) => event(exercise, count, count * earlySeconds))
  const later = [6, 7, 8, 9, 10].map((count) => event(exercise, count,
    5 * earlySeconds + (count - 5) * recentSeconds,
    exercise === 'pullup' ? { round: 2 } : {}))
  return { sessionId: 'session-a', counts: { [exercise]: 10 }, baselineEvents: first,
    recentEvents: [...first, ...later].slice(-8), ...extra }
}

test('early-session and recent intervals classify experimental thresholds independently', () => {
  for (const exercise of ['pullup', 'pushup', 'squat']) {
    const low = exercisePerformance(performanceSession(exercise, 2, 2.18), exercise, base + 25_000)
    const moderate = exercisePerformance(performanceSession(exercise, 2, 2.2), exercise, base + 25_000)
    const boundary = exercisePerformance(performanceSession(exercise, 2, 2.5), exercise, base + 25_000)
    const high = exercisePerformance(performanceSession(exercise, 2, 2.6), exercise, base + 25_000)
    assert.equal(low.fatigue, 'low')
    assert.equal(moderate.fatigue, 'moderate')
    assert.equal(boundary.fatigue, 'moderate')
    assert.equal(high.fatigue, 'high')
    assert.equal(high.pace.secondsPerRep, 2.6)
  }
  const custom = { ...PERFORMANCE_CONFIG, moderateSlowdown: 0.20, highSlowdown: 0.30 }
  assert.equal(exercisePerformance(performanceSession('pushup', 2, 2.3), 'pushup', base + 25_000, custom).fatigue, 'low')
})

test('exercise baselines never mix and fresh session does not inherit old history', () => {
  const pushup = performanceSession('pushup', 1, 1.3)
  const squat = performanceSession('squat', 2, 2)
  const together = {
    sessionId: 'session-a',
    counts: { pushup: 10, squat: 10 },
    baselineEvents: [...pushup.baselineEvents, ...squat.baselineEvents],
    recentEvents: [...pushup.recentEvents, ...squat.recentEvents],
  }
  assert.equal(exercisePerformance(together, 'pushup', base + 20_000).fatigue, 'high')
  assert.equal(exercisePerformance(together, 'squat', base + 20_000).fatigue, 'low')
  assert.equal(exercisePerformance({ ...pushup, sessionId: 'session-b', baselineEvents: [] }, 'pushup', base + 20_000).fatigue, 'insufficient-data')
})

test('duplicates and delivery order do not change persisted refresh calculation', () => {
  const stored = performanceSession('pushup')
  const before = exercisePerformance(stored, 'pushup', base + 25_000)
  const refreshed = { ...structuredClone(stored), recentEvents: [...stored.recentEvents].reverse().concat(stored.recentEvents.at(-1)) }
  assert.deepEqual(exercisePerformance(refreshed, 'pushup', base + 25_000), before)
  const conflicting = { ...stored, recentEvents: [...stored.recentEvents, { ...stored.recentEvents.at(-1), timestamp: new Date(base + 99_000).toISOString() }] }
  assert.equal(exercisePerformance(conflicting, 'pushup', base + 25_000).fatigue, 'tracking-unstable')
})

test('insufficient early observations, zero reps, and invalid occurrence times stay neutral', () => {
  const stored = performanceSession('squat')
  assert.equal(exercisePerformance(null, 'squat', base + 25_000).fatigue, 'insufficient-data')
  assert.equal(exercisePerformance({ sessionId: 'empty', recentEvents: [], baselineEvents: [] }, 'squat', base + 25_000).fatigue, 'insufficient-data')
  assert.equal(exercisePerformance({ ...stored, baselineEvents: stored.baselineEvents.slice(1) }, 'squat', base + 25_000).fatigue, 'insufficient-data')
  assert.equal(exercisePerformance({ ...stored, baselineEvents: stored.baselineEvents.map((item, index) => index === 2 ? { ...item, timestamp: null } : item) }, 'squat', base + 25_000).fatigue, 'insufficient-data')
  assert.equal(exercisePerformance({ ...stored, recentEvents: stored.recentEvents.map((item, index) => index === 7 ? { ...item, timestamp: null } : item) }, 'squat', base + 25_000).fatigue, 'tracking-unstable')
})

test('count gaps, reversed occurrence times and camera interruption suppress fatigue', () => {
  const stored = performanceSession('pushup')
  const gap = { ...stored, recentEvents: stored.recentEvents.filter((item) => item.repetitions !== 8) }
  assert.equal(exercisePerformance(gap, 'pushup', base + 25_000).fatigue, 'tracking-unstable')
  const reversed = { ...stored, recentEvents: stored.recentEvents.map((item) =>
    item.repetitions === 9 ? { ...item, timestamp: new Date(base + 8_000).toISOString() } : item) }
  assert.equal(exercisePerformance(reversed, 'pushup', base + 25_000).fatigue, 'tracking-unstable')
  assert.equal(exercisePerformance(stored, 'pushup', base + 25_000, PERFORMANCE_CONFIG, true).fatigue, 'tracking-unstable')
  assert.equal(exercisePerformance(stored, 'pushup', base + 25_000, PERFORMANCE_CONFIG, true).pace.secondsPerRep, 2)
  const uniformlyLong = performanceSession('pushup', 2, 6)
  assert.equal(exercisePerformance(uniformlyLong, 'pushup', base + 45_000).fatigue, 'tracking-unstable')
})

test('rests, delayed events, and incomplete new bursts are not fatigue evidence', () => {
  const stored = performanceSession('squat')
  assert.equal(exercisePerformance(stored, 'squat', base + 60_000).fatigue, 'insufficient-data')
  const rest = performanceSession('squat', 2, 2)
  rest.recentEvents = rest.recentEvents.map((item) => item.repetitions >= 9
    ? { ...item, timestamp: new Date(Date.parse(item.timestamp) + 40_000).toISOString() } : item)
  assert.equal(exercisePerformance(rest, 'squat', base + 65_000).fatigue, 'insufficient-data')
  assert.equal(exercisePerformance(stored, 'squat', base + 25_000).fatigue, 'low')
  assert.equal(exercisePerformance(stored, 'squat', base + 90_000).pace, null)
})
