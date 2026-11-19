// Pace is measured only between consecutive detected reps of one exercise
// within one round. It is not total-session throughput or physical-rep pace.
const MAX_IDLE_MS = 30_000
const REST_MULTIPLIER = 2.5 // Matches the existing backend performance analysis.
const ROLLING_INTERVALS = 4
const SET_TARGETS = { pullup: 5, pushup: 10, squat: 15 }

// Experimental, unvalidated pace-change thresholds. This is not a measure of
// physiological fatigue. The first five detected reps supply four intervals.
export const PERFORMANCE_CONFIG = Object.freeze({
  rollingIntervals: ROLLING_INTERVALS,
  baselineIntervals: 4,
  moderateSlowdown: 0.10,
  highSlowdown: 0.25,
})

export function exercisePace(session, exercise, now) {
  const events = session?.recentEvents
  if (!Array.isArray(events) || !Number.isFinite(now)) return null
  const unique = new Map()
  for (const event of events) {
    if (event?.exercise !== exercise || typeof event.eventId !== 'string' || !event.eventId ||
        !Number.isSafeInteger(event.eventSequence) || event.eventSequence < 1 ||
        !Number.isSafeInteger(event.repetitions) || event.repetitions < 1 ||
        !Number.isSafeInteger(event.round) || event.round < 1) continue
    const at = Date.parse(event.timestamp)
    if (!Number.isFinite(at)) continue
    unique.set(event.eventId, { ...event, at })
  }
  const ordered = [...unique.values()].sort((a, b) => a.eventSequence - b.eventSequence || a.at - b.at)
  const latest = ordered.at(-1)
  if (!latest || now - latest.at > MAX_IDLE_MS || latest.at - now > 2_000) return null
  // SSE may have advanced the displayed count before the persisted snapshot
  // includes its occurrence time. Do not present the older burst as current.
  if (Number.isSafeInteger(session.counts?.[exercise]) &&
      session.counts[exercise] > latest.repetitions) return null

  let intervals = []
  for (let index = 1; index < ordered.length; index += 1) {
    const before = ordered[index - 1]
    const after = ordered[index]
    const gap = (after.at - before.at) / 1000
    if (after.eventSequence <= before.eventSequence || after.round !== before.round ||
        after.repetitions !== before.repetitions + 1 || gap <= 0 || gap > MAX_IDLE_MS / 1000) {
      intervals = []
      continue
    }
    // A long pause or lost detection starts a new burst; never smooth it into
    // an apparently slow repetition. Two prior intervals establish context.
    if (intervals.length >= 2) {
      const baseline = [...intervals].sort((a, b) => a - b)[Math.floor(intervals.length / 2)]
      if (gap > baseline * REST_MULTIPLIER) {
        intervals = []
        continue
      }
    } else if (intervals.length === 1 &&
               Math.max(intervals[0], gap) > Math.min(intervals[0], gap) * REST_MULTIPLIER) {
      intervals = intervals[0] > gap ? [gap] : []
      continue
    }
    intervals = [...intervals, gap].slice(-ROLLING_INTERVALS)
  }
  if (intervals.length < 2) return null
  const secondsPerRep = intervals.reduce((sum, value) => sum + value, 0) / intervals.length
  return { secondsPerRep, repsPerMinute: 60 / secondsPerRep, intervalCount: intervals.length }
}

// The snapshot's bounded set summary survives refresh even when the eight
// recent occurrences no longer include the last valid interval burst.
export function setBasedPace(session, exercise, now, trackingInterrupted = false) {
  const count = session?.counts?.[exercise]
  if (!session?.sessionId || !Number.isSafeInteger(count) || count < 0) {
    return { round: null, pace: null, status: 'insufficient-data' }
  }
  if (count === 0) return { round: null, pace: null, status: 'waiting-for-repetitions' }
  const summary = session.setPaces?.[exercise]
  let latest = summary
  let pace = summary?.pace ?? null
  if (!summary) {
    const events = orderedOccurrences(session.recentEvents, exercise)
    if (!events?.length) return { round: null, pace: null, status: 'insufficient-data' }
    const last = events.at(-1)
    latest = { round: last.round, repetitions: last.repetitions,
      lastEventAt: new Date(last.at).toISOString(),
      setComplete: last.repetitions % SET_TARGETS[exercise] === 0 }
    const currentSet = events.filter((event) => event.round === last.round)
    for (let index = 3; index <= currentSet.length; index += 1) {
      const prefix = currentSet.slice(0, index)
      const measured = exercisePace({ recentEvents: prefix }, exercise, prefix.at(-1).at + 1)
      if (measured) pace = { ...measured, measuredAt: new Date(prefix.at(-1).at).toISOString() }
    }
  }
  if (!Number.isSafeInteger(latest?.round) || latest.round < 1 ||
      latest.repetitions !== count || !Number.isFinite(Date.parse(latest.lastEventAt)) ||
      Date.parse(latest.lastEventAt) - now > 2_000) {
    return { round: null, pace: null, status: 'insufficient-data' }
  }
  if (!Number.isFinite(pace?.secondsPerRep) || pace.secondsPerRep <= 0 ||
      !Number.isFinite(pace?.repsPerMinute) || pace.repsPerMinute <= 0 ||
      !Number.isSafeInteger(pace?.intervalCount) || pace.intervalCount < 2) pace = null
  const currentSetRep = ((count - 1) % SET_TARGETS[exercise]) + 1
  const fresh = now - Date.parse(latest.lastEventAt) <= MAX_IDLE_MS &&
    Date.parse(pace?.measuredAt) === Date.parse(latest.lastEventAt)
  const status = trackingInterrupted ? 'tracking-interrupted' : !pace
    ? currentSetRep === 1 ? 'new-set-started' : 'insufficient-data'
    : fresh && !latest.setComplete ? 'live-set-pace' : 'last-measured-set-pace'
  return { round: latest.round, pace, status }
}

function orderedOccurrences(events, exercise) {
  if (!Array.isArray(events)) return null
  const byId = new Map()
  const sequences = new Map()
  for (const event of events.filter((item) => item?.exercise === exercise)) {
    const at = typeof event.timestamp === 'string' ? Date.parse(event.timestamp) : NaN
    if (typeof event.eventId !== 'string' || !event.eventId ||
        !Number.isSafeInteger(event.eventSequence) || event.eventSequence < 1 ||
        !Number.isSafeInteger(event.repetitions) || event.repetitions < 1 ||
        !Number.isSafeInteger(event.round) || event.round < 1 || !Number.isFinite(at)) return null
    const normalized = { ...event, at }
    const prior = byId.get(event.eventId)
    if (prior) {
      if (prior.eventSequence !== event.eventSequence || prior.repetitions !== event.repetitions ||
          prior.round !== event.round || prior.at !== at) return null
      continue
    }
    if (sequences.has(event.eventSequence)) return null
    byId.set(event.eventId, normalized)
    sequences.set(event.eventSequence, true)
  }
  return [...byId.values()].sort((a, b) => a.eventSequence - b.eventSequence)
}

function consecutive(events, initialCount = null) {
  if (!events.length || (initialCount !== null && events[0].repetitions !== initialCount)) return false
  return events.every((event, index) => index === 0 ||
    (event.round === events[index - 1].round &&
      event.repetitions === events[index - 1].repetitions + 1 &&
      event.at > events[index - 1].at))
}

export function exercisePerformance(session, exercise, now, config = PERFORMANCE_CONFIG, trackingInterrupted = false) {
  const pace = exercisePace(session, exercise, now)
  if (trackingInterrupted) return { pace, fatigue: 'tracking-unstable', slowdown: null }
  if (!session?.sessionId || !Number.isFinite(now)) return { pace, fatigue: 'insufficient-data', slowdown: null }
  const baseline = orderedOccurrences(session.baselineEvents, exercise)
  const recent = orderedOccurrences(session.recentEvents, exercise)
  if (recent === null) return { pace: null, fatigue: 'tracking-unstable', slowdown: null }
  const windowSize = config.rollingIntervals + 1
  const tail = recent.slice(-windowSize)
  if (tail.length === windowSize && !consecutive(tail)) {
    return { pace: null, fatigue: 'tracking-unstable', slowdown: null }
  }
  if (!pace || pace.intervalCount < config.rollingIntervals || tail.length < windowSize) {
    return { pace, fatigue: 'insufficient-data', slowdown: null }
  }
  // Never substitute a later burst for missing early-session observations.
  const first = baseline?.slice(0, config.baselineIntervals + 1)
  if (!first || first.length !== config.baselineIntervals + 1 ||
      first[0].round !== 1 || !consecutive(first, 1) ||
      tail[0].eventSequence <= first.at(-1).eventSequence) {
    return { pace, fatigue: 'insufficient-data', slowdown: null }
  }
  const baselinePace = exercisePace(
    { recentEvents: first }, exercise, first.at(-1).at + 1000,
  )
  if (!baselinePace || baselinePace.intervalCount < config.baselineIntervals) {
    return { pace, fatigue: 'insufficient-data', slowdown: null }
  }
  // Uniformly long gaps can otherwise look like a clean, slow burst even when
  // detections were missed. With no per-event pose quality, do not classify it.
  if (tail.some((event, index) => index > 0 &&
      (event.at - tail[index - 1].at) / 1000 > baselinePace.secondsPerRep * REST_MULTIPLIER)) {
    return { pace, fatigue: 'tracking-unstable', slowdown: null }
  }
  const slowdown = (pace.secondsPerRep - baselinePace.secondsPerRep) / baselinePace.secondsPerRep
  const fatigue = slowdown < config.moderateSlowdown ? 'low' :
    slowdown <= config.highSlowdown ? 'moderate' : 'high'
  return { pace, fatigue, slowdown, baselineSecondsPerRep: baselinePace.secondsPerRep }
}
