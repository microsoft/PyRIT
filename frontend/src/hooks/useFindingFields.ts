import { useCallback, useEffect, useRef, useState } from 'react'

import { operationsApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { FindingFields } from '@/types'

import { useRuntime } from './useRuntime'

interface FindingFieldsState {
  /** Null until the fields first load. A reload keeps the last fields until the new ones arrive. */
  readonly fields: FindingFields | null
  /** The last load failure, or an empty string. */
  readonly error: string
  readonly retry: () => void
  /** Changes when a reload brings a different severity ranking, so lists can load their pages again. */
  readonly orderRevision: number
}

/**
 * Loads the active finding fields, and again when the runtime generation changes, because a live apply can
 * change them.
 * @param enabled - Loads only while true, and again each time it turns true, so a closed dialog fetches nothing.
 */
export function useFindingFields(enabled = true): FindingFieldsState {
  const { generation } = useRuntime()
  const [fields, setFields] = useState<FindingFields | null>(null)
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  const [orderRevision, setOrderRevision] = useState(0)
  const loadedOrder = useRef<string | null>(null)

  useEffect(() => {
    if (!enabled) return
    let ignore = false
    operationsApi.getFindingFields()
      .then((loaded: FindingFields) => {
        if (!ignore) {
          const order = JSON.stringify(loaded.severity.options.map(({ key }) => key))
          if (loadedOrder.current !== null && loadedOrder.current !== order) {
            setOrderRevision((count: number) => count + 1)
          }
          loadedOrder.current = order
          setFields(loaded)
          setError('')
        }
      })
      .catch((cause: unknown) => {
        if (!ignore) setError(toApiError(cause).detail)
      })
    return () => {
      ignore = true
    }
  }, [attempt, enabled, generation])

  const retry = useCallback(() => {
    setError('')
    setAttempt((count: number) => count + 1)
  }, [])

  return { fields, error, retry, orderRevision }
}
