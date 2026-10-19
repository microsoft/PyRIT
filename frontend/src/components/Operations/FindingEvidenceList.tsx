import { useEffect, useRef, useState } from 'react'

import {
  Button, Dialog, DialogActions, DialogBody, DialogContent, DialogSurface, DialogTitle,
  MessageBar, MessageBarBody, Spinner, Table, TableBody, TableCell, TableHeader, TableHeaderCell, TableRow, Text,
} from '@fluentui/react-components'
import { ChevronDownRegular, ChevronLeftRegular, ChevronRightRegular, DeleteRegular } from '@fluentui/react-icons'
import { Link } from 'react-router'

import OutcomeBadge from '@/components/OutcomeBadge'
import { operationsApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { AttackOutcome, BackendScore, FindingEvidence, FindingEvidenceListResponse } from '@/types'
import { attackConversationRoutePath } from '@/utils/routeParams'

import { useFindingEvidenceListStyles } from './FindingEvidenceList.styles'

const PAGE_SIZE = 20

interface FindingEvidenceListProps {
  operationId: string
  findingId: string
  count: number
  onDetached: () => void
  /** Finding-level actions, shown at the far end of the Evidence toggle's row. */
  actions?: React.ReactNode
}

/** The attack's outcome with the score behind it, the same result Chat shows for the attack. */
function EvidenceResult({ outcome, score }: { outcome: AttackOutcome; score?: BackendScore | null }) {
  const styles = useFindingEvidenceListStyles()
  return (
    <div className={styles.stack}>
      <OutcomeBadge outcome={outcome} appearance="tint" size="small" />
      {score ? <>
        <Text size={200}>{score.scorer_type}</Text>
        {score.score_rationale && <Text size={200}>{score.score_rationale}</Text>}
      </> : <Text size={200}>Not scored</Text>}
    </div>
  )
}

/** An attack that no longer exists has no objective or outcome, so its ID stands in for it. */
function evidenceLabel(item: FindingEvidence, outcome?: AttackOutcome | null, objective?: string | null): string {
  if (!outcome) return item.attack_result_id
  return objective?.trim() || 'No objective'
}

export default function FindingEvidenceList({
  operationId, findingId, count, onDetached, actions,
}: FindingEvidenceListProps) {
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
      <div className={styles.header}>
        <Button ref={triggerRef} className={styles.button} aria-expanded={expanded}
          icon={expanded ? <ChevronDownRegular /> : <ChevronRightRegular />}
          onClick={() => { setExpanded(value => !value) }}>Evidence ({count})</Button>
        {actions && <div className={styles.headerActions}>{actions}</div>}
      </div>
      {expanded && <div role="region" aria-label="Finding evidence" className={styles.region}>
        {settledKey !== requestKey ? <Spinner label="Loading evidence" /> : error ? (
          <MessageBar intent="error"><MessageBarBody>Could not load evidence: {error}{' '}
            <Button onClick={() => { setRevision(value => value + 1) }}>Retry evidence</Button>
          </MessageBarBody></MessageBar>
        ) : page?.items.length === 0 ? <Text>No evidence attached.</Text> : (
          <div className={styles.tableScroll}>
            <Table aria-label="Evidence attacks" className={styles.table}>
              <TableHeader><TableRow>
                <TableHeaderCell className={styles.textColumn}>Attack</TableHeaderCell>
                <TableHeaderCell className={styles.textColumn}>Result</TableHeaderCell>
                <TableHeaderCell>Attached</TableHeaderCell>
                <TableHeaderCell>Actions</TableHeaderCell>
              </TableRow></TableHeader>
              <TableBody>{page?.items.map(({
                item, availability, scenario_result_id: scenarioResultId, source_operation_id: sourceOperationId,
                source_operation_name: sourceOperationName, objective, outcome, score,
                conversation_id: conversationId,
              }) => {
                const label = evidenceLabel(item, outcome, objective)
                return (
                  <TableRow key={item.id}>
                    <TableCell className={styles.cell}><div className={styles.stack}>
                      <Text>{label}</Text>
                      {outcome && sourceOperationId !== operationId && (
                        <Text size={200}>{sourceOperationName ? `From operation: ${sourceOperationName}` : 'Not in an operation'}</Text>
                      )}
                    </div></TableCell>
                    <TableCell className={styles.cell}>{outcome && <EvidenceResult outcome={outcome} score={score} />}</TableCell>
                    <TableCell className={styles.cell}>
                      <time dateTime={item.attached_at}>{new Date(item.attached_at).toLocaleString()}</time>
                    </TableCell>
                    <TableCell className={styles.cell}><div className={styles.rowActions}>
                      {availability === 'available' && conversationId ? (
                        <Link to={attackConversationRoutePath(item.attack_result_id, conversationId, scenarioResultId, item.id)}>
                          Open attack
                        </Link>
                      ) : <Text>Evidence unavailable</Text>}
                      <Button className={styles.button} icon={<DeleteRegular />} title="Remove link"
                        aria-label={`Remove link: ${label}`}
                        onClick={() => { setRemoving(item); setRemoveError('') }} />
                    </div></TableCell>
                  </TableRow>
                )
              })}</TableBody>
            </Table>
          </div>
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
            <Text>Remove the link to attack {removing?.attack_result_id}? The attack is not deleted.</Text>
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
