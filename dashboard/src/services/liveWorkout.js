const EXERCISE_KEYS = { pullup: 'pullUps', pushup: 'pushUps', squat: 'squats' }
const TARGETS = { pullUps: 5, pushUps: 10, squats: 15 }
const EMPTY_COUNTS = { pullUps: 0, pushUps: 0, squats: 0 }

export const EMPTY_LIVE_WORKOUTS = { webcam: null, mpu6050: null }

// Events carry an absolute count for one exercise. Keep sources and sessions separate.
export function applyLiveEvent(workouts, event) {
  const source = event?.source
  const exerciseKey = EXERCISE_KEYS[event?.exercise]
  if (!Object.hasOwn(workouts, source) || !exerciseKey ||
      !event.eventId || !event.sessionId ||
      !Number.isSafeInteger(event.repetitions) || event.repetitions < 0) return workouts

  const timestamp = Date.parse(event.timestamp)
  if (!Number.isFinite(timestamp)) return workouts
  const previous = workouts[source]
  const sameSession = previous?.sessionId === event.sessionId
  if (!sameSession && previous && timestamp < previous.lastTimestamp) return workouts
  if (sameSession && previous.lastEventId === event.eventId) return workouts

  const counts = { ...(sameSession ? previous.counts : EMPTY_COUNTS) }
  counts[exerciseKey] = Math.max(counts[exerciseKey], event.repetitions)
  const completedRounds = Math.min(...Object.keys(TARGETS).map((key) =>
    Math.floor(counts[key] / TARGETS[key])))

  return { ...workouts, [source]: {
    sessionId: event.sessionId,
    counts,
    completedRounds,
    lastEventId: event.eventId,
    lastTimestamp: sameSession ? Math.max(previous.lastTimestamp, timestamp) : timestamp,
  } }
}
