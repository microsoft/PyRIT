import { useCallback, useEffect, useRef, useState } from 'react'

import { scenariosApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { ScenarioQueueSnapshot } from '@/types'

export const SCENARIO_QUEUE_POLL_INTERVAL_MS = 2_500

export interface ScenarioQueueState {
  readonly snapshot: ScenarioQueueSnapshot | null
  readonly loading: boolean
  readonly stale: boolean
  readonly error: string | null
}

export interface UseScenarioQueueResult extends ScenarioQueueState {
  readonly retry: () => void
}

export function useScenarioQueue(): UseScenarioQueueResult {
  const [state, setState] = useState<ScenarioQueueState>({
    snapshot: null,
    loading: true,
    stale: false,
    error: null,
  })
  const [retryEpoch, setRetryEpoch] = useState(0)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    let active = true
    const controller = new AbortController()

    const fetchQueueAsync = async (): Promise<void> => {
      if (!active) {
        return
      }
      try {
        const snapshot = await scenariosApi.getQueue(controller.signal)
        if (!active) {
          return
        }
        setState({ snapshot, loading: false, stale: false, error: null })
      } catch (error: unknown) {
        if (!active || controller.signal.aborted) {
          return
        }
        const message = toApiError(error).detail
        setState((previous) => ({
          ...previous,
          loading: false,
          stale: previous.snapshot !== null,
          error: message,
        }))
      } finally {
        if (active) {
          timerRef.current = setTimeout(() => {
            timerRef.current = null
            void fetchQueueAsync()
          }, SCENARIO_QUEUE_POLL_INTERVAL_MS)
        }
      }
    }

    void fetchQueueAsync()
    return () => {
      active = false
      controller.abort()
      if (timerRef.current !== null) {
        clearTimeout(timerRef.current)
        timerRef.current = null
      }
    }
  }, [retryEpoch])

  const retry = useCallback((): void => {
    setState((previous) => ({ ...previous, loading: previous.snapshot === null, stale: false, error: null }))
    setRetryEpoch((epoch) => epoch + 1)
  }, [])

  return { ...state, retry }
}
