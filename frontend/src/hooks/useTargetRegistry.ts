import { useCallback, useEffect, useRef, useState } from 'react'

import { toApiError } from '@/services/errors'
import { listRegisteredTargets } from '@/services/targetRegistry'
import type { TargetInstance } from '@/types'

export function useTargetRegistry() {
  const [targets, setTargets] = useState<TargetInstance[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [revision, setRevision] = useState(0)
  const pendingUpdates = useRef<Map<string, TargetInstance> | null>(null)

  useEffect(() => {
    let cancelled = false
    const updates = new Map<string, TargetInstance>()
    pendingUpdates.current = updates
    listRegisteredTargets().then((items: TargetInstance[]) => {
      if (cancelled) return
      const merged = new Map(items.map((item: TargetInstance) => [item.target_registry_name, item]))
      updates.forEach((target: TargetInstance, name: string) => merged.set(name, target))
      setTargets([...merged.values()])
      pendingUpdates.current = null
      setError(null)
      setLoading(false)
    }).catch((cause: unknown) => {
      if (cancelled) return
      pendingUpdates.current = null
      setError(toApiError(cause).detail)
      setLoading(false)
    })
    return () => { cancelled = true }
  }, [revision])

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
    ])
  }, [])

  return { targets, loading, error, refresh, rememberTarget }
}
