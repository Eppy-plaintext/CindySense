import { fatigueRows, fatigueStateForSession } from '../services/fatigueDisplay.js'

const EXERCISE_LABELS = { pullup: 'Pull-ups', pushup: 'Push-ups', squat: 'Squats' }

export function FatiguePanel({ result, sessionId, live = true }) {
  const state = fatigueStateForSession(result, sessionId, live)
  const rows = fatigueRows(state.results)
  return <section className="panel fatigue-panel" aria-label="Fatigue estimate">
    <div className="panel-header">
      <div><p className="eyebrow">SESSION SIGNALS</p><h2>Fatigue estimate</h2></div>
      <span className="fatigue-tag">READ-ONLY</span>
    </div>
    <p className="fatigue-disclaimer">Heuristic workout-performance estimate — not medically validated fatigue detection.</p>
    {(state.status === 'ready' || state.status === 'unknown') && <div className="fatigue-rows">
      {rows.map((row) => <div className="fatigue-row" key={row.exercise}>
        <span className="fatigue-exercise">{EXERCISE_LABELS[row.exercise]}</span>
        <strong className={`fatigue-level ${row.level}`}>{row.label}</strong>
        {row.score != null && <span className="fatigue-score">{row.score.toFixed(1)}</span>}
        {row.signals.length > 0 && <small>{row.signals.join(' · ').replaceAll('_', ' ')}</small>}
      </div>)}
    </div>}
    {state.message && <p className={`fatigue-message ${state.status === 'error' ? 'error' : ''}`} role={state.status === 'error' ? 'alert' : 'status'}>{state.message}</p>}
    <p className="fatigue-caption">Only validated events from the active webcam session are considered. Historical and test results are excluded.</p>
  </section>
}
