import { isTestGoal, recommendationMatchesProgress } from '../services/cindyptDisplay.js'

function StatusItem({ label, state, detail }) {
  return <span className={`connection-item ${state}`}><span className="connection-dot" aria-hidden="true" /><span><strong>{label}</strong><small>{detail}</small></span></span>
}

export function ConnectionStatus({ result }) {
  const { status, data, error } = result
  const backendState = status === 'success' ? data?.backend?.available === true ? 'online' : 'offline' : status === 'error' ? 'offline' : 'pending'
  const tinyState = backendState === 'online' ? data?.tinyiot?.reachable === true ? 'online' : 'offline' : 'pending'
  const tinyDetail = tinyState === 'online'
    ? data?.tinyiot?.resource_verified === true ? 'Reachable · resource verified' : 'Reachable · resource unverified'
    : tinyState === 'offline' ? data?.tinyiot?.message || 'Unavailable' : backendState === 'offline' ? 'Unknown · backend unavailable' : 'Checking…'

  return <div className="connection-status" role="status" aria-label="Connection status" title={error || undefined}>
    <StatusItem label="Backend" state={backendState} detail={backendState === 'online' ? 'Available' : backendState === 'offline' ? 'Offline' : 'Checking…'} />
    <StatusItem label="TinyIoT" state={tinyState} detail={tinyDetail} />
  </div>
}

function StoredPanelShell({ eyebrow, title, children }) {
  return <section className="panel stored-panel"><div className="panel-header"><div><p className="eyebrow">{eyebrow}</p><h2>{title}</h2></div><span className="stored-tag">STORED DATA</span></div>{children}</section>
}

function ResultMessage({ result, loading, empty }) {
  if (result.status === 'loading' || result.status === 'idle') return <p className="stored-message" role="status">{loading}</p>
  if (result.status === 'error') return <p className="stored-message error" role="alert">{result.error}</p>
  return <p className="stored-message">{empty}</p>
}

export function StoredRepetitionPanel({ result, snapshot }) {
  const data = result.data
  const value = data?.latest_repetition_content
  const structured = data?.content_kind === 'structured_event' && data?.structured_event
  const hasValue = data?.available === true && value !== null && value !== undefined && value !== ''
  return <StoredPanelShell eyebrow="TINYIOT SNAPSHOT" title={structured ? 'Latest stored structured event' : 'Latest stored repetition content (unclassified)'}>
    <div className="live-source-grid" aria-label="Observed live workout counts">
      {[['webcam', 'Webcam'], ['mpu6050', 'ESP32 / MPU6050']].map(([source, label]) => {
        const session = snapshot?.sources?.[source]
        return <div className="live-source" key={source}>
          <strong>{label}</strong>
          {session ? <>
            <span>Pull-ups {session.counts.pullup} · Push-ups {session.counts.pushup} · Squats {session.counts.squat}</span>
            <span>Completed Cindy rounds: <b>{session.completedRounds}</b></span>
            <small>Session {session.sessionId}</small>
          </> : <span>Waiting for an event from this source</span>}
        </div>
      })}
    </div>
    <p className="stored-caution">Aggregated from retained TinyIoT events by the backend. A complete Cindy round requires 5 pull-ups, 10 push-ups, and 15 squats in the same source and session.</p>
    {result.status === 'success' && hasValue ? <>
      <div className="stored-value">{typeof value === 'object' ? <pre>{JSON.stringify(value, null, 2)}</pre> : String(value)}</div>
      <p className="stored-caption">Retrieved from {data.source || 'backend'}{data.recorded_at ? ` · Stored at ${data.recorded_at}` : ''}</p>
      <p className="stored-caution">{structured ? 'The event round field labels the round in progress; it does not mean that a round was completed. The separate demo controls above remain simulated.' : 'No exercise or round can be inferred from this legacy value.'}</p>
    </> : <ResultMessage result={result} loading="Loading stored repetition content…" empty="No stored repetition content is available." />}
  </StoredPanelShell>
}
export function StoredRecommendationPanel({ result, progress }) {
  const data = result.data
  const stored = data?.recommendations
  const details = stored && typeof stored === 'object' && !Array.isArray(stored) ? stored : null
  const advice = typeof stored === 'string' ? stored : details?.recommendation
  const hasAdvice = data?.available === true && typeof advice === 'string' && advice.trim() !== ''
  const savedForCurrentGoal = recommendationMatchesProgress(details, progress)
  return <StoredPanelShell eyebrow="TINYIOT · STORED ADVICE" title="Latest saved recommendation">
    {result.status === 'success' && hasAdvice ? <>
      {isTestGoal(details?.goal) && <p className="pt-test-label">TEST DATA — saved for the integration test goal.</p>}
      <p className="pt-advice-label">TRAINING · {details?.source === 'rule-based' ? 'RULE-BASED' : 'STORED'}</p>
      <p className="stored-advice">{advice}</p>
      <p className="pt-advice-label">RECOVERY</p>
      <p className="stored-advice">{details?.recoveryRecommendation || 'No stored recovery recommendation is available.'}</p>
      <div className="stored-metadata">
        {details?.source != null && <span>Advice source <strong>{String(details.source)}</strong></span>}
        {details?.roundsCompleted != null && <span>Stored rounds <strong>{String(details.roundsCompleted)}</strong></span>}
        {details?.durationSeconds != null && <span>Stored duration <strong>{String(details.durationSeconds)}s</strong></span>}
      </div>
      <p className="stored-caption">Retrieved from {data.source || 'backend'}{data.recorded_at ? ` · Stored at ${data.recorded_at}` : ''}</p>
      {progress?.available && !savedForCurrentGoal && <p className="stored-caution">This advice was saved for an earlier workout or goal. Generate new advice for the current completed workout when ready.</p>}
    </> : <ResultMessage result={result} loading="Loading stored recommendation…" empty="No saved recommendation is available yet." />}
  </StoredPanelShell>
}

export function HistoryAvailability({ result }) {
  const data = result.data
  return <StoredPanelShell eyebrow="BACKEND HISTORY" title="Stored workout history">
    {result.status === 'success' && data?.available === false ? <p className="stored-message">{data.message || 'Stored workout history is not available yet.'}</p>
      : result.status === 'success' && data?.available === true ? <p className="stored-message">{Array.isArray(data.sessions) ? `${data.sessions.length} stored sessions reported by the backend.` : 'Stored session details are unavailable.'}</p>
        : <ResultMessage result={result} loading="Checking stored workout history…" empty="Stored workout history is unavailable." />}
    <p className="stored-caution">The example sessions below are simulated and are not returned by TinyIoT.</p>
  </StoredPanelShell>
}
