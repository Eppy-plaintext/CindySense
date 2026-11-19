import { comparableSessions, formatHistoryDate, historyRows } from '../services/historyDisplay.js'

const STATUS = {
  completed_20_minute: 'Completed 20-minute workout',
  stopped_early: 'Explicitly stopped',
  no_verified_summary: 'No verified completion (crash or unfinished)',
  summary_event_mismatch: 'Summary and events disagree',
  unverified_summary: 'Summary source unverified',
  completed_duration_unverified: 'Completed, duration unverified',
  summary_without_retained_events: 'Summary only; events no longer retained',
  test_or_synthetic: 'Test or synthetic',
}

export function StoredHistoryPage({ result }) {
  const rows = historyRows(result?.data)
  const comparable = comparableSessions(result?.data)
  const scale = Math.max(1, ...comparable.map((row) => row.completedRounds || 0))
  return <div className="page-wrap"><section className="page-heading"><div><p className="eyebrow">STORED WORKOUTS</p><h1>Workout history</h1><p className="subheading">Retained TinyIoT events joined to completed workout summaries.</p></div></section>
    {result?.status === 'error' && <p role="alert">{result.error}</p>}
    {result?.status === 'loading' && <p role="status">Loading retained workout history…</p>}
    {result?.status === 'success' && result.data?.available === false && <p className="pt-message">{result.data.message || 'No stored workout history is available.'}</p>}
    {result?.status === 'success' && result.data?.available === true && <>
      <p className="pt-message">{rows.length} sessions · {comparable.length} comparable 20-minute workouts. Missing event history may reflect TinyIoT retention.</p>
      <section className="panel history-panel" aria-label="Completed rounds over time"><h2>Completed rounds over time</h2>
        {comparable.length ? [...comparable].reverse().map((row) => <div className="history-chart-row" key={row.sessionId}><span>{formatHistoryDate(row.date || row.recordedAt)}</span><div className="progress-track"><span style={{ width: `${100 * row.completedRounds / scale}%` }} /></div><strong>{row.completedRounds}</strong></div>) : <p className="pt-message">Comparable 20-minute workouts are not available yet.</p>}
      </section>
      <section className="panel history-panel" aria-label="Stored workout sessions"><h2>Sessions and performance</h2>
        <div className="history-table"><div className="history-row history-head"><span>DATE</span><span>REPS / ROUNDS</span><span>DURATION</span><span>STATUS</span><span></span></div>
          {rows.map((row) => <div className="history-row" key={row.sessionId}><span><strong>{formatHistoryDate(row.date, true)}</strong></span>
            <span className="result">{row.exerciseCounts ? `Pull-ups ${row.exerciseCounts.pullup ?? '?'} · Push-ups ${row.exerciseCounts.pushup ?? '?'} · Squats ${row.exerciseCounts.squat ?? '?'}` : 'Totals unavailable'}<small>Rounds {row.completedRounds ?? 'unverified'} · Push-up pace {row.pace == null ? 'unavailable' : `${row.pace}s/rep`} · Possible rest gaps {row.restGaps ?? 'unavailable'}</small><small>{row.comparable && row.targetRounds ? `Target ${row.targetRounds} rounds · ${row.targetProgressPercent}% achieved` : 'Excluded from equal-duration comparison'}</small></span>
            <span className="duration">{row.durationSeconds == null ? 'Unknown' : `${Math.floor(row.durationSeconds / 60)}m ${row.durationSeconds % 60}s`}</span>
            <span><em className={`status-chip ${row.comparable ? 'complete' : 'stopped'}`}>{STATUS[row.status] || row.status}</em><small>{row.performance?.state?.replaceAll('_', ' ') || 'Pace unavailable'}{row.roundChangeFromPrevious != null ? ` · ${row.roundChangeFromPrevious > 0 ? '+' : ''}${row.roundChangeFromPrevious} rounds vs previous` : ''}</small></span><span></span></div>)}
        </div></section>
      <p className="pt-message">Pace uses observed repetition timestamps. Long gaps can be rest or missed webcam detections; they are not medical fatigue measurements.</p>
    </>}
  </div>
}
