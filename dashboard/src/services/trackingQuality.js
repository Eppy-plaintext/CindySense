export function trackingStateForExercise(worker, sessionId, exercise, now = Date.now()) {
  if (!sessionId || worker?.sessionId !== sessionId) return 'insufficient_evidence'
  const updatedAt = Date.parse(worker.updatedAt)
  if (!Number.isFinite(updatedAt) || !Number.isFinite(now) ||
      Math.abs(now - updatedAt) > 10_000) return 'insufficient_evidence'
  const state = worker?.trackingQuality?.[exercise]?.state
  return ['stable', 'degraded', 'insufficient_evidence'].includes(state) ? state : 'insufficient_evidence'
}
