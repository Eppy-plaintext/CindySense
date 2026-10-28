const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000').replace(/\/+$/, '')

async function getJson(path, { signal } = {}) {
  let response
  try {
    response = await fetch(`${API_BASE_URL}${path}`, { signal })
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
export const getWorkoutHistory = (options) => getJson('/api/workout/history', options)
export const getCindyPtRecommendations = (options) => getJson('/api/cindypt/recommendations', options)
