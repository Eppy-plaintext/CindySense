import assert from 'node:assert/strict'
import test from 'node:test'
import { API_BASE_URL, generateCindyPtRecommendation, getBackendHealth, getCindyPtGoal, getCindyPtProgress, getCindyPtRecommendations, getFederatedLearningResults, getFederatedLearningStatus, getWorkoutFatigue, getWebcamWorker, startWebcamWorker, stopWebcamWorker, webcamPreviewUrl, saveCindyPtGoal, previewCindyPtPlan, saveCindyPtPlan, getSavedCindyPtPlan, getCindyPtRecovery } from './api.js'

test('local API and webcam routes use FastAPI port 8000', async () => {
  const originalFetch = globalThis.fetch
  const calls = []
  globalThis.fetch = async (url, options) => {
    calls.push({ url, method: options.method })
    return { ok: true, json: async () => ({ state: 'idle' }) }
  }
  try {
    await getBackendHealth()
    await getWebcamWorker()
    await startWebcamWorker()
    await stopWebcamWorker()
    assert.equal(new URL(API_BASE_URL).port, '8000')
    assert.equal(new URL(webcamPreviewUrl).port, '8000')
    assert.deepEqual(calls.map(({ url, method }) => [new URL(url).pathname, method]), [
      ['/api/health', 'GET'], ['/api/workout/webcam-worker', 'GET'],
      ['/api/workout/webcam-worker/start', 'POST'], ['/api/workout/webcam-worker/stop', 'POST'],
    ])
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('HTTP 200 health and idle worker responses remain independent', async () => {
  const originalFetch = globalThis.fetch
  globalThis.fetch = async (url) => ({
    ok: true,
    status: 200,
    json: async () => url.endsWith('/api/health')
      ? { backend: { available: true }, tinyiot: { reachable: true, resource_verified: true } }
      : { state: 'idle' },
  })
  try {
    assert.deepEqual(await getBackendHealth(), {
      backend: { available: true }, tinyiot: { reachable: true, resource_verified: true },
    })
    assert.deepEqual(await getWebcamWorker(), { state: 'idle' })
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('a failed request does not poison a later successful poll', async () => {
  const originalFetch = globalThis.fetch
  let calls = 0
  globalThis.fetch = async () => {
    calls += 1
    if (calls === 1) throw new TypeError('connection refused')
    return { ok: true, status: 200, json: async () => ({ state: 'idle' }) }
  }
  try {
    await assert.rejects(getWebcamWorker(), /Cannot reach the CindySense backend/)
    assert.deepEqual(await getWebcamWorker(), { state: 'idle' })
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('Cindy PT reads use GET and writes happen only on explicit calls', async () => {
  const originalFetch = globalThis.fetch
  const calls = []
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options })
    return { ok: true, json: async () => ({ available: true }) }
  }
  try {
    await getCindyPtGoal()
    await getCindyPtProgress()
    await getCindyPtRecommendations()
    assert.deepEqual(calls.map(({ url }) => new URL(url).pathname),
      ['/api/cindypt/goal', '/api/cindypt/progress', '/api/cindypt/recommendations'])
    assert.ok(calls.every(({ options }) => options.method === 'GET'))
    const goal = { targetRounds: 7, durationSeconds: 1200 }
    await saveCindyPtGoal(goal)
    await generateCindyPtRecommendation()
    assert.equal(calls[3].options.method, 'POST')
    assert.deepEqual(JSON.parse(calls[3].options.body), goal)
    assert.equal(calls[4].options.method, 'POST')
    assert.equal(new URL(calls[4].url).pathname, '/api/cindypt/recommendations/generate')
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('Cindy PT API exposes server and connection failures', async () => {
  const originalFetch = globalThis.fetch
  try {
    globalThis.fetch = async () => ({ ok: false, status: 503, json: async () => ({ detail: { message: 'TinyIoT could not be reached.' } }) })
    await assert.rejects(getCindyPtGoal(), /TinyIoT could not be reached/)
    globalThis.fetch = async () => { throw new TypeError('disconnected') }
    await assert.rejects(getCindyPtProgress(), /Cannot reach the CindySense backend/)
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('fatigue API is a read-only session-scoped GET', async () => {
  const originalFetch = globalThis.fetch
  const calls = []
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options })
    return { ok: true, json: async () => ({ selectedSessionId: 'webcam-current' }) }
  }
  try {
    await getWorkoutFatigue('webcam/current')
    assert.equal(new URL(calls[0].url).pathname, '/api/workout/fatigue')
    assert.equal(new URL(calls[0].url).searchParams.get('sessionId'), 'webcam/current')
    assert.equal(calls[0].options.method, 'GET')
    assert.equal(calls[0].options.body, undefined)
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('FL status and results are separate read-only GETs', async () => {
  const originalFetch = globalThis.fetch
  const calls = []
  globalThis.fetch = async (url, options) => {
    calls.push({ path: new URL(url).pathname, method: options.method })
    return { ok: true, status: 200, json: async () => ({ available: false, status: 'not_trained' }) }
  }
  try {
    await getFederatedLearningStatus()
    await getFederatedLearningResults()
    assert.deepEqual(calls, [
      { path: '/api/fl/status', method: 'GET' },
      { path: '/api/fl/results', method: 'GET' },
    ])
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('weekly plan preview and retrieval are separate from explicit writes', async () => {
  const originalFetch = globalThis.fetch
  const calls = []
  globalThis.fetch = async (url, options) => {
    calls.push({ path: new URL(url).pathname, options })
    return { ok: true, json: async () => ({ available: true }) }
  }
  try {
    await getSavedCindyPtPlan()
    await getCindyPtRecovery()
    const preferences = { timezone: 'Asia/Seoul', trainingDays: [0, 2, 4], experienceLevel: 'beginner' }
    await previewCindyPtPlan(preferences)
    assert.deepEqual(calls.map((call) => call.path),
      ['/api/cindypt/plans', '/api/cindypt/recovery', '/api/cindypt/plans/preview'])
    assert.deepEqual(calls.map((call) => call.options.method), ['GET', 'GET', 'POST'])
    await saveCindyPtPlan({ preferences, planId: 'a'.repeat(64) })
    assert.equal(calls[3].path, '/api/cindypt/plans')
    assert.equal(calls[3].options.method, 'POST')
  } finally {
    globalThis.fetch = originalFetch
  }
})
