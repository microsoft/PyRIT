import { useEffect, useRef, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router'
import {
  Badge, Button, Dialog, DialogBody, DialogContent, DialogSurface, DialogTitle,
  MessageBar, MessageBarBody, mergeClasses, Spinner, Text,
} from '@fluentui/react-components'
import { ArrowLeftRegular } from '@fluentui/react-icons'

import { DEFAULT_HISTORY_FILTERS, filtersToSearchParams } from '@/components/History/historyFilters'
import { operationsApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import { findingSeverityLabel, findingSeverityColor } from '@/utils/findingSeverity'
import type { Finding, FindingCreate, FindingListItem, FindingListResponse, Operation } from '@/types'
import FindingDialog from './FindingDialog'
import FindingEvidenceList from './FindingEvidenceList'
import { useOperationsStyles } from './Operations.styles'

const PAGE_SIZE = 20

type OperationState =
  | { key: string; status: 'loaded'; operation: Operation }
  | { key: string; status: 'missing' }
  | { key: string; status: 'error'; error: string }

export default function OperationDetailPage() {
  const { operationId = '' } = useParams()
  return <OperationDetailContent key={operationId} operationId={operationId} />
}

function OperationDetailContent({ operationId }: { operationId: string }) {
  const styles = useOperationsStyles()
  const [params, setParams] = useSearchParams()
  const rawOffset = Number(params.get('offset') ?? 0)
  const offset = Number.isSafeInteger(rawOffset) && rawOffset >= 0 ? rawOffset : 0
  const [operationRevision, setOperationRevision] = useState(0)
  const [operationState, setOperationState] = useState<OperationState | null>(null)
  const operationKey = JSON.stringify([operationId, operationRevision])
  const [page, setPage] = useState<FindingListResponse>({ items: [], has_more: false, next_offset: null })
  const [revision, setRevision] = useState(0)
  const [evidenceRevision, setEvidenceRevision] = useState(0)
  const [settledKey, setSettledKey] = useState('')
  const [error, setError] = useState('')
  const requestKey = JSON.stringify([operationId, offset, revision])
  const [notice, setNotice] = useState('')
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState<Finding | null>(null)
  const [deleting, setDeleting] = useState<Finding | null>(null)
  const [deleteError, setDeleteError] = useState('')
  const [removing, setRemoving] = useState(false)
  const deleteSubmitting = useRef(false)
  const returnFocusRef = useRef<HTMLElement | null>(null)
  const newFindingRef = useRef<HTMLButtonElement>(null)
  const wasModalOpen = useRef(false)
  const modalOpen = open || deleting !== null

  useEffect(() => {
    if (wasModalOpen.current && !modalOpen) {
      const target = returnFocusRef.current
      if (target?.isConnected) target.focus()
      else newFindingRef.current?.focus()
    }
    wasModalOpen.current = modalOpen
  }, [modalOpen])

  useEffect(() => {
    let ignore = false
    operationsApi.get(operationId)
      .then(operation => { if (!ignore) setOperationState({ key: operationKey, status: 'loaded', operation }) })
      .catch((cause: unknown) => {
        if (ignore) return
        const apiError = toApiError(cause)
        setOperationState(apiError.status === 404
          ? { key: operationKey, status: 'missing' }
          : { key: operationKey, status: 'error', error: apiError.detail })
      })
    return () => { ignore = true }
  }, [operationId, operationKey])

  useEffect(() => {
    let ignore = false
    operationsApi.listFindings(operationId, { limit: PAGE_SIZE, offset })
      .then(result => { if (!ignore) { setPage(result); setError(''); setSettledKey(requestKey) } })
      .catch((cause: unknown) => { if (!ignore) { setError(toApiError(cause).detail); setSettledKey(requestKey) } })
    return () => { ignore = true }
  }, [operationId, offset, requestKey, evidenceRevision])

  const navigatePage = (nextOffset: number): void => {
    setParams(nextOffset > 0 ? { offset: String(nextOffset) } : {})
  }

  const save = async (draft: FindingCreate): Promise<void> => {
    const saved = editing
      ? await operationsApi.updateFinding(operationId, editing.id, draft)
      : await operationsApi.createFinding(operationId, draft)
    setNotice(`Finding ${editing ? 'updated' : 'saved'}: ${saved.title}`)
    setOpen(false)
    setEditing(null)
    navigatePage(0)
    setRevision(value => value + 1)
  }

  const remove = async (): Promise<void> => {
    if (!deleting || deleteSubmitting.current) return
    deleteSubmitting.current = true
    setRemoving(true)
    setDeleteError('')
    try {
      await operationsApi.deleteFinding(operationId, deleting.id)
      setNotice(`Finding deleted: ${deleting.title}`)
      setDeleting(null)
      navigatePage(0)
      setRevision(value => value + 1)
    } catch (cause: unknown) {
      setDeleteError(toApiError(cause).detail)
    } finally {
      deleteSubmitting.current = false
      setRemoving(false)
    }
  }

  const backLink = (
    <Link to="/operations" className={styles.link}><ArrowLeftRegular /> Operations</Link>
  )
  const current = operationState?.key === operationKey ? operationState : null

  if (current === null) {
    return <section className={styles.root} aria-label="Operation">{backLink}<Spinner label="Loading operation…" /></section>
  }
  if (current.status === 'missing') {
    return (
      <section className={styles.root} aria-label="Operation">
        {backLink}
        <Text as="h1" size={600} weight="semibold" className={styles.heading}>Operation not found</Text>
        <Text>It may have been removed, or the link may be incorrect.</Text>
      </section>
    )
  }
  if (current.status === 'error') {
    return (
      <section className={styles.root} aria-label="Operation">
        {backLink}
        <MessageBar intent="error"><MessageBarBody>
          Could not load operation: {current.error}{' '}
          <Button className={styles.button} onClick={() => { setOperationRevision(value => value + 1) }}>Retry</Button>
        </MessageBarBody></MessageBar>
      </section>
    )
  }

  const renderFinding = (finding: FindingListItem): React.ReactNode => (
    <li key={finding.id} className={styles.item}>
      <h2 className={styles.title}>{finding.title}</h2>
      <div className={styles.metadata}>
        <Badge appearance="tint" color={findingSeverityColor(finding.severity)}>
          {findingSeverityLabel(finding)}
        </Badge>
        <time dateTime={finding.created_at}>{new Date(finding.created_at).toLocaleString()}</time>
      </div>
      {finding.harm_type && <Text>Harm-type: {finding.harm_type === 'Other' ? finding.harm_type_other : finding.harm_type}</Text>}
      {finding.description && <p className={styles.description}>{finding.description}</p>}
      <FindingEvidenceList operationId={operationId} findingId={finding.id} count={finding.evidence_count}
        onDetached={() => { setEvidenceRevision(value => value + 1) }} />
      <div className={mergeClasses(styles.actions, styles.findingActions)}>
        <Button className={styles.button} aria-label={`Edit finding: ${finding.title}`} onClick={event => {
          returnFocusRef.current = event.currentTarget
          setEditing(finding)
          setOpen(true)
        }}>Edit</Button>
        <Button className={styles.button} aria-label={`Delete finding: ${finding.title}`} onClick={event => {
          returnFocusRef.current = event.currentTarget
          setDeleteError('')
          setDeleting(finding)
        }}>Delete</Button>
      </div>
    </li>
  )

  return (
    <section className={styles.root} aria-label="Operation">
      {backLink}
      <header className={styles.header}>
        <Text as="h1" size={600} weight="semibold" className={styles.heading} translate="no">
          {current.operation.name}
        </Text>
        <Button ref={newFindingRef} appearance="primary" className={styles.button} onClick={event => {
          returnFocusRef.current = event.currentTarget
          setEditing(null)
          setOpen(true)
        }}>New finding</Button>
        {open && <FindingDialog editing={editing !== null}
          initialValues={editing ? {
            title: editing.title, description: editing.description, severity: editing.severity,
            severity_other: editing.severity_other ?? null, harm_type: editing.harm_type ?? null,
            harm_type_other: editing.harm_type_other ?? null,
          } : undefined}
          onSave={save} onClose={() => { setOpen(false); setEditing(null) }} />}
      </header>
      <Dialog open={deleting !== null} onOpenChange={(_, data) => {
        if (!removing && !data.open) { setDeleting(null); setDeleteError('') }
      }}>
        <DialogSurface>
          <DialogBody>
            <DialogTitle>Delete finding?</DialogTitle>
            <DialogContent className={styles.form}>
              <Text>Permanently delete “{deleting?.title}”? This cannot be undone.</Text>
              {deleteError && <MessageBar intent="error" aria-live="polite"><MessageBarBody>{deleteError}</MessageBarBody></MessageBar>}
              <div className={styles.actions}>
                <Button className={styles.button} disabled={removing} onClick={() => {
                  setDeleting(null)
                  setDeleteError('')
                }}>Cancel</Button>
                <Button appearance="primary" className={styles.button} disabledFocusable={removing}
                  onClick={() => { void remove() }}>{removing ? 'Deleting…' : 'Delete finding'}</Button>
              </div>
            </DialogContent>
          </DialogBody>
        </DialogSurface>
      </Dialog>
      <Link className={styles.link} to={`/history/attacks?${filtersToSearchParams({
        ...DEFAULT_HISTORY_FILTERS, operation: [current.operation.name],
      })}`}>View execution history</Link>
      <Text>Human assessments, separate from attack outcomes and scores.</Text>
      {notice && <MessageBar intent="success" aria-live="polite"><MessageBarBody>{notice}</MessageBarBody></MessageBar>}
      {settledKey !== requestKey ? <Spinner label="Loading findings…" /> : error ? (
        <MessageBar intent="error"><MessageBarBody>
          Could not load findings: {error}{' '}
          <Button className={styles.button} onClick={() => { setRevision(value => value + 1) }}>Retry</Button>
        </MessageBarBody></MessageBar>
      ) : (
        <>
          {page.items.length === 0 ? <Text>No findings in this operation yet. Create one to record an assessment.</Text>
            : <ul className={styles.list}>{page.items.map(renderFinding)}</ul>}
          <div className={styles.header}>
            <Button className={styles.button} disabled={offset === 0} onClick={() => { navigatePage(0) }}>First</Button>
            <Text>Page {Math.floor(offset / PAGE_SIZE) + 1}</Text>
            <Button className={styles.button} disabled={!page.has_more}
              onClick={() => { if (page.next_offset !== null) navigatePage(page.next_offset) }}>Next</Button>
          </div>
        </>
      )}
    </section>
  )
}
