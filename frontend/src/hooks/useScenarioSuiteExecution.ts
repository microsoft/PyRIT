import { useCallback, useEffect, useRef, useState } from 'react'

import { scenarioSuitesApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type {
  ScenarioSuiteExecutionStatus,
  ScenarioSuiteExecutionSummary,
} from '@/types'

export const SCENARIO_SUITE_EXECUTION_POLL_INTERVAL_MS = 2_500

export type ScenarioSuiteExecutionLoadStatus = 'loading' | 'ready' | 'not-found' | 'error'

export interface ScenarioSuiteExecutionState {
  readonly loadStatus: ScenarioSuiteExecutionLoadStatus
  readonly execution: ScenarioSuiteExecutionSummary | null
  readonly error: string | null
  readonly stale: boolean
}

export interface UseScenarioSuiteExecutionResult {
  readonly state: ScenarioSuiteExecutionState
  readonly retry: () => void
  readonly applyExecution: (execution: ScenarioSuiteExecutionSummary) => void
}

const TERMINAL_STATUSES: ReadonlySet<ScenarioSuiteExecutionStatus> = new Set([
  'COMPLETED',
  'FAILED',
  'CANCELLED',
])

export function isTerminalSuiteExecutionStatus(status: ScenarioSuiteExecutionStatus): boolean {
  return TERMINAL_STATUSES.has(status)
}

export function useScenarioSuiteExecution(
  executionId: string,
): UseScenarioSuiteExecutionResult {
  const executionIdValid = executionId.trim() !== ''
  const [state, setState] = useState<ScenarioSuiteExecutionState>(() => (
    executionIdValid
      ? {
          loadStatus: 'loading',
          execution: null,
          error: null,
          stale: false,
        }
      : {
          loadStatus: 'not-found',
          execution: null,
          error: 'The scenario suite execution ID in this URL is missing or invalid.',
          stale: false,
        }
  ))
  const [retryEpoch, setRetryEpoch] = useState(0)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const controllerRef = useRef<AbortController | null>(null)
  const latestExecutionRef = useRef<ScenarioSuiteExecutionSummary | null>(null)
  const pollingStoppedRef = useRef(false)
  const retriedRef = useRef(false)

  useEffect(() => {
    let active = true
    pollingStoppedRef.current = false
    retriedRef.current = false

    const clearPollTimer = (): void => {
      if (timerRef.current !== null) {
        clearTimeout(timerRef.current)
        timerRef.current = null
      }
    }

    const schedulePoll = (): void => {
      clearPollTimer()
      timerRef.current = setTimeout(() => {
        timerRef.current = null
        void fetchExecutionAsync()
      }, SCENARIO_SUITE_EXECUTION_POLL_INTERVAL_MS)
    }

    const fetchExecutionAsync = async (): Promise<void> => {
      if (!active || pollingStoppedRef.current) {
        return
      }
      const controller = new AbortController()
      controllerRef.current = controller
      try {
        const execution = await scenarioSuitesApi.getExecution(executionId, controller.signal)
        if (!active || pollingStoppedRef.current) {
          return
        }
        latestExecutionRef.current = execution
        retriedRef.current = false
        setState({ loadStatus: 'ready', execution, error: null, stale: false })
        if (isTerminalSuiteExecutionStatus(execution.status)) {
          pollingStoppedRef.current = true
          return
        }
        schedulePoll()
      } catch (error: unknown) {
        if (!active || controller.signal.aborted) {
          return
        }
        const apiError = toApiError(error)
        if (apiError.status !== 404 && !retriedRef.current) {
          retriedRef.current = true
          schedulePoll()
          return
        }
        const previous = latestExecutionRef.current
        setState({
          loadStatus: apiError.status === 404 && previous === null
            ? 'not-found'
            : previous === null
              ? 'error'
              : 'ready',
          execution: previous,
          error: apiError.detail,
          stale: previous !== null,
        })
        if (
          apiError.status !== 404
          && previous !== null
          && !isTerminalSuiteExecutionStatus(previous.status)
        ) {
          schedulePoll()
        }
      }
    }

    if (executionIdValid) {
      void fetchExecutionAsync()
    }

    return () => {
      active = false
      clearPollTimer()
      controllerRef.current?.abort()
      controllerRef.current = null
    }
  }, [executionId, executionIdValid, retryEpoch])

  const retry = useCallback((): void => {
    pollingStoppedRef.current = false
    retriedRef.current = false
    setState((previous) => ({
      ...previous,
      loadStatus: previous.execution ? 'ready' : 'loading',
      error: null,
      stale: false,
    }))
    setRetryEpoch((epoch) => epoch + 1)
  }, [])

  const applyExecution = useCallback((execution: ScenarioSuiteExecutionSummary): void => {
    latestExecutionRef.current = execution
    setState({ loadStatus: 'ready', execution, error: null, stale: false })
    if (isTerminalSuiteExecutionStatus(execution.status)) {
      pollingStoppedRef.current = true
      if (timerRef.current !== null) {
        clearTimeout(timerRef.current)
        timerRef.current = null
      }
      controllerRef.current?.abort()
      controllerRef.current = null
    }
  }, [])

  return { state, retry, applyExecution }
}
