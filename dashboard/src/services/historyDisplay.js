export function comparableSessions(data) {
  return Array.isArray(data?.sessions) ? data.sessions.filter((session) => session.comparable === true) : []
}

export function formatHistoryDate(value, withTime = false) {
  if (typeof value !== 'string' || !value) return 'Date unknown'
  const compact = /^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})(?:,(\d{1,3}))?$/.exec(value)
  const normalized = compact
    ? `${compact[1]}-${compact[2]}-${compact[3]}T${compact[4]}:${compact[5]}:${compact[6]}.${(compact[7] || '0').padEnd(3, '0')}Z`
    : value
  const date = new Date(normalized)
  if (Number.isNaN(date.getTime())) return 'Date unknown'
  return withTime ? date.toLocaleString() : date.toLocaleDateString()
}

export function historyRows(data) {
  if (!Array.isArray(data?.sessions)) return []
  return data.sessions.map((session) => ({
    ...session,
    date: session.firstEventAt || session.recordedAt,
    pace: session.performance?.exercises?.pushup?.paceSecondsPerRep ?? null,
    restGaps: session.performance?.estimatedRestGaps?.length ?? null,
  }))
}
