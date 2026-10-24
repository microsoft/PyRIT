import { useCallback, useEffect, useState } from 'react'

import { techniquesApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { TechniqueConfiguration, TechniqueEditResponse } from '@/types'

interface TechniqueResource {
  key: string
  original?: TechniqueConfiguration
  applied?: TechniqueEditResponse
  pending?: boolean
  revision?: number
  error?: string
}

/** One form-local edit. Neither loading nor applying settings changes the registry. */
export function useRunTechniqueEditor(source: string | undefined, generation: string | null, ready: boolean) {
  const key = JSON.stringify([source, generation])
  const [resource, setResource] = useState<TechniqueResource | null>(null)
  const [retry, setRetry] = useState(0)
  useEffect(() => {
    if (!source || !ready) return
    let ignore = false
    void techniquesApi.getConfiguration(source).then((configuration) => {
      if (!ignore) setResource({ key, original: configuration })
    }).catch((error: unknown) => {
      if (!ignore) setResource({ key, error: toApiError(error).detail })
    })
    return () => { ignore = true }
  }, [source, generation, key, ready, retry])

  const current = ready && resource?.key === key ? resource : null
  const applied = current?.applied
  const edited = applied !== undefined && Object.keys(applied.override.changes).length > 0
  const markPending = useCallback((): void => {
    setResource((value) => value?.key === key ? { ...value, pending: true } : value)
  }, [key])
  const apply = useCallback((result: TechniqueEditResponse): void => {
    setResource((value) => value?.key === key ? { ...value, applied: result, pending: false } : value)
  }, [key])
  return {
    original: current?.original,
    configuration: applied?.configuration ?? current?.original,
    override: edited ? applied.override : undefined,
    edited,
    hasChanges: edited || current?.pending === true,
    pending: current?.pending === true,
    formKey: `${key}:${current?.revision ?? 0}`,
    error: current?.error,
    unresolved: Boolean(source) && !current?.original,
    retry: (): void => { setResource(null); setRetry((value) => value + 1) },
    reset: (): void => { setResource((value) => value?.key === key ? {
      ...value, applied: undefined, pending: false, revision: (value.revision ?? 0) + 1,
    } : value) },
    discard: (): void => { setResource(null) },
    markPending,
    apply,
  }
}
