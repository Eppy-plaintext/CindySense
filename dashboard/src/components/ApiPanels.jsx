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

export function StoredRepetitionPanel({ result }) {
  const data = result.data
  const value = data?.latest_repetition_content
  const structured = data?.content_kind === 'structured_event' && data?.structured_event
  const hasValue = data?.available === true && value !== null && value !== undefined && value !== ''
  return <StoredPanelShell eyebrow="TINYIOT SNAPSHOT" title={structured ? 'Latest stored structured event' : 'Latest stored repetition content (unclassified)'}>
    {result.status === 'success' && hasValue ? <>
      <div className="stored-value">{typeof value === 'object' ? <pre>{JSON.stringify(value, null, 2)}</pre> : String(value)}</div>
      <p className="stored-caption">Retrieved from {data.source || 'backend'}{data.recorded_at ? ` · Stored at ${data.recorded_at}` : ''}</p>
      <p className="stored-caution">{structured ? 'Stored event only. It does not update the demo counters or reconstruct a complete session; polling is not a live sensor stream.' : 'No exercise, current-session count, or round can be inferred from this value. Refreshed by polling; not a live sensor stream.'}</p>
    </> : <ResultMessage result={result} loading="Loading stored repetition content…" empty="No stored repetition content is available." />}
  </StoredPanelShell>
}

export function StoredRecommendationPanel({ result }) {
  const data = result.data
  const stored = data?.recommendations
  const details = stored && typeof stored === 'object' && !Array.isArray(stored) ? stored : null
  const advice = typeof stored === 'string' ? stored : details?.recommendation
  const hasAdvice = data?.available === true && typeof advice === 'string' && advice.trim() !== ''
  return <StoredPanelShell eyebrow="TINYIOT · STORED ADVICE" title="Latest saved recommendation">
    {result.status === 'success' && hasAdvice ? <>
      <p className="stored-advice">{advice}</p>
      <div className="stored-metadata">
        {details?.source != null && <span>Advice source <strong>{String(details.source)}</strong></span>}
        {details?.roundsCompleted != null && <span>Stored rounds <strong>{String(details.roundsCompleted)}</strong></span>}
        {details?.durationSeconds != null && <span>Stored duration <strong>{String(details.durationSeconds)}s</strong></span>}
      </div>
      <p className="stored-caption">Retrieved from {data.source || 'backend'}{data.recorded_at ? ` · Stored at ${data.recorded_at}` : ''}</p>
      <p className="stored-caution">Saved advice only. It is separate from the simulated training plan below.</p>
    </> : <ResultMessage result={result} loading="Loading stored recommendation…" empty="No displayable stored recommendation is available." />}
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
