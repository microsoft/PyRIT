import { useCallback, useEffect, useState } from 'react'

import { attacksApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { AttackFindingItem } from '@/types'

export interface AttackFindingsState {
  findings: AttackFindingItem[]
  loading: boolean
  error: string | null
  refresh: () => void
}

interface LoadedFindings {
  attackResultId: string
  revision: number
  findings: AttackFindingItem[]
  error: string | null
}

/** Findings in any Operation that link this attack as evidence. Without an attack id, there are none. */
export function useAttackFindings(attackResultId: string | null | undefined): AttackFindingsState {
  const [revision, setRevision] = useState(0)
  const [loaded, setLoaded] = useState<LoadedFindings | null>(null)

  useEffect(() => {
    if (!attackResultId) return
    let ignore = false
    attacksApi.getAttackFindings(attackResultId)
      .then((response) => {
        if (!ignore) setLoaded({ attackResultId, revision, findings: response.items, error: null })
      })
      .catch((cause: unknown) => {
        if (!ignore) setLoaded({ attackResultId, revision, findings: [], error: toApiError(cause).detail })
      })
    return () => { ignore = true }
  }, [attackResultId, revision])

  const refresh = useCallback((): void => {
    setRevision((current: number) => current + 1)
  }, [])

  // Keep the previous answer for the same attack while a refresh is in flight, so attached findings do not flicker.
  const current = attackResultId && loaded?.attackResultId === attackResultId ? loaded : null
  return {
    findings: current?.findings ?? [],
    loading: Boolean(attackResultId) && current?.revision !== revision,
    error: current?.error ?? null,
    refresh,
  }
}
