import { useEffect, useRef, useState } from 'react'

import {
  Button, Combobox, Dialog, DialogBody, DialogContent, DialogSurface, DialogTitle,
  Field, Input, MessageBar, MessageBarBody, Option, Select, Text, Textarea,
} from '@fluentui/react-components'

import { operationsApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { FindingCreate, FindingSeverity } from '@/types'
import { FINDING_SEVERITY_LABELS } from '@/utils/findingSeverity'

import { useFindingDialogStyles } from './FindingDialog.styles'

const EMPTY_DRAFT: FindingCreate = {
  title: '', description: '', severity: 'moderate', severity_other: null, harm_type: null, harm_type_other: null,
}

interface FindingDialogProps {
  initialValues?: FindingCreate
  editing?: boolean
  context?: React.ReactNode
  recovery?: React.ReactNode
  saveDisabled?: boolean
  onSave: (draft: FindingCreate) => Promise<void>
  onClose: () => void
}

export default function FindingDialog({
  initialValues = EMPTY_DRAFT, editing = false, context, recovery, saveDisabled = false, onSave, onClose,
}: FindingDialogProps) {
  const styles = useFindingDialogStyles()
  const [draft, setDraft] = useState<FindingCreate>({ ...EMPTY_DRAFT, ...initialValues })
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const submitting = useRef(false)
  const [harmTypes, setHarmTypes] = useState<string[] | null>(null)
  const [optionsError, setOptionsError] = useState('')
  const [optionsRevision, setOptionsRevision] = useState(0)
  const [harmOpen, setHarmOpen] = useState(false)
  const [harmSearch, setHarmSearch] = useState('')
  const recovering = Boolean(recovery)
  const classificationsValid = (draft.severity !== 'other' || Boolean(draft.severity_other?.trim()))
    && (draft.harm_type !== 'Other' || Boolean(draft.harm_type_other?.trim()))
  const canSave = !saveDisabled && (recovering || (
    Boolean(draft.title.trim()) && classificationsValid && harmTypes !== null && !optionsError
  ))

  useEffect(() => {
    if (recovering) return
    let ignore = false
    operationsApi.getFindingOptions()
      .then(options => { if (!ignore) { setHarmTypes(options.harm_types); setOptionsError('') } })
      .catch((cause: unknown) => { if (!ignore) setOptionsError(toApiError(cause).detail) })
    return () => { ignore = true }
  }, [optionsRevision, recovering])

  useEffect(() => {
    if (recovery || (!draft.title && !draft.description && !draft.severity_other && !draft.harm_type && !draft.harm_type_other)) return
    const protectDraft = (event: BeforeUnloadEvent): void => { event.preventDefault() }
    window.addEventListener('beforeunload', protectDraft)
    return () => { window.removeEventListener('beforeunload', protectDraft) }
  }, [draft.title, draft.description, draft.severity_other, draft.harm_type, draft.harm_type_other, recovery])

  const save = async (): Promise<void> => {
    if (submitting.current || !canSave) return
    submitting.current = true
    setSaving(true)
    setError('')
    try {
      await onSave(draft)
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
            {recovery ?? <>
              <Field label="Title" required>
                <Input value={draft.title} disabled={saving} name="title" autoComplete="off"
                  onChange={(_, data) => { setDraft(value => ({ ...value, title: data.value })) }} />
              </Field>
              <Field label="Severity" required>
                <Select value={draft.severity} disabled={saving} name="severity" onChange={event => {
                  const severity = event.target.value
                  if (severity in FINDING_SEVERITY_LABELS) setDraft(value => ({
                    ...value, severity: severity as FindingSeverity, severity_other: severity === 'other' ? '' : null,
                  }))
                }}>
                  {Object.entries(FINDING_SEVERITY_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                </Select>
              </Field>
              {draft.severity === 'other' && <Field label="Other severity" required>
                <Input value={draft.severity_other ?? ''} disabled={saving} name="severity_other" autoComplete="off"
                  onChange={(_, data) => { setDraft(value => ({ ...value, severity_other: data.value })) }} />
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
                ? 'Loading harm categories…' : 'Optional. Select a category, or choose Other for custom text.'}>
                <Combobox className={styles.harmPicker} inlinePopup open={harmOpen}
                  disabled={saving || harmTypes === null || Boolean(optionsError)}
                  placeholder="Not set" value={harmOpen ? harmSearch : draft.harm_type ?? ''}
                  selectedOptions={[draft.harm_type ?? '']}
                  onOpenChange={(_, data) => { setHarmOpen(data.open); setHarmSearch('') }}
                  onChange={event => { setHarmSearch(event.target.value); setHarmOpen(true) }}
                  onOptionSelect={(_, data) => {
                    const harmType = data.optionValue
                    if (harmType !== '' && !harmTypes?.includes(harmType ?? '')) return
                    setDraft(value => ({
                      ...value, harm_type: harmType || null,
                      harm_type_other: harmType === 'Other' ? (value.harm_type === 'Other' ? value.harm_type_other : '') : null,
                    }))
                    setHarmOpen(false)
                    setHarmSearch('')
                  }}
                  listbox={{ className: styles.harmListbox }}>
                  <Option value="">Not set</Option>
                  {harmTypes?.filter(category => category.toLowerCase().includes(harmSearch.toLowerCase()))
                    .map(category => <Option key={category} value={category}>{category}</Option>)}
                  {harmTypes && !harmTypes.some(category => category.toLowerCase().includes(harmSearch.toLowerCase()))
                    && <Option disabled>No matching categories. Choose Other to enter custom text.</Option>}
                </Combobox>
              </Field>
              {draft.harm_type === 'Other' && <Field label="Other harm-type" required>
                <Input value={draft.harm_type_other ?? ''} disabled={saving} name="harm_type_other" autoComplete="off"
                  onChange={(_, data) => { setDraft(value => ({ ...value, harm_type_other: data.value })) }} />
              </Field>}
              <Field label="Description" hint="Optional">
                <Textarea value={draft.description} disabled={saving} resize="vertical" name="description" autoComplete="off"
                  onChange={(_, data) => { setDraft(value => ({ ...value, description: data.value })) }} />
              </Field>
            </>}
            <div className={styles.actions}>
              <Button className={styles.button} disabled={saving} onClick={onClose}>Cancel</Button>
              <Button type="submit" appearance="primary" className={styles.button} disabledFocusable={saving}
                disabled={!canSave}>
                {saving ? 'Saving…' : recovery ? 'Retry attachment' : 'Save finding'}
              </Button>
            </div>
          </form>
        </DialogContent>
      </DialogBody></DialogSurface>
    </Dialog>
  )
}
