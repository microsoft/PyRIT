import { useCallback, useEffect, useRef, useState } from 'react'

import { useRuntime } from '@/hooks/useRuntime'
import { useAutoRefresh } from '@/hooks/useAutoRefresh'
import { datasetsApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { DatasetInfo } from '@/types'

interface LoadedDatasetsState {
  readonly datasets: DatasetInfo[]
  readonly loading: boolean
  readonly error: string | null
  /** Reloads catalog metadata without hiding a page that already has data. */
  readonly refresh: () => void
}

/**
 * Loads the memory-backed dataset catalog. The selection key is not a parameter:
 * cards and the selection route look up keys in this response.
 */
export function useLoadedDatasets(): LoadedDatasetsState {
  const { generation } = useRuntime()
  const [datasets, setDatasets] = useState<DatasetInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [refetchCount, setRefetchCount] = useState(0)
  const [activeGeneration, setActiveGeneration] = useState(generation)
  const pending = useRef(false)

  // Reset before paint so a selection key is not matched against the previous runtime's catalog.
  if (activeGeneration !== generation) {
    setActiveGeneration(generation)
    setLoading(true)
    setDatasets([])
    setError(null)
  }

  useEffect(() => {
    const controller = new AbortController()
    pending.current = true

    const load = async (): Promise<void> => {
      try {
        const response = await datasetsApi.listDatasets({ loaded_only: true }, controller.signal)
        if (controller.signal.aborted) return
        setDatasets(response.items)
        setError(null)
      } catch (err: unknown) {
        if (controller.signal.aborted) return
        setError(toApiError(err).detail)
      } finally {
        if (!controller.signal.aborted) {
          setLoading(false)
          pending.current = false
        }
      }
    }

    void load()
    return () => {
      controller.abort()
      pending.current = false
    }
  }, [generation, refetchCount])

  const refresh = useCallback(() => {
    if (pending.current) return
    setRefetchCount((count) => count + 1)
  }, [])
  useAutoRefresh(refresh, !loading)

  return { datasets, loading, error, refresh }
}
