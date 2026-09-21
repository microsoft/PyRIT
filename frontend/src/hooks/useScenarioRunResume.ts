import { useEffect, useRef, useState } from 'react'

import { scenariosApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { ScenarioResumeExecutionOptions, ScenarioRunSummary } from '@/types'

interface UseScenarioRunResumeOptions {
  readonly onResumed: (run: ScenarioRunSummary) => void
  readonly onRefresh: (succeeded: boolean) => void
}

interface UseScenarioRunResumeResult {
  readonly pendingRunId: string | null
  readonly legacyRunId: string | null
  readonly error: string | null
  readonly executionError: string | null
  readonly requestResume: (scenarioResultId: string) => void
  readonly confirmResume: (options: ScenarioResumeExecutionOptions) => void
  readonly cancel: () => void
}

/** A user action starts preflight; legacy limits always require a separate confirmation. */
export function useScenarioRunResume({
  onResumed,
  onRefresh,
}: UseScenarioRunResumeOptions): UseScenarioRunResumeResult {
  const pendingRef = useRef(false)
  const mountedRef = useRef(true)
  const legacyRunIdRef = useRef<string | null>(null)
  const [pendingRunId, setPendingRunId] = useState<string | null>(null)
  const [legacyRunId, setLegacyRunId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [executionError, setExecutionError] = useState<string | null>(null)

  useEffect(() => {
    mountedRef.current = true
    return () => { mountedRef.current = false }
  }, [])

  const execute = async (
    scenarioResultId: string,
    preflight: boolean,
    options?: ScenarioResumeExecutionOptions,
  ): Promise<void> => {
    if (pendingRef.current || (preflight && legacyRunIdRef.current !== null)) return
    pendingRef.current = true
    setPendingRunId(scenarioResultId)
    setError(null)
    setExecutionError(null)
    let refresh = false
    let succeeded = false
    try {
      if (preflight) {
        const requirements = await scenariosApi.getResumeRequirements(scenarioResultId)
        if (!mountedRef.current) return
        if (requirements.requires_execution_options) {
          legacyRunIdRef.current = scenarioResultId
          setLegacyRunId(scenarioResultId)
          return
        }
      }
      refresh = true
      const resumedRun = options
        ? await scenariosApi.resumeRun(scenarioResultId, options)
        : await scenariosApi.resumeRun(scenarioResultId)
      if (!mountedRef.current) return
      legacyRunIdRef.current = null
      setLegacyRunId(null)
      onResumed(resumedRun)
      if (resumedRun.status === 'FAILED') {
        setExecutionError(resumedRun.error || 'The resumed run failed. Finished results remain available.')
      }
      succeeded = true
    } catch (requestError: unknown) {
      refresh = true
      if (mountedRef.current) setError(toApiError(requestError).detail)
    } finally {
      pendingRef.current = false
      if (mountedRef.current) {
        if (refresh) onRefresh(succeeded)
        setPendingRunId(null)
      }
    }
  }

  return {
    pendingRunId,
    legacyRunId,
    error,
    executionError,
    requestResume: (scenarioResultId: string): void => { void execute(scenarioResultId, true) },
    confirmResume: (options: ScenarioResumeExecutionOptions): void => {
      if (legacyRunIdRef.current !== null) {
        void execute(legacyRunIdRef.current, false, options)
      }
    },
    cancel: (): void => {
      if (pendingRef.current) return
      legacyRunIdRef.current = null
      setLegacyRunId(null)
    },
  }
}
