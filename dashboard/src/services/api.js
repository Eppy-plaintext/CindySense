const API_BASE_URL = (import.meta.env?.VITE_API_BASE_URL || 'http://localhost:8000').replace(/\/+$/, '')

async function getJson(path, { signal, method = 'GET', body } = {}) {
  let response
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      signal, method,
      ...(body === undefined ? {} : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
    })
  } catch (error) {
    if (error.name === 'AbortError') throw error
    throw new Error('Cannot reach the CindySense backend. Check that FastAPI is running.')
  }

  let payload
  try {
    payload = await response.json()
  } catch {
    throw new Error('The backend returned an unreadable response.')
  }

  if (!response.ok) {
    const message = payload?.detail?.message || payload?.detail || `Backend returned HTTP ${response.status}.`
    throw new Error(typeof message === 'string' ? message : `Backend returned HTTP ${response.status}.`)
  }
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
    throw new Error('The backend returned an unexpected response.')
  }
  return payload
}

export const getBackendHealth = (options) => getJson('/api/health', options)
export const getLatestRepetition = (options) => getJson('/api/workout/live', options)
export const getWorkoutSnapshot = (options) => getJson('/api/workout/snapshot', options)
export const getWebcamStatus = (options) => getJson('/api/workout/webcam-status', options)
export const getWorkoutHistory = (options) => getJson('/api/workout/history', options)
export const getCindyPtRecommendations = (options) => getJson('/api/cindypt/recommendations', options)
export const getCindyPtGoal = (options) => getJson('/api/cindypt/goal', options)
export const getCindyPtProgress = (options) => getJson('/api/cindypt/progress', options)
export const saveCindyPtGoal = (goal) => getJson('/api/cindypt/goal', { method: 'POST', body: goal })
export const generateCindyPtRecommendation = () => getJson('/api/cindypt/recommendations/generate', { method: 'POST' })
export const previewCindyPtPlan = (preferences) => getJson('/api/cindypt/plans/preview', { method: 'POST', body: preferences })
export const saveCindyPtPlan = (request) => getJson('/api/cindypt/plans', { method: 'POST', body: request })
export const getSavedCindyPtPlan = (options) => getJson('/api/cindypt/plans', options)
export const getCindyPtRecovery = (options) => getJson('/api/cindypt/recovery', options)
