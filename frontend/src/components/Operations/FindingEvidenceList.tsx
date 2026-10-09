import { useEffect, useRef, useState } from 'react'

import {
  Button, Dialog, DialogActions, DialogBody, DialogContent, DialogSurface, DialogTitle,
  MessageBar, MessageBarBody, Spinner, Text,
} from '@fluentui/react-components'
import { ChevronLeftRegular, ChevronRightRegular, DeleteRegular } from '@fluentui/react-icons'
import { Link } from 'react-router'

import { operationsApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { FindingEvidence, FindingEvidenceListResponse } from '@/types'
import { attackConversationRoutePath } from '@/utils/routeParams'

import { useFindingEvidenceListStyles } from './FindingEvidenceList.styles'

const PAGE_SIZE = 20

interface FindingEvidenceListProps {
  operationId: string
  findingId: string
  count: number
  onDetached: () => void
}

export default function FindingEvidenceList({ operationId, findingId, count, onDetached }: FindingEvidenceListProps) {
  const styles = useFindingEvidenceListStyles()
  const [expanded, setExpanded] = useState(false)
  const [offset, setOffset] = useState(0)
  const [revision, setRevision] = useState(0)
  const [page, setPage] = useState<FindingEvidenceListResponse | null>(null)
  const [settledKey, setSettledKey] = useState('')
  const [error, setError] = useState('')
  const [removing, setRemoving] = useState<FindingEvidence | null>(null)
  const [removeError, setRemoveError] = useState('')
  const [busy, setBusy] = useState(false)
  const busyRef = useRef(false)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const wasRemoving = useRef(false)
  const requestKey = JSON.stringify([operationId, findingId, offset, revision])

  useEffect(() => {
    if (!expanded) return
    let ignore = false
    operationsApi.listFindingEvidence(operationId, findingId, { limit: PAGE_SIZE, offset })
      .then(result => {
        if (ignore) return
        if (result.items.length === 0 && offset > 0) {
          setOffset(value => Math.max(0, value - PAGE_SIZE))
          return
        }
        setPage(result); setError(''); setSettledKey(requestKey)
      })
      .catch((cause: unknown) => {
        if (!ignore) { setError(toApiError(cause).detail); setSettledKey(requestKey) }
      })
    return () => { ignore = true }
  }, [expanded, operationId, findingId, offset, requestKey])

  useEffect(() => {
    if (wasRemoving.current && !removing) triggerRef.current?.focus()
    wasRemoving.current = removing !== null
  }, [removing])

  const detach = async (): Promise<void> => {
    if (!removing || busyRef.current) return
    busyRef.current = true
    setBusy(true)
    setRemoveError('')
    try {
      await operationsApi.detachFindingEvidence(operationId, findingId, removing.id)
      setRemoving(null)
      setRevision(value => value + 1)
      onDetached()
    } catch (cause: unknown) {
      setRemoveError(toApiError(cause).detail)
    } finally {
      busyRef.current = false
      setBusy(false)
    }
  }

  return (
    <div className={styles.root}>
      <Button ref={triggerRef} className={styles.button} aria-expanded={expanded}
        onClick={() => { setExpanded(value => !value) }}>Evidence ({count})</Button>
      {expanded && <div role="region" aria-label="Finding evidence" className={styles.region}>
        {settledKey !== requestKey ? <Spinner label="Loading evidence" /> : error ? (
          <MessageBar intent="error"><MessageBarBody>Could not load evidence: {error}{' '}
            <Button onClick={() => { setRevision(value => value + 1) }}>Retry evidence</Button>
          </MessageBarBody></MessageBar>
        ) : page?.items.length === 0 ? <Text>No evidence attached.</Text> : (
          <ul className={styles.list}>{page?.items.map(({ item, availability, scenario_result_id: scenarioResultId }) => (
            <li key={item.id} className={styles.item}>
              <Text>{item.conversation_id}</Text>
              <time dateTime={item.attached_at}>{new Date(item.attached_at).toLocaleString()}</time>
              <div className={styles.actions}>
                {availability === 'available' ? (
                  <Link to={attackConversationRoutePath(item.attack_result_id, item.conversation_id, scenarioResultId, item.id)}>
                    Open conversation
                  </Link>
                ) : <Text>Evidence unavailable</Text>}
                <Button className={styles.button} icon={<DeleteRegular />} title="Remove link"
                  aria-label={`Remove link: ${item.conversation_id}`}
                  onClick={() => { setRemoving(item); setRemoveError('') }} />
              </div>
            </li>
          ))}</ul>
        )}
        <div className={styles.actions}>
          <Button className={styles.button} icon={<ChevronLeftRegular />} title="Previous evidence"
            aria-label="Previous evidence" disabled={offset === 0 || busy}
            onClick={() => { setOffset(value => Math.max(0, value - PAGE_SIZE)) }} />
          <Button className={styles.button} icon={<ChevronRightRegular />} title="Next evidence"
            aria-label="Next evidence"
            disabled={settledKey !== requestKey || !page?.has_more || Boolean(error) || busy}
            onClick={() => { setOffset(page?.next_offset ?? 0) }} />
        </div>
      </div>}
      <Dialog open={removing !== null} onOpenChange={(_, data) => {
        if (!data.open && !busyRef.current) setRemoving(null)
      }}>
        <DialogSurface><DialogBody>
          <DialogTitle>Remove evidence link?</DialogTitle>
          <DialogContent>
            <Text>Remove the link to conversation {removing?.conversation_id}? The conversation is not deleted.</Text>
            {removeError && <MessageBar intent="error"><MessageBarBody>Could not remove link: {removeError}</MessageBarBody></MessageBar>}
          </DialogContent>
          <DialogActions>
            <Button className={styles.button} disabled={busy} onClick={() => { setRemoving(null) }}>Cancel</Button>
            <Button className={styles.button} appearance="primary" disabled={busy}
              onClick={() => { void detach() }}>{busy ? 'Removing...' : 'Remove evidence link'}</Button>
          </DialogActions>
        </DialogBody></DialogSurface>
      </Dialog>
    </div>
  )
}
