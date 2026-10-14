import { useCallback, useEffect, useRef, useState } from 'react'

import { datasetsApi, scenariosApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { DatasetInfo, ScenarioRunSizeEstimateRequest } from '@/types'

import { useRuntime } from './useRuntime'

interface DatasetSnapshot {
  key: string
  generation: string
  items: DatasetInfo[]
  error: string | null
}

/** Page-scoped metadata cache with an explicit, missing-only load action. */
export function useScenarioDatasets() {
  const runtime = useRuntime()
  const [snapshot, setSnapshot] = useState<DatasetSnapshot | null>(null)
  const [reloadCount, setReloadCount] = useState(0)
  const [operation, setOperation] = useState<{ generation: string; busy: boolean; error: string | null } | null>(null)
  const generationRef = useRef(runtime.generation)
  const mountedRef = useRef(true)
  const pendingRef = useRef<Promise<void> | null>(null)
  const reload = useCallback(() => { setReloadCount((count) => count + 1) }, [])
  const key = JSON.stringify([runtime.generation, runtime.ready, reloadCount])

  useEffect(() => { generationRef.current = runtime.generation }, [runtime.generation])
  useEffect(() => {
    mountedRef.current = true
    return () => { mountedRef.current = false }
  }, [])

  const loadSelected = (scenarioName: string, request: ScenarioRunSizeEstimateRequest): Promise<void> => {
    if (pendingRef.current) return pendingRef.current
    const generation = runtime.generation
    setOperation({ generation, busy: true, error: null })
    const task = async (): Promise<void> => {
      let error: string | null = null
      try {
        await scenariosApi.loadDatasets(scenarioName, request)
      } catch (err: unknown) {
        error = toApiError(err).detail
      } finally {
        pendingRef.current = null
        if (mountedRef.current && generationRef.current === generation) {
          setOperation({ generation, busy: false, error })
          reload()
        }
      }
    }
    pendingRef.current = task()
    return pendingRef.current
  }

  useEffect(() => {
    if (!runtime.ready) return
    const controller = new AbortController()
    const load = async (): Promise<void> => {
      try {
        const data = await datasetsApi.listDatasets({ loaded_only: false }, controller.signal)
        if (controller.signal.aborted) return
        setSnapshot({
          key,
          generation: runtime.generation,
          items: data.items.filter((item) => item.selection_key !== 'dataset:unnamed'),
          error: null,
        })
      } catch (err: unknown) {
        if (!controller.signal.aborted) setSnapshot({
          key, generation: runtime.generation, items: [], error: toApiError(err).detail,
        })
      }
    }
    void load()
    return () => { controller.abort() }
  }, [key, runtime.ready, runtime.generation])

  const current = runtime.ready && snapshot?.key === key ? snapshot : null
  return {
    items: runtime.ready && snapshot?.generation === runtime.generation ? snapshot.items : [],
    error: current?.error ?? null,
    loading: runtime.ready && current === null,
    reload,
    loadSelected,
    preparing: operation?.generation === runtime.generation && operation.busy,
    loadError: operation?.generation === runtime.generation ? operation.error : null,
  }
}
