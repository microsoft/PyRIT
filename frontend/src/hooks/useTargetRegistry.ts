import { useCallback, useEffect, useRef, useState } from 'react'

import { useRuntime } from '@/hooks/useRuntime'
import { toApiError } from '@/services/errors'
import { listRegisteredTargets } from '@/services/targetRegistry'
import type { TargetInstance } from '@/types'
import { isGuiTarget } from '@/utils/targetVisibility'

export function useTargetRegistry() {
  const { generation, ready } = useRuntime()
  const [targets, setTargets] = useState<TargetInstance[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [loadedGeneration, setLoadedGeneration] = useState<string | null>(null)
  const [revision, setRevision] = useState(0)
  const pendingUpdates = useRef<Map<string, TargetInstance> | null>(null)

  useEffect(() => {
    if (!ready) return
    let cancelled = false
    const updates = new Map<string, TargetInstance>()
    pendingUpdates.current = updates
    listRegisteredTargets().then((items: TargetInstance[]) => {
      if (cancelled || pendingUpdates.current !== updates) return
      const merged = new Map(items.map((item: TargetInstance) => [item.target_registry_name, item]))
      updates.forEach((target: TargetInstance, name: string) => merged.set(name, target))
      setTargets([...merged.values()].filter(isGuiTarget))
      pendingUpdates.current = null
      setError(null)
      setLoadedGeneration(generation)
      setLoading(false)
    }).catch((cause: unknown) => {
      if (cancelled || pendingUpdates.current !== updates) return
      pendingUpdates.current = null
      setError(toApiError(cause).detail)
      setLoadedGeneration(generation)
      setLoading(false)
    })
    return () => { cancelled = true }
  }, [generation, ready, revision])

  const refresh = useCallback((): void => {
    setLoading(true)
    setError(null)
    setRevision((current: number) => current + 1)
  }, [])

  const rememberTarget = useCallback((target: TargetInstance): void => {
    pendingUpdates.current?.set(target.target_registry_name, target)
    setTargets((current: TargetInstance[]) => [
      ...current.filter((item: TargetInstance) => item.target_registry_name !== target.target_registry_name),
      target,
    ].filter(isGuiTarget))
  }, [])

  const synchronizeTargets = useCallback((items: TargetInstance[]): void => {
    pendingUpdates.current = null
    setTargets(items.filter(isGuiTarget))
    setError(null)
    setLoadedGeneration(generation)
    setLoading(false)
  }, [generation])

  const isEmpty = ready && !loading && !error && loadedGeneration === generation && targets.length === 0
  // A generation change refetches but does not raise the loading flag, so the
  // previous runtime's targets would stay selectable until the new list lands.
  // Gate loading on the loaded generation the way the scorer registry does.
  const registryLoading = loading || !ready || loadedGeneration !== generation
  return { targets, loading: registryLoading, error, isEmpty, refresh, rememberTarget, synchronizeTargets }
}
