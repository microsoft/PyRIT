import { useCallback, useEffect, useRef, useState } from 'react'

import { scenariosApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { DatasetInfo, DatasetListResponse } from '@/types'

import { useRuntime } from './useRuntime'

interface DatasetSnapshot {
  key: string
  items: DatasetInfo[]
  error: string | null
}

/** Load the scenario's eligible choices once, independently of launch-form edits. */
export function useScenarioDatasets(scenarioName: string, enabled: boolean = true) {
  const runtime = useRuntime()
  const [snapshot, setSnapshot] = useState<DatasetSnapshot | null>(null)
  const [retryCount, setRetryCount] = useState(0)
  const ready = runtime.ready && enabled
  const key = JSON.stringify([scenarioName, runtime.generation, ready, retryCount])
  const retry = useCallback(() => { setRetryCount((count) => count + 1) }, [])
  // StrictMode can replay the subscription without needing another request.
  const pendingRef = useRef<{
    key: string
    controller: AbortController
    response: Promise<DatasetListResponse>
  } | null>(null)

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
        key, controller, response: scenariosApi.listCompatibleDatasets(scenarioName, controller.signal),
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
          items: data.items.filter((item) => item.selection_key !== 'dataset:unnamed'),
          error: null,
        })
      } catch (err: unknown) {
        if (!ignore) setSnapshot({
          key, items: [], error: toApiError(err).detail,
        })
      }
    }
    void load()
    return () => { ignore = true }
  }, [key, scenarioName, ready])

  const current = ready && snapshot?.key === key ? snapshot : null
  return {
    items: current?.items ?? [],
    error: current?.error ?? null,
    loading: ready && current === null,
    retry,
  }
}
