import { useEffect, useRef, useState } from 'react'
import { createSearchParams, Link, useNavigate, useParams, useSearchParams } from 'react-router'
import {
  Button, Dialog, DialogBody, DialogContent, DialogSurface, DialogTitle,
  MessageBar, MessageBarBody, Spinner, Text,
} from '@fluentui/react-components'
import { AddRegular, ArrowLeftRegular } from '@fluentui/react-icons'

import { DEFAULT_HISTORY_FILTERS, filtersToSearchParams } from '@/components/History/historyFilters'
import { operationsApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { FindingCreate, FindingListItem, FindingListResponse, OperationDetail } from '@/types'
import FindingDialog, { type FindingSaveAttempt } from './FindingDialog'
import FindingExportDialog from './FindingExportDialog'
import FindingPathsDialog from './FindingPathsDialog'
import FindingTreeItem, { type FindingTreeActions } from './FindingTreeItem'
import OperationDeleteDialog from './OperationDeleteDialog'
import OperationEditDialog from './OperationEditDialog'
import { useOperationsStyles } from './Operations.styles'

const PAGE_SIZE = 20

type OperationState =
  | { key: string; status: 'loaded'; operation: OperationDetail }
  | { key: string; status: 'missing' }
  | { key: string; status: 'error'; error: string }

/** The finding dialog's subject: a new finding, a new sub-finding of a parent, or an existing finding. */
interface FindingDialogTarget {
  editing: FindingListItem | null
  parent: { id: string; title: string } | null
}

/** A finding whose paths are being edited, with the sub-findings its steps choose from. */
interface PathsTarget {
  finding: FindingListItem
  subFindings: FindingListItem[]
}

interface OperationDetailPageProps {
  /** Called after the Operation is renamed or deleted, so views holding it can refresh. */
  onOperationChanged?: () => void
}

export default function OperationDetailPage({ onOperationChanged }: OperationDetailPageProps) {
  const { operationId = '' } = useParams()
  return <OperationDetailContent key={operationId} operationId={operationId} onOperationChanged={onOperationChanged} />
}

function OperationDetailContent({ operationId, onOperationChanged }: { operationId: string } & OperationDetailPageProps) {
  const styles = useOperationsStyles()
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const rawOffset = Number(params.get('offset') ?? 0)
  const offset = Number.isSafeInteger(rawOffset) && rawOffset >= 0 ? rawOffset : 0
  const historySearch = filtersToSearchParams({ ...DEFAULT_HISTORY_FILTERS, operationId: [operationId] })
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
  const [dialog, setDialog] = useState<FindingDialogTarget | null>(null)
  const [deleting, setDeleting] = useState<FindingListItem | null>(null)
  const [deleteError, setDeleteError] = useState('')
  const [removing, setRemoving] = useState(false)
  const [pathsTarget, setPathsTarget] = useState<PathsTarget | null>(null)
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set())
  const deleteSubmitting = useRef(false)
  const returnFocusRef = useRef<HTMLElement | null>(null)
  const newFindingRef = useRef<HTMLButtonElement>(null)
  const wasModalOpen = useRef(false)
  const modalOpen = dialog !== null || deleting !== null || pathsTarget !== null

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
        // A failed usage refresh after a finding change keeps the page; the server still checks before deleting.
        setOperationState(state => state?.key === operationKey && state.status === 'loaded' ? state
          : apiError.status === 404 ? { key: operationKey, status: 'missing' }
            : { key: operationKey, status: 'error', error: apiError.detail })
      })
    return () => { ignore = true }
  }, [operationId, operationKey, revision])

  useEffect(() => {
    let ignore = false
    operationsApi.listFindings(operationId, { limit: PAGE_SIZE, offset, top_level: true })
      .then(result => { if (!ignore) { setPage(result); setError(''); setSettledKey(requestKey) } })
      .catch((cause: unknown) => { if (!ignore) { setError(toApiError(cause).detail); setSettledKey(requestKey) } })
    return () => { ignore = true }
  }, [operationId, offset, requestKey, evidenceRevision])

  const navigatePage = (nextOffset: number): void => {
    // Writing the same query again would push a duplicate history entry for an
    // identical URL, so a save or delete on the first page must not navigate.
    const next = createSearchParams(nextOffset > 0 ? { offset: String(nextOffset) } : {})
    if (next.toString() === params.toString()) return
    setParams(next)
  }

  /** Reload after a change. Top-level changes can reorder the list, so they return to its first page. */
  const refresh = (topLevel: boolean): void => {
    if (topLevel) navigatePage(0)
    setRevision(value => value + 1)
  }

  const save = async (draft: FindingCreate, attempt: FindingSaveAttempt): Promise<void> => {
    if (dialog === null) return
    const { editing, parent } = dialog
    const saved = editing
      ? await operationsApi.updateFinding(operationId, editing.id, {
        ...draft, parent_finding_id: editing.parent_finding_id,
      })
      : await operationsApi.createFinding(operationId, {
        ...draft, id: attempt.id, ...(parent ? { parent_finding_id: parent.id } : {}),
        ...(attempt.attackResultId ? { attack_result_id: attempt.attackResultId } : {}),
      })
    setNotice(`Finding ${editing ? 'updated' : 'saved'}: ${saved.title}`)
    setDialog(null)
    if (parent) setExpanded(current => new Set(current).add(parent.id))
    refresh(parent === null && !editing?.parent_finding_id)
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
      refresh(!deleting.parent_finding_id)
    } catch (cause: unknown) {
      setDeleteError(toApiError(cause).detail)
    } finally {
      deleteSubmitting.current = false
      setRemoving(false)
    }
  }

  const treeActions: FindingTreeActions = {
    onToggle: findingId => {
      setExpanded(current => {
        const next = new Set(current)
        if (!next.delete(findingId)) next.add(findingId)
        return next
      })
    },
    onEdit: (finding, parentTitle, trigger) => {
      returnFocusRef.current = trigger
      setDialog({
        editing: finding,
        parent: finding.parent_finding_id && parentTitle !== undefined
          ? { id: finding.parent_finding_id, title: parentTitle } : null,
      })
    },
    onAddSubFinding: (parent, trigger) => {
      returnFocusRef.current = trigger
      setDialog({ editing: null, parent: { id: parent.id, title: parent.title } })
    },
    onDelete: (finding, trigger) => {
      returnFocusRef.current = trigger
      setDeleteError('')
      setDeleting(finding)
    },
    onEditPaths: (finding, subFindings, trigger) => {
      returnFocusRef.current = trigger
      setPathsTarget({ finding, subFindings })
    },
    onEvidenceDetached: () => { setEvidenceRevision(value => value + 1) },
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

  const editing = dialog?.editing ?? null

  return (
    <section className={styles.root} aria-label="Operation">
      {backLink}
      <header className={styles.header}>
        <Text as="h1" size={600} weight="semibold" className={styles.heading} translate="no">
          {current.operation.name}
        </Text>
        <div className={styles.headerActions}>
          <OperationEditDialog operation={current.operation} onSaved={operation => {
            setOperationState({ key: operationKey, status: 'loaded', operation: { ...current.operation, ...operation } })
            setNotice('Operation updated.')
            onOperationChanged?.()
          }} />
          <OperationDeleteDialog operation={current.operation}
            onInUse={() => { setRevision(value => value + 1) }}
            onDeleted={() => {
              onOperationChanged?.()
              void navigate('/operations')
            }} />
          <FindingExportDialog key={current.operation.id} operation={current.operation} />
          <Button ref={newFindingRef} appearance="primary" className={styles.button} icon={<AddRegular />} onClick={event => {
            returnFocusRef.current = event.currentTarget
            setDialog({ editing: null, parent: null })
          }}>New finding</Button>
        </div>
        {dialog && <FindingDialog editing={editing !== null} evidenceField={editing === null}
          parentTitle={dialog.parent?.title}
          initialValues={editing ? {
            title: editing.title, description: editing.description, severity: editing.severity,
            severity_other: editing.severity_other ?? null, harm_type: editing.harm_type ?? null,
            harm_type_other: editing.harm_type_other ?? null, demonstrated: editing.demonstrated,
          } : undefined}
          onSave={save} onClose={() => { setDialog(null) }} />}
        {pathsTarget && <FindingPathsDialog operationId={operationId} finding={pathsTarget.finding}
          subFindings={pathsTarget.subFindings} onClose={() => { setPathsTarget(null) }}
          onSaved={saved => {
            setNotice(`Paths saved: ${saved.title}`)
            setPathsTarget(null)
            refresh(false)
          }} />}
      </header>
      {current.operation.description && <p className={styles.description}>{current.operation.description}</p>}
      <Dialog open={deleting !== null} onOpenChange={(_, data) => {
        if (!removing && !data.open) { setDeleting(null); setDeleteError('') }
      }}>
        <DialogSurface>
          <DialogBody>
            <DialogTitle>Delete finding?</DialogTitle>
            <DialogContent className={styles.form}>
              <Text>
                Permanently delete “{deleting?.title}”{deleting?.child_count ? ' and all of its sub-findings' : ''}?
                This cannot be undone.
              </Text>
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
      {notice && <MessageBar className={styles.notice} intent="success" aria-live="polite">
        <MessageBarBody>{notice}</MessageBarBody>
      </MessageBar>}
      {settledKey !== requestKey ? <Spinner label="Loading findings…" /> : error ? (
        <MessageBar intent="error"><MessageBarBody>
          Could not load findings: {error}{' '}
          <Button className={styles.button} onClick={() => { setRevision(value => value + 1) }}>Retry</Button>
        </MessageBarBody></MessageBar>
      ) : (
        <>
          {page.items.length === 0 ? <Text>No findings in this operation yet. Create one to record an assessment.</Text>
            : <ul className={styles.list}>{page.items.map(finding => (
              <FindingTreeItem key={finding.id} operationId={operationId} finding={finding} depth={0}
                revision={revision + evidenceRevision} expanded={expanded} actions={treeActions} />
            ))}</ul>}
          <div className={styles.header}>
            <Button className={styles.button} disabled={offset === 0} onClick={() => { navigatePage(0) }}>First</Button>
            <Text>Page {Math.floor(offset / PAGE_SIZE) + 1}</Text>
            <Button className={styles.button} disabled={!page.has_more}
              onClick={() => { if (page.next_offset !== null) navigatePage(page.next_offset) }}>Next</Button>
          </div>
        </>
      )}
      <div className={styles.headerActions}>
        <Link className={styles.link} to={`/attack/history?${historySearch}`}>Attack history</Link>
        <Link className={styles.link} to={`/scanner/history?${historySearch}`}>Scanner history</Link>
      </div>
    </section>
  )
}
