import { useEffect, useState } from 'react'

/** One request at a time; schedule the next poll only after the current one settles. */
export function usePolling(request, { enabled = true, intervalMs = 5000, refreshKey = 0 } = {}) {
  const [result, setResult] = useState({ status: 'idle', data: null, error: null })

  useEffect(() => {
    if (!enabled) return undefined
    let active = true
    let timeoutId
    let controller

    const poll = async () => {
      controller = new AbortController()
      try {
        const data = await request({ signal: controller.signal, refreshKey })
        if (active) setResult({ status: 'success', data, error: null })
      } catch (error) {
        if (active && error.name !== 'AbortError') {
          setResult({ status: 'error', data: null, error: error.message })
        }
      } finally {
        if (active && intervalMs != null) timeoutId = window.setTimeout(poll, intervalMs)
      }
    }

    queueMicrotask(() => {
      if (active) setResult({ status: 'loading', data: null, error: null })
    })
    poll()
    return () => {
      active = false
      window.clearTimeout(timeoutId)
      controller?.abort()
    }
  }, [request, enabled, intervalMs, refreshKey])

  return result
}
