import { useState } from 'react'
import { usePolling } from '../hooks/usePolling.js'
import { getCindyPtRecovery, getSavedCindyPtPlan, previewCindyPtPlan, saveCindyPtPlan } from '../services/api.js'
import { formatCindyDuration } from '../services/cindyptDisplay.js'
import { savePlanAfterConfirmation } from '../services/weeklyPlan.js'

const WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']

export function WeeklyPlanPanel() {
  const [trainingDays, setTrainingDays] = useState([0, 2, 4])
  const [experienceLevel, setExperienceLevel] = useState('beginner')
  const [preview, setPreview] = useState(null)
  const [refresh, setRefresh] = useState(0)
  const [action, setAction] = useState({ status: 'idle', message: '' })
  const savedResult = usePolling(getSavedCindyPtPlan, { refreshKey: refresh })
  const recoveryResult = usePolling(getCindyPtRecovery, { refreshKey: refresh })
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
  const preferences = { timezone, trainingDays, experienceLevel }
  const savedPlan = savedResult.data?.available ? savedResult.data.plan : null
  const plan = preview || savedPlan
  const isPreview = Boolean(preview)
  const alreadySaved = Boolean(preview && savedPlan?.planId === preview.planId)
  const busy = action.status === 'pending'

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
      setPreview(null)
      setRefresh((value) => value + 1)
    } catch (error) {
      setAction({ status: 'error', message: error.message })
    }
  }

  return <section className="panel pt-weekly" aria-label="Cindy PT weekly plan">
    <div className="panel-header"><div><p className="eyebrow">SEVEN-DAY TRAINING</p><h2>Weekly Cindy plan</h2></div><span className="stored-tag">RULE-BASED</span></div>
    <p className="pt-message">Plan dates use your local timezone ({timezone}). The next plan starts on the coming Monday, or today if it is Monday.</p>
    <div className="pt-plan-controls">
      <label>Experience level<select value={experienceLevel} onChange={(event) => { setExperienceLevel(event.target.value); setPreview(null) }}><option value="beginner">Beginner</option><option value="intermediate">Intermediate</option><option value="advanced">Advanced</option></select></label>
      <fieldset><legend>Training days</legend><div className="pt-day-picker">{WEEKDAYS.map((name, day) => <label key={name}><input type="checkbox" checked={trainingDays.includes(day)} onChange={() => toggleDay(day)} />{name.slice(0, 3)}</label>)}</div></fieldset>
    </div>
    <div className="pt-plan-actions"><button className="control-btn secondary" type="button" onClick={createPreview} disabled={busy}>Preview Plan</button><button className="control-btn primary" type="button" onClick={savePreview} disabled={!preview || busy || alreadySaved}>Save Plan</button>{preview && savedPlan && <button className="control-btn secondary" type="button" onClick={() => setPreview(null)}>Show Saved Plan</button>}</div>
    {action.status !== 'idle' && <p className={action.status === 'error' ? 'pt-error' : 'pt-message'} role={action.status === 'error' ? 'alert' : 'status'}>{action.message}</p>}
    {!preview && (savedResult.status === 'loading' || savedResult.status === 'idle') && <p className="pt-message" role="status">Loading saved weekly plan…</p>}
    {!preview && savedResult.status === 'error' && <p className="pt-error" role="alert">{savedResult.error}</p>}
    {!plan && savedResult.status === 'success' && <p className="pt-message">{savedResult.data?.incomplete ? 'A previous save is incomplete. Preview and save again to retry.' : 'No saved weekly plan yet. Preview a plan without writing to TinyIoT.'}</p>}
    {plan && <>
      <p className="pt-plan-state">{isPreview ? 'PREVIEW — NOT SAVED' : 'SAVED IN TINYIOT'}{alreadySaved ? ' · IDENTICAL PLAN ALREADY SAVED' : ''}</p>
      <p className="pt-message">Week of {plan.weekStart} · {plan.timezone} · {plan.experienceLevel}</p>
      {plan.testGoal && <p className="pt-test-label">TEST DATA — this plan uses the integration test goal.</p>}
      {plan.workoutProvenance === 'historical-test' && <p className="pt-test-label">TEST DATA — the source workout is the historical integration record.</p>}
      {plan.workoutProvenance === 'unverified' && <p className="pt-message">The stored workout has no provenance label; treat its origin as unverified.</p>}
      {plan.limitedInformation && <p className="pt-message">Limited information: {plan.completedWorkoutCount === 0 ? 'no completed workouts are stored.' : 'fewer than two distinct completed workouts are stored.'} Targets stay conservative.</p>}
      <div className="pt-week-list">{plan.days.map((day) => <article key={day.date} className={`pt-week-day ${day.type}`}><div><strong>{day.weekday}</strong><span>{day.date}</span></div><div><b>{day.type === 'workout' ? 'WORKOUT' : 'RECOVERY'}</b><strong>{day.focus}</strong><span>{day.type === 'workout' ? `${day.suggestedRounds} Cindy round${day.suggestedRounds === 1 ? '' : 's'} · ${formatCindyDuration(day.suggestedDurationSeconds)} · ${day.exerciseVolume.pullup}/${day.exerciseVolume.pushup}/${day.exerciseVolume.squat} reps` : 'Rest or easy movement'}</span><small>{day.reason}</small></div></article>)}</div>
      <h3>Recovery guidance</h3><ul className="pt-recovery-list">{(isPreview ? plan.recoveryRecommendations : recoveryResult.data?.recovery?.items || plan.recoveryRecommendations).map((item) => <li key={item}>{item}</li>)}</ul>
      {!isPreview && recoveryResult.status === 'error' && <p className="pt-error" role="alert">Saved recovery guidance could not be retrieved: {recoveryResult.error}</p>}
    </>}
  </section>
}
