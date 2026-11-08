import { useState } from 'react'
import { StoredRecommendationPanel } from './ApiPanels.jsx'
import { usePolling } from '../hooks/usePolling.js'
import { generateCindyPtRecommendation, getCindyPtGoal, getCindyPtProgress, getCindyPtRecommendations, saveCindyPtGoal } from '../services/api.js'
import { formatCindyDuration, isTestGoal, recommendationMatchesProgress } from '../services/cindyptDisplay.js'
import { WeeklyPlanPanel } from './WeeklyPlanPanel.jsx'

export function CindyPtPage() {
  const [refresh, setRefresh] = useState(0)
  const [roundsInput, setRoundsInput] = useState(null)
  const [minutesInput, setMinutesInput] = useState(null)
  const [action, setAction] = useState({ status: 'idle', message: '' })
  const goalResult = usePolling(getCindyPtGoal, { refreshKey: refresh })
  const progressResult = usePolling(getCindyPtProgress, { refreshKey: refresh })
  const recommendationResult = usePolling(getCindyPtRecommendations, { refreshKey: refresh })

  const goal = goalResult.data?.goal
  const visibleRoundsInput = roundsInput ?? String(goal?.targetRounds ?? 7)
  const visibleMinutesInput = minutesInput ?? String(goal?.durationSeconds != null ? goal.durationSeconds / 60 : 20)
  const progress = progressResult.data
  const workout = progressResult.status === 'success' && progress?.available ? progress.workout : null
  const recommendation = recommendationResult.data?.recommendations
  const alreadySaved = recommendationMatchesProgress(recommendation, progress)
  const busy = action.status === 'pending'
  const canGenerate = !busy && progressResult.status === 'success' && progress?.available &&
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
    if (!window.confirm('Generate and save a rule-based recommendation for the latest completed workout and current goal?')) return
    setAction({ status: 'pending', message: 'Generating recommendation…' })
    try {
      await generateCindyPtRecommendation()
      setAction({ status: 'success', message: 'Rule-based recommendation saved to TinyIoT.' })
      setRefresh((value) => value + 1)
    } catch (error) {
      setAction({ status: 'error', message: error.message })
    }
  }

  const target = progress?.goal?.targetRounds
  const fraction = workout && target ? Math.min(100, Math.round(workout.roundsCompleted / target * 100)) : 0
  return <div className="page-wrap"><section className="page-heading"><div><p className="eyebrow">YOUR DIGITAL COACH</p><h1>Cindy PT</h1><p className="subheading">Goals and rule-based guidance from stored, completed workouts.</p></div><span className="coach-badge"><span className="pulse-dot" /> STORED CINDY PT DATA</span></section>
    <div className="pt-grid">
      <section className="goal-card panel" aria-label="Cindy workout goal">
        <p className="eyebrow">CURRENT FITNESS GOAL</p><h2>{goal?.label || 'Cindy workout goal'}</h2>
        {goalResult.status === 'loading' || goalResult.status === 'idle' ? <p role="status">Loading saved goal…</p>
          : goalResult.status === 'error' ? <p className="pt-error" role="alert">{goalResult.error}</p>
            : goalResult.data?.available ? <><p>{goal.targetRounds} complete rounds in {formatCindyDuration(goal.durationSeconds)}.</p>
              {isTestGoal(goal) && <p className="pt-test-label">TEST DATA — replace this integration goal with your own.</p>}</>
              : <p>No saved Cindy goal yet.</p>}
        <form className="pt-goal-form" onSubmit={saveGoal}>
          <label>Target rounds<input type="number" min="1" max="100" step="1" required value={visibleRoundsInput} onChange={(event) => setRoundsInput(event.target.value)} /></label>
          <label>Target duration (minutes)<input type="number" min="1" max="120" step="any" required value={visibleMinutesInput} onChange={(event) => setMinutesInput(event.target.value)} /></label>
          <button className="control-btn primary" type="submit" disabled={busy}>Save goal</button>
        </form>
      </section>
      <section className="schedule-card panel" aria-label="Completed workout progress">
        <p className="eyebrow">COMPLETED WORKOUT PERFORMANCE</p><h2>Progress toward your goal</h2>
        {progressResult.status === 'loading' || progressResult.status === 'idle' ? <p className="pt-message" role="status">Loading completed workout summary…</p>
          : progressResult.status === 'error' ? <p className="pt-error" role="alert">{progressResult.error}</p>
            : !progress?.available ? <p className="pt-message">{progress?.message || 'No completed workout summary is available.'}</p>
              : <><div className="pt-stats"><div><span>Completed rounds</span><strong>{workout.roundsCompleted}</strong></div><div><span>Target rounds</span><strong>{target}</strong></div><div><span>Remaining rounds</span><strong>{progress.roundsRemaining}</strong></div></div>
                <div className="goal-progress"><div><span>Round progress</span><strong>{fraction}%</strong></div><div className="progress-track"><span style={{ width: `${fraction}%` }} /></div></div>
                <p className="pt-message">Workout duration: {formatCindyDuration(workout.durationSeconds)} · Target: {formatCindyDuration(progress.goal.durationSeconds)}</p>
                <p className="pt-message">{progress.goalAchieved ? 'Goal achieved in this completed workout.' : progress.durationDifferenceSeconds > 0 ? `Workout exceeded the target duration by ${formatCindyDuration(progress.durationDifferenceSeconds)}.` : 'Workout was within the target duration; keep building complete rounds.'}</p>
                <p className="pt-message">{workout.reps ? `Observed reps: ${Object.entries(workout.reps).map(([name, count]) => `${name} ${count}`).join(' · ')}` : 'Repetition totals were not included in this summary.'}</p>
                <p className="stored-caution">Only completed WorkoutSummary records are used here. Live repetitions and an ongoing workout are separate.</p></>}
      </section>
      <section className="recommendations panel" aria-label="Cindy PT recommendations">
        <div className="panel-header"><div><p className="eyebrow">COACH'S NOTES</p><h2>Training and recovery recommendations</h2></div><span className="stored-tag">RULE-BASED</span></div>
        <button className="control-btn primary pt-generate" type="button" onClick={generateRecommendation} disabled={!canGenerate}>Generate Recommendation</button>
        {alreadySaved && <p className="pt-message">A recommendation is already saved for this workout and goal.</p>}
        {progressResult.status === 'success' && !progress?.available && <p className="pt-message">A saved goal and completed workout are required before generating advice.</p>}
        {recommendationResult.status === 'error' && <p className="pt-message">Recommendation generation is unavailable until the latest saved advice can be checked.</p>}
        <StoredRecommendationPanel result={recommendationResult} progress={progress} />
      </section>
    </div>
    <WeeklyPlanPanel key={refresh} progressResult={progressResult} />
    {action.status !== 'idle' && <p className={action.status === 'error' ? 'pt-action pt-error' : 'pt-action'} role={action.status === 'error' ? 'alert' : 'status'}>{action.message}</p>}
  </div>
}
