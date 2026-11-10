import { useEffect, useState } from 'react'

import { useRuntime } from '@/hooks/useRuntime'
import { datasetsApi } from '@/services/api'
import { toApiError } from '@/services/errors'

export interface RequestedDatasetsState {
  readonly loading: boolean
  /** Requested names the dataset catalog knows, in request order. */
  readonly known: string[]
  /** Requested names the catalog does not know; they are not preselected. */
  readonly unknown: string[]
  /** Set when the catalog could not be read, so no requested name was checked. */
  readonly error: string | null
}

interface CatalogSnapshot {
  readonly key: string
  readonly names: string[] | null
  readonly error: string | null
}

const NO_REQUEST: RequestedDatasetsState = { loading: false, known: [], unknown: [], error: null }

/** Checks dataset names from a link against the dataset catalog before they preselect anything. */
export function useRequestedDatasets(names: readonly string[]): RequestedDatasetsState {
  const { ready } = useRuntime()
  const key = JSON.stringify(names)
  const [snapshot, setSnapshot] = useState<CatalogSnapshot | null>(null)
  const requested = names.length > 0

  useEffect(() => {
    if (!requested || !ready) return
    const controller = new AbortController()
    const loadAsync = async (): Promise<void> => {
      try {
        const response = await datasetsApi.listDatasets({ loaded_only: false }, controller.signal)
        if (controller.signal.aborted) return
        setSnapshot({
          key,
          names: response.items.filter((item) => item.selection_key !== 'dataset:unnamed').map((item) => item.name),
          error: null,
        })
      } catch (error: unknown) {
        if (!controller.signal.aborted) setSnapshot({ key, names: null, error: toApiError(error).detail })
      }
    }
    void loadAsync()
    return () => { controller.abort() }
  }, [key, requested, ready])

  if (!requested) return NO_REQUEST
  const current = snapshot?.key === key ? snapshot : null
  if (current === null) return { loading: true, known: [], unknown: [], error: null }
  if (current.names === null) return { loading: false, known: [], unknown: [...names], error: current.error }
  const available = new Set(current.names)
  return {
    loading: false,
    known: names.filter((name) => available.has(name)),
    unknown: names.filter((name) => !available.has(name)),
    error: null,
  }
}
