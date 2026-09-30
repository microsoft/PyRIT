import { useEffect, useRef, useState } from 'react'

import { scenariosApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { ScenarioRunSummary } from '@/types'

interface UseScenarioRunResumeOptions {
  readonly onResumed: (run: ScenarioRunSummary) => void
  readonly onRefresh: (succeeded: boolean) => void
}

interface UseScenarioRunResumeResult {
  readonly pendingRunId: string | null
  readonly error: string | null
  readonly executionError: string | null
  readonly requestResume: (scenarioResultId: string) => void
}

export function useScenarioRunResume({
  onResumed,
  onRefresh,
}: UseScenarioRunResumeOptions): UseScenarioRunResumeResult {
  const pendingRef = useRef(false)
  const mountedRef = useRef(true)
  const [pendingRunId, setPendingRunId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [executionError, setExecutionError] = useState<string | null>(null)

  useEffect(() => {
    mountedRef.current = true
    return () => { mountedRef.current = false }
  }, [])

  const execute = async (scenarioResultId: string): Promise<void> => {
    if (pendingRef.current || !mountedRef.current) return
    pendingRef.current = true
    setPendingRunId(scenarioResultId)
    setError(null)
    setExecutionError(null)
    let succeeded = false
    try {
      const resumedRun = await scenariosApi.resumeRun(scenarioResultId)
      if (!mountedRef.current) return
      onResumed(resumedRun)
      if (resumedRun.status === 'FAILED') {
        setExecutionError(resumedRun.error || 'The resumed run failed. Finished results remain available.')
      }
      succeeded = true
    } catch (requestError: unknown) {
      if (mountedRef.current) setError(toApiError(requestError).detail)
    } finally {
      pendingRef.current = false
      if (mountedRef.current) {
        onRefresh(succeeded)
        setPendingRunId(null)
      }
    }
  }

  return {
    pendingRunId,
    error,
    executionError,
    requestResume: (scenarioResultId: string): void => { void execute(scenarioResultId) },
  }
}
