import { useCallback, useEffect, useRef, useState } from 'react'

import { scenariosApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { DatasetInfo, ScenarioDatasetLoadResponse, ScenarioRunSizeEstimateRequest } from '@/types'

import { useRuntime } from './useRuntime'

interface PreparationOptions {
  readonly scenarioName: string
  readonly datasetNames: readonly string[]
  readonly request: ScenarioRunSizeEstimateRequest | null
  readonly metadata: readonly DatasetInfo[]
  readonly metadataLoading: boolean
  readonly metadataError: string | null
  readonly enabled: boolean
  readonly onPrepared: () => void
}

interface PreparationSnapshot {
  readonly key: string
  readonly error: string | null
}

/** Observe selected-source preparation; provider deduplication and lifetime belong to the runtime. */
export function useScenarioDatasetPreparation({
  scenarioName, datasetNames, request, metadata, metadataLoading, metadataError, enabled, onPrepared,
}: PreparationOptions) {
  const runtime = useRuntime()
  const [attempt, setAttempt] = useState(0)
  const retrySelection = useRef<string | null>(null)
  const [snapshot, setSnapshot] = useState<PreparationSnapshot | null>(null)
  const namesKey = JSON.stringify([...new Set(datasetNames)].sort())
  const selectionKey = JSON.stringify([scenarioName, runtime.generation, namesKey])
  const key = JSON.stringify([selectionKey, attempt])
  const ready = enabled && runtime.ready && request !== null
  const allStored = !metadataLoading && !metadataError
    && datasetNames.every((name) => metadata.some((item) => item.name === name && item.loaded))
  const requestRef = useRef(request)
  const onPreparedRef = useRef(onPrepared)
  const pendingRef = useRef<{ key: string; promise: Promise<ScenarioDatasetLoadResponse> } | null>(null)

  useEffect(() => {
    requestRef.current = request
    onPreparedRef.current = onPrepared
  }, [request, onPrepared])

  useEffect(() => {
    if (!ready || allStored || metadataLoading || metadataError || snapshot?.key === key) return
    const request = requestRef.current
    if (request === null) return
    let ignore = false
    let pending = pendingRef.current
    if (pending?.key !== key) {
      const retry = retrySelection.current === selectionKey
      retrySelection.current = null
      pending = { key, promise: scenariosApi.loadDatasets(scenarioName, request, retry) }
      pendingRef.current = pending
    }
    void pending.promise.then((response) => {
      if (ignore) return
      const selected: string[] = JSON.parse(namesKey)
      const missing = selected.filter((name) => !response.items.some((item) => item.name === name && item.loaded))
      if (missing.length > 0) {
        setSnapshot({ key, error: `Selected datasets are not stored after preparation: ${missing.join(', ')}.` })
        return
      }
      setSnapshot({ key, error: null })
      onPreparedRef.current()
    }).catch((error: unknown) => {
      if (!ignore) setSnapshot({ key, error: toApiError(error).detail })
    })
    return () => { ignore = true }
  }, [key, namesKey, ready, allStored, metadataLoading, metadataError, scenarioName, selectionKey, snapshot])

  const current = snapshot?.key === key ? snapshot : null
  const prepared = datasetNames.length === 0 || allStored || current?.error === null
  return {
    preparing: ready && !prepared && !metadataLoading && !metadataError && !current?.error,
    error: !prepared ? current?.error ?? null : null,
    retry: useCallback(() => {
      retrySelection.current = selectionKey
      setAttempt((value) => value + 1)
    }, [selectionKey]),
  }
}
