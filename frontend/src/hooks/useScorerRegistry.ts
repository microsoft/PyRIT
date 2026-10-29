import { useCallback, useEffect, useRef, useState } from 'react'

import { useRuntime } from '@/hooks/useRuntime'
import { listRegisteredScorers } from '@/services/scorerRegistry'
import { toApiError } from '@/services/errors'
import type { ScorerInstance } from '@/types'

export function useScorerRegistry() {
  const { generation, ready } = useRuntime()
  const [scorers, setScorers] = useState<ScorerInstance[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [revision, setRevision] = useState(0)
  const [loadedGeneration, setLoadedGeneration] = useState<string | null>(null)
  const pending = useRef(false)
  useEffect(() => {
    if (!ready) return
    let cancelled = false
    pending.current = true
    listRegisteredScorers().then((items: ScorerInstance[]) => {
      if (cancelled) return
      setScorers(items)
      setError(null)
      setLoading(false)
      setLoadedGeneration(generation)
      pending.current = false
    }).catch((cause: unknown) => {
      if (cancelled) return
      setError(toApiError(cause).detail)
      setLoading(false)
      setLoadedGeneration(generation)
      pending.current = false
    })
    return () => { cancelled = true; pending.current = false }
  }, [generation, ready, revision])
  const refresh = useCallback((): void => {
    if (pending.current) return
    setRevision((current: number) => current + 1)
  }, [])
  return { scorers, loading: loading || !ready || loadedGeneration !== generation, error, refresh }
}
