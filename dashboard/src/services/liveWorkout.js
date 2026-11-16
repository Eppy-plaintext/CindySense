const EXERCISE_KEYS = { pullup: 'pullUps', pushup: 'pushUps', squat: 'squats' }
const TARGETS = { pullUps: 5, pushUps: 10, squats: 15 }
const EMPTY_COUNTS = { pullUps: 0, pushUps: 0, squats: 0 }

export const EMPTY_LIVE_WORKOUTS = { webcam: null, mpu6050: null }

function completedRounds(counts) {
  return Math.min(...Object.keys(TARGETS).map((key) =>
    Math.floor(counts[key] / TARGETS[key])))
}

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
  const priorCount = counts[exerciseKey]
  counts[exerciseKey] = Math.max(counts[exerciseKey], event.repetitions)
  const lastTimestamp = sameSession ? Math.max(previous.lastTimestamp, timestamp) : timestamp
  if (sameSession && counts[exerciseKey] === priorCount && timestamp <= previous.lastTimestamp) return workouts

  return { ...workouts, [source]: {
    sessionId: event.sessionId,
    counts,
    completedRounds: completedRounds(counts),
    lastEventId: event.eventId,
    lastTimestamp,
  } }
}

export function mergeLiveEventsIntoSnapshot(snapshot, workouts) {
  if (!snapshot) return snapshot
  const sources = { ...snapshot.sources }
  for (const source of Object.keys(sources)) {
    const session = sources[source]
    const live = workouts?.[source]
    if (!live || live.sessionId !== session?.sessionId) continue
    const counts = {
      pullup: Math.max(session.counts?.pullup ?? 0, live.counts.pullUps),
      pushup: Math.max(session.counts?.pushup ?? 0, live.counts.pushUps),
      squat: Math.max(session.counts?.squat ?? 0, live.counts.squats),
    }
    const snapshotTimestamp = Date.parse(session.lastEventAt)
    const lastEventAt = Number.isFinite(snapshotTimestamp)
      ? new Date(Math.max(snapshotTimestamp, live.lastTimestamp)).toISOString()
      : new Date(live.lastTimestamp).toISOString()
    sources[source] = { ...session, counts, lastEventAt, completedRounds: Math.max(
      session.completedRounds ?? 0, live.completedRounds,
    ) }
  }
  return { ...snapshot, sources }
}
