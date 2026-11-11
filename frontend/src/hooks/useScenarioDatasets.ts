import { useCallback, useEffect, useRef, useState } from 'react'

import { scenariosApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { DatasetInfo, DatasetListResponse, ScenarioRunSizeEstimateRequest } from '@/types'

import { useRuntime } from './useRuntime'

interface DatasetSnapshot {
  key: string
  generation: string
  scenarioName: string
  items: DatasetInfo[]
  error: string | null
}

/** Page-scoped metadata cache with an explicit, missing-only load action. */
export function useScenarioDatasets(scenarioName: string, enabled = true) {
  const runtime = useRuntime()
  const [snapshot, setSnapshot] = useState<DatasetSnapshot | null>(null)
  const [reloadCount, setReloadCount] = useState(0)
  const [operation, setOperation] = useState<{ generation: string; busy: boolean; error: string | null } | null>(null)
  const generationRef = useRef(runtime.generation)
  const mountedRef = useRef(true)
  const pendingRef = useRef<Promise<void> | null>(null)
  const listingRef = useRef<{
    key: string
    controller: AbortController
    response: Promise<DatasetListResponse>
  } | null>(null)
  const ready = runtime.ready && enabled && scenarioName !== ''
  const reload = useCallback(() => { setReloadCount((count) => count + 1) }, [])
  const key = JSON.stringify([scenarioName, runtime.generation, ready, reloadCount])

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
    if (!ready) {
      listingRef.current?.controller.abort()
      listingRef.current = null
      return
    }
    if (listingRef.current?.key !== key) {
      listingRef.current?.controller.abort()
      const controller = new AbortController()
      listingRef.current = {
        key, controller, response: scenariosApi.listCompatibleDatasets(scenarioName, controller.signal),
      }
    }
    const listing = listingRef.current
    let ignore = false
    const load = async (): Promise<void> => {
      try {
        const data = await listing.response
        if (ignore) return
        setSnapshot({
          key,
          generation: runtime.generation,
          scenarioName,
          items: data.items.filter((item) => item.selection_key !== 'dataset:unnamed'),
          error: null,
        })
      } catch (err: unknown) {
        if (!ignore) setSnapshot({
          key, generation: runtime.generation, scenarioName, items: [], error: toApiError(err).detail,
        })
      }
    }
    void load()
    return () => { ignore = true }
  }, [key, ready, scenarioName, runtime.generation])

  const current = ready && snapshot?.key === key ? snapshot : null
  return {
    items: ready && snapshot?.generation === runtime.generation && snapshot.scenarioName === scenarioName
      ? snapshot.items : [],
    error: current?.error ?? null,
    loading: ready && current === null,
    reload,
    loadSelected,
    preparing: operation?.generation === runtime.generation && operation.busy,
    loadError: operation?.generation === runtime.generation ? operation.error : null,
  }
}
