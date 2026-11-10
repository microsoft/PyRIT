import { useCallback, useEffect, useState } from 'react'

import { useRuntime } from '@/hooks/useRuntime'
import { scenarioPresetsApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { ScenarioPresetResponse } from '@/types'

export type ScenarioPresetLoadStatus = 'loading' | 'success' | 'not-found' | 'error'

export interface ScenarioPresetState {
  status: ScenarioPresetLoadStatus
  preset: ScenarioPresetResponse | null
  error: string | null
  retry: () => void
}

/** Load the preset that pre-fills the launch form, or stay idle when none is named. */
export function useScenarioPreset(presetName: string): ScenarioPresetState {
  const { generation, ready } = useRuntime()
  const [preset, setPreset] = useState<ScenarioPresetResponse | null>(null)
  const [status, setStatus] = useState<ScenarioPresetLoadStatus>(presetName ? 'loading' : 'success')
  const [error, setError] = useState<string | null>(null)
  const [refetchCount, setRefetchCount] = useState(0)

  useEffect(() => {
    if (!ready || !presetName) return
    let cancelled = false
    scenarioPresetsApi
      .get(presetName)
      .then((data) => {
        if (cancelled) return
        setPreset(data)
        setStatus('success')
        setError(null)
      })
      .catch((err: unknown) => {
        if (cancelled) return
        const apiError = toApiError(err)
        setPreset(null)
        setStatus(apiError.status === 404 ? 'not-found' : 'error')
        setError(apiError.status === 404 ? null : apiError.detail)
      })
    return () => {
      cancelled = true
    }
  }, [presetName, refetchCount, generation, ready])

  const retry = useCallback((): void => {
    if (presetName) {
      setStatus('loading')
      setError(null)
    }
    setRefetchCount((count) => count + 1)
  }, [presetName])

  return { status, preset, error, retry }
}
