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
// Same bounding idea as fetchAllPages: a misbehaving cursor must not page forever.
export const MAX_PROGRESS_PAGES_PER_CYCLE = 50

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

    let seenCursors = new Set<string>()
    const schedulePoll = (): void => {
      clearPollTimer()
      timerRef.current = setTimeout(() => {
        timerRef.current = null
        void beginFetch()
      }, SCENARIO_RUN_POLL_INTERVAL_MS)
    }
    const fetchPage = async (since: string | null, depth: number = 0): Promise<void> => {
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
          const nextCursor = page.next_cursor
          const cursorAdvances =
            nextCursor != null && nextCursor !== since && !seenCursors.has(nextCursor)
          if (cursorAdvances && depth < MAX_PROGRESS_PAGES_PER_CYCLE) {
            seenCursors.add(nextCursor!)
            await fetchPage(nextCursor!, depth + 1)
            return
          }
          if (cursorAdvances) {
            // The page budget ran out while pages remain. Schedule another bounded
            // cycle from the saved cursor so the tail of a long or finished run is
            // still loaded; only a page without has_more ends polling for good.
            schedulePoll()
            return
          }
          // The cursor did not advance, so refetching would request the same page
          // forever (the same guard fetchAllPages uses). Schedule the next bounded
          // poll instead: a finished run only stops after a page without has_more,
          // and the next response may carry a valid cursor again.
          schedulePoll()
          return
        }
        if (isTerminalRunState(page.run.status)) {
          pollingStoppedRef.current = true
          return
        }
        schedulePoll()
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

    const beginFetch = (): Promise<void> => {
      // Each cycle gets a fresh cursor budget, so a long run paged to the limit
      // can continue from the saved cursor on the next scheduled cycle.
      seenCursors = new Set<string>()
      return fetchPage(cursorRef.current)
    }

    if (!scenarioResultId.trim()) {
      dispatch({
        type: 'request-failed',
        message: 'The scenario run ID in this URL is missing or invalid.',
        notFound: true,
      })
    } else {
      void beginFetch()
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
    // Catch up after cancellation or resume, retaining the cursor and finished results.
    pollingStoppedRef.current = true
    abortControllerRef.current?.abort()
    setRetryEpoch((epoch: number) => epoch + 1)
  }, [])

  return { state, retry, applyRunSummary }
}
