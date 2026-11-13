import { Fragment, useEffect, useRef, useState } from 'react'

import {
  Button, Dialog, DialogBody, DialogContent, DialogSurface, DialogTitle,
  Field, Input, MessageBar, MessageBarBody, Text,
} from '@fluentui/react-components'

import { type FieldError, toApiError } from '@/services/errors'
import type { FindingCreate, FindingField, FindingFields, FindingListItem } from '@/types'
import { generateClientId } from '@/utils/clientId'
import { SEVERITY_KEY } from '@/utils/findingFields'

import { useFindingDialogStyles } from './FindingDialog.styles'
import FindingFieldInput from './FindingFieldInput'
import RetiredFieldValues from './RetiredFieldValues'

const NO_KEYS: ReadonlySet<string> = new Set()

/** One save attempt. The id repeats while the input does, so a retry returns a finding a lost response saved. */
export interface FindingSaveAttempt {
  id: string
  attackResultId: string
}

/** The finding Save sends. An edit lists its retired values by key: those it keeps and those to delete. */
export interface FindingDraft extends FindingCreate {
  keep_retired_keys?: string[]
  remove_retired_keys?: string[]
}

interface FindingDialogProps {
  /** Read once, when the dialog opens. A reload while it's open doesn't change its inputs or retired values. */
  fields: FindingFields
  /** An edit's finding, with the retired values the findings list sets apart. */
  initialValues?: FindingCreate & Pick<FindingListItem, 'retired'>
  editing?: boolean
  context?: React.ReactNode
  /** Shows an optional attack ID to link as evidence. */
  evidenceField?: boolean
  saveDisabled?: boolean
  onSave: (draft: FindingDraft, attempt: FindingSaveAttempt) => Promise<void>
  onClose: () => void
}

interface RetiredValue {
  key: string
  value: string
}

/** A new finding starts from the field defaults. An edit starts from its values, with retired ones set apart. */
function openingState(
  fields: FindingFields, initialValues: FindingDialogProps['initialValues'],
): { draft: FindingCreate; retired: RetiredValue[] } {
  if (initialValues === undefined) {
    const defaults: Record<string, string> = {}
    for (const field of fields.fields) {
      if (field.default !== null) defaults[field.key] = field.default
    }
    return { draft: { title: '', severity: fields.severity.default ?? '', field_values: defaults }, retired: [] }
  }
  const retiredKeys = new Set(initialValues.retired.map(([key]: [string, string]) => key))
  const current = Object.entries(initialValues.field_values).filter(([key]: [string, string]) => !retiredKeys.has(key))
  return {
    draft: { title: initialValues.title, severity: initialValues.severity, field_values: Object.fromEntries(current) },
    retired: initialValues.retired.map(([key, value]: [string, string]) => ({ key, value })),
  }
}

/** The draft as the backend stores it, which drops blank values. */
function filledFinding(draft: FindingCreate): FindingCreate {
  const filled = Object.entries(draft.field_values).filter(([, value]: [string, string]) => value.trim() !== '')
  return { title: draft.title, severity: draft.severity, field_values: Object.fromEntries(filled) }
}

function sameFinding(left: FindingCreate, right: FindingCreate): boolean {
  const leftValues = Object.entries(filledFinding(left).field_values)
  const rightValues = filledFinding(right).field_values
  return left.title === right.title && left.severity === right.severity
    && leftValues.length === Object.keys(rightValues).length
    && leftValues.every(([key, value]: [string, string]) => rightValues[key] === value)
}

/** Each field's section heading, shown where the section differs from the field before it. */
function sectionHeading(field: FindingField, slots: FindingField[], index: number): string | null {
  const section = field.section
  return section !== null && section !== slots[index - 1]?.section ? section : null
}

export default function FindingDialog({
  fields, initialValues, editing = false, context, evidenceField = false, saveDisabled = false, onSave, onClose,
}: FindingDialogProps) {
  const styles = useFindingDialogStyles()
  const [openFields] = useState(fields)
  const [opening] = useState(() => openingState(openFields, initialValues))
  const [draft, setDraft] = useState<FindingCreate>(opening.draft)
  const [removed, setRemoved] = useState<ReadonlySet<string>>(NO_KEYS)
  // Whether each field shows Other with blank text, which leaves its value '' just like Not set.
  const [blankOther, setBlankOther] = useState<Record<string, boolean>>({})
  const [attackResultId, setAttackResultId] = useState('')
  const [error, setError] = useState('')
  const [attackResultIdError, setAttackResultIdError] = useState('')
  const [saving, setSaving] = useState(false)
  const submitting = useRef(false)
  const attempt = useRef<{ signature: string; id: string } | null>(null)
  const slots = [openFields.severity, ...openFields.fields]
  const values = { ...draft.field_values, [SEVERITY_KEY]: draft.severity }
  // Title and severity are part of every finding. The server checks the configured required values and names a
  // missing one; only blank Other text, which it never sees, is held here.
  const canSave = !saveDisabled && !attackResultIdError && Boolean(draft.title.trim()) && Boolean(draft.severity.trim())
    && !Object.values(blankOther).some(Boolean)
  const changed = !sameFinding(draft, opening.draft) || removed.size > 0 || attackResultId !== ''

  useEffect(() => {
    if (!changed) return
    const protectDraft = (event: BeforeUnloadEvent): void => { event.preventDefault() }
    window.addEventListener('beforeunload', protectDraft)
    return () => { window.removeEventListener('beforeunload', protectDraft) }
  }, [changed])

  const applyChanges = (changes: Record<string, string>): void => {
    setDraft((current: FindingCreate) => {
      const { [SEVERITY_KEY]: severity = current.severity, ...fieldChanges } = changes
      return { ...current, severity, field_values: { ...current.field_values, ...fieldChanges } }
    })
  }

  const save = async (): Promise<void> => {
    if (submitting.current || !canSave) return
    submitting.current = true
    setSaving(true)
    setError('')
    setAttackResultIdError('')
    const trimmedAttackResultId = attackResultId.trim()
    // The server keeps a listed retired value even if a live apply has configured its field again.
    const filled = filledFinding(draft)
    const finding: FindingDraft = editing
      ? {
        ...filled,
        keep_retired_keys: opening.retired.map(({ key }: RetiredValue) => key).filter((key: string) => !removed.has(key)),
        remove_retired_keys: [...removed],
      }
      : filled
    const signature = JSON.stringify([finding, trimmedAttackResultId])
    if (attempt.current?.signature !== signature) attempt.current = { signature, id: generateClientId() }
    try {
      await onSave(finding, { id: attempt.current.id, attackResultId: trimmedAttackResultId })
    } catch (cause: unknown) {
      const apiError = toApiError(cause)
      // Match the whole path: a deployment's own field can also be keyed attack_result_id under field_values.
      const isEvidenceError = (item: FieldError): boolean =>
        evidenceField && item.field.replace(/^body\./, '') === 'attack_result_id'
      const evidenceMessages = apiError.fieldErrors.filter(isEvidenceError).map(item => item.message)
      const otherMessages = apiError.fieldErrors.filter(item => !isEvidenceError(item))
        .map(item => `${item.field.replace(/^body\./, '')}: ${item.message}`)
      setAttackResultIdError(evidenceMessages.join(' '))
      if (evidenceMessages.length === 0 || otherMessages.length > 0) {
        setError([otherMessages.length > 0 ? '' : apiError.detail, ...otherMessages].filter(Boolean).join(' '))
      }
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
          <form className={styles.form} onSubmit={event => { event.preventDefault(); void save() }}>
            {context ?? <Text>Record a human assessment. No attack is required.</Text>}
            <Field label="Title" required>
              <Input className={styles.input} value={draft.title} disabled={saving} name="title" autoComplete="off"
                onChange={(_, data) => { setDraft(value => ({ ...value, title: data.value })) }} />
            </Field>
            {slots.map((field: FindingField, index: number) => {
              const heading = sectionHeading(field, slots, index)
              return (
                <Fragment key={field.key}>
                  {heading !== null && <Text as="h3" size={400} weight="semibold" className={styles.section}>
                    {heading}
                  </Text>}
                  <FindingFieldInput field={field} values={values} disabled={saving} onChange={applyChanges}
                    onOtherBlankChange={(blank: boolean) => {
                      setBlankOther(current => ({ ...current, [field.key]: blank }))
                    }} />
                </Fragment>
              )
            })}
            <RetiredFieldValues values={opening.retired} removed={removed} disabled={saving}
              onRemove={(key: string) => { setRemoved(previous => new Set([...previous, key])) }}
              onUndo={() => { setRemoved(new Set()) }} />
            {evidenceField && <Field label="Evidence attack ID"
              validationState={attackResultIdError ? 'error' : 'none'} validationMessage={attackResultIdError || undefined}
              hint="Optional. Links a saved attack as evidence; the attack is not changed. Its ID is in the attack's address.">
              <Input className={styles.input} value={attackResultId} disabled={saving} name="attack_result_id"
                autoComplete="off"
                onChange={(_, data) => { setAttackResultId(data.value); setAttackResultIdError('') }} />
            </Field>}
            {error && <MessageBar intent="error" aria-live="polite"><MessageBarBody>{error}</MessageBarBody></MessageBar>}
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
