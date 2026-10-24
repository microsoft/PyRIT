import { useEffect, useRef, useState } from 'react'

import { operationsApi } from '@/services/api'
import type { AttackAnalyticsFilters, OperationListItem, OperationListResponse } from '@/types'
import { analyticsLabelEpoch } from '@/utils/attackAnalytics'

import { useRuntime } from './useRuntime'

interface RequestedOperationIds {
  readonly epoch: string
  readonly ids: Set<string>
}

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
 * link, for display only. Each ID is looked up at most once per label epoch, which
 * Reload (`refreshVersion`) or a runtime change restarts for every ID in filters.
 * A lookup replaces the names of the IDs it requested; IDs missing from the Operations
 * list lose their name, so callers show the ID. Names stay until a newer lookup succeeds.
 */
export function useAnalyticsOperationLabels(
  filters: AttackAnalyticsFilters,
  refreshVersion: number,
): Readonly<Record<string, string>> {
  const runtime = useRuntime()
  const epoch = analyticsLabelEpoch(refreshVersion, runtime.generation)
  const [labels, setLabels] = useState<Readonly<Record<string, string>>>({})
  const requested = useRef<RequestedOperationIds | null>(null)
  // Null after unmount. A lookup from an earlier epoch must not replace newer names.
  const activeEpoch = useRef<string | null>(null)

  useEffect(() => {
    activeEpoch.current = epoch
    return () => { activeEpoch.current = null }
  }, [epoch])

  useEffect(() => {
    if (!runtime.ready) return
    if (requested.current?.epoch !== epoch) requested.current = { epoch, ids: new Set() }
    const requestedIds = requested.current.ids
    const ids = operationIds(filters).filter((id: string) => !requestedIds.has(id))
    if (ids.length === 0) return
    for (const id of ids) requestedIds.add(id)
    operationsApi.list()
      .then((response: OperationListResponse) => {
        if (activeEpoch.current !== epoch) return
        const names = response.items
          .filter((operation: OperationListItem) => ids.includes(operation.id.toLowerCase()))
          .map((operation: OperationListItem): [string, string] => [operation.id.toLowerCase(), operation.name])
        setLabels((previous: Readonly<Record<string, string>>) => ({
          ...Object.fromEntries(Object.entries(previous).filter(([id]: [string, string]) => !ids.includes(id))),
          ...Object.fromEntries(names),
        }))
      })
      .catch(() => {
        // Names are presentation only: the chip keeps showing its last name or the ID, and the query is unaffected.
      })
  }, [filters, runtime.ready, epoch])

  return labels
}
