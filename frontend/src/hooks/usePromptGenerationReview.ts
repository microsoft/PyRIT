import { useCallback, useEffect, useState } from 'react'

import { promptGenerationsApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { PromptGenerationEstimate, PromptGenerationReview } from '@/types'

export interface GenerationResourceState<T> {
  /** The latest loaded value, kept while a reload runs. */
  readonly value: T | null
  /** Why the latest load failed, or null. */
  readonly error: string | null
  /** The HTTP status of the latest failed load, such as 409 when the generation moved on, or null. */
  readonly errorStatus: number | null
  /** True until the latest load settles. */
  readonly loading: boolean
  /** Counts reloads, so a view can start over from freshly loaded data. */
  readonly attempt: number
  readonly reload: () => void
}

interface Loaded<T> {
  readonly key: string
  readonly value: T | null
  readonly error: string | null
  readonly errorStatus: number | null
}

function loadReview(generationId: string, signal: AbortSignal): Promise<PromptGenerationReview> {
  return promptGenerationsApi.getReview(generationId, signal)
}

function loadEstimate(generationId: string, signal: AbortSignal): Promise<PromptGenerationEstimate> {
  return promptGenerationsApi.getEstimate(generationId, signal)
}

function useGenerationResource<T>(
  generationId: string,
  load: (generationId: string, signal: AbortSignal) => Promise<T>,
): GenerationResourceState<T> {
  const [attempt, setAttempt] = useState(0)
  const [loaded, setLoaded] = useState<Loaded<T> | null>(null)
  const key = JSON.stringify([generationId, attempt])

  useEffect(() => {
    const controller = new AbortController()
    const loadAsync = async (): Promise<void> => {
      try {
        const value = await load(generationId, controller.signal)
        if (!controller.signal.aborted) setLoaded({ key, value, error: null, errorStatus: null })
      } catch (error: unknown) {
        if (controller.signal.aborted) return
        const apiError = toApiError(error)
        setLoaded({ key, value: null, error: apiError.detail, errorStatus: apiError.status })
      }
    }
    void loadAsync()
    return () => { controller.abort() }
  }, [generationId, key, load])

  const reload = useCallback((): void => {
    setAttempt((value) => value + 1)
  }, [])

  const current = loaded?.key === key ? loaded : null
  return {
    value: current ? current.value : loaded?.value ?? null,
    error: current?.error ?? null,
    errorStatus: current?.errorStatus ?? null,
    loading: current === null,
    attempt,
    reload,
  }
}

/** Loads a paused generation's review and its estimate of the remaining work; each can be reloaded. */
export function usePromptGenerationReview(generationId: string): {
  readonly review: GenerationResourceState<PromptGenerationReview>
  readonly estimate: GenerationResourceState<PromptGenerationEstimate>
} {
  return {
    review: useGenerationResource(generationId, loadReview),
    estimate: useGenerationResource(generationId, loadEstimate),
  }
}
