import assert from 'node:assert/strict'
import test from 'node:test'
import { VoiceCoach } from './voiceCoach.js'

function fixture(startImmediately = true) {
  const spoken = []
  const synth = { speak: (utterance) => { spoken.push(utterance); if (startImmediately) utterance.onstart?.() },
    cancel: () => { spoken.length = 0 }, getVoices: () => [{ lang: 'en-US', localService: true }] }
  const coach = new VoiceCoach(synth, class { constructor(text) { this.text = text } })
  return { coach, spoken, finish: () => spoken.at(-1)?.onend() }
}

test('Cindy counts and transitions follow confirmed events once', () => {
  const { coach, spoken, finish } = fixture()
  coach.start('session')
  finish()
  for (const [exercise, target, next] of [['pullup', 5, 'pushup'], ['pushup', 10, 'squat'], ['squat', 15, 'pullup']]) {
    for (let count = 1; count <= target; count++) {
      const event = { sessionId: 'session', eventId: `${exercise}-${count}`, exercise, count,
        nextExercise: count === target ? next : null, roundComplete: exercise === 'squat' && count === target, round: 2 }
      coach.rep(event)
      coach.rep(event)
      finish()
    }
  }
  assert.match(spoken.find((item) => item.text.includes('Next, ten push-ups')).text, /^Five!/)
  assert.match(spoken.find((item) => item.text.includes('Next, fifteen squats')).text, /^Ten!/)
  assert.match(spoken.find((item) => item.text.includes('Round complete')).text, /Round Two/)
  assert.equal(spoken.filter((item) => item.text === 'One').length, 3)
})

test('browser gesture unlock is silent and does not queue workout speech', () => {
  const { coach, spoken } = fixture()
  coach.unlock()
  assert.equal(spoken.length, 0)
})

test('fast counts retain only the latest pending count', () => {
  const { coach, spoken, finish } = fixture()
  coach.start('session'); finish()
  for (let count = 1; count <= 4; count++) coach.rep({ sessionId: 'session', eventId: String(count), exercise: 'pullup', count })
  assert.equal(spoken.at(-1).text, 'One')
  finish()
  assert.equal(spoken.at(-1).text, 'Four')
  assert.equal(spoken.some((item) => item.text === 'Two' || item.text === 'Three'), false)
})

test('a newer count replaces a number before playback starts', () => {
  const { coach, spoken, finish } = fixture(false)
  coach.start('session'); finish()
  coach.rep({ sessionId: 'session', eventId: 'one', eventSequence: 1, exercise: 'pullup', count: 1 })
  coach.rep({ sessionId: 'session', eventId: 'two', eventSequence: 2, exercise: 'pullup', count: 2 })
  assert.deepEqual(spoken.map((item) => item.text), ['Two'])
  coach.rep({ sessionId: 'session', eventId: 'duplicate', eventSequence: 2, exercise: 'pullup', count: 2 })
  assert.equal(spoken.length, 1)
})

test('transition takes precedence over an unstarted count and stale counts are skipped', () => {
  const { coach, spoken, finish } = fixture(false)
  coach.start('session'); finish()
  coach.rep({ sessionId: 'session', eventId: 'stale', eventSequence: 1, exercise: 'pullup', count: 1,
    confirmedAtEpochMs: Date.now() - 2000 })
  assert.equal(spoken.length, 1)
  coach.rep({ sessionId: 'session', eventId: 'four', eventSequence: 2, exercise: 'pullup', count: 4 })
  coach.rep({ sessionId: 'session', eventId: 'five', eventSequence: 3, exercise: 'pullup', count: 5,
    nextExercise: 'pushup' })
  assert.deepEqual(spoken.map((item) => item.text), ['Five! Great job! Next, ten push-ups!'])
})

test('off, reset, timer deduplication, and completion', () => {
  const { coach, spoken, finish } = fixture()
  coach.start('session'); finish()
  coach.timer('session', 10); coach.timer('session', 10)
  finish()
  assert.equal(spoken.filter((item) => item.text === '10 minutes remaining.').length, 1)
  coach.configure({ enabled: false, volume: 0.5, rate: 1 })
  coach.rep({ sessionId: 'session', eventId: 'one', exercise: 'pullup', count: 1 })
  assert.equal(spoken.length, 0)
  coach.configure({ enabled: true, volume: 0.5, rate: 1 })
  coach.end('session')
  assert.equal(spoken.at(-1).text, 'Time’s up! Workout complete!')
  coach.reset()
  assert.equal(spoken.length, 0)
})
