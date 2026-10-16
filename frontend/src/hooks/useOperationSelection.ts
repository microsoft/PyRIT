import { useCallback, useEffect, useState } from 'react'

import { operationsApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { Operation, OperationSelection } from '@/types'

export type OperationSelectionState =
  | { status: 'empty' }
  | { status: 'unregistered'; name: string }
  | { status: 'loading' }
  | { status: 'resolved'; operation: Operation }
  | { status: 'missing' }
  | { status: 'error'; error: string }

/** Looks up a selected Operation ID. A name selection stays unregistered until the user resolves it. */
export function useOperationSelection(selection: OperationSelection | null, generation: string) {
  const id = selection && 'id' in selection ? selection.id : null
  const [revision, setRevision] = useState(0)
  const [lookup, setLookup] = useState<{ key: string; state: OperationSelectionState } | null>(null)
  const keyFor = useCallback((operationId: string) => `${generation}:${operationId}:${revision}`, [generation, revision])
  const key = id ? keyFor(id) : null

  useEffect(() => {
    if (!id || !key) return
    let ignore = false
    operationsApi.get(id)
      .then(operation => { if (!ignore) setLookup({ key, state: { status: 'resolved', operation } }) })
      .catch((cause: unknown) => {
        if (ignore) return
        const error = toApiError(cause)
        setLookup({ key, state: error.status === 404 ? { status: 'missing' } : { status: 'error', error: error.detail } })
      })
    return () => { ignore = true }
  }, [id, key])

  const state: OperationSelectionState = !selection ? { status: 'empty' }
    : 'name' in selection ? { status: 'unregistered', name: selection.name }
    : lookup?.key === key ? lookup.state : { status: 'loading' }

  /** Records an Operation the user just chose, so selecting it does not wait for another lookup. */
  const remember = useCallback((operation: Operation) => {
    setLookup({ key: keyFor(operation.id), state: { status: 'resolved', operation } })
  }, [keyFor])

  const retry = useCallback(() => setRevision(value => value + 1), [])
  return { state, remember, retry }
}
