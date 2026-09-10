import { useCallback, useEffect, useReducer, useRef, useState } from 'react'

import { scenariosApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { ScenarioRunSummary } from '@/types'
import {
  INITIAL_SCENARIO_RUN_PROGRESS_STATE,
  isTerminalRunState,
  scenarioRunProgressReducer,
  type ScenarioRunProgressState,
} from '@/utils/scenarioRunProgress'

export const SCENARIO_RUN_POLL_INTERVAL_MS = 2_500
const PROGRESS_PAGE_LIMIT = 500

export interface UseScenarioRunProgressResult {
  readonly state: ScenarioRunProgressState
  readonly retry: () => void
  readonly applyRunSummary: (run: ScenarioRunSummary) => void
}

export function useScenarioRunProgress(scenarioResultId: string): UseScenarioRunProgressResult {
  const [state, dispatch] = useReducer(
    scenarioRunProgressReducer,
    INITIAL_SCENARIO_RUN_PROGRESS_STATE,
  )
  const [retryEpoch, setRetryEpoch] = useState(0)
  const cursorRef = useRef<string | null>(null)
  const cursorScenarioResultIdRef = useRef(scenarioResultId)
  const abortControllerRef = useRef<AbortController | null>(null)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const pollingStoppedRef = useRef(false)
  const retriedRef = useRef(false)

  useEffect(() => {
    if (cursorScenarioResultIdRef.current !== scenarioResultId) {
      cursorScenarioResultIdRef.current = scenarioResultId
      cursorRef.current = null
    }

    let active = true
    pollingStoppedRef.current = false
    retriedRef.current = false

    const clearPollTimer = (): void => {
      if (timerRef.current !== null) {
        clearTimeout(timerRef.current)
        timerRef.current = null
      }
    }

    const fetchPage = async (since: string | null): Promise<void> => {
      if (!active || pollingStoppedRef.current) {
        return
      }
      const controller = new AbortController()
      abortControllerRef.current = controller
      try {
        const page = await scenariosApi.getRunProgress(
          scenarioResultId,
          { since: since ?? undefined, limit: PROGRESS_PAGE_LIMIT },
          controller.signal,
        )
        if (!active || pollingStoppedRef.current) {
          return
        }

        const appliedCursor = page.next_cursor ?? since
        cursorRef.current = appliedCursor
        retriedRef.current = false
        dispatch({ type: 'apply-page', page, fresh: since === null })

        if (page.has_more) {
          await fetchPage(appliedCursor)
          return
        }
        if (isTerminalRunState(page.run.status)) {
          pollingStoppedRef.current = true
          return
        }
        clearPollTimer()
        timerRef.current = setTimeout(() => {
          timerRef.current = null
          void fetchPage(cursorRef.current)
        }, SCENARIO_RUN_POLL_INTERVAL_MS)
      } catch (error: unknown) {
        if (!active || controller.signal.aborted) {
          return
        }
        const apiError = toApiError(error)
        // A live dashboard should survive one blip. Retry once on the same cursor before
        // surfacing the stale banner and handing control back to the user.
        if (apiError.status !== 404 && !retriedRef.current) {
          retriedRef.current = true
          clearPollTimer()
          timerRef.current = setTimeout(() => {
            timerRef.current = null
            void fetchPage(since)
          }, SCENARIO_RUN_POLL_INTERVAL_MS)
          return
        }
        dispatch({
          type: 'request-failed',
          message: apiError.detail,
          notFound: apiError.status === 404,
        })
      }
    }

    if (!scenarioResultId.trim()) {
      dispatch({
        type: 'request-failed',
        message: 'The scenario run ID in this URL is missing or invalid.',
        notFound: true,
      })
    } else {
      void fetchPage(cursorRef.current)
    }

    return () => {
      active = false
      clearPollTimer()
      abortControllerRef.current?.abort()
      abortControllerRef.current = null
    }
  }, [scenarioResultId, retryEpoch])

  const retry = useCallback((): void => {
    dispatch({ type: 'retry' })
    pollingStoppedRef.current = false
    retriedRef.current = false
    setRetryEpoch((epoch) => epoch + 1)
  }, [])

  const applyRunSummary = useCallback((run: ScenarioRunSummary): void => {
    dispatch({ type: 'apply-run-summary', run })
    if (isTerminalRunState(run.status)) {
      pollingStoppedRef.current = false
      setRetryEpoch((epoch) => epoch + 1)
    }
  }, [])

  return { state, retry, applyRunSummary }
}
