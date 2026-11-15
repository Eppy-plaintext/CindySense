export function comparableSessions(data) {
  return Array.isArray(data?.sessions) ? data.sessions.filter((session) => session.comparable === true) : []
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
