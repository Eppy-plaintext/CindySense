const WORDS = ['Zero', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight', 'Nine', 'Ten', 'Eleven', 'Twelve', 'Thirteen', 'Fourteen', 'Fifteen']
const TARGETS = { pullup: 5, pushup: 10, squat: 15 }
const NEXT = { pullup: 'pushup', pushup: 'squat', squat: 'pullup' }
const LABELS = { pullup: 'pull-ups', pushup: 'push-ups', squat: 'squats' }

export class VoiceCoach {
  constructor(synth, Utterance) {
    this.synth = synth
    this.Utterance = Utterance
    this.enabled = true
    this.volume = 0.8
    this.rate = 1
    this.sessionId = null
    this.seen = new Set()
    this.milestones = new Set()
    this.lastSequence = 0
    this.pendingCount = null
    this.priority = []
    this.speaking = false
    this.active = false
    this.generation = 0
    this.current = null
    try { this.synth?.getVoices() } catch { /* optional browser voices */ }
  }

  configure({ enabled, volume, rate }) {
    this.enabled = enabled
    this.volume = volume
    this.rate = rate
    if (!enabled) this.clear()
  }

  unlock() {
    if (!this.enabled || !this.synth || !this.Utterance) return
    try {
      const utterance = new this.Utterance('')
      utterance.volume = 0
      this.synth.speak(utterance)
      this.synth.cancel?.()
    } catch { /* speech is optional */ }
  }

  clear() {
    this.generation += 1
    this.pendingCount = null
    this.priority = []
    this.speaking = false
    this.current = null
    try { this.synth?.cancel() } catch { /* speech is optional */ }
  }

  start(sessionId) {
    if (!sessionId || this.sessionId === sessionId) return
    this.clear()
    this.sessionId = sessionId
    this.seen.clear()
    this.milestones.clear()
    this.lastSequence = 0
    this.active = true
    this.sayPriority('Workout started. Five pull-ups. Let’s go!')
  }

  end(sessionId, completed = true) {
    if (sessionId !== this.sessionId || !this.active) return
    this.clear()
    this.active = false
    if (completed) {
      this.priority.push({ text: "Time’s up! Workout complete!", kind: 'priority' })
      this.drain()
    }
  }

  reset() {
    this.clear()
    this.active = false
    this.sessionId = null
    this.seen.clear()
    this.milestones.clear()
    this.lastSequence = 0
  }

  timer(sessionId, minutes) {
    if (!this.active || sessionId !== this.sessionId || ![10, 5, 1].includes(Number(minutes))) return
    const key = `timer:${minutes}`
    if (this.milestones.has(key)) return
    this.milestones.add(key)
    this.sayPriority(`${minutes} minute${Number(minutes) === 1 ? '' : 's'} remaining.`)
  }

  rep({ sessionId, eventId, eventSequence, exercise, count, nextExercise, roundComplete, round,
    confirmedAtEpochMs, publishedAtEpochMs, receivedAtEpochMs }) {
    if (!this.active || sessionId !== this.sessionId || !eventId || this.seen.has(eventId) ||
        (Number.isSafeInteger(eventSequence) && eventSequence <= this.lastSequence) ||
        !Object.hasOwn(TARGETS, exercise) || !Number.isSafeInteger(count) || count < 1 || count > TARGETS[exercise]) return
    this.seen.add(eventId)
    if (Number.isSafeInteger(eventSequence)) this.lastSequence = eventSequence
    const timing = { eventId, confirmedAtEpochMs, publishedAtEpochMs, receivedAtEpochMs }
    const word = WORDS[count]
    if (nextExercise) {
      if (nextExercise !== NEXT[exercise] || count !== TARGETS[exercise]) return
      const text = roundComplete
        ? `${word}! Round complete! Back to pull-ups! Round ${WORDS[round] || round}. Let’s go!`
        : `${word}! ${exercise === 'pullup' ? 'Great job' : 'Nice work'}! Next, ${WORDS[TARGETS[nextExercise]]?.toLowerCase()} ${LABELS[nextExercise]}!`
      this.pendingCount = null
      this.sayPriority(text, timing)
    } else {
      if (Number.isFinite(confirmedAtEpochMs) && Date.now() - confirmedAtEpochMs > 1500) return
      // Replace a number that has been submitted but has not begun playing.
      // Never cancel a number already audible or an exercise transition.
      if (this.current?.kind === 'count' && !this.current.startedAt && !this.priority.length) this.clear()
      this.pendingCount = { text: word, timing, kind: 'count' }
      this.drain()
    }
  }

  sayPriority(text, timing) {
    if (!this.active || !this.enabled) return
    if (this.current?.kind === 'count' && !this.current.startedAt) this.clear()
    this.pendingCount = null
    this.priority = [{ text, timing, kind: 'priority' }]
    this.drain()
  }

  drain() {
    if (this.speaking || !this.enabled || !this.synth || !this.Utterance) return
    const item = this.priority.shift() || this.pendingCount
    if (!item) return
    if (item === this.pendingCount) this.pendingCount = null
    try {
      const utterance = new this.Utterance(item.text)
      utterance.lang = 'en-US'
      utterance.volume = this.volume
      utterance.rate = this.rate
      const voices = this.synth.getVoices?.() || []
      const localEnglish = voices.find((voice) => voice.localService && voice.lang?.toLowerCase().startsWith('en'))
      if (localEnglish) utterance.voice = localEnglish
      this.speaking = true
      this.current = { ...item, startedAt: null }
      const generation = this.generation
      utterance.onstart = () => {
        if (generation !== this.generation) return
        const startedAt = Date.now()
        this.current.startedAt = startedAt
        if (item.timing?.confirmedAtEpochMs) {
          const { confirmedAtEpochMs, publishedAtEpochMs, receivedAtEpochMs, eventId } = item.timing
          console.debug('[CindySense voice latency]', { eventId,
            confirmToLocalPublishMs: publishedAtEpochMs - confirmedAtEpochMs,
            localPublishToDashboardMs: receivedAtEpochMs - publishedAtEpochMs,
            dashboardToSpeechStartMs: startedAt - receivedAtEpochMs,
            confirmToSpeechStartMs: startedAt - confirmedAtEpochMs })
        }
      }
      utterance.onend = utterance.onerror = () => {
        if (generation !== this.generation) return
        this.speaking = false
        this.current = null
        this.drain()
      }
      this.synth.speak(utterance)
    } catch { this.speaking = false; this.current = null }
  }
}

export function handleLocalVoiceEvent(coach, event) {
  if (event.type === 'start') coach.start(event.sessionId)
  if (event.type === 'rep') coach.rep(event)
  if (event.type === 'timer') coach.timer(event.sessionId, event.minutes)
  if (event.type === 'complete') coach.end(event.sessionId)
  if (event.type === 'stop') coach.end(event.sessionId, false)
  if (event.type === 'reset') coach.reset()
}
