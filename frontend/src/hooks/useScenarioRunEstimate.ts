import { useEffect, useRef, useState } from 'react'

import { mapScenarioRunEstimate } from '@/components/Scenarios/scenarioRunEstimateAdapter'
import { scenariosApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type {
  ScenarioDatasetSettings,
  ScenarioRunEstimateResult,
  ScenarioRunEstimateState,
  ScenarioRunSizeEstimateRequest,
  ScenarioRunSizeEstimateResponse,
} from '@/types'

export const ESTIMATE_DEBOUNCE_MS = 300

type SuccessfulEstimateResult = Extract<ScenarioRunEstimateResult, { status: 'available' | 'conditional' }>

/** The backend answer for one request key. */
export type EstimateRequestState =
  | { status: 'resolved', requestKey: string, result: ScenarioRunEstimateResult, datasetSettings?: ScenarioDatasetSettings }
  | { status: 'error', requestKey: string, error: string }

interface UseScenarioRunEstimateInput {
  scenarioName: string
  /** The request to estimate, or null when the form can't produce one. */
  request: ScenarioRunSizeEstimateRequest | null
  /** Identifies the request; answers for any other key are stale. */
  requestKey: string | null
  /** Why no request could be built, shown while ``request`` is null. */
  invalidReason: string | null
  ready: boolean
  /** Pauses estimation, for example while a reinitialized runtime awaits review. */
  paused: boolean
}

export interface ScenarioRunEstimate {
  state: ScenarioRunEstimateState
  /** The latest dataset projection from the backend, if any. */
  datasetProjection: ScenarioRunSizeEstimateResponse | null
  /** The backend answer for the current request, or null while it's pending. */
  current: EstimateRequestState | null
}

/**
 * Debounce estimate requests and keep only the answer for the newest request.
 * Older answers, aborted requests, and answers after unmount are dropped.
 */
export function useScenarioRunEstimate({
  scenarioName,
  request,
  requestKey,
  invalidReason,
  ready,
  paused,
}: UseScenarioRunEstimateInput): ScenarioRunEstimate {
  const [requestState, setRequestState] = useState<EstimateRequestState | null>(null)
  const [lastGoodEstimate, setLastGoodEstimate] = useState<SuccessfulEstimateResult | null>(null)
  const [datasetProjection, setDatasetProjection] = useState<ScenarioRunSizeEstimateResponse | null>(null)
  const sequenceRef = useRef(0)
  // Drive estimation off the request key, not the request object, so launch-only
  // edits that rebuild an identical request don't re-POST the same estimate.
  const requestRef = useRef(request)
  useEffect(() => { requestRef.current = request }, [request])

  useEffect(() => {
    const request = requestRef.current
    if (!ready || paused || request === null || requestKey === null) {
      return
    }

    const requestSequence = sequenceRef.current + 1
    sequenceRef.current = requestSequence
    const controller = new AbortController()
    const isStale = (): boolean => controller.signal.aborted || requestSequence !== sequenceRef.current

    const debounceTimer = window.setTimeout(() => {
      scenariosApi
        .estimateRun(scenarioName, request, controller.signal)
        .then((response) => {
          if (isStale()) return
          const result = mapScenarioRunEstimate(response, 'request')
          setDatasetProjection(response)
          setRequestState({ status: 'resolved', requestKey, result, datasetSettings: response.effective_datasets })
          if (result.status === 'available' || result.status === 'conditional') {
            setLastGoodEstimate(result)
          }
        })
        .catch((err: unknown) => {
          if (isStale()) return
          const error = toApiError(err)
          setRequestState({ status: 'error', requestKey, error: error.detail })
          if (request.datasets && (error.status === 400 || error.status === 422)) {
            const { datasets, ...defaultRequest } = request
            void datasets
            // Keep invalid edits visible while refreshing parameter-aware allowed sources.
            void scenariosApi.estimateRun(scenarioName, defaultRequest, controller.signal)
              .then((response) => {
                if (isStale()) return
                setDatasetProjection((previous) => ({
                  ...response,
                  effective_datasets: previous?.effective_datasets ?? response.effective_datasets,
                }))
              })
              .catch(() => { /* The original validation error remains authoritative. */ })
          }
        })
    }, ESTIMATE_DEBOUNCE_MS)

    return () => {
      window.clearTimeout(debounceTimer)
      controller.abort()
    }
  }, [requestKey, scenarioName, ready, paused])

  const current = requestState?.requestKey === requestKey ? requestState : null
  return {
    state: estimateState({ invalidReason: request === null ? invalidReason : null, current, lastGoodEstimate }),
    datasetProjection,
    current,
  }
}

function estimateState({
  invalidReason,
  current,
  lastGoodEstimate,
}: {
  invalidReason: string | null
  current: EstimateRequestState | null
  lastGoodEstimate: SuccessfulEstimateResult | null
}): ScenarioRunEstimateState {
  if (invalidReason !== null) {
    return {
      status: 'unavailable',
      scope: 'request',
      label: 'Complete the required configuration to request an estimate.',
      note: invalidReason,
    }
  }
  if (current?.status === 'resolved') {
    return current.result
  }
  if (current?.status === 'error') {
    return lastGoodEstimate
      ? {
          status: 'stale',
          estimate: lastGoodEstimate.estimate,
          label: 'Showing the last successful estimate.',
          error: current.error,
        }
      : {
          status: 'unavailable',
          scope: 'request',
          label: 'The backend estimate could not be refreshed.',
          note: current.error,
        }
  }
  if (lastGoodEstimate) {
    return {
      status: 'refreshing',
      estimate: lastGoodEstimate.estimate,
      label: 'Updating for the current configuration…',
    }
  }
  return { status: 'loading', scope: 'request' }
}
