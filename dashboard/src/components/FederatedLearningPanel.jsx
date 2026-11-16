export function FederatedLearningPanel({ status, results }) {
  const data = results.data?.available ? results.data : null
  const validation = data?.validation
  const audit = data?.audit
  const label = data?.status === 'trained' ? 'TRAINED OFFLINE' : 'NOT TRAINED'
  return <section className="panel fl-panel" aria-label="Federated learning experiment">
    <div className="panel-header"><div><p className="eyebrow">EXPERIMENTAL ML</p><h2>Federated pace experiment</h2></div><span className="stored-tag">{label}</span></div>
    <p className="pt-message">Single-machine simulation · three virtual clients · not three real users · separate from CindyPT warnings</p>
    {status.status === 'loading' || results.status === 'loading' ? <p role="status">Loading FL experiment status…</p> : data ? <>
      <div className="fl-grid">
        <span><small>Model</small><strong>{data.modelVersion || 'Unavailable'}</strong></span>
        <span><small>Rounds</small><strong>{data.roundMetrics?.length ?? 0}</strong></span>
        <span><small>Validation</small><strong>{validation ? `${Math.round(validation.accuracy * 100)}% accuracy` : 'Unavailable'}</strong></span>
        <span><small>Provenance</small><strong>{data.provenance || 'Unknown'}</strong></span>
      </div>
      <p className="fl-note">Sessions: {audit?.derivedSessions ?? 0} derived · train {data.trainSampleCount ?? 0} · validation {data.validationSampleCount ?? 0} · F1 {validation ? validation.f1.toFixed(2) : '—'}</p>
    </> : <p className="fl-note">{status.data?.message || 'Run the explicit offline training command to view results.'}</p>}
  </section>
}
