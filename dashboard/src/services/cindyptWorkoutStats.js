const ALERT_LEVELS = new Set(['possible_fatigue', 'elevated_fatigue'])

export function workoutStatDisplay(value) {
  if (Number.isSafeInteger(value) && value >= 0) return { text: String(value), numeric: true }
  const description = typeof value === 'string' ? value.trim() : ''
  return { text: description && description.toLowerCase() !== 'unavailable' ? description : 'Not available', numeric: false }
}

export function countSelectedFatigueAlerts(history, sessionId) {
  if (!sessionId || history?.complete !== true || !Array.isArray(history.events) ||
      (history.sessionId && history.sessionId !== sessionId)) return null
  const ids = new Set()
  for (const event of history.events) {
    if (event?.sessionId !== sessionId || typeof event.analysisId !== 'string' || !event.analysisId ||
        event.testOnly === true || event.synthetic === true || event.historical === true ||
        event.dataQuality?.dataSufficient !== true || !ALERT_LEVELS.has(event.level)) continue
    ids.add(event.analysisId)
  }
  return ids.size
}

export function selectedWorkoutStats(progress, alertHistory = null) {
  const selected = progress?.coachingWorkout
  const verified = Boolean(selected?.summaryVerified === true && selected?.sessionId &&
    selected.status !== 'test_or_synthetic' && progress?.workout?.workoutId === selected.sessionId)
  const counts = verified ? selected.exerciseCounts : null
  const repetition = (exercise) => Number.isSafeInteger(counts?.[exercise]) && counts[exercise] >= 0
    ? counts[exercise] : null
  return {
    pullups: repetition('pullup'),
    pushups: repetition('pushup'),
    squats: repetition('squat'),
    fatigueAlerts: verified && progress?.detectorReliability === 'verified'
      ? countSelectedFatigueAlerts(alertHistory, selected.sessionId) : null,
    status: !selected ? progress?.latestWorkout && !progress.latestWorkout.summaryVerified
      ? 'Latest workout summary unverified; no workout selected for coaching.'
      : 'No verified workout selected.' : !verified ? 'Workout summary unverified.' :
      !selected.comparable ? 'Short verified workout; not a full 20-minute result.' : null,
  }
}

export function fatigueAlertValue(progress, count) {
  if (progress?.detectorReliability === 'verified' && Number.isSafeInteger(count) && count >= 0) {
    return count
  }
  if (progress?.coachingWorkout && progress?.detectorReliability !== 'verified') {
    return 'Not verified'
  }
  return 'Not available'
}

export function selectedAveragePaces(progress) {
  const selected = progress?.coachingWorkout
  if (!selected?.summaryVerified || !selected.sessionId ||
      selected.sessionId !== progress?.workout?.workoutId) return null
  const exercises = selected.performance?.exercises
  return Object.fromEntries(['pullup', 'pushup', 'squat'].map((exercise) => {
    const metric = exercises?.[exercise]?.sessionAveragePace
    return [exercise, Number.isFinite(metric?.secondsPerRep) && metric.secondsPerRep > 0 &&
      Number.isSafeInteger(metric.acceptedIntervalCount) && metric.acceptedIntervalCount >= 3 &&
      Number.isFinite(metric.coverage) && metric.coverage > 0 ? metric : null]
  }))
}
