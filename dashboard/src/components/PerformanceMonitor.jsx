export function PerformanceMonitor({ result, sessionId }) {
  const data = result?.data
  const selected = data?.availableSessions?.find((item) => item.sessionId === sessionId)
  const performance = selected?.performance
  return <section className="panel fatigue-panel" aria-label="Performance monitor">
    <div className="panel-header"><div><p className="eyebrow">OBSERVED PACE</p><h2>Performance monitor</h2></div><span className="fatigue-tag">RULE-BASED</span></div>
    {!performance ? <p className="pt-message">{result?.status === 'error' ? 'Performance history is unavailable.' : 'Waiting for validated events from this session.'}</p> : <>
      <p className="pt-message">Advisory: {performance.state.replaceAll('_', ' ')} · Detector quality {performance.detectorQuality}</p>
      <div className="fatigue-rows">{Object.entries(performance.exercises).map(([exercise, item]) => <div className="fatigue-row" key={exercise}><span>{exercise}</span><strong>{item.repsPerMinute == null ? 'Insufficient data' : `${item.repsPerMinute} reps/min`}</strong><span>{item.paceChangePercent == null ? '—' : `${item.paceChangePercent}% pace change`}</span><small>{item.estimatedRestGaps.length} possible rest gaps</small></div>)}</div>
      <p className="fatigue-caption">A long event gap can reflect a break or missed webcam landmarks. No medical fatigue is inferred from it.</p>
    </>}
  </section>
}
