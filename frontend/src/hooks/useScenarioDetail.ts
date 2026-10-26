import { useCallback, useEffect, useState } from 'react'

import { useRuntime } from '@/hooks/useRuntime'
import { scenariosApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { RegisteredScenario } from '@/types'

export type ScenarioLoadStatus = 'loading' | 'success' | 'not-found' | 'error'

export interface ScenarioDetailState {
  status: ScenarioLoadStatus
  scenario: RegisteredScenario | null
  error: string | null
  retry: () => void
}

/** Load one registered scenario for the current runtime generation, ignoring answers for an older request. */
export function useScenarioDetail(scenarioName: string): ScenarioDetailState {
  const { generation, ready } = useRuntime()
  const [scenario, setScenario] = useState<RegisteredScenario | null>(null)
  const [status, setStatus] = useState<ScenarioLoadStatus>('loading')
  const [error, setError] = useState<string | null>(null)
  const [refetchCount, setRefetchCount] = useState(0)

  useEffect(() => {
    if (!ready) return
    let cancelled = false
    scenariosApi
      .getScenario(scenarioName)
      .then((data) => {
        if (cancelled) return
        if (!Array.isArray(data.supported_parameter_names)) {
          setScenario(null)
          setStatus('error')
          setError('The scenario catalog has no input support metadata. Update the backend and reload.')
          return
        }
        setScenario(data)
        setStatus('success')
        setError(null)
      })
      .catch((err: unknown) => {
        if (cancelled) return
        const apiError = toApiError(err)
        setScenario(null)
        setStatus(apiError.status === 404 ? 'not-found' : 'error')
        setError(apiError.status === 404 ? null : apiError.detail)
      })
    return () => {
      cancelled = true
    }
  }, [scenarioName, refetchCount, generation, ready])

  const retry = useCallback((): void => {
    setStatus('loading')
    setError(null)
    setRefetchCount((count) => count + 1)
  }, [])

  return { status, scenario, error, retry }
}
