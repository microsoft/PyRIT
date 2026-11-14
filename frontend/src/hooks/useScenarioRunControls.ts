import { useEffect, useRef, useState } from 'react'

import { useLocation, useNavigate } from 'react-router'

import { useScenarioRunResume } from '@/hooks/useScenarioRunResume'
import { scenariosApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { ScenarioRunSummary } from '@/types'
import { scenarioRerunRoutePath } from '@/utils/routeParams'

interface UseScenarioRunControlsOptions {
  readonly scenarioResultId: string
  readonly applyRunSummary: (run: ScenarioRunSummary) => void
  /** Reload run progress after a resume that did not start. */
  readonly retry: () => void
  readonly refreshQueue: () => void
  /** The run's own error, which the page already shows with the run. */
  readonly runError?: string | null
}

export interface ScenarioRunControls {
  readonly rerunPending: boolean
  readonly rerun: () => void
  readonly cancelOpen: boolean
  readonly setCancelOpen: (open: boolean) => void
  readonly cancelling: boolean
  readonly cancelError: string | null
  readonly cancel: () => void
  readonly resumePending: boolean
  readonly resume: () => void
  /** The rerun or resume failure to show beside the controls. */
  readonly controlError: string | null
}

/** Rerun, cancel, and resume one scan, keeping each request's pending state and errors. */
export function useScenarioRunControls({
  scenarioResultId,
  applyRunSummary,
  retry,
  refreshQueue,
  runError,
}: UseScenarioRunControlsOptions): ScenarioRunControls {
  const navigate = useNavigate()
  const location = useLocation()
  const [cancelOpen, setCancelOpen] = useState(false)
  const [cancelling, setCancelling] = useState(false)
  const [cancelError, setCancelError] = useState<string | null>(null)
  const [rerunPending, setRerunPending] = useState(false)
  const [rerunError, setRerunError] = useState<string | null>(null)
  const rerunRequest = useRef<AbortController | null>(null)
  const resumeRequest = useScenarioRunResume({
    onResumed: applyRunSummary,
    onRefresh: (succeeded: boolean): void => {
      refreshQueue()
      if (!succeeded) retry()
    },
  })

  // Leaving the run, or opening an attempt on it, abandons a rerun still loading its configuration.
  useEffect(() => () => {
    rerunRequest.current?.abort()
    rerunRequest.current = null
  }, [location.pathname])

  async function requestRerun(): Promise<void> {
    rerunRequest.current?.abort()
    const controller = new AbortController()
    rerunRequest.current = controller
    setRerunPending(true)
    setRerunError(null)
    try {
      const request = await scenariosApi.getLaunchConfiguration(scenarioResultId, controller.signal)
      if (rerunRequest.current !== controller || controller.signal.aborted) return
      navigate(scenarioRerunRoutePath(request.scenario_name, scenarioResultId))
    } catch (error: unknown) {
      if (rerunRequest.current !== controller || controller.signal.aborted) return
      setRerunError(toApiError(error).detail)
    } finally {
      if (rerunRequest.current === controller) {
        rerunRequest.current = null
        setRerunPending(false)
      }
    }
  }

  async function requestCancel(): Promise<void> {
    setCancelling(true)
    setCancelError(null)
    try {
      applyRunSummary(await scenariosApi.cancelRun(scenarioResultId))
      setCancelOpen(false)
    } catch (error: unknown) {
      setCancelError(toApiError(error).detail)
    } finally {
      setCancelling(false)
    }
  }

  return {
    rerunPending,
    rerun: (): void => { void requestRerun() },
    cancelOpen,
    setCancelOpen: (open: boolean): void => {
      if (open) setCancelError(null)
      if (!cancelling) setCancelOpen(open)
    },
    cancelling,
    cancelError,
    cancel: (): void => { void requestCancel() },
    resumePending: resumeRequest.pendingRunId !== null,
    resume: (): void => {
      setRerunError(null)
      resumeRequest.requestResume(scenarioResultId)
    },
    controlError: rerunError ?? resumeRequest.error
      ?? (resumeRequest.executionError !== runError ? resumeRequest.executionError : null),
  }
}
