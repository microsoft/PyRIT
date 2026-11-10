import { useEffect, useRef, useState } from 'react'

import { scenariosApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { ScenarioDatasetCompatibility, ScenarioRunSizeEstimateRequest } from '@/types'

interface UseScenarioDatasetCompatibilityOptions {
  readonly scenarioName: string
  readonly request: ScenarioRunSizeEstimateRequest | null
  readonly revision: string
  readonly ready: boolean
}

interface CompatibilitySnapshot {
  readonly key: string
  readonly data: ScenarioDatasetCompatibility | null
  readonly error: string | null
}

const ASSESSMENT_DELAY_MS = 250

/** Revalidates the current selection; a previous configuration's assessment is never launch evidence. */
export function useScenarioDatasetCompatibility({
  scenarioName, request, revision, ready,
}: UseScenarioDatasetCompatibilityOptions) {
  const [snapshot, setSnapshot] = useState<CompatibilitySnapshot | null>(null)
  const requestRef = useRef(request)
  const key = JSON.stringify([scenarioName, request, revision, ready])

  useEffect(() => {
    requestRef.current = request
  }, [request])

  useEffect(() => {
    const currentRequest = requestRef.current
    if (!ready || !currentRequest) return
    const controller = new AbortController()
    const assess = async (): Promise<void> => {
      try {
        const data = await scenariosApi.assessDatasets(scenarioName, currentRequest, true, controller.signal)
        if (!controller.signal.aborted) setSnapshot({ key, data, error: null })
      } catch (error: unknown) {
        if (!controller.signal.aborted) setSnapshot({ key, data: null, error: toApiError(error).detail })
      }
    }
    const timer = setTimeout(() => { void assess() }, ASSESSMENT_DELAY_MS)
    return () => {
      clearTimeout(timer)
      controller.abort()
    }
  }, [key, scenarioName, ready])

  const current = snapshot?.key === key ? snapshot : null
  return {
    data: current?.data ?? null,
    error: current?.error ?? null,
    loading: ready && request !== null && current === null,
  }
}
