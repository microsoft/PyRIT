import { useEffect, useEffectEvent, useMemo, useRef, useState } from 'react'

import { analyticsApi } from '@/services/api'
import { retryAfterMilliseconds, toApiError } from '@/services/errors'
import type { AttackAnalyticsDimension, AttackAnalyticsFacetQuery, AttackAnalyticsFacets, AttackAnalyticsFilters } from '@/types'
import { ANALYTICS_DEBOUNCE_MS, analyticsDimensionKey, analyticsFiltersKey } from '@/utils/attackAnalytics'

import { useRuntime } from './useRuntime'

const FACET_PAGE_SIZE = 50

interface FacetRequestIdentity {
  readonly key: string
  readonly pageKey: string
  readonly scope: { readonly key: string }
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
  const [settled, setSettled] = useState<FacetState | null>(null)
  const retryAt = useRef(0)
  const scope = JSON.stringify([
    analyticsDimensionKey(dimension), analyticsFiltersKey(filters), search, runtime.generation, runtime.ready,
  ])
  const scopeIdentity = useMemo(() => ({ key: scope }), [scope])
  // Only a different search or cohort resets the page.
  const offset = paging.scope === scopeIdentity ? paging.offset : 0
  const query: AttackAnalyticsFacetQuery = { dimension, filters, search, offset, limit: FACET_PAGE_SIZE }
  const pageKey = JSON.stringify([scope, offset])
  const key = JSON.stringify([pageKey, refreshVersion])
  // Returning to an earlier search is a new read, not a revival of its old options.
  const request = useMemo(() => ({ key, pageKey, scope: scopeIdentity }), [key, pageKey, scopeIdentity])
  const current = settled?.request === request ? settled : null
  const retained = settled?.request.scope === request.scope && settled.request.pageKey === request.pageKey ? settled : null
  const data = current?.data ?? retained?.data ?? null
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
            setSettled((previous) => ({
              request,
              data: previous?.request.scope === request.scope && previous.request.pageKey === request.pageKey
                ? previous.data : null,
              error: toApiError(reason).detail,
            }))
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
    items: data?.items ?? [],
    loading: runtime.ready && current === null && data === null,
    ready: runtime.ready,
    error: current?.error ?? retained?.error ?? null,
    hasMore: data?.has_more ?? false,
    setSearch: (value: string): void => { setSearch(value) },
    firstPage: (): void => { setPaging({ scope: scopeIdentity, offset: 0 }) },
    nextPage: (): void => {
      if (data?.has_more && data.next_offset !== null) {
        setPaging({ scope: scopeIdentity, offset: data.next_offset })
      }
    },
  }
}
