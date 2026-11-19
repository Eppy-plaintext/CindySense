import { useState } from 'react'
import { usePolling } from '../hooks/usePolling.js'
import { getCindyPtRecovery, getSavedCindyPtPlan, previewCindyPtPlan, saveCindyPtPlan } from '../services/api.js'
import { formatCindyDuration, isTestGoal } from '../services/cindyptDisplay.js'
import { savePlanAfterConfirmation } from '../services/weeklyPlan.js'
import { hasNewCompletedWorkout, validateSavedRecovery, validateWeeklyPlan } from '../services/weeklyPlanDisplay.js'

const WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']

export function WeeklyPlanPanel({ progressResult }) {
  const [trainingDays, setTrainingDays] = useState([0, 2, 4])
  const [experienceLevel, setExperienceLevel] = useState('beginner')
  const [preview, setPreview] = useState(null)
  const [refresh, setRefresh] = useState(0)
  const [recentSave, setRecentSave] = useState(null)
  const [action, setAction] = useState({ status: 'idle', message: '' })
  const savedResult = usePolling(getSavedCindyPtPlan, { refreshKey: refresh })
  const recoveryResult = usePolling(getCindyPtRecovery, { refreshKey: refresh })
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
  const preferences = { timezone, trainingDays, experienceLevel }
  const savedPlanError = savedResult.status === 'success' && savedResult.data?.available
    ? validateWeeklyPlan(savedResult.data.plan) : null
  const savedPlan = savedResult.data?.available && !savedPlanError ? savedResult.data.plan : null
  const plan = preview || savedPlan
  const testPlan = Boolean(plan && (plan.testGoal || isTestGoal(plan.goal) || plan.workoutProvenance === 'historical-test'))
  const isPreview = Boolean(preview)
  const alreadySaved = Boolean(preview && savedPlan?.planId === preview.planId)
  const newWorkoutAvailable = hasNewCompletedWorkout(savedPlan, progressResult, recentSave)
  const busy = action.status === 'pending'
  const recoveryError = !isPreview && savedPlan && recoveryResult.status === 'success'
    ? recoveryResult.data?.available
      ? validateSavedRecovery(recoveryResult.data.recovery, savedPlan)
      : 'No saved recovery guidance is available for this plan.'
    : null
  const recoveryItems = isPreview ? plan?.recoveryRecommendations
    : savedPlan && !recoveryError && recoveryResult.status === 'success' ? recoveryResult.data.recovery.items : null

  function toggleDay(day) {
    setTrainingDays((current) => current.includes(day) ? current.filter((value) => value !== day) : [...current, day].sort())
    setPreview(null)
  }

  async function createPreview() {
    if (trainingDays.length < 1 || trainingDays.length > 4) {
      setAction({ status: 'error', message: 'Choose one to four training days.' })
      return
    }
    setAction({ status: 'pending', message: 'Preparing a read-only plan preview…' })
    try {
      const result = await previewCindyPtPlan(preferences)
      const validationError = validateWeeklyPlan(result.plan)
      if (validationError) throw new Error(validationError)
      setPreview(result.plan)
      setAction({ status: 'success', message: 'Preview generated. No TinyIoT records were written.' })
    } catch (error) {
      setAction({ status: 'error', message: error.message })
    }
  }

  async function savePreview() {
    if (!preview || busy || alreadySaved) return
    const requestPreferences = { timezone: preview.timezone, trainingDays: preview.trainingDays, experienceLevel: preview.experienceLevel }
    setAction({ status: 'pending', message: 'Confirm the plan before saving…' })
    try {
      const result = await savePlanAfterConfirmation(preview, requestPreferences, window.confirm, saveCindyPtPlan)
      if (!result) {
        setAction({ status: 'idle', message: '' })
        return
      }
      setAction({ status: 'success', message: result.alreadySaved ? 'This plan was already saved.' : 'Weekly plan saved to TinyIoT.' })
      setRecentSave({ previousPlanId: savedPlan?.planId, workoutId: result.plan?.sourceWorkoutId })
      setPreview(null)
      setRefresh((value) => value + 1)
    } catch (error) {
      setAction({ status: 'error', message: error.message })
    }
  }

  return <section className="panel pt-weekly" aria-label="Cindy PT weekly plan">
    <div className="panel-header"><div><p className="eyebrow">SEVEN-DAY TRAINING</p><h2>Your Weekly Cindy Plan</h2></div></div>
    <p className="pt-message">Plan dates follow your local timezone.</p>
    <div className="pt-plan-controls">
      <label>Experience level<select value={experienceLevel} onChange={(event) => { setExperienceLevel(event.target.value); setPreview(null) }}><option value="beginner">Beginner</option><option value="intermediate">Intermediate</option><option value="advanced">Advanced</option></select></label>
      <fieldset><legend>Training days</legend><div className="pt-day-picker">{WEEKDAYS.map((name, day) => <label key={name}><input type="checkbox" checked={trainingDays.includes(day)} onChange={() => toggleDay(day)} />{name.slice(0, 3)}</label>)}</div></fieldset>
    </div>
    <div className="pt-plan-actions"><button className="control-btn secondary" type="button" onClick={createPreview} disabled={busy}>Preview Plan</button><button className="control-btn primary" type="button" onClick={savePreview} disabled={!preview || busy || alreadySaved}>Save Plan</button>{preview && savedPlan && <button className="control-btn secondary" type="button" onClick={() => setPreview(null)}>Show Saved Plan</button>}</div>
    {action.status !== 'idle' && <p className={action.status === 'error' ? 'pt-error' : 'pt-message'} role={action.status === 'error' ? 'alert' : 'status'}>{action.message}</p>}
    {!preview && (savedResult.status === 'loading' || savedResult.status === 'idle') && <p className="pt-message" role="status">Loading saved weekly plan…</p>}
    {!preview && savedResult.status === 'error' && <p className="pt-error" role="alert">{savedResult.error}</p>}
    {!preview && savedPlanError && <p className="pt-error" role="alert">{savedPlanError}</p>}
    {!plan && savedResult.status === 'success' && !savedPlanError && <p className="pt-message">{savedResult.data?.incomplete ? 'A previous save is incomplete. Preview and save again to retry.' : 'No saved weekly plan yet. Preview a plan without writing to TinyIoT.'}</p>}
    {newWorkoutAvailable && <p className="pt-plan-update" role="status">New workout available. Preview an updated CindyPT plan.</p>}
    {plan && <>
      <p className="pt-plan-state">{isPreview ? 'Preview — not saved' : 'Saved plan'}{alreadySaved ? ' · Already saved' : ''}</p>
      <div className="pt-plan-summary"><p>Week of <strong>{plan.weekStart}</strong></p><p>{testPlan ? 'Test plan' : 'Workout goal'}: <strong>{plan.goal.targetRounds} rounds in {formatCindyDuration(plan.goal.durationSeconds)}</strong></p></div>
      {testPlan && <p className="pt-test-label">Test plan; review before using for training.</p>}
      {plan.workoutProvenance === 'unverified' && <p className="pt-message">Workout source is unverified.</p>}
      {plan.limitedInformation && <p className="pt-message">Limited workout history; targets remain conservative.</p>}
      <div className="pt-week-list">{plan.days.map((day) => <article key={day.date} className={`pt-week-day ${day.type}`}><div><strong>{day.weekday}</strong><span>{day.date}</span></div><div><b>{day.type === 'workout' ? 'WORKOUT' : 'RECOVERY'}</b><strong>{day.focus}</strong>{day.type === 'workout' ? <><span>{day.suggestedRounds} Cindy round{day.suggestedRounds === 1 ? '' : 's'} · {formatCindyDuration(day.suggestedDurationSeconds)}</span><span>Pull-ups {day.exerciseVolume.pullup} · Push-ups {day.exerciseVolume.pushup} · Squats {day.exerciseVolume.squat}</span></> : <span>Rest or easy movement</span>}<small>{day.reason}</small></div></article>)}</div>
      <h3>Recovery guidance</h3>
      {!isPreview && (recoveryResult.status === 'loading' || recoveryResult.status === 'idle') && <p className="pt-message" role="status">Loading saved recovery guidance…</p>}
      {recoveryItems && <ul className="pt-recovery-list">{recoveryItems.map((item) => <li key={item}>{item}</li>)}</ul>}
      {recoveryError && <p className="pt-error" role="alert">{recoveryError}</p>}
      {!isPreview && recoveryResult.status === 'error' && <p className="pt-error" role="alert">Saved recovery guidance could not be retrieved: {recoveryResult.error}</p>}
    </>}
  </section>
}
