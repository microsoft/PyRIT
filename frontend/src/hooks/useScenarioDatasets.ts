import { useCallback, useEffect, useRef, useState } from 'react'

import { scenariosApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { DatasetInfo, DatasetListResponse } from '@/types'

import { useRuntime } from './useRuntime'

interface DatasetSnapshot {
  key: string
  generation: string
  scenarioName: string
  items: DatasetInfo[]
  error: string | null
}

/** Read-only compatible metadata; callers own selection and preparation separately. */
export function useScenarioDatasets(scenarioName: string, enabled = true) {
  const runtime = useRuntime()
  const [snapshot, setSnapshot] = useState<DatasetSnapshot | null>(null)
  const [reloadCount, setReloadCount] = useState(0)
  const listingRef = useRef<{
    key: string
    controller: AbortController
    response: Promise<DatasetListResponse>
  } | null>(null)
  const ready = runtime.ready && enabled && scenarioName !== ''
  const reload = useCallback(() => { setReloadCount((count) => count + 1) }, [])
  const key = JSON.stringify([scenarioName, runtime.generation, ready, reloadCount])

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
  }
}
