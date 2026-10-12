import { useCallback, useEffect, useState } from 'react'

import { useRuntime } from '@/hooks/useRuntime'
import { datasetsApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { DatasetInfo } from '@/types'

interface LoadedDatasetsState {
  readonly datasets: DatasetInfo[]
  readonly loading: boolean
  readonly error: string | null
  readonly retry: () => void
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

  // Reset before paint so a selection key is not matched against the previous runtime's catalog.
  if (activeGeneration !== generation) {
    setActiveGeneration(generation)
    setLoading(true)
    setDatasets([])
    setError(null)
  }

  useEffect(() => {
    const controller = new AbortController()

    const load = async (): Promise<void> => {
      try {
        const response = await datasetsApi.listDatasets({ loaded_only: true }, controller.signal)
        if (controller.signal.aborted) return
        setDatasets(response.items)
        setError(null)
      } catch (err: unknown) {
        if (controller.signal.aborted) return
        setDatasets([])
        setError(toApiError(err).detail)
      } finally {
        if (!controller.signal.aborted) setLoading(false)
      }
    }

    void load()
    return () => {
      controller.abort()
    }
  }, [generation, refetchCount])

  const retry = useCallback(() => {
    setLoading(true)
    setError(null)
    setRefetchCount((count) => count + 1)
  }, [])

  return { datasets, loading, error, retry }
}
