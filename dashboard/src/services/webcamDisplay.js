export function canStartWebcamWorker(worker) {
  return Boolean(worker && ['idle', 'stopped', 'completed', 'error'].includes(worker.state))
}

export function webcamWorkoutDisplay(worker) {
  if (!worker?.sessionId || !['running', 'stopping', 'completed', 'stopped'].includes(worker.state)) return null
  const counts = worker.counts || {}
  const rounds = worker.completedRounds
  if (!Number.isSafeInteger(rounds) || rounds < 0 ||
      !['pullup', 'pushup', 'squat'].every((name) => Number.isSafeInteger(counts[name]) && counts[name] >= 0)) return null
  const reps = {
    pullUps: Math.max(0, counts.pullup - rounds * 5),
    pushUps: Math.max(0, counts.pushup - rounds * 10),
    squats: Math.max(0, counts.squat - rounds * 15),
  }
  if (!Object.entries(reps).every(([name, count]) => count <= { pullUps: 5, pushUps: 10, squats: 15 }[name])) return null
  const remaining = ['completed', 'stopped'].includes(worker.state) && worker.completionReason === 'timer'
    ? 0 : Math.max(0, Math.ceil(worker.remainingSeconds ?? 1200))
  return { reps, rounds, secondsLeft: remaining, sessionId: worker.sessionId,
    complete: ['completed', 'stopped'].includes(worker.state), stale: false, coverageComplete: true, startInferred: false }
}
