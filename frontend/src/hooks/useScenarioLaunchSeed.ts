import { useCallback, useEffect, useState } from 'react'

import { scenariosApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { RunScenarioRequest } from '@/types'
import { UUID_PATTERN } from '@/utils/routeParams'

interface LaunchSeedResource {
  readonly key: string
  readonly initialRequest?: RunScenarioRequest
  readonly request?: RunScenarioRequest
  readonly error?: string
}

interface ScenarioLaunchSeed {
  readonly active: boolean
  readonly loading: boolean
  readonly error?: string
  readonly initialRequest?: RunScenarioRequest
  readonly request?: RunScenarioRequest
  readonly updateAttribution: (update: Pick<RunScenarioRequest, 'labels' | 'operation_id'>) => void
  readonly retry: () => void
}

/** A saved run's form-local attribution. Reading it never writes user preferences. */
export function useScenarioLaunchSeed(runId: string | null, generation: string, ready: boolean): ScenarioLaunchSeed {
  const [revision, setRevision] = useState(0)
  const [resource, setResource] = useState<LaunchSeedResource | null>(null)
  const key = JSON.stringify([runId, generation, revision])
  const valid = runId !== null && UUID_PATTERN.test(runId)
  useEffect(() => {
    if (!valid || !ready || !runId) return
    const controller = new AbortController()
    void scenariosApi.getLaunchConfiguration(runId, controller.signal).then((request: RunScenarioRequest) => {
      if (!controller.signal.aborted) setResource({ key, request, initialRequest: request })
    }).catch((error: unknown) => {
      if (!controller.signal.aborted) setResource({ key, error: toApiError(error).detail })
    })
    return () => { controller.abort() }
  }, [key, valid, ready, runId])
  const current = resource?.key === key ? resource : null
  const updateAttribution = useCallback((update: Pick<RunScenarioRequest, 'labels' | 'operation_id'>): void => {
    setResource((value: LaunchSeedResource | null) => value?.key === key && value.request
      ? { ...value, request: { ...value.request, ...update } } : value)
  }, [key])
  return {
    active: runId !== null,
    loading: runId !== null && valid && !current,
    error: runId !== null && !valid ? 'The saved run reference is invalid.' : current?.error,
    initialRequest: current?.initialRequest,
    request: current?.request,
    updateAttribution,
    retry: (): void => { setRevision((value: number) => value + 1) },
  }
}
