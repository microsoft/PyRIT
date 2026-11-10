import { useCallback, useEffect, useState } from 'react'

import { promptGenerationsApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { PromptGenerationCreateRequest, PromptGenerationPreflightResponse } from '@/types'

export const PREFLIGHT_DEBOUNCE_MS = 500

export type PreflightStatus = 'idle' | 'checking' | 'ready' | 'error'

export interface PreflightState {
  readonly status: PreflightStatus
  /** One result per request, from the latest finished check. */
  readonly results: PromptGenerationPreflightResponse[]
  readonly error: string | null
  /** Checks the same requests again, such as after a failed check. */
  readonly retry: () => void
}

interface PreflightSnapshot {
  readonly key: string
  readonly attempt: number
  readonly results: PromptGenerationPreflightResponse[]
  readonly error: string | null
}

/**
 * Validates and estimates requests after the inputs settle. No target is called.
 * Pass null while the form has client-side errors so nothing is sent.
 */
export function usePromptGenerationPreflight(
  requests: readonly PromptGenerationCreateRequest[] | null,
): PreflightState {
  const key = requests === null || requests.length === 0 ? null : JSON.stringify(requests)
  const [snapshot, setSnapshot] = useState<PreflightSnapshot | null>(null)
  const [attempt, setAttempt] = useState(0)
  const retry = useCallback((): void => { setAttempt((value) => value + 1) }, [])

  useEffect(() => {
    if (key === null) return
    const pending = JSON.parse(key) as PromptGenerationCreateRequest[]
    const controller = new AbortController()
    const timer = setTimeout(() => {
      const checkAsync = async (): Promise<void> => {
        try {
          const results = await Promise.all(
            pending.map((request) => promptGenerationsApi.preflight(request, controller.signal)),
          )
          if (!controller.signal.aborted) setSnapshot({ key, attempt, results, error: null })
        } catch (error: unknown) {
          if (!controller.signal.aborted) setSnapshot({ key, attempt, results: [], error: toApiError(error).detail })
        }
      }
      void checkAsync()
    }, PREFLIGHT_DEBOUNCE_MS)
    return () => {
      clearTimeout(timer)
      controller.abort()
    }
  }, [key, attempt])

  if (key === null) return { status: 'idle', results: [], error: null, retry }
  if (snapshot?.key !== key || snapshot.attempt !== attempt) {
    return { status: 'checking', results: snapshot?.results ?? [], error: null, retry }
  }
  return { status: snapshot.error ? 'error' : 'ready', results: snapshot.results, error: snapshot.error, retry }
}
