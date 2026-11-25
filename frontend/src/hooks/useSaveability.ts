import { useEffect, useMemo, useRef, useState } from 'react'

import type { SaveabilityResponse } from '@/types'

export const SAVEABILITY_DEBOUNCE_MS = 300

interface SaveabilityAnswer {
  readonly requestKey: string
  readonly reason: string | null
}

/**
 * Ask the backend, debounced, whether a request passes its save checks.
 *
 * Returns why it can't be saved, null when the save checks pass, or undefined until the backend answers for the current
 * request. An answer is dropped once its request changes or goes away, so a request that comes back, such as the same
 * values in a reopened dialog, waits for a fresh answer. Failed checks give no answer, since saving reports its own
 * refusal.
 */
export function useSaveability<Request>(
  request: Request | null,
  check: (request: Request, signal: AbortSignal) => Promise<SaveabilityResponse>,
): string | null | undefined {
  // A request can hold an uploaded file's data, so it's only serialized again when it changes.
  const requestKey = useMemo(() => (request === null ? null : JSON.stringify(request)), [request])
  const [answer, setAnswer] = useState<SaveabilityAnswer | null>(null)
  if (answer !== null && answer.requestKey !== requestKey) setAnswer(null)
  // Checks follow the request key, so an identical request rebuilt by a render isn't checked again.
  const latest = useRef({ request, check })
  useEffect(() => {
    latest.current = { request, check }
  })

  useEffect(() => {
    const { request: current, check: checkCurrent } = latest.current
    if (requestKey === null || current === null) return
    const controller = new AbortController()
    const timer = window.setTimeout(() => {
      checkCurrent(current, controller.signal)
        .then((response) => {
          if (!controller.signal.aborted) setAnswer({ requestKey, reason: response.not_saveable_reason })
        })
        .catch(() => { /* Saving reports why, if the check couldn't. */ })
    }, SAVEABILITY_DEBOUNCE_MS)
    return () => {
      window.clearTimeout(timer)
      controller.abort()
    }
  }, [requestKey])

  return answer?.requestKey === requestKey ? answer.reason : undefined
}
