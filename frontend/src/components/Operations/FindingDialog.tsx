import { useEffect, useMemo, useRef, useState } from 'react'

import {
  Button, Combobox, Dialog, DialogBody, DialogContent, DialogSurface, DialogTitle,
  Field, Input, MessageBar, MessageBarBody, Option, Select, Text, Textarea,
} from '@fluentui/react-components'

import { operationsApi } from '@/services/api'
import { toApiError, type FieldError } from '@/services/errors'
import type { FindingCreate, FindingSeverity } from '@/types'
import { generateClientId } from '@/utils/clientId'
import { FINDING_SEVERITY_LABELS } from '@/utils/findingSeverity'

import { FINDING_FIELD_LABELS, findingFieldErrors, type FindingFormField } from './findingValidation'
import { useFindingDialogStyles } from './FindingDialog.styles'

const EMPTY_DRAFT: FindingCreate = {
  title: '', description: '', severity: 'moderate', severity_other: null, harm_type: null, harm_type_other: null,
}

/** One save attempt. The id repeats while the input does, so a retry returns a finding a lost response saved. */
export interface FindingSaveAttempt {
  id: string
  attackResultId: string
}

interface FindingDialogProps {
  initialValues?: FindingCreate
  editing?: boolean
  context?: React.ReactNode
  /** Shows an optional attack ID to link as evidence. */
  evidenceField?: boolean
  saveDisabled?: boolean
  onSave: (draft: FindingCreate, attempt: FindingSaveAttempt) => Promise<void>
  onClose: () => void
}

export default function FindingDialog({
  initialValues = EMPTY_DRAFT, editing = false, context, evidenceField = false, saveDisabled = false, onSave, onClose,
}: FindingDialogProps) {
  const styles = useFindingDialogStyles()
  const [initialDraft] = useState<FindingCreate>({ ...EMPTY_DRAFT, ...initialValues })
  const [draft, setDraft] = useState<FindingCreate>({ ...EMPTY_DRAFT, ...initialValues })
  const [attackResultId, setAttackResultId] = useState('')
  const [error, setError] = useState('')
  const [fieldErrors, setFieldErrors] = useState<Partial<Record<FindingFormField, string>>>({})
  const [unmatchedErrors, setUnmatchedErrors] = useState<FieldError[]>([])
  const [saving, setSaving] = useState(false)
  const form = useRef<HTMLFormElement>(null)
  const focusInvalidOnNextRender = useRef(false)
  const submitting = useRef(false)
  const attempt = useRef<{ signature: string; id: string } | null>(null)
  const [harmTypes, setHarmTypes] = useState<string[] | null>(null)
  const [optionsError, setOptionsError] = useState('')
  const [optionsRevision, setOptionsRevision] = useState(0)
  const [harmOpen, setHarmOpen] = useState(false)
  const [harmSearch, setHarmSearch] = useState('')
  // Only fields that are actually rendered can carry inline validation feedback.
  const visibleFields = useMemo<FindingFormField[]>(() => {
    const fields: FindingFormField[] = ['title', 'description', 'severity', 'harm_type']
    if (draft.severity === 'other') fields.push('severity_other')
    if (draft.harm_type === 'Other') fields.push('harm_type_other')
    if (evidenceField) fields.push('attack_result_id')
    return fields
  }, [draft.severity, draft.harm_type, evidenceField])
  // A conditional field can disappear after the server flagged it (e.g. the
  // user switches away from a custom severity). Derive the errors that map to
  // currently rendered controls so a hidden error never blocks Save invisibly.
  const activeFieldErrors = useMemo<Partial<Record<FindingFormField, string>>>(() => {
    const visible = new Set<string>(visibleFields)
    return Object.fromEntries(Object.entries(fieldErrors).filter(([field]) => visible.has(field)))
  }, [fieldErrors, visibleFields])
  const invalidFields = (Object.keys(activeFieldErrors) as FindingFormField[])
    .map(field => FINDING_FIELD_LABELS[field])
  const classificationsValid = (draft.severity !== 'other' || Boolean(draft.severity_other?.trim()))
    && (draft.harm_type !== 'Other' || Boolean(draft.harm_type_other?.trim()))
  const canSave = !saveDisabled && Boolean(draft.title.trim()) && classificationsValid && harmTypes !== null
    && !optionsError && invalidFields.length === 0

  /** Editing a field clears only its own error, so other invalid fields stay flagged. */
  const clearFieldError = (field: FindingFormField): void => {
    setFieldErrors(current => {
      if (!current[field]) return current
      const next = { ...current }
      delete next[field]
      return next
    })
  }

  useEffect(() => {
    let ignore = false
    operationsApi.getFindingOptions()
      .then(options => { if (!ignore) { setHarmTypes(options.harm_types); setOptionsError('') } })
      .catch((cause: unknown) => { if (!ignore) setOptionsError(toApiError(cause).detail) })
    return () => { ignore = true }
  }, [optionsRevision])

  useEffect(() => {
    // Clean means the draft still equals its initial values. An edit dialog
    // opens prefilled, so emptiness can never prove it clean; every watched
    // field is compared against what the dialog started with.
    const unchanged = (field: keyof FindingCreate): boolean => draft[field] === initialDraft[field]
    if (unchanged('title') && unchanged('description') && unchanged('severity_other')
      && unchanged('harm_type') && unchanged('harm_type_other') && unchanged('severity')
      && !attackResultId) return
    const protectDraft = (event: BeforeUnloadEvent): void => { event.preventDefault() }
    window.addEventListener('beforeunload', protectDraft)
    return () => { window.removeEventListener('beforeunload', protectDraft) }
  }, [draft, initialDraft, attackResultId])

  useEffect(() => {
    // Disabling Save after a failed submit would otherwise drop focus on BODY.
    // Move focus once for that failure only; re-running on every edit would
    // steal focus from a user who is correcting another field.
    if (!focusInvalidOnNextRender.current) return
    focusInvalidOnNextRender.current = false
    const firstInvalid = (Object.keys(activeFieldErrors) as FindingFormField[])[0]
    if (!firstInvalid) return
    form.current?.querySelector<HTMLElement>(`[name="${firstInvalid}"]`)?.focus()
  }, [activeFieldErrors])

  const save = async (): Promise<void> => {
    if (submitting.current || !canSave) return
    submitting.current = true
    setSaving(true)
    setError('')
    setFieldErrors({})
    setUnmatchedErrors([])
    focusInvalidOnNextRender.current = false
    const trimmedAttackResultId = attackResultId.trim()
    const signature = JSON.stringify([draft, trimmedAttackResultId])
    if (attempt.current?.signature !== signature) attempt.current = { signature, id: generateClientId() }
    try {
      await onSave(draft, { id: attempt.current.id, attackResultId: trimmedAttackResultId })
    } catch (cause: unknown) {
      const apiError = toApiError(cause)
      const grouped = findingFieldErrors(apiError, visibleFields)
      setFieldErrors(grouped.fields)
      setUnmatchedErrors(grouped.unmatched)
      setError(apiError.detail)
      focusInvalidOnNextRender.current = Object.keys(grouped.fields).length > 0
    } finally {
      submitting.current = false
      setSaving(false)
    }
  }

  return (
    <Dialog open onOpenChange={(_, data) => { if (!data.open && !submitting.current) onClose() }}>
      <DialogSurface><DialogBody>
        <DialogTitle>{editing ? 'Edit finding' : 'New finding'}</DialogTitle>
        <DialogContent>
          <form ref={form} className={styles.form} onSubmit={event => { event.preventDefault(); void save() }}>
            {context ?? <Text>Record a human assessment. No attack is required.</Text>}
            {error && <MessageBar intent="error" aria-live="polite"><MessageBarBody>
              {error}
              {invalidFields.length > 0 && ` Check ${invalidFields.join(', ')}.`}
              {unmatchedErrors.length > 0 && <ul className={styles.fieldErrorList}>
                {unmatchedErrors.map(item => <li key={`${item.field}:${item.message}`}>
                  {item.field}: {item.message}
                </li>)}
              </ul>}
            </MessageBarBody></MessageBar>}
            <Field label="Title" required validationState={activeFieldErrors.title ? 'error' : 'none'}
              validationMessage={activeFieldErrors.title}>
              <Input value={draft.title} disabled={saving} name="title" autoComplete="off"
                aria-invalid={Boolean(activeFieldErrors.title)}
                onChange={(_, data) => { clearFieldError('title'); setDraft(value => ({ ...value, title: data.value })) }} />
            </Field>
            <Field label="Severity" required validationState={activeFieldErrors.severity ? 'error' : 'none'}
              validationMessage={activeFieldErrors.severity}>
              <Select value={draft.severity} disabled={saving} name="severity" aria-invalid={Boolean(activeFieldErrors.severity)}
                onChange={event => {
                  clearFieldError('severity')
                  const severity = event.target.value
                  if (severity in FINDING_SEVERITY_LABELS) setDraft(value => ({
                    ...value, severity: severity as FindingSeverity, severity_other: severity === 'other' ? '' : null,
                  }))
                }}>
                {Object.entries(FINDING_SEVERITY_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </Select>
            </Field>
            {draft.severity === 'other' && <Field label="Other severity" required
              validationState={activeFieldErrors.severity_other ? 'error' : 'none'} validationMessage={activeFieldErrors.severity_other}>
              <Input value={draft.severity_other ?? ''} disabled={saving} name="severity_other" autoComplete="off"
                aria-invalid={Boolean(activeFieldErrors.severity_other)}
                onChange={(_, data) => {
                  clearFieldError('severity_other')
                  setDraft(value => ({ ...value, severity_other: data.value }))
                }} />
            </Field>}
            {optionsError && <MessageBar intent="error" aria-live="polite"><MessageBarBody>
              Could not load harm categories: {optionsError}{' '}
              <Button className={styles.button} aria-label="Retry harm categories" onClick={() => {
                setOptionsError('')
                setHarmTypes(null)
                setOptionsRevision(value => value + 1)
              }}>Retry</Button>
            </MessageBarBody></MessageBar>}
            <Field label="Harm-type" hint={harmTypes === null && !optionsError
              ? 'Loading harm categories…' : 'Optional. Select a category, or choose Other for custom text.'}
              validationState={activeFieldErrors.harm_type ? 'error' : 'none'} validationMessage={activeFieldErrors.harm_type}>
              <Combobox className={styles.harmPicker} inlinePopup open={harmOpen} name="harm_type"
                disabled={saving || harmTypes === null || Boolean(optionsError)}
                aria-invalid={Boolean(activeFieldErrors.harm_type)}
                placeholder="Not set" value={harmOpen ? harmSearch : draft.harm_type ?? ''}
                selectedOptions={[draft.harm_type ?? '']}
                onOpenChange={(_, data) => { setHarmOpen(data.open); setHarmSearch('') }}
                onChange={event => { setHarmSearch(event.target.value); setHarmOpen(true) }}
                onOptionSelect={(_, data) => {
                  const harmType = data.optionValue
                  if (harmType !== '' && !harmTypes?.includes(harmType ?? '')) return
                  if ((harmType || null) !== draft.harm_type) clearFieldError('harm_type')
                  setDraft(value => ({
                    ...value, harm_type: harmType || null,
                    harm_type_other: harmType === 'Other' ? (value.harm_type === 'Other' ? value.harm_type_other : '') : null,
                  }))
                  setHarmOpen(false)
                  setHarmSearch('')
                }}
                listbox={{ className: styles.harmListbox }}>
                <Option value="">Not set</Option>
                {harmTypes?.filter(category => category === 'Other'
                  || category.toLowerCase().includes(harmSearch.toLowerCase()))
                  .map(category => <Option key={category} value={category}>{category}</Option>)}
                {harmTypes && !harmTypes.some(category => category.toLowerCase().includes(harmSearch.toLowerCase()))
                  && <Option disabled>No matching categories. Choose Other to enter custom text.</Option>}
              </Combobox>
            </Field>
            {draft.harm_type === 'Other' && <Field label="Other harm-type" required
              validationState={activeFieldErrors.harm_type_other ? 'error' : 'none'}
              validationMessage={activeFieldErrors.harm_type_other}>
              <Input value={draft.harm_type_other ?? ''} disabled={saving} name="harm_type_other" autoComplete="off"
                aria-invalid={Boolean(activeFieldErrors.harm_type_other)}
                onChange={(_, data) => {
                  clearFieldError('harm_type_other')
                  setDraft(value => ({ ...value, harm_type_other: data.value }))
                }} />
            </Field>}
            <Field label="Description" hint="Optional" validationState={activeFieldErrors.description ? 'error' : 'none'}
              validationMessage={activeFieldErrors.description}>
              <Textarea value={draft.description} disabled={saving} resize="vertical" name="description" autoComplete="off"
                aria-invalid={Boolean(activeFieldErrors.description)}
                onChange={(_, data) => {
                  clearFieldError('description')
                  setDraft(value => ({ ...value, description: data.value }))
                }} />
            </Field>
            {evidenceField && <Field label="Evidence attack ID"
              hint="Optional. Links a saved attack as evidence; the attack is not changed. Its ID is in the attack's address."
              validationState={activeFieldErrors.attack_result_id ? 'error' : 'none'}
              validationMessage={activeFieldErrors.attack_result_id}>
              <Input value={attackResultId} disabled={saving} name="attack_result_id" autoComplete="off"
                aria-invalid={Boolean(activeFieldErrors.attack_result_id)}
                onChange={(_, data) => {
                  if (data.value.trim() !== attackResultId.trim()) clearFieldError('attack_result_id')
                  setAttackResultId(data.value)
                }} />
            </Field>}
            <div className={styles.actions}>
              <Button className={styles.button} disabled={saving} onClick={onClose}>Cancel</Button>
              <Button type="submit" appearance="primary" className={styles.button} disabledFocusable={saving}
                disabled={!canSave}>
                {saving ? 'Saving…' : 'Save finding'}
              </Button>
            </div>
          </form>
        </DialogContent>
      </DialogBody></DialogSurface>
    </Dialog>
  )
}
