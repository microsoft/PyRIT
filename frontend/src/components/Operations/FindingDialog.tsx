import { Fragment, useEffect, useRef, useState } from 'react'

import {
  Button, Dialog, DialogBody, DialogContent, DialogSurface, DialogTitle,
  Field, Input, MessageBar, MessageBarBody, Text,
} from '@fluentui/react-components'

import { toApiError } from '@/services/errors'
import type { FindingCreate, FindingField, FindingFields } from '@/types'
import { generateClientId } from '@/utils/clientId'
import { firstMissingRequired, type RetiredValue, SEVERITY_KEY, splitRetiredValues } from '@/utils/findingFields'

import { useFindingDialogStyles } from './FindingDialog.styles'
import FindingFieldInput from './FindingFieldInput'
import RetiredFieldValues from './RetiredFieldValues'

const NO_KEYS: ReadonlySet<string> = new Set()

/** One save attempt. The id repeats while the input does, so a retry returns a finding a lost response saved. */
export interface FindingSaveAttempt {
  id: string
  attackResultId: string
}

/** The finding Save sends. An edit also resends its kept retired values unchanged, and lists the keys to delete. */
export interface FindingDraft extends FindingCreate {
  remove_retired_keys?: string[]
}

interface FindingDialogProps {
  /** Read once, when the dialog opens. A reload while it's open doesn't change its inputs or retired values. */
  fields: FindingFields
  initialValues?: FindingCreate
  editing?: boolean
  context?: React.ReactNode
  /** Shows an optional attack ID to link as evidence. */
  evidenceField?: boolean
  saveDisabled?: boolean
  onSave: (draft: FindingDraft, attempt: FindingSaveAttempt) => Promise<void>
  onClose: () => void
}

/** A new finding starts from the field defaults. An edit starts from its values, with retired ones set apart. */
function openingState(
  fields: FindingFields, initialValues: FindingCreate | undefined,
): { draft: FindingCreate; retired: RetiredValue[] } {
  if (initialValues === undefined) {
    const defaults: Record<string, string> = {}
    for (const field of fields.fields) {
      if (field.default !== null) defaults[field.key] = field.default
    }
    return { draft: { title: '', severity: fields.severity.default ?? '', field_values: defaults }, retired: [] }
  }
  const { current, retired } = splitRetiredValues(fields, initialValues.field_values)
  return { draft: { title: initialValues.title, severity: initialValues.severity, field_values: current }, retired }
}

/** The draft as the backend stores it, which drops blank values. */
function filledFinding(draft: FindingCreate): FindingCreate {
  const filled = Object.entries(draft.field_values).filter(([, value]: [string, string]) => value.trim() !== '')
  return { title: draft.title, severity: draft.severity, field_values: Object.fromEntries(filled) }
}

/** The retired values an edit keeps and resends unchanged: all but the removed ones, whose keys it never sends. */
function keptValues(retired: RetiredValue[], removed: ReadonlySet<string>): Record<string, string> {
  const kept = retired.filter(({ key }: RetiredValue) => !removed.has(key))
  return Object.fromEntries(kept.map(({ key, value }: RetiredValue) => [key, value]))
}

function sameFinding(left: FindingCreate, right: FindingCreate): boolean {
  const leftValues = Object.entries(filledFinding(left).field_values)
  const rightValues = filledFinding(right).field_values
  return left.title === right.title && left.severity === right.severity
    && leftValues.length === Object.keys(rightValues).length
    && leftValues.every(([key, value]: [string, string]) => rightValues[key] === value)
}

/** Each field's section heading, shown where the section differs from the field before it. */
function sectionHeading(slots: FindingField[], index: number): string | null {
  const section = slots[index].section
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
  const [saving, setSaving] = useState(false)
  const submitting = useRef(false)
  const attempt = useRef<{ signature: string; id: string } | null>(null)
  const slots = [openFields.severity, ...openFields.fields]
  const values = { ...draft.field_values, [SEVERITY_KEY]: draft.severity }
  const canSave = !saveDisabled && Boolean(draft.title.trim())
    && firstMissingRequired(openFields, draft.severity, draft.field_values) === null
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
    const trimmedAttackResultId = attackResultId.trim()
    // The server clears a current field left out, and its fields may differ from those this dialog opened with.
    const filled = filledFinding(draft)
    const finding: FindingDraft = editing
      ? {
        ...filled,
        field_values: { ...keptValues(opening.retired, removed), ...filled.field_values },
        remove_retired_keys: [...removed],
      }
      : filled
    const signature = JSON.stringify([finding, trimmedAttackResultId])
    if (attempt.current?.signature !== signature) attempt.current = { signature, id: generateClientId() }
    try {
      await onSave(finding, { id: attempt.current.id, attackResultId: trimmedAttackResultId })
    } catch (cause: unknown) {
      setError(toApiError(cause).detail)
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
            {error && <MessageBar intent="error" aria-live="polite"><MessageBarBody>{error}</MessageBarBody></MessageBar>}
            <Field label="Title" required>
              <Input value={draft.title} disabled={saving} name="title" autoComplete="off"
                onChange={(_, data) => { setDraft(value => ({ ...value, title: data.value })) }} />
            </Field>
            {slots.map((field: FindingField, index: number) => {
              const heading = sectionHeading(slots, index)
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
              hint="Optional. Links a saved attack as evidence; the attack is not changed. Its ID is in the attack's address.">
              <Input value={attackResultId} disabled={saving} name="attack_result_id" autoComplete="off"
                onChange={(_, data) => { setAttackResultId(data.value) }} />
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
