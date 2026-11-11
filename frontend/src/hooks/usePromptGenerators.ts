import { useCallback, useEffect, useState } from 'react'

import { useRuntime } from '@/hooks/useRuntime'
import { promptGeneratorsApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { PromptGeneratorDescriptor } from '@/types'

interface GeneratorSnapshot {
  readonly key: string
  readonly generators: PromptGeneratorDescriptor[]
  readonly error: string | null
}

export interface PromptGeneratorsState {
  /** The latest generators, kept while they reload after a runtime change. */
  readonly generators: PromptGeneratorDescriptor[]
  /** True until the first generators load. */
  readonly loading: boolean
  /** True while the generators reload, so forms can stay mounted but shouldn't submit yet. */
  readonly refreshing: boolean
  readonly error: string | null
  readonly retry: () => void
}

/** Loads the registered prompt generators for the current runtime generation, keeping the last list while they reload. */
export function usePromptGenerators(): PromptGeneratorsState {
  const { generation, ready } = useRuntime()
  const [snapshot, setSnapshot] = useState<GeneratorSnapshot | null>(null)
  const [attempt, setAttempt] = useState(0)
  const key = JSON.stringify([generation, attempt])

  useEffect(() => {
    if (!ready) return
    const controller = new AbortController()
    const loadAsync = async (): Promise<void> => {
      try {
        const response = await promptGeneratorsApi.list(controller.signal)
        if (!controller.signal.aborted) setSnapshot({ key, generators: response.items, error: null })
      } catch (error: unknown) {
        if (!controller.signal.aborted) setSnapshot({ key, generators: [], error: toApiError(error).detail })
      }
    }
    void loadAsync()
    return () => { controller.abort() }
  }, [key, ready])

  const retry = useCallback((): void => {
    setAttempt((value) => value + 1)
  }, [])

  const current = snapshot?.key === key ? snapshot : null
  const shown = current ?? (snapshot?.error === null ? snapshot : null)
  return {
    generators: shown?.generators ?? [],
    loading: shown === null,
    refreshing: current === null && shown !== null,
    error: current?.error ?? null,
    retry,
  }
}
