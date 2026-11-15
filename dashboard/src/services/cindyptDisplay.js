export function isTestGoal(goal) {
  return /^TEST\b/i.test(goal?.label || '')
}

export function recommendationMatchesProgress(recommendation, progress) {
  if (!recommendation || typeof recommendation !== 'object' || !progress?.available ||
      progress.selectionStatus !== 'sufficient' || recommendation.dataSufficiency !== 'sufficient' ||
      !progress.workout || !progress.goal) return false
  const goal = recommendation.goal
  return recommendation.sourceWorkoutId === progress.workout.workoutId &&
    goal?.targetRounds === progress.goal.targetRounds &&
    goal?.durationSeconds === progress.goal.durationSeconds &&
    (goal?.label ?? null) === (progress.goal.label ?? null)
}

export function formatCindyDuration(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return 'Unknown'
  const minutes = Math.floor(seconds / 60)
  const remainder = seconds % 60
  return remainder ? `${minutes}m ${remainder}s` : `${minutes} min`
}
