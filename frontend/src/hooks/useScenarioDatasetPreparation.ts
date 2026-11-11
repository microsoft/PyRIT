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
  readonly status: 'prepared' | 'error'
  readonly error?: string
}

/** Prepare only the current named selection. Stored data is reused; input edits do not refetch providers. */
export function useScenarioDatasetPreparation({
  scenarioName, datasetNames, request, metadata, metadataLoading, metadataError, enabled, onPrepared,
}: PreparationOptions) {
  const runtime = useRuntime()
  const [attempt, setAttempt] = useState(0)
  const [snapshot, setSnapshot] = useState<PreparationSnapshot | null>(null)
  const namesKey = JSON.stringify([...new Set(datasetNames)].sort())
  const key = JSON.stringify([scenarioName, runtime.generation, namesKey, attempt])
  const hasSources = datasetNames.length > 0
  const ready = enabled && runtime.ready
  const allStored = !metadataLoading && !metadataError
    && datasetNames.every((name) => metadata.some((item) => item.name === name && item.loaded))
  const requestRef = useRef(request)
  const onPreparedRef = useRef(onPrepared)
  const keyRef = useRef(key)
  const pendingRef = useRef<{
    key: string
    promise: Promise<ScenarioDatasetLoadResponse>
  } | null>(null)
  const requestReady = request !== null

  useEffect(() => {
    requestRef.current = request
    onPreparedRef.current = onPrepared
    keyRef.current = key
  }, [request, onPrepared, key])

  useEffect(() => {
    if (!ready || !hasSources || allStored || metadataLoading || metadataError || !requestReady) return
    if (snapshot?.key === key) return
    let ignore = false
    const prepare = async (): Promise<void> => {
      const previous = pendingRef.current
      if (previous && previous.key !== key) {
        // A superseded request can still store its original selection. Wait, then reuse those stored rows.
        await previous.promise.then(() => undefined, () => undefined)
      }
      if (ignore || keyRef.current !== key || !requestRef.current) return
      let pending = pendingRef.current
      try {
        if (!pending || pending.key !== key) {
          pending = { key, promise: scenariosApi.loadDatasets(scenarioName, requestRef.current) }
          pendingRef.current = pending
        }
        const response = await pending.promise
        const selectedNames: string[] = JSON.parse(namesKey)
        const missing = selectedNames.filter((name) => !response.items.some((item) => item.name === name && item.loaded))
        if (missing.length > 0) throw new Error(`Selected datasets are not stored after preparation: ${missing.join(', ')}.`)
        if (!ignore && keyRef.current === key) {
          setSnapshot({ key, status: 'prepared' })
          onPreparedRef.current()
        }
      } catch (error: unknown) {
        if (!ignore && keyRef.current === key) {
          setSnapshot({ key, status: 'error', error: toApiError(error).detail })
        }
      } finally {
        if (pendingRef.current === pending) pendingRef.current = null
      }
    }
    void prepare()
    return () => { ignore = true }
  }, [key, namesKey, ready, hasSources, allStored, metadataLoading, metadataError, requestReady, scenarioName, snapshot])

  const current = snapshot?.key === key ? snapshot : null
  const prepared = !hasSources || allStored || current?.status === 'prepared'
  const error = !prepared && current?.status === 'error' ? current.error ?? null : null
  return {
    prepared,
    preparing: ready && hasSources && !prepared && !metadataLoading && !metadataError && requestReady && !error,
    error,
    retry: useCallback(() => { setAttempt((value) => value + 1) }, []),
  }
}
