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
  /** Changes when a reload brings different fields, so lists can load their pages again with the new labels. */
  readonly fieldsRevision: number
}

/**
 * Loads the finding fields of an operation, or of the deployment when no operation is given, and again when the
 * runtime generation changes, because a live apply can change them.
 * @param operationId - The operation whose findings the fields label and check.
 * @param enabled - Loads only while true, and again each time it turns true, so a closed dialog fetches nothing.
 */
export function useFindingFields(operationId?: string, enabled = true): FindingFieldsState {
  const { generation } = useRuntime()
  const [fields, setFields] = useState<FindingFields | null>(null)
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  const [fieldsRevision, setFieldsRevision] = useState(0)
  const loadedFields = useRef<string | null>(null)

  useEffect(() => {
    if (!enabled) return
    let ignore = false
    operationsApi.getFindingFields(operationId)
      .then((loaded: FindingFields) => {
        if (!ignore) {
          const serialized = JSON.stringify(loaded)
          if (loadedFields.current !== null && loadedFields.current !== serialized) {
            setFieldsRevision((count: number) => count + 1)
          }
          loadedFields.current = serialized
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
  }, [attempt, enabled, generation, operationId])

  const retry = useCallback(() => {
    setError('')
    setAttempt((count: number) => count + 1)
  }, [])

  return { fields, error, retry, fieldsRevision }
}
