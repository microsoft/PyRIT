import { useCallback, useEffect, useState } from 'react'

import { useRuntime } from '@/hooks/useRuntime'
import { scenariosApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { AssertPolicyMetrics } from '@/types'

export type AssertPolicyMetricsStatus = 'loading' | 'success' | 'error'

export interface AssertPolicyMetricsState {
  status: AssertPolicyMetricsStatus
  metrics: AssertPolicyMetrics | null
  error: string | null
  retry: () => void
}

/** Load ASSERT metrics for one completed run, ignoring answers for an older request or runtime. */
export function useAssertPolicyMetrics(scenarioResultId: string): AssertPolicyMetricsState {
  const { generation, ready } = useRuntime()
  const [metrics, setMetrics] = useState<AssertPolicyMetrics | null>(null)
  const [status, setStatus] = useState<AssertPolicyMetricsStatus>('loading')
  const [error, setError] = useState<string | null>(null)
  const [refetchCount, setRefetchCount] = useState(0)

  useEffect(() => {
    if (!ready) return
    const controller = new AbortController()
    scenariosApi
      .getAssertPolicyMetrics(scenarioResultId, controller.signal)
      .then((data) => {
        if (controller.signal.aborted) return
        setMetrics(data)
        setStatus('success')
        setError(null)
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return
        setMetrics(null)
        setStatus('error')
        setError(toApiError(err).detail)
      })
    return () => {
      controller.abort()
    }
  }, [scenarioResultId, refetchCount, generation, ready])

  const retry = useCallback((): void => {
    setStatus('loading')
    setError(null)
    setRefetchCount((count) => count + 1)
  }, [])

  return { status, metrics, error, retry }
}
