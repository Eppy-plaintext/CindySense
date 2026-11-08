const WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']

const positiveInteger = (value) => Number.isInteger(value) && value > 0
const nonnegativeInteger = (value) => Number.isInteger(value) && value >= 0
const nonempty = (value) => typeof value === 'string' && value.trim().length > 0

function validDate(value) {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false
  const date = new Date(`${value}T00:00:00Z`)
  return !Number.isNaN(date.getTime()) && date.toISOString().slice(0, 10) === value
}

export function validateWeeklyPlan(plan) {
  if (!plan || typeof plan !== 'object' || Array.isArray(plan)) return 'The saved weekly plan is missing or invalid.'
  if (!/^[a-f0-9]{64}$/.test(plan.planId || '') || !validDate(plan.weekStart) ||
      !nonempty(plan.timezone) || !['beginner', 'intermediate', 'advanced'].includes(plan.experienceLevel) ||
      !Array.isArray(plan.trainingDays) || plan.trainingDays.length < 1 || plan.trainingDays.length > 4 ||
      new Set(plan.trainingDays).size !== plan.trainingDays.length ||
      plan.trainingDays.some((day) => !Number.isInteger(day) || day < 0 || day > 6)) {
    return 'The saved weekly plan has invalid identity, dates, or preferences.'
  }
  const goal = plan.goal
  if (!goal || !positiveInteger(goal.targetRounds) || !positiveInteger(goal.durationSeconds) ||
      (goal.label != null && !nonempty(goal.label)) ||
      !nonnegativeInteger(plan.completedWorkoutCount) || typeof plan.testGoal !== 'boolean' ||
      typeof plan.limitedInformation !== 'boolean' || !nonempty(plan.workoutProvenance) ||
      !Array.isArray(plan.recoveryRecommendations) || !plan.recoveryRecommendations.length ||
      plan.recoveryRecommendations.some((item) => !nonempty(item))) {
    return 'The saved weekly plan has invalid goal, history, or recovery details.'
  }
  if (!Array.isArray(plan.days) || plan.days.length !== 7) return 'The saved weekly schedule must contain seven days.'
  const monday = new Date(`${plan.weekStart}T00:00:00Z`)
  if (monday.getUTCDay() !== 1) return 'The saved weekly schedule does not start on Monday.'
  for (const [index, day] of plan.days.entries()) {
    const expected = new Date(monday)
    expected.setUTCDate(monday.getUTCDate() + index)
    if (!day || day.date !== expected.toISOString().slice(0, 10) || day.weekday !== WEEKDAYS[index] ||
        !nonempty(day.focus) || !nonempty(day.reason)) return 'The saved weekly schedule has an invalid day.'
    if (plan.trainingDays.includes(index)) {
      const rounds = day.suggestedRounds
      const volume = day.exerciseVolume
      if (day.type !== 'workout' || !positiveInteger(rounds) || !positiveInteger(day.suggestedDurationSeconds) ||
          !volume || volume.pullup !== rounds * 5 || volume.pushup !== rounds * 10 || volume.squat !== rounds * 15) {
        return 'The saved weekly schedule has invalid workout targets.'
      }
    } else if (day.type !== 'recovery' || day.suggestedRounds !== 0 ||
               day.suggestedDurationSeconds !== 0 || day.exerciseVolume !== null) {
      return 'The saved weekly schedule has invalid recovery days.'
    }
  }
  return null
}

export function validateSavedRecovery(recovery, plan) {
  if (!recovery || recovery.planId !== plan.planId || recovery.weekStart !== plan.weekStart ||
      recovery.timezone !== plan.timezone || !Array.isArray(recovery.items) ||
      recovery.items.length !== plan.recoveryRecommendations.length ||
      recovery.items.some((item, index) => !nonempty(item) || item !== plan.recoveryRecommendations[index])) {
    return 'Saved recovery guidance is missing, invalid, or does not match the weekly plan.'
  }
  return null
}

const validStoredId = (value) => typeof value === 'string' && value.length <= 200 &&
  /^[A-Za-z0-9][A-Za-z0-9._,:-]*$/.test(value)
const webcamSessionId = /^webcam-\d{8}T\d{6}Z-[a-f0-9]{8}$/

export function hasNewCompletedWorkout(savedPlan, progressResult, recentSave = null) {
  if (!savedPlan || progressResult?.status !== 'success' || progressResult.data?.available !== true) return false
  const workout = progressResult.data.workout
  if (!workout || workout.source !== 'webcam' || !webcamSessionId.test(workout.workoutId || '') ||
      (workout.status && !['complete', 'completed'].includes(workout.status))) return false
  if (recentSave && recentSave.previousPlanId === savedPlan.planId && recentSave.workoutId === workout.workoutId) return false
  const previousId = savedPlan.sourceWorkoutId
  if (previousId == null) return savedPlan.completedWorkoutCount === 0
  return validStoredId(previousId) && previousId !== workout.workoutId
}
