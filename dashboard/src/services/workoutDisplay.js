export const CINDY_TARGETS = { pullUps: 5, pushUps: 10, squats: 15 }
const EXERCISE_NAMES = { pullUps: 'pullup', pushUps: 'pushup', squats: 'squat' }

export function acceptSnapshot(previous, incoming) {
  if (!previous) return true
  const oldTime = Date.parse(previous.retrievedAt)
  const newTime = Date.parse(incoming?.retrievedAt)
  if (!Number.isFinite(newTime)) return false
  if (Number.isFinite(oldTime) && newTime < oldTime) return false
  if (newTime === oldTime && incoming.snapshotRevision < previous.snapshotRevision) return false
  for (const source of ['webcam', 'mpu6050']) {
    const oldStart = Date.parse(previous.sources?.[source]?.startAt)
    const newStart = Date.parse(incoming.sources?.[source]?.startAt)
    if (Number.isFinite(oldStart) && Number.isFinite(newStart) && newStart < oldStart) return false
  }
  return true
}

export function liveDisplay(snapshot, source, now) {
  const session = snapshot?.sources?.[source]
  if (!session) return null
  const counts = {
    pullUps: session.counts.pullup,
    pushUps: session.counts.pushup,
    squats: session.counts.squat,
  }
  if (Object.values(counts).some((count) => !Number.isSafeInteger(count) || count < 0)) return null
  const rounds = Math.min(...Object.keys(CINDY_TARGETS).map((key) =>
    Math.floor(counts[key] / CINDY_TARGETS[key])))
  const reps = Object.fromEntries(Object.keys(CINDY_TARGETS).map((key) => [key,
    Math.min(CINDY_TARGETS[key], Math.max(0, counts[key] - rounds * CINDY_TARGETS[key])),
  ]))
  const start = Date.parse(session.startAt)
  const lastEvent = Date.parse(session.lastEventAt)
  if (!Number.isFinite(start) || !Number.isFinite(lastEvent)) return null
  const secondsLeft = Math.max(0, 1200 - Math.max(0, Math.floor((now - start) / 1000)))
  const observed = Object.values(EXERCISE_NAMES).every((name) => session.observedExercises?.includes(name))
  return { counts, reps, rounds, secondsLeft, sessionId: session.sessionId,
    stale: now - lastEvent > 30000, complete: secondsLeft === 0,
    coverageComplete: observed, startInferred: session.startInferred }
}
