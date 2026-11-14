const EXERCISES = ['pullup', 'pushup', 'squat']

export const FATIGUE_LABELS = {
  normal: 'Normal',
  possible_fatigue: 'Possible fatigue',
  elevated_fatigue: 'Elevated fatigue',
  unknown: 'Unknown',
}

export function fatigueStateForSession(result, sessionId, live = true) {
  if (!sessionId || !live) return { status: 'unavailable', message: 'Waiting for a current live workout session.' }
  if (result?.status === 'error') return { status: 'error', message: result.error || 'Fatigue analysis is unavailable.' }
  if (result?.status !== 'success' || !result.data) return { status: 'loading', message: 'Checking fatigue signals…' }
  const data = result.data
  if (data.selectedSessionId !== sessionId) return { status: 'loading', message: 'Waiting for fatigue analysis for the current session.' }
  if (data.testOnly === true || data.historicalTest === true ||
      data.results?.some((item) => item?.testOnly === true || item?.historicalTest === true)) {
    return { status: 'unavailable', message: 'Historical/test fatigue data is not shown as live-session status.' }
  }
  if (data.dataSufficient !== true) return { status: 'unknown', message: 'Insufficient validated repetitions for a fatigue estimate.', results: data.results || [] }
  return { status: 'ready', message: null, results: data.results || [] }
}

export function fatigueRows(results = []) {
  const byExercise = new Map(results.map((item) => [item.exercise, item]))
  return EXERCISES.map((exercise) => {
    const item = byExercise.get(exercise)
    const level = FATIGUE_LABELS[item?.fatigueLevel] ? item.fatigueLevel : 'unknown'
    return { exercise, level, label: FATIGUE_LABELS[level], score: item?.fatigueScore,
      signals: Array.isArray(item?.contributingSignals) ? item.contributingSignals : [] }
  })
}
