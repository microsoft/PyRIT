import { useCallback, useEffect, useState } from 'react'

import { datasetsApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { DatasetInfo } from '@/types'

import { useRuntime } from './useRuntime'

interface DatasetSnapshot {
  key: string
  items: DatasetInfo[]
  error: string | null
}

/** Page-scoped metadata cache; no dataset loading or generation occurs here. */
export function useScenarioDatasets() {
  const runtime = useRuntime()
  const [snapshot, setSnapshot] = useState<DatasetSnapshot | null>(null)
  const [reloadCount, setReloadCount] = useState(0)
  const reload = useCallback(() => { setReloadCount((count) => count + 1) }, [])
  const key = JSON.stringify([runtime.generation, runtime.ready, reloadCount])

  useEffect(() => {
    if (!runtime.ready) return
    const controller = new AbortController()
    const load = async (): Promise<void> => {
      try {
        const data = await datasetsApi.list(controller.signal)
        if (controller.signal.aborted) return
        setSnapshot({
          key,
          items: data.filter((item) => item.selection_key !== 'dataset:unnamed'),
          error: null,
        })
      } catch (err: unknown) {
        if (!controller.signal.aborted) setSnapshot({ key, items: [], error: toApiError(err).detail })
      }
    }
    void load()
    return () => { controller.abort() }
  }, [key, runtime.ready])

  const current = runtime.ready && snapshot?.key === key ? snapshot : null
  return {
    items: current?.items ?? [],
    error: current?.error ?? null,
    loading: runtime.ready && current === null,
    reload,
  }
}
