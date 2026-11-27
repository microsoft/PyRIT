import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

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

interface DatasetLoads {
  generation: string
  /** One entry per request still running, with the dataset names it was started for. */
  pending: Array<{ id: number; names: string[] }>
  /** The latest failure for each dataset name, cleared when a new load starts for it. */
  errors: Record<string, string>
}

const NO_LOADING_NAMES: readonly string[] = []
const NO_LOAD_ERRORS: Readonly<Record<string, string>> = {}

/** Page-scoped metadata cache with a missing-only load action that tracks each dataset name. */
export function useScenarioDatasets(scenarioName: string, enabled = true) {
  const runtime = useRuntime()
  const [snapshot, setSnapshot] = useState<DatasetSnapshot | null>(null)
  const [reloadCount, setReloadCount] = useState(0)
  const [loads, setLoads] = useState<DatasetLoads | null>(null)
  const generationRef = useRef(runtime.generation)
  const mountedRef = useRef(true)
  const nextLoadId = useRef(0)
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

  /**
   * Load the selection's missing datasets on behalf of `names`. Every call starts its own request,
   * so a dataset selected while another load runs still loads; the backend takes one writer per
   * dataset and rechecks storage, so overlapping requests fetch each dataset once.
   */
  const loadSelected = useCallback(async (
    loadScenarioName: string, request: ScenarioRunSizeEstimateRequest, names: readonly string[],
  ): Promise<void> => {
    const generation = runtime.generation
    const id = ++nextLoadId.current
    setLoads((previous) => {
      const current = previous?.generation === generation ? previous : { generation, pending: [], errors: {} }
      return {
        generation,
        pending: [...current.pending, { id, names: [...names] }],
        errors: Object.fromEntries(Object.entries(current.errors).filter(([name]) => !names.includes(name))),
      }
    })
    const failed: Record<string, string> = {}
    try {
      await scenariosApi.loadDatasets(loadScenarioName, request)
    } catch (err: unknown) {
      const detail = toApiError(err).detail
      for (const name of names) failed[name] = detail
    }
    if (!mountedRef.current || generationRef.current !== generation) return
    setLoads((previous) => previous?.generation !== generation ? previous : {
      generation,
      pending: previous.pending.filter((load) => load.id !== id),
      errors: { ...previous.errors, ...failed },
    })
    reload()
  }, [runtime.generation, reload])
  const currentLoads = loads?.generation === runtime.generation ? loads : null
  const loadingNames = useMemo(
    () => currentLoads ? [...new Set(currentLoads.pending.flatMap((load) => load.names))] : NO_LOADING_NAMES,
    [currentLoads],
  )

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
    // Don't abort here: StrictMode reruns this effect with the same key and must reuse the in-flight
    // listing. A key change or disabling aborts it; after unmount the read completes and is ignored.
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
    /** Dataset names with a load still running in this runtime generation. */
    loadingNames,
    /** The failure of the latest finished load for each dataset name, in this runtime generation. */
    loadErrors: currentLoads?.errors ?? NO_LOAD_ERRORS,
  }
}
