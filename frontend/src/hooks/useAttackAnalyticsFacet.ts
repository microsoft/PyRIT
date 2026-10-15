import { useEffect, useEffectEvent, useMemo, useRef, useState } from 'react'

import { analyticsApi } from '@/services/api'
import { retryAfterMilliseconds, toApiError } from '@/services/errors'
import type { AttackAnalyticsDimension, AttackAnalyticsFacetQuery, AttackAnalyticsFacets, AttackAnalyticsFilters } from '@/types'
import { ANALYTICS_DEBOUNCE_MS, analyticsDimensionKey, analyticsFiltersKey } from '@/utils/attackAnalytics'

import { useRuntime } from './useRuntime'

const FACET_PAGE_SIZE = 50

interface FacetRequestIdentity {
  readonly key: string
}

interface FacetState {
  readonly request: FacetRequestIdentity
  readonly data: AttackAnalyticsFacets | null
  readonly error: string | null
}

/**
 * Searches one opened facet; unmounting cancels reads and discards its options.
 * The server omits this dimension's own predicates when finding alternatives,
 * while retaining other dimensions, outcomes, and time bounds. Send the complete
 * filters: self-filter exclusion belongs to the SDK, not to the editor's state.
 */
export function useAttackAnalyticsFacet(
  dimension: AttackAnalyticsDimension,
  filters: AttackAnalyticsFilters,
  refreshVersion: number,
) {
  const runtime = useRuntime()
  const [search, setSearch] = useState('')
  const [paging, setPaging] = useState<{ scope: { key: string } | null; offset: number }>({ scope: null, offset: 0 })
  const [retryVersion, setRetryVersion] = useState(0)
  const [settled, setSettled] = useState<FacetState | null>(null)
  const retryAt = useRef(0)
  const scope = JSON.stringify([
    analyticsDimensionKey(dimension), analyticsFiltersKey(filters), search, refreshVersion, runtime.generation, runtime.ready,
  ])
  const scopeIdentity = useMemo(() => ({ key: scope }), [scope])
  // Search/cohort/Reload changes start at the first page; Retry keeps the offset.
  const offset = paging.scope === scopeIdentity ? paging.offset : 0
  const query: AttackAnalyticsFacetQuery = { dimension, filters, search, offset, limit: FACET_PAGE_SIZE }
  const key = JSON.stringify([scope, offset, retryVersion])
  // Returning to an earlier search is a new read, not a revival of its old options.
  const request = useMemo(() => ({ key }), [key])
  const current = settled?.request === request ? settled : null
  const captureQuery = useEffectEvent((): AttackAnalyticsFacetQuery => query)

  useEffect(() => {
    if (!runtime.ready) return
    // As with reports, only a changed serialized request restarts the debounce.
    const payload = captureQuery()
    const controller = new AbortController()
    const timer = setTimeout(() => {
      analyticsApi.facets(payload, controller.signal)
        .then((data: AttackAnalyticsFacets) => {
          if (!controller.signal.aborted) setSettled({ request, data, error: null })
        })
        .catch((reason: unknown) => {
          if (!controller.signal.aborted) {
            retryAt.current = Date.now() + retryAfterMilliseconds(reason)
            setSettled({ request, data: null, error: toApiError(reason).detail })
          }
        })
    }, Math.max(ANALYTICS_DEBOUNCE_MS, retryAt.current - Date.now()))
    return () => {
      clearTimeout(timer)
      controller.abort()
    }
  }, [request, runtime.ready])

  return {
    search,
    offset,
    items: current?.data?.items ?? [],
    loading: runtime.ready && current === null,
    ready: runtime.ready,
    error: current?.error ?? null,
    hasMore: current?.data?.has_more ?? false,
    setSearch: (value: string): void => { setSearch(value) },
    firstPage: (): void => { setPaging({ scope: scopeIdentity, offset: 0 }) },
    nextPage: (): void => {
      if (current?.data?.has_more && current.data.next_offset !== null) {
        setPaging({ scope: scopeIdentity, offset: current.data.next_offset })
      }
    },
    retry: (): void => { setRetryVersion((version: number) => version + 1) },
  }
}
