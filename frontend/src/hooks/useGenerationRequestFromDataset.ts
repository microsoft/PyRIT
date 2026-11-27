import { useCallback, useEffect, useState } from 'react'

import { promptGenerationsApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { PromptGenerationRequestFromDataset } from '@/types'

export interface GenerationRequestFromDatasetState {
  readonly loading: boolean
  /** The generator request that made the dataset, or null. */
  readonly source: PromptGenerationRequestFromDataset | null
  /** True when no known generator made the dataset. */
  readonly notFound: boolean
  /** Why the lookup failed, other than not found; it can be retried. */
  readonly error: string | null
  readonly retry: () => void
}

interface Loaded {
  readonly key: string
  readonly source: PromptGenerationRequestFromDataset | null
  readonly notFound: boolean
  readonly error: string | null
}

/** Looks up the generator request that made a stored dataset; pass null to skip the lookup. */
export function useGenerationRequestFromDataset(selectionKey: string | null): GenerationRequestFromDatasetState {
  const [attempt, setAttempt] = useState(0)
  const [loaded, setLoaded] = useState<Loaded | null>(null)
  const key = JSON.stringify([selectionKey, attempt])

  useEffect(() => {
    if (selectionKey === null) return
    const controller = new AbortController()
    const loadAsync = async (): Promise<void> => {
      try {
        const source = await promptGenerationsApi.getRequestFromDataset(selectionKey, controller.signal)
        if (!controller.signal.aborted) setLoaded({ key, source, notFound: false, error: null })
      } catch (error: unknown) {
        if (controller.signal.aborted) return
        const apiError = toApiError(error)
        // Only a 404 means no known generator made the dataset; other failures can be retried.
        const notFound = apiError.status === 404
        setLoaded({ key, source: null, notFound, error: notFound ? null : apiError.detail })
      }
    }
    void loadAsync()
    return () => { controller.abort() }
  }, [selectionKey, key])

  const retry = useCallback((): void => {
    setAttempt((value) => value + 1)
  }, [])

  const current = selectionKey !== null && loaded?.key === key ? loaded : null
  return {
    loading: selectionKey !== null && current === null,
    source: current?.source ?? null,
    notFound: current?.notFound ?? false,
    error: current?.error ?? null,
    retry,
  }
}
