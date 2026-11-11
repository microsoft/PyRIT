import { useCallback, useEffect, useState } from 'react'

import type { PromptGenerationFormSeed } from '@/components/PromptGeneration/generatorUi'
import { nextDatasetName } from '@/components/PromptGeneration/promptGenerationPresentation'
import type { NewGenerationParams } from '@/components/PromptGeneration/promptGenerationRoutes'
import { promptGenerationsApi } from '@/services/api'
import { toApiError } from '@/services/errors'

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

interface LoadedSeed {
  readonly attempt: number
  readonly error: string | null
  readonly generator?: string
  readonly seed: PromptGenerationFormSeed
  readonly sourceDescription?: string
}

async function loadSeedAsync(params: NewGenerationParams, signal: AbortSignal): Promise<Omit<LoadedSeed, 'attempt'>> {
  if (params.from) {
    const source = await promptGenerationsApi.get(params.from, signal)
    return {
      error: null,
      generator: source.generator,
      seed: {
        request: source.request,
        targets: source.targets,
        pauseForReview: source.pause_for_review,
        datasetName: nextDatasetName(source.dataset_name),
      },
      sourceDescription: `Pre-filled from the generation of "${source.dataset_name}". `
        + 'The new generation saves a new dataset.',
    }
  }
  const source = await promptGenerationsApi.getRequestFromDataset(params.selectionKey ?? '', signal)
  return {
    error: null,
    generator: source.generator,
    seed: { request: source.request, datasetName: nextDatasetName(source.source_dataset_name) },
    sourceDescription: `Pre-filled from the policy that generated "${source.source_dataset_name}". `
      + 'The new generation saves a new dataset.',
  }
}

/** Resolves the new-generation form's initial values from an earlier generation or a stored dataset. */
export function usePromptGenerationSeed(params: NewGenerationParams): PromptGenerationSeedState {
  const { from, selectionKey, template, suite } = params
  const needsLoad = Boolean(from || selectionKey)
  const [attempt, setAttempt] = useState(0)
  const [loaded, setLoaded] = useState<LoadedSeed | null>(null)

  useEffect(() => {
    if (!needsLoad) return
    const controller = new AbortController()
    const runAsync = async (): Promise<void> => {
      try {
        const result = await loadSeedAsync({ from, selectionKey }, controller.signal)
        if (!controller.signal.aborted) setLoaded({ ...result, attempt })
      } catch (error: unknown) {
        if (controller.signal.aborted) return
        const apiError = toApiError(error)
        setLoaded({
          attempt,
          error: apiError.status === 404
            ? (from ? 'That generation no longer exists.' : 'That dataset was not made by a known generator.')
            : apiError.detail,
          seed: {},
        })
      }
    }
    void runAsync()
    return () => { controller.abort() }
  }, [needsLoad, from, selectionKey, attempt])

  const retry = useCallback((): void => {
    setAttempt((value) => value + 1)
  }, [])

  const templates = { template, suite }
  if (!needsLoad) return { loading: false, error: null, seed: templates, retry }
  const current = loaded?.attempt === attempt ? loaded : null
  if (current === null) return { loading: true, error: null, seed: templates, retry }
  return {
    loading: false,
    error: current.error,
    generator: current.generator,
    seed: { ...current.seed, ...templates },
    sourceDescription: current.sourceDescription,
    retry,
  }
}
