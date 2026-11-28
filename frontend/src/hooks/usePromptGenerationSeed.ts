import { useCallback, useEffect, useState } from 'react'

import type { PromptGenerationFormSeed } from '@/components/PromptGeneration/generatorUi'
import { nextDatasetName } from '@/components/PromptGeneration/promptGenerationPresentation'
import type { NewGenerationParams } from '@/components/PromptGeneration/promptGenerationRoutes'
import { promptGenerationsApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { PromptGenerationDetail } from '@/types'

import { useGenerationRequestFromDataset } from './useGenerationRequestFromDataset'

export interface PromptGenerationSeedState {
  readonly loading: boolean
  readonly error: string | null
  /** The generator that made the source, when the seed came from one. */
  readonly generator?: string
  readonly seed: PromptGenerationFormSeed
  /** Describes where the seed came from, for the user. */
  readonly sourceDescription?: string
  readonly retry: () => void
}

interface LoadedGeneration {
  readonly attempt: number
  readonly source: PromptGenerationDetail | null
  readonly error: string | null
}

/** Resolves the new-generation form's initial values from an earlier generation or a stored dataset. */
export function usePromptGenerationSeed(params: NewGenerationParams): PromptGenerationSeedState {
  const { from, selectionKey, template, suite } = params
  const dataset = useGenerationRequestFromDataset(!from && selectionKey ? selectionKey : null)
  const [attempt, setAttempt] = useState(0)
  const [loaded, setLoaded] = useState<LoadedGeneration | null>(null)

  useEffect(() => {
    if (!from) return
    const controller = new AbortController()
    const runAsync = async (): Promise<void> => {
      try {
        const source = await promptGenerationsApi.get(from, controller.signal)
        if (!controller.signal.aborted) setLoaded({ attempt, source, error: null })
      } catch (error: unknown) {
        if (controller.signal.aborted) return
        const apiError = toApiError(error)
        setLoaded({
          attempt,
          source: null,
          error: apiError.status === 404 ? 'That generation no longer exists.' : apiError.detail,
        })
      }
    }
    void runAsync()
    return () => { controller.abort() }
  }, [from, attempt])

  const retryGeneration = useCallback((): void => {
    setAttempt((value) => value + 1)
  }, [])

  const templates: PromptGenerationFormSeed = {
    ...(template !== undefined ? { template } : {}),
    ...(suite !== undefined ? { suite } : {}),
  }
  if (from) {
    const current = loaded?.attempt === attempt ? loaded : null
    if (current === null) return { loading: true, error: null, seed: templates, retry: retryGeneration }
    if (current.source === null) return { loading: false, error: current.error, seed: templates, retry: retryGeneration }
    const source = current.source
    return {
      loading: false,
      error: null,
      generator: source.generator,
      seed: {
        request: source.request,
        targets: source.targets,
        pauseForReview: source.pause_for_review,
        datasetName: nextDatasetName(source.dataset_name),
        ...templates,
      },
      sourceDescription: `Pre-filled from the generation of "${source.dataset_name}". `
        + 'The new generation saves a new dataset.',
      retry: retryGeneration,
    }
  }
  if (!selectionKey) return { loading: false, error: null, seed: templates, retry: retryGeneration }
  if (dataset.loading) return { loading: true, error: null, seed: templates, retry: dataset.retry }
  if (dataset.source === null) {
    return {
      loading: false,
      error: dataset.notFound ? 'That dataset was not made by a known generator.' : dataset.error,
      seed: templates,
      retry: dataset.retry,
    }
  }
  const source = dataset.source
  return {
    loading: false,
    error: null,
    generator: source.generator,
    seed: { request: source.request, datasetName: nextDatasetName(source.source_dataset_name), ...templates },
    sourceDescription: `Pre-filled from the policy that generated "${source.source_dataset_name}". `
      + 'The new generation saves a new dataset.',
    retry: dataset.retry,
  }
}
