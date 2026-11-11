import { useCallback, useEffect, useRef, useState } from 'react'

import { datasetsApi, scenariosApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { DatasetInfo, DatasetListResponse, ScenarioRunSizeEstimateRequest } from '@/types'

import { useRuntime } from './useRuntime'

interface DatasetSnapshot {
  key: string
  generation: string
  items: DatasetInfo[]
  error: string | null
}

/** ASSERT opts into a one-load eligibility snapshot; other forms retain generic metadata and explicit preparation. */
export function useScenarioDatasets(
  scenarioName: string, enabled: boolean = true, compatibleOnly: boolean = false,
) {
  const runtime = useRuntime()
  const [snapshot, setSnapshot] = useState<DatasetSnapshot | null>(null)
  const [retryCount, setRetryCount] = useState(0)
  const [operation, setOperation] = useState<{ generation: string; busy: boolean; error: string | null } | null>(null)
  const generationRef = useRef(runtime.generation)
  const mountedRef = useRef(true)
  const loadingRef = useRef<Promise<void> | null>(null)
  const ready = runtime.ready && enabled
  const key = JSON.stringify([compatibleOnly ? scenarioName : '', runtime.generation, ready, retryCount, compatibleOnly])
  const retry = useCallback(() => { setRetryCount((count) => count + 1) }, [])
  // StrictMode can replay the subscription without needing another request.
  const pendingRef = useRef<{
    key: string
    controller: AbortController
    response: Promise<DatasetListResponse>
  } | null>(null)

  useEffect(() => { generationRef.current = runtime.generation }, [runtime.generation])
  useEffect(() => {
    mountedRef.current = true
    return () => { mountedRef.current = false }
  }, [])

  const loadSelected = (name: string, request: ScenarioRunSizeEstimateRequest): Promise<void> => {
    if (loadingRef.current) return loadingRef.current
    const generation = runtime.generation
    setOperation({ generation, busy: true, error: null })
    const load = async (): Promise<void> => {
      let error: string | null = null
      try {
        await scenariosApi.loadDatasets(name, request)
      } catch (cause: unknown) {
        error = toApiError(cause).detail
      } finally {
        loadingRef.current = null
        if (mountedRef.current && generationRef.current === generation) {
          setOperation({ generation, busy: false, error })
          retry()
        }
      }
    }
    loadingRef.current = load()
    return loadingRef.current
  }

  useEffect(() => {
    if (!ready) {
      pendingRef.current?.controller.abort()
      pendingRef.current = null
      return
    }
    if (pendingRef.current?.key !== key) {
      pendingRef.current?.controller.abort()
      const controller = new AbortController()
      pendingRef.current = {
        key, controller,
        response: compatibleOnly
          ? scenariosApi.listCompatibleDatasets(scenarioName, controller.signal)
          : datasetsApi.listDatasets({ loaded_only: false }, controller.signal),
      }
    }
    const pending = pendingRef.current
    let ignore = false
    const load = async (): Promise<void> => {
      try {
        const data = await pending.response
        if (ignore) return
        setSnapshot({
          key,
          generation: runtime.generation,
          items: data.items.filter((item) => item.selection_key !== 'dataset:unnamed'),
          error: null,
        })
      } catch (err: unknown) {
        if (!ignore) setSnapshot({
          key, generation: runtime.generation, items: [], error: toApiError(err).detail,
        })
      }
    }
    void load()
    return () => { ignore = true }
  }, [key, scenarioName, ready, compatibleOnly, runtime.generation])

  const current = ready && snapshot?.key === key ? snapshot : null
  return {
    items: compatibleOnly ? current?.items ?? []
      : ready && snapshot?.generation === runtime.generation ? snapshot.items : [],
    error: current?.error ?? null,
    loading: ready && current === null,
    initialLoading: ready && snapshot === null,
    retry,
    reload: retry,
    loadSelected,
    preparing: operation?.generation === runtime.generation && operation.busy,
    loadError: operation?.generation === runtime.generation ? operation.error : null,
  }
}
