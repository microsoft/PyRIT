import { useEffect, useLayoutEffect, useRef, useState } from 'react'

import {
  Button, Dialog, DialogActions, DialogBody, DialogContent, DialogSurface, DialogTitle,
  Badge, Field, Input, MessageBar, MessageBarBody, Radio, RadioGroup, Spinner, Text, Tooltip,
} from '@fluentui/react-components'
import { AddRegular, ChevronLeftRegular, ChevronRightRegular, LinkRegular } from '@fluentui/react-icons'
import { Link } from 'react-router'

import { attacksApi, operationsApi } from '@/services/api'
import FindingDialog from '@/components/Operations/FindingDialog'
import { toApiError } from '@/services/errors'
import { findingSeverityLabel, findingSeverityColor } from '@/utils/findingSeverity'
import type { Finding, FindingCreate, FindingListItem, FindingListResponse, Operation } from '@/types'

import { useFindingEvidenceDialogStyles } from './FindingEvidenceDialog.styles'

const PAGE_SIZE = 20

interface FindingEvidenceDialogProps {
  attackResultId: string
  conversationId: string
  disabled: boolean
}

type AttributionState =
  | { status: 'loading' }
  | { status: 'eligible'; operation: Operation }
  | { status: 'ineligible'; reason: string }
  | { status: 'error'; reason: string }

interface AttachmentSource {
  operation: Operation
  attackResultId: string
  conversationId: string
}

interface SavedAttachment extends AttachmentSource {
  finding: Finding
}

export default function FindingEvidenceDialog({
  attackResultId, conversationId, disabled,
}: FindingEvidenceDialogProps) {
  const styles = useFindingEvidenceDialogStyles()
  const [attribution, setAttribution] = useState<AttributionState>({ status: 'loading' })
  const [lookupRevision, setLookupRevision] = useState(0)
  const [open, setOpen] = useState(false)
  const [creating, setCreating] = useState(false)
  const [creationSource, setCreationSource] = useState<AttachmentSource | null>(null)
  const [savedAttachment, setSavedAttachment] = useState<SavedAttachment | null>(null)
  const [creationAttachError, setCreationAttachError] = useState('')
  const [title, setTitle] = useState('')
  const [offset, setOffset] = useState(0)
  const [page, setPage] = useState<FindingListResponse | null>(null)
  const [picked, setPicked] = useState<{ key: string; finding: FindingListItem } | null>(null)
  const [listError, setListError] = useState('')
  const [listRevision, setListRevision] = useState(0)
  const [settledKey, setSettledKey] = useState('')
  const [attachError, setAttachError] = useState('')
  const [notice, setNotice] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const submittingRef = useRef(false)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const newFindingRef = useRef<HTMLButtonElement>(null)
  const wasOpen = useRef(false)
  const wasCreating = useRef(false)
  const viewRef = useRef({ attackResultId, conversationId, disabled, generation: 0 })
  const [attributionKey, setAttributionKey] = useState('')
  const operation = attributionKey === attackResultId && attribution.status === 'eligible' ? attribution.operation : null
  const requestKey = JSON.stringify([attackResultId, conversationId, operation?.id, title, offset, listRevision])
  // A selection only applies to the conversation and operation it was made for.
  const selectionKey = JSON.stringify([attackResultId, conversationId, operation?.id])
  const selection = picked?.key === selectionKey ? picked.finding : null
  const setSelection = (finding: FindingListItem | null): void => {
    setPicked(finding ? { key: selectionKey, finding } : null)
  }

  useLayoutEffect(() => {
    viewRef.current = { attackResultId, conversationId, disabled, generation: viewRef.current.generation + 1 }
    return () => { viewRef.current = { ...viewRef.current, disabled: true, generation: viewRef.current.generation + 1 } }
  }, [attackResultId, conversationId, disabled])

  useEffect(() => {
    let ignore = false
    const resolve = async (): Promise<void> => {
      try {
        if (!attackResultId || !conversationId) return
        const attack = await attacksApi.getAttack(attackResultId)
        if (!attack?.operation) {
          if (!ignore) {
            setAttributionKey(attackResultId)
            setAttribution({ status: 'ineligible', reason: 'The owning attack has no saved Operation attribution.' })
          }
          return
        }
        const saved = await operationsApi.list()
        const matching = saved.items.find(item => item.name === attack.operation)
        if (!ignore) {
          setAttributionKey(attackResultId)
          setAttribution(matching
            ? { status: 'eligible', operation: matching }
            : { status: 'ineligible', reason: 'The owning attack attribution does not match a saved Operation exactly.' })
        }
      } catch (cause: unknown) {
        if (!ignore) {
          setAttributionKey(attackResultId)
          setAttribution({ status: 'error', reason: toApiError(cause).detail })
        }
      }
    }
    void resolve()
    return () => { ignore = true }
  }, [attackResultId, conversationId, lookupRevision])

  useEffect(() => {
    if (!open || !operation) return
    let ignore = false
    operationsApi.searchFindings(operation.id, { limit: PAGE_SIZE, offset, title })
      .then(result => {
        if (!ignore) { setPage(result); setListError(''); setSettledKey(requestKey) }
      })
      .catch((cause: unknown) => {
        if (!ignore) { setListError(toApiError(cause).detail); setSettledKey(requestKey) }
      })
    return () => { ignore = true }
  }, [open, operation, offset, title, requestKey])

  useEffect(() => {
    if (wasOpen.current && !open) triggerRef.current?.focus()
    if (wasCreating.current && !creating) {
      if (open) newFindingRef.current?.focus()
      else triggerRef.current?.focus()
    }
    wasOpen.current = open
    wasCreating.current = creating
  }, [open, creating])

  const retryEligible = savedAttachment !== null && !disabled
    && savedAttachment.attackResultId === attackResultId && savedAttachment.conversationId === conversationId
  const draftEligible = creationSource !== null && !disabled
    && creationSource.attackResultId === attackResultId && creationSource.conversationId === conversationId
    && creationSource.operation.id === operation?.id

  const attachSaved = async (saved: SavedAttachment, generation: number): Promise<void> => {
    try {
      if (viewRef.current.generation !== generation || viewRef.current.disabled
        || viewRef.current.attackResultId !== saved.attackResultId || viewRef.current.conversationId !== saved.conversationId) {
        throw new Error('The viewed conversation changed. Return to the original conversation to retry attachment.')
      }
      await operationsApi.attachFindingEvidence(saved.operation.id, saved.finding.id, {
        attack_result_id: saved.attackResultId, conversation_id: saved.conversationId,
      })
      setSavedAttachment(null)
      setCreationAttachError('')
      setCreating(false)
      setOpen(false)
      setNotice(`Finding created and conversation attached: ${saved.finding.title}`)
    } catch (cause: unknown) {
      setCreationAttachError(toApiError(cause).detail)
    }
  }

  const createAndAttach = async (draft: FindingCreate): Promise<void> => {
    if (submittingRef.current) return
    if (savedAttachment ? !retryEligible : !draftEligible || !creationSource) {
      throw new Error('The viewed conversation changed. Return to the original conversation before saving.')
    }
    submittingRef.current = true
    setSubmitting(true)
    try {
      const generation = viewRef.current.generation
      if (savedAttachment) {
        await attachSaved(savedAttachment, generation)
      } else if (creationSource) {
        const finding = await operationsApi.createFinding(creationSource.operation.id, draft)
        const saved = { ...creationSource, finding }
        setSavedAttachment(saved)
        await attachSaved(saved, generation)
      }
    } finally {
      submittingRef.current = false
      setSubmitting(false)
    }
  }

  const recovery = savedAttachment && creationAttachError && (
    <div className={styles.content}>
      <MessageBar intent="error" aria-live="polite"><MessageBarBody>
        Finding created, but conversation attachment failed: {creationAttachError}
      </MessageBarBody></MessageBar>
      <Text>{savedAttachment.finding.title} — Finding ID: {savedAttachment.finding.id}</Text>
      <Link to={`/operations/${encodeURIComponent(savedAttachment.operation.id)}`}>Open saved finding Operation</Link>
      {!retryEligible && <Text>Return to the original conversation to retry attachment.</Text>}
    </div>
  )

  const attach = async (): Promise<void> => {
    if (!operation || !selection || submittingRef.current || disabled) return
    submittingRef.current = true
    setSubmitting(true)
    setAttachError('')
    setNotice('')
    try {
      const response = await operationsApi.attachFindingEvidence(operation.id, selection.id, {
        attack_result_id: attackResultId, conversation_id: conversationId,
      })
      setNotice(response.created ? 'Attached' : 'Already attached')
      setOpen(false)
    } catch (cause: unknown) {
      setAttachError(toApiError(cause).detail)
    } finally {
      submittingRef.current = false
      setSubmitting(false)
    }
  }

  return (
    <>
      {attackResultId && conversationId && <Tooltip content="Link to finding" relationship="label">
        <Button ref={triggerRef} appearance="subtle" icon={<LinkRegular />}
          aria-label="Link to finding" className={styles.button} disabled={disabled || !operation}
          onClick={() => {
            if (savedAttachment) setCreating(true)
            else setOpen(true)
            setSelection(null); setNotice(''); setAttachError('')
          }} />
      </Tooltip>}
      {!creating && !open && savedAttachment && <div className={styles.recovery}>
        {recovery}
        <Button className={styles.button} disabled={submitting || !retryEligible}
          onClick={() => { setCreating(true) }}>Retry attachment</Button>
      </div>}
      {notice && <Text size={200} role="status">{notice}</Text>}
      {attackResultId && conversationId && attribution.status === 'loading' && <Text size={200}>Resolving Operation...</Text>}
      {attackResultId && conversationId && attribution.status === 'ineligible' && <Text size={200}>{attribution.reason}</Text>}
      {attackResultId && conversationId && attribution.status === 'error' && <MessageBar intent="error"><MessageBarBody>
        Could not resolve Operation: {attribution.reason}{' '}
        <Button className={styles.button} onClick={() => {
          setAttribution({ status: 'loading' }); setLookupRevision(value => value + 1)
        }}>Retry Operation lookup</Button>
      </MessageBarBody></MessageBar>}
      {creating && <FindingDialog
        context={<>
          <Text>Operation: {savedAttachment?.operation.name ?? creationSource?.operation.name}. Save and attach the entire viewed conversation.</Text>
          {!savedAttachment && !draftEligible && <MessageBar intent="error"><MessageBarBody>
            The viewed conversation changed. Return to the original conversation before saving.
          </MessageBarBody></MessageBar>}
        </>}
        recovery={recovery || undefined} saveDisabled={savedAttachment ? !retryEligible : !draftEligible}
        onSave={createAndAttach} onClose={() => {
          setCreating(false)
          if (savedAttachment) setOpen(false)
          setListRevision(value => value + 1)
        }} />}
      <Dialog open={open && !creating} onOpenChange={(_, data) => { if (!submittingRef.current) setOpen(data.open) }}>
        <DialogSurface><DialogBody>
          <DialogTitle>Attach to finding</DialogTitle>
          <DialogContent className={styles.content}>
            <div className={styles.pickerHeader}>
              <Text>Operation: {operation?.name}</Text>
              <Button ref={newFindingRef} className={styles.button} icon={<AddRegular />}
                disabled={submitting || disabled || !operation || savedAttachment !== null}
                onClick={() => {
                  if (operation) {
                    setCreationSource({ operation, attackResultId, conversationId })
                    setCreating(true)
                  }
                }}>New finding</Button>
            </div>
            <Field label="Search findings by title">
              <Input value={title} disabled={submitting} onChange={(_, data) => {
                setTitle(data.value); setOffset(0); setSelection(null); setNotice(''); setAttachError('')
              }} />
            </Field>
            {settledKey !== requestKey ? <Spinner label="Loading findings" /> : listError ? (
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
                setSelection(page?.items.find(item => item.id === data.value) ?? null); setNotice(''); setAttachError('')
              }}>
                {page?.items.map(item => <Radio key={item.id} value={item.id} disabled={submitting} label={
                  <span title={`Finding ID: ${item.id}`}>
                    <span className={styles.findingHeading}>{item.title}
                      <Badge appearance="tint" color={findingSeverityColor(item.severity)}>
                      {findingSeverityLabel(item)}
                    </Badge></span>
                    {item.harm_type && <span className={styles.context}>Harm-type: {
                      item.harm_type === 'Other' ? item.harm_type_other : item.harm_type
                    }</span>}
                    <span className={styles.context}>{new Date(item.created_at).toLocaleString()}</span>
                  </span>
                } />)}
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
            {attachError && <MessageBar intent="error"><MessageBarBody>Could not attach: {attachError}</MessageBarBody></MessageBar>}
          </DialogContent>
          <DialogActions>
            <Button className={styles.button} disabled={submitting} onClick={() => { setOpen(false) }}>Cancel</Button>
            <Button className={styles.button} appearance="primary"
              disabled={submitting || !selection || settledKey !== requestKey || Boolean(listError) || disabled}
              aria-label={submitting ? 'Attaching...' : 'Attach conversation'}
              onClick={() => { void attach() }}>{submitting ? 'Attaching...' : 'Attach'}</Button>
          </DialogActions>
        </DialogBody></DialogSurface>
      </Dialog>
    </>
  )
}
