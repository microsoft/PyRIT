import { useEffect, useRef, useState } from 'react'

import { operationsApi } from '@/services/api'
import type { AttackAnalyticsFilters, OperationListItem, OperationListResponse } from '@/types'

import { useRuntime } from './useRuntime'

function operationIds(filters: AttackAnalyticsFilters): string[] {
  const ids = new Set<string>()
  for (const predicate of filters.dimensions) {
    if (predicate.dimension.name !== 'operation') continue
    for (const value of predicate.values) {
      if (value.kind === 'value') ids.add(value.value)
    }
  }
  return [...ids]
}

/**
 * Names the saved Operation IDs in filters, such as those restored from a shared
 * link, for display only. Each ID is looked up at most once per mount. IDs missing
 * from the Operations list, or whose lookup failed, stay unnamed, so callers show the ID.
 */
export function useAnalyticsOperationLabels(filters: AttackAnalyticsFilters): Readonly<Record<string, string>> {
  const runtime = useRuntime()
  const [labels, setLabels] = useState<Readonly<Record<string, string>>>({})
  const requested = useRef(new Set<string>())
  const mounted = useRef(false)

  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false }
  }, [])

  useEffect(() => {
    if (!runtime.ready) return
    const ids = operationIds(filters).filter((id: string) => !requested.current.has(id))
    if (ids.length === 0) return
    for (const id of ids) requested.current.add(id)
    operationsApi.list()
      .then((response: OperationListResponse) => {
        if (!mounted.current) return
        const names = response.items
          .filter((operation: OperationListItem) => ids.includes(operation.id.toLowerCase()))
          .map((operation: OperationListItem): [string, string] => [operation.id.toLowerCase(), operation.name])
        setLabels((previous: Readonly<Record<string, string>>) => ({ ...previous, ...Object.fromEntries(names) }))
      })
      .catch(() => {
        // Names are presentation only: the chip keeps showing the ID and the query is unaffected.
      })
  }, [filters, runtime.ready])

  return labels
}
