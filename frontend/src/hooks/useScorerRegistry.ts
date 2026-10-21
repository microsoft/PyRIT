import { useCallback, useEffect, useState } from 'react'

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
  useEffect(() => {
    if (!ready) return
    let cancelled = false
    listRegisteredScorers().then((items: ScorerInstance[]) => {
      if (cancelled) return
      setScorers(items)
      setError(null)
      setLoading(false)
      setLoadedGeneration(generation)
    }).catch((cause: unknown) => {
      if (cancelled) return
      setError(toApiError(cause).detail)
      setLoading(false)
      setLoadedGeneration(generation)
    })
    return () => { cancelled = true }
  }, [generation, ready, revision])
  const refresh = useCallback((): void => {
    setLoading(true)
    setError(null)
    setRevision((current: number) => current + 1)
  }, [])
  return { scorers, loading: loading || !ready || loadedGeneration !== generation, error, refresh }
}
