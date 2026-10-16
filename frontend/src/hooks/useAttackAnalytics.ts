import { useEffect, useEffectEvent, useMemo, useRef, useState } from 'react'

import { analyticsApi } from '@/services/api'
import { retryAfterMilliseconds, toApiError } from '@/services/errors'
import type { AttackAnalyticsQuery, AttackAnalyticsReport, AttackAnalyticsResults } from '@/types'
import { ANALYTICS_DEBOUNCE_MS, ANALYTICS_PAGE_SIZE, analyticsQueryKey } from '@/utils/attackAnalytics'

import { useRuntime } from './useRuntime'

interface ReportRequestIdentity {
  readonly scopeKey: string
  readonly key: string
}

interface ReportState {
  readonly request: ReportRequestIdentity
  readonly report: AttackAnalyticsReport | null
  readonly groupOffset: number
  readonly error: string | null
}

function waitForRetry(milliseconds: number, signal: AbortSignal): Promise<void> {
  if (milliseconds <= 0 || signal.aborted) return Promise.resolve()
  return new Promise<void>((resolve: () => void) => {
    const finish = (): void => {
      clearTimeout(timer)
      signal.removeEventListener('abort', finish)
      resolve()
    }
    const timer = setTimeout(finish, milliseconds)
    signal.addEventListener('abort', finish, { once: true })
  })
}

interface ResultsState {
  readonly request: ReportRequestIdentity
  readonly results: AttackAnalyticsResults
  readonly page: number
  readonly requestedPage: number
  readonly cursor: string | null
  readonly loading: boolean
  readonly error: string | null
  readonly invalidCursor: boolean
}

/**
 * Owns report and result-page reads, never aggregation. A null query disables reads
 * (including for invalid shared URLs). Equal serialized payloads are one request;
 * Reload explicitly starts another generation. There is no polling.
 *
 * Keep a failed same-scope refresh's report and timestamps intact. Filters or axes
 * changing invalidate it immediately, so old results cannot describe a new cohort.
 */
export function useAttackAnalytics(query: AttackAnalyticsQuery | null) {
  const runtime = useRuntime()
  const enabledQuery = runtime.ready ? query : null
  const [refreshVersion, setRefreshVersion] = useState(0)
  const [settled, setSettled] = useState<ReportState | null>(null)
  const [resultState, setResultState] = useState<ResultsState | null>(null)
  const resultController = useRef<AbortController | null>(null)
  const reportRetryAt = useRef(0)
  const resultsRetryAt = useRef(0)
  // Group pages share a report scope, but each page/refresh is a distinct request.
  const scopeKey = JSON.stringify([
    runtime.generation,
    analyticsQueryKey(enabledQuery && { ...enabledQuery, group_offset: undefined, group_limit: undefined }),
  ])
  const requestKey = JSON.stringify([analyticsQueryKey(enabledQuery), refreshVersion, runtime.generation])
  // Equal consecutive keys reuse one identity. A -> B -> A creates a new one:
  // revisiting A must not revive a cancelled/older A request or its result page.
  const request = useMemo(() => ({ scopeKey, key: requestKey }), [scopeKey, requestKey])
  const report = settled?.request.scopeKey === scopeKey ? settled.report : null
  const loading = enabledQuery !== null && settled?.request !== request
  const error = settled?.request === request ? settled.error : null
  // A cancelled page belongs to the previous request even if a failed refresh
  // retains the very same report object. Never revive that page's loading state.
  const currentResults = resultState?.request === request && !loading ? resultState : null
  const results = currentResults?.results ?? report?.results ?? null
  const page = currentResults?.page ?? 0
  const captureQuery = useEffectEvent((): AttackAnalyticsQuery | null => enabledQuery)

  useEffect(() => {
    // The serialized request key owns reactivity, not the caller's object identity.
    // Capture its typed payload once, before debouncing; equal new objects must not
    // restart this effect or cancel an independently loading results page.
    const payload = captureQuery()
    if (payload === null) return
    const controller = new AbortController()
    resultController.current?.abort()
    const timer = setTimeout(() => {
      analyticsApi.query(payload, controller.signal)
        .then((response: AttackAnalyticsReport) => {
          if (controller.signal.aborted) return
          setSettled({ request, report: response, groupOffset: payload.group_offset ?? 0, error: null })
        })
        .catch((reason: unknown) => {
          if (controller.signal.aborted) return
          reportRetryAt.current = Date.now() + retryAfterMilliseconds(reason)
          setSettled((previous: ReportState | null) => ({
            request,
            report: previous?.request.scopeKey === request.scopeKey ? previous.report : null,
            groupOffset: previous?.request.scopeKey === request.scopeKey ? previous.groupOffset : 0,
            error: toApiError(reason).detail,
          }))
        })
    }, Math.max(ANALYTICS_DEBOUNCE_MS, reportRetryAt.current - Date.now()))
    return () => {
      clearTimeout(timer)
      controller.abort()
      resultController.current?.abort()
    }
  }, [request])

  function reload(): void {
    resultController.current?.abort()
    setRefreshVersion((version: number) => version + 1)
  }

  /** Read only rows for the displayed report's filters, leaving its aggregates and timestamp untouched. */
  async function loadResults(cursor: string | null, requestedPage: number): Promise<void> {
    if (!report || !results || loading) return
    resultController.current?.abort()
    const controller = new AbortController()
    resultController.current = controller
    // Keep the displayed page until success; retry uses the requested cursor/page,
    // not the displayed page's next cursor (which could be a different request).
    const requestState: ResultsState = {
      request, results, page, requestedPage, cursor, loading: true, error: null, invalidCursor: false,
    }
    setResultState(requestState)
    try {
      await waitForRetry(resultsRetryAt.current - Date.now(), controller.signal)
      if (controller.signal.aborted) return
      const response = await analyticsApi.results(
        { filters: report.filters, cursor, limit: ANALYTICS_PAGE_SIZE },
        controller.signal,
      )
      if (controller.signal.aborted) return
      setResultState({ ...requestState, results: response, page: requestedPage, loading: false })
    } catch (reason: unknown) {
      if (controller.signal.aborted) return
      const failure = toApiError(reason)
      resultsRetryAt.current = Date.now() + retryAfterMilliseconds(reason)
      setResultState({ ...requestState, loading: false, error: failure.detail, invalidCursor: failure.status === 400 })
    }
  }

  return {
    report,
    reportGroupOffset: settled?.request.scopeKey === scopeKey ? settled.groupOffset : 0,
    loading,
    error,
    refreshVersion,
    reload,
    results,
    page,
    resultsLoading: currentResults?.loading ?? false,
    resultsError: currentResults?.error ?? null,
    resultsCursorInvalid: currentResults?.invalidCursor ?? false,
    nextPage: (): void => {
      if (results?.has_more && results.next_cursor && !currentResults?.loading) {
        void loadResults(results.next_cursor, page + 1)
      }
    },
    firstPage: (): void => { void loadResults(null, 0) },
    retryResults: (): void => {
      if (currentResults) {
        void loadResults(
          currentResults.invalidCursor ? null : currentResults.cursor,
          currentResults.invalidCursor ? 0 : currentResults.requestedPage,
        )
      }
    },
  }
}
