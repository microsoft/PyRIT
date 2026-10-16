import { useEffect, useRef, useState } from 'react'

import {
  Button, Dialog, DialogActions, DialogBody, DialogContent, DialogSurface, DialogTitle,
  Dropdown, Field, Input, MessageBar, MessageBarBody, Option, Radio, RadioGroup, Spinner, Text,
} from '@fluentui/react-components'
import { AddRegular, ChevronLeftRegular, ChevronRightRegular } from '@fluentui/react-icons'
import { Link } from 'react-router'

import { attacksApi, operationsApi } from '@/services/api'
import FindingDialog, { type FindingSaveAttempt } from '@/components/Operations/FindingDialog'
import FindingSeverityBadge from '@/components/Operations/FindingSeverityBadge'
import { toApiError } from '@/services/errors'
import type { FindingCreate, FindingListItem, FindingListResponse, Operation } from '@/types'

import { useFindingEvidenceDialogStyles } from './FindingEvidenceDialog.styles'

const PAGE_SIZE = 20

interface FindingEvidenceDialogProps {
  attackResultId: string
  disabled: boolean
  /** The Operation selected for new activity, used when the attack has no Operation of its own. */
  defaultOperationId?: string | null
  open: boolean
  onOpenChange: (open: boolean) => void
  /** Receives focus when the dialog closes, such as the control that opened it. */
  returnFocusRef?: React.RefObject<HTMLElement | null>
}

interface AttachmentSource {
  operation: Operation
  attackResultId: string
}

export default function FindingEvidenceDialog({
  attackResultId, disabled, defaultOperationId = null, open, onOpenChange, returnFocusRef,
}: FindingEvidenceDialogProps) {
  const styles = useFindingEvidenceDialogStyles()
  const viewKey = attackResultId
  const [defaultLookup, setDefaultLookup] = useState<{ key: string; operation: Operation | null } | null>(null)
  const [chosen, setChosen] = useState<{ key: string; operation: Operation } | null>(null)
  const [operations, setOperations] = useState<Operation[]>([])
  const [operationsError, setOperationsError] = useState('')
  const [shownOpen, setShownOpen] = useState(open)
  const [creating, setCreating] = useState(false)
  const [creationSource, setCreationSource] = useState<AttachmentSource | null>(null)
  const [title, setTitle] = useState('')
  const [offset, setOffset] = useState(0)
  const [page, setPage] = useState<FindingListResponse | null>(null)
  const [picked, setPicked] = useState<{ key: string; finding: FindingListItem } | null>(null)
  const [listError, setListError] = useState('')
  const [listRevision, setListRevision] = useState(0)
  const [settledKey, setSettledKey] = useState('')
  const [attachError, setAttachError] = useState('')
  // Kept per viewed attack, so switching attacks does not show another one's result.
  const [notice, setNotice] = useState<{ key: string; text: string } | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const submittingRef = useRef(false)
  const newFindingRef = useRef<HTMLButtonElement>(null)
  const wasOpen = useRef(false)
  const wasCreating = useRef(false)
  // Each opening starts without a previous selection or result.
  if (open !== shownOpen) {
    setShownOpen(open)
    if (open) { setPicked(null); setNotice(null); setAttachError('') }
  }
  // Evidence can come from any attack; the user picks whose findings to link, defaulting to the attack's own Operation.
  const operation = chosen?.key === viewKey ? chosen.operation
    : defaultLookup?.key === viewKey ? defaultLookup.operation : null
  const requestKey = JSON.stringify([attackResultId, operation?.id, title, offset, listRevision])
  // A selection only applies to the attack and operation it was made for.
  const selectionKey = JSON.stringify([attackResultId, operation?.id])
  const selection = picked?.key === selectionKey ? picked.finding : null
  const setSelection = (finding: FindingListItem | null): void => {
    setPicked(finding ? { key: selectionKey, finding } : null)
  }

  useEffect(() => {
    if (!attackResultId) return
    let ignore = false
    const resolve = async (): Promise<void> => {
      try {
        const attack = await attacksApi.getAttack(attackResultId)
        const operationId = attack?.operation_id ?? defaultOperationId
        const operation = operationId ? await operationsApi.get(operationId) : null
        if (!ignore) setDefaultLookup({ key: viewKey, operation })
      } catch {
        // Only the default is lost; the user can still choose an Operation in the dialog.
        if (!ignore) setDefaultLookup({ key: viewKey, operation: null })
      }
    }
    void resolve()
    return () => { ignore = true }
  }, [attackResultId, defaultOperationId, viewKey])

  useEffect(() => {
    if (!open) return
    let ignore = false
    operationsApi.list()
      .then(result => { if (!ignore) { setOperations(result.items); setOperationsError('') } })
      .catch((cause: unknown) => { if (!ignore) setOperationsError(toApiError(cause).detail) })
    return () => { ignore = true }
  }, [open])

  useEffect(() => {
    if (!open || !operation) return
    let ignore = false
    operationsApi.listFindings(operation.id, { limit: PAGE_SIZE, offset, title })
      .then(result => {
        if (!ignore) { setPage(result); setListError(''); setSettledKey(requestKey) }
      })
      .catch((cause: unknown) => {
        if (!ignore) { setListError(toApiError(cause).detail); setSettledKey(requestKey) }
      })
    return () => { ignore = true }
  }, [open, operation, offset, title, requestKey])

  useEffect(() => {
    if (wasOpen.current && !open) returnFocusRef?.current?.focus()
    if (wasCreating.current && !creating) {
      if (open) newFindingRef.current?.focus()
      else returnFocusRef?.current?.focus()
    }
    wasOpen.current = open
    wasCreating.current = creating
  }, [open, creating, returnFocusRef])

  const draftEligible = creationSource !== null && !disabled
    && creationSource.attackResultId === attackResultId && creationSource.operation.id === operation?.id

  // One request saves the finding and its evidence together, and a retry with the same id cannot duplicate it.
  const createAndAttach = async (draft: FindingCreate, attempt: FindingSaveAttempt): Promise<void> => {
    if (!draftEligible || !creationSource) {
      throw new Error('The viewed attack changed. Return to the original attack before saving.')
    }
    const finding = await operationsApi.createFinding(creationSource.operation.id, {
      ...draft, id: attempt.id, attack_result_id: creationSource.attackResultId,
    })
    setCreating(false)
    onOpenChange(false)
    setNotice({ key: viewKey, text: `Finding created and attack attached: ${finding.title}` })
  }

  const attach = async (): Promise<void> => {
    if (!operation || !selection || submittingRef.current || disabled) return
    submittingRef.current = true
    setSubmitting(true)
    setAttachError('')
    setNotice(null)
    try {
      const response = await operationsApi.attachFindingEvidence(operation.id, selection.id, {
        attack_result_id: attackResultId,
      })
      setNotice({ key: viewKey, text: response.created ? 'Attached' : 'Already attached' })
      onOpenChange(false)
    } catch (cause: unknown) {
      setAttachError(toApiError(cause).detail)
    } finally {
      submittingRef.current = false
      setSubmitting(false)
    }
  }

  return (
    <>
      {notice?.key === viewKey && <Text size={200} role="status">{notice.text}</Text>}
      {creating && <FindingDialog
        context={<>
          <Text>Operation: {creationSource?.operation.name}. Save and attach the viewed attack.</Text>
          {!draftEligible && <MessageBar intent="error"><MessageBarBody>
            The viewed attack changed. Return to the original attack before saving.
          </MessageBarBody></MessageBar>}
        </>}
        saveDisabled={!draftEligible}
        onSave={createAndAttach} onClose={() => {
          setCreating(false)
          setListRevision(value => value + 1)
        }} />}
      <Dialog open={open && !creating} onOpenChange={(_, data) => { if (!submittingRef.current) onOpenChange(data.open) }}>
        <DialogSurface><DialogBody>
          <DialogTitle>Attach to finding</DialogTitle>
          <DialogContent className={styles.content}>
            <div className={styles.pickerHeader}>
              <Field label="Operation" validationState={operationsError ? 'error' : 'none'}
                validationMessage={operationsError ? `Could not load operations: ${operationsError}` : undefined}>
                <Dropdown placeholder="Choose an operation" value={operation?.name ?? ''}
                  selectedOptions={operation ? [operation.id] : []} disabled={submitting}
                  onOptionSelect={(_, data) => {
                    const next = operations.find(item => item.id === data.optionValue)
                    if (!next) return
                    setChosen({ key: viewKey, operation: next })
                    setOffset(0); setSelection(null); setNotice(null); setAttachError('')
                  }}>
                  {operations.map(item => <Option key={item.id} value={item.id}>{item.name}</Option>)}
                </Dropdown>
              </Field>
              <Button ref={newFindingRef} className={styles.button} icon={<AddRegular />}
                disabled={submitting || disabled || !operation}
                onClick={() => {
                  if (operation) {
                    setCreationSource({ operation, attackResultId })
                    setCreating(true)
                  }
                }}>New finding</Button>
            </div>
            <Field label="Search findings by title">
              <Input value={title} disabled={submitting} onChange={(_, data) => {
                setTitle(data.value); setOffset(0); setSelection(null); setNotice(null); setAttachError('')
              }} />
            </Field>
            {!operation ? <Text>Choose an operation to see its findings.</Text>
              : settledKey !== requestKey ? <Spinner label="Loading findings" /> : listError ? (
              <MessageBar intent="error"><MessageBarBody>
                Could not load findings: {listError}{' '}
                <Button onClick={() => { setListRevision(value => value + 1) }}>Retry findings</Button>
              </MessageBarBody></MessageBar>
            ) : page?.items.length === 0 ? (
              <Text>{title ? 'No matching findings.' : 'No findings in this Operation yet. Choose New finding to create and attach.'}{' '}
                <Link to={`/operations/${encodeURIComponent(operation?.id ?? '')}`}>Open Operation</Link>
              </Text>
            ) : (
              <RadioGroup className={styles.list} value={selection?.id ?? ''} onChange={(_, data) => {
                setSelection(page?.items.find(item => item.id === data.value) ?? null); setNotice(null); setAttachError('')
              }}>
                {page?.items.map(item => <Radio key={item.id} value={item.id} disabled={submitting} label={{
                  className: styles.findingLabel,
                  children: (
                    <span title={`Finding ID: ${item.id}`}>
                      <span className={styles.findingHeading}><span className={styles.findingTitle}>{item.title}</span>
                        <FindingSeverityBadge finding={item} /></span>
                      {item.harm_type && <span className={styles.context}>Harm-type: {
                        item.harm_type === 'Other' ? item.harm_type_other : item.harm_type
                      }</span>}
                      <span className={styles.context}>{new Date(item.created_at).toLocaleString()}</span>
                    </span>
                  ),
                }} />)}
              </RadioGroup>
            )}
            {(offset > 0 || page?.has_more) && <div className={styles.pagination}>
              <Button className={styles.button} disabled={offset === 0 || submitting}
                icon={<ChevronLeftRegular />} aria-label="Previous findings"
                onClick={() => { setOffset(value => Math.max(0, value - PAGE_SIZE)); setSelection(null) }} />
              <Text size={200}>Page {Math.floor(offset / PAGE_SIZE) + 1}</Text>
              <Button className={styles.button} disabled={settledKey !== requestKey || !page?.has_more || submitting}
                icon={<ChevronRightRegular />} aria-label="Next findings"
                onClick={() => { setOffset(page?.next_offset ?? 0); setSelection(null) }} />
            </div>}
            {attachError && <MessageBar intent="error" aria-live="polite">
              <MessageBarBody>Could not attach: {attachError}</MessageBarBody>
            </MessageBar>}
          </DialogContent>
          <DialogActions>
            <Button className={styles.button} disabled={submitting} onClick={() => { onOpenChange(false) }}>Cancel</Button>
            <Button className={styles.button} appearance="primary"
              disabledFocusable={submitting}
              disabled={!selection || settledKey !== requestKey || Boolean(listError) || disabled}
              aria-label={submitting ? 'Attaching...' : 'Attach attack'}
              onClick={() => { void attach() }}>{submitting ? 'Attaching...' : 'Attach'}</Button>
          </DialogActions>
        </DialogBody></DialogSurface>
      </Dialog>
    </>
  )
}
