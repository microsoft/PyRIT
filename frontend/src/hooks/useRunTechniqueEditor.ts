import { useEffect, useState } from 'react'

import { techniquesApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { TechniqueConfiguration, TechniqueEditResponse } from '@/types'

interface TechniqueResource {
  key: string
  original?: TechniqueConfiguration
  applied?: TechniqueEditResponse
  editing?: boolean
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
  return {
    original: current?.original,
    configuration: applied?.configuration ?? current?.original,
    override: edited ? applied.override : undefined,
    edited,
    editing: current?.editing === true,
    error: current?.error,
    unresolved: Boolean(source) && !current?.original,
    retry: (): void => { setResource(null); setRetry((value) => value + 1) },
    open: (): void => { setResource((value) => value?.key === key ? { ...value, editing: true } : value) },
    close: (): void => { setResource((value) => value?.key === key ? { ...value, editing: false } : value) },
    reset: (): void => { setResource((value) => value?.key === key ? { ...value, applied: undefined, editing: false } : value) },
    discard: (): void => { setResource(null) },
    apply: (result: TechniqueEditResponse): void => {
      setResource((value) => value?.key === key ? { ...value, applied: result, editing: false } : value)
    },
  }
}
