import { useState } from 'react'
import { StoredRecommendationPanel } from './ApiPanels.jsx'
import { usePolling } from '../hooks/usePolling.js'
import { generateCindyPtRecommendation, getCindyPtGoal, getCindyPtProgress, getCindyPtRecommendations, getCurrentCindyPtRecommendation, saveCindyPtGoal } from '../services/api.js'
import { formatCindyDuration, isTestGoal, recommendationMatchesProgress } from '../services/cindyptDisplay.js'
import { fatigueAlertValue, selectedAveragePaces, selectedWorkoutStats, workoutStatDisplay } from '../services/cindyptWorkoutStats.js'
import { WeeklyPlanPanel } from './WeeklyPlanPanel.jsx'

export function CindyPtPage({ summaryRevision = 0 }) {
  const [refresh, setRefresh] = useState(0)
  const [roundsInput, setRoundsInput] = useState(null)
  const [minutesInput, setMinutesInput] = useState(null)
  const [action, setAction] = useState({ status: 'idle', message: '' })
  const goalResult = usePolling(getCindyPtGoal, { refreshKey: refresh })
  const progressResult = usePolling(getCindyPtProgress, { refreshKey: `${refresh}-${summaryRevision}` })
  const recommendationResult = usePolling(getCindyPtRecommendations, { refreshKey: `${refresh}-${summaryRevision}` })
  const currentAdviceResult = usePolling(getCurrentCindyPtRecommendation, { refreshKey: `${refresh}-${summaryRevision}` })

  const goal = goalResult.data?.goal
  const visibleRoundsInput = roundsInput ?? String(goal?.targetRounds ?? 7)
  const visibleMinutesInput = minutesInput ?? String(goal?.durationSeconds != null ? goal.durationSeconds / 60 : 20)
  const progress = progressResult.data
  const selectionReady = progress && Object.hasOwn(progress, 'latestWorkout')
  const workout = progressResult.status === 'success' && selectionReady && progress?.available ? progress.workout : null
  const latest = progress?.latestWorkout
  const stats = selectedWorkoutStats(progress, progress?.fatigueAlertHistory)
  const alertValue = fatigueAlertValue(progress, stats.fatigueAlerts)
  const averagePaces = selectedAveragePaces(progress)
  const recommendation = recommendationResult.data?.recommendations
  const alreadySaved = recommendationMatchesProgress(recommendation, progress)
  const busy = action.status === 'pending'
  const canGenerate = !busy && progressResult.status === 'success' && progress?.selectionStatus === 'sufficient' &&
    recommendationResult.status === 'success' && !alreadySaved

  async function saveGoal(event) {
    event.preventDefault()
    const targetRounds = Number(visibleRoundsInput)
    const minutes = Number(visibleMinutesInput)
    const durationSeconds = Math.round(minutes * 60)
    if (!Number.isInteger(targetRounds) || targetRounds < 1 || targetRounds > 100 ||
        !Number.isFinite(minutes) || durationSeconds < 60 || durationSeconds > 7200) {
      setAction({ status: 'error', message: 'Enter 1–100 rounds and a duration from 1–120 minutes.' })
      return
    }
    setAction({ status: 'pending', message: 'Saving goal…' })
    try {
      await saveCindyPtGoal({ targetRounds, durationSeconds })
      setAction({ status: 'success', message: 'Goal saved to TinyIoT.' })
      setRefresh((value) => value + 1)
    } catch (error) {
      setAction({ status: 'error', message: error.message })
    }
  }

  async function generateRecommendation() {
    if (!canGenerate) return
    if (!window.confirm('Generate and save a rule-based recommendation for the selected verified workout and current goal?')) return
    setAction({ status: 'pending', message: 'Generating recommendation…' })
    try {
      await generateCindyPtRecommendation()
      setAction({ status: 'success', message: 'Rule-based recommendation saved to TinyIoT.' })
      setRefresh((value) => value + 1)
    } catch (error) {
      setAction({ status: 'error', message: error.message })
    }
  }

  return <div className="page-wrap"><section className="page-heading"><div><p className="eyebrow">YOUR DIGITAL COACH</p><h1>Cindy PT</h1><p className="subheading">Goals and rule-based guidance from stored, completed workouts.</p></div><span className="coach-badge"><span className="pulse-dot" /> STORED CINDY PT DATA</span></section>
    <div className="pt-grid">
      <section className="goal-card panel" aria-label="Cindy workout goal">
        <p className="eyebrow">CURRENT FITNESS GOAL</p><h2>{isTestGoal(goal) ? 'Test fitness goal' : goal?.label || 'Cindy workout goal'}</h2>
        {goalResult.status === 'loading' || goalResult.status === 'idle' ? <p role="status">Loading saved goal…</p>
          : goalResult.status === 'error' ? <p className="pt-error" role="alert">{goalResult.error}</p>
            : goalResult.data?.available ? <><p>{goal.targetRounds} complete rounds in {formatCindyDuration(goal.durationSeconds)}.</p>
              {isTestGoal(goal) && <p className="pt-test-label">Test goal</p>}</>
              : <p>No saved Cindy goal yet.</p>}
        <form className="pt-goal-form" onSubmit={saveGoal}>
          <label>Target rounds<input type="number" min="1" max="100" step="1" required value={visibleRoundsInput} onChange={(event) => setRoundsInput(event.target.value)} /></label>
          <label>Target duration (minutes)<input type="number" min="1" max="120" step="any" required value={visibleMinutesInput} onChange={(event) => setMinutesInput(event.target.value)} /></label>
          <button className="control-btn primary" type="submit" disabled={busy}>Save goal</button>
        </form>
      </section>
      <section className="schedule-card panel" aria-label="Selected workout for coaching">
        <p className="eyebrow">WORKOUT FOR COACHING</p><h2>Selected workout summary</h2>
        {progressResult.status === 'loading' || progressResult.status === 'idle' ? <p className="pt-message" role="status">Loading workout history…</p>
          : progressResult.status === 'error' ? <p className="pt-error" role="alert">{progressResult.error}</p>
            : !selectionReady ? <p className="pt-message">FastAPI is serving an older CindyPT response. Restart the backend to enable verified workout selection.</p>
            : <><div className="pt-workout-stats">
                {([['Total Pull-ups', stats.pullups], ['Total Push-ups', stats.pushups], ['Total Squats', stats.squats], ['Fatigue Alerts', stats.fatigueAlerts]]).map(([label, value]) => {
                  const display = workoutStatDisplay(label === 'Fatigue Alerts' ? alertValue : value)
                  return <div key={label}><span>{label}</span><strong className={display.numeric ? 'is-number' : 'is-status'}>{display.text}</strong></div>
                })}
              </div>
              {averagePaces && <p className="pt-workout-notice">Detected session-average pace: {[
                ['Pull-ups', 'pullup'], ['Push-ups', 'pushup'], ['Squats', 'squat'],
              ].map(([label, key]) => `${label} ${averagePaces[key] ? `${averagePaces[key].secondsPerRep.toFixed(1)} s/rep` : '--'}`).join(' · ')}. Accepted within-set intervals only; missed detections may affect pace.</p>}
              {stats.status && <p className="pt-workout-notice" role="status">{stats.status}</p>}</>}
      </section>
      <section className="recommendations panel" aria-label="Cindy PT recommendations">
        <div className="panel-header"><div><p className="eyebrow">COACH'S NOTES</p><h2>Training and recovery recommendations</h2></div><span className="stored-tag">RULE-BASED</span></div>
        {selectionReady && currentAdviceResult.status === 'success' && currentAdviceResult.data?.available &&
          currentAdviceResult.data.recommendations?.sourceWorkoutId === workout?.workoutId &&
          currentAdviceResult.data.recommendations?.dataSufficiency === progress.selectionStatus &&
          !alreadySaved && <><p className="pt-advice-label">CURRENT WORKOUT · PREVIEW</p>
            <p className="stored-advice">{currentAdviceResult.data.recommendations.recommendation}</p>
            {Number.isInteger(currentAdviceResult.data.recommendations.proposedTargetRounds) && <p className="pt-message">Suggested target: {currentAdviceResult.data.recommendations.proposedTargetRounds} rounds. Approve a plan before saving.</p>}
            <p className="stored-advice">{currentAdviceResult.data.recommendations.recoveryRecommendation}</p></>}
        <button className="control-btn primary pt-generate" type="button" onClick={generateRecommendation} disabled={!canGenerate}>Generate Recommendation</button>
        {alreadySaved && <p className="pt-message">A recommendation is already saved for this workout and goal.</p>}
        {progressResult.status === 'success' && !progress?.available && <p className="pt-message">{latest?.durationSeconds != null ? `Your latest session lasted ${formatCindyDuration(latest.durationSeconds)}. ` : ''}Complete a longer verified workout before CindyPT recommends changing your 20-minute target.</p>}
        {recommendationResult.status === 'error' && <p className="pt-message">Recommendation generation is unavailable until the latest saved advice can be checked.</p>}
        <StoredRecommendationPanel result={recommendationResult} progress={progress} />
      </section>
    </div>
    <WeeklyPlanPanel key={refresh} progressResult={progressResult} />
    {action.status !== 'idle' && <p className={action.status === 'error' ? 'pt-action pt-error' : 'pt-action'} role={action.status === 'error' ? 'alert' : 'status'}>{action.message}</p>}
  </div>
}
