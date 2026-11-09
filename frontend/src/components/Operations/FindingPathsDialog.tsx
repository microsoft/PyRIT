import { useRef, useState } from 'react'

import {
  Badge, Button, Dialog, DialogBody, DialogContent, DialogSurface, DialogTitle,
  Field, Input, MessageBar, MessageBarBody, Select, Text,
} from '@fluentui/react-components'
import { AddRegular, ArrowDownRegular, ArrowUpRegular, DismissRegular } from '@fluentui/react-icons'

import { operationsApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { FindingListItem } from '@/types'
import { generateClientId } from '@/utils/clientId'
import { findingPathName, isFindingPathComplete } from '@/utils/findingTree'

import { useFindingPathsDialogStyles } from './FindingPathsDialog.styles'

/** A path being edited. Saved paths keep their ID; new ones get one when saved. */
interface PathDraft {
  key: string
  id?: string
  name: string
  description: string
  steps: string[]
}

interface FindingPathsDialogProps {
  operationId: string
  finding: FindingListItem
  /** The finding's direct sub-findings: the only findings a step can be. */
  subFindings: readonly FindingListItem[]
  onSaved: (saved: FindingListItem) => void
  onClose: () => void
}

/** Add, rename, reorder, and remove the ordered paths through a finding's sub-findings, then save them together. */
export default function FindingPathsDialog({ operationId, finding, subFindings, onSaved, onClose }: FindingPathsDialogProps) {
  const styles = useFindingPathsDialogStyles()
  const [drafts, setDrafts] = useState<PathDraft[]>(() => finding.paths.map(path => ({
    key: path.id, id: path.id, name: path.name ?? '', description: path.description ?? '', steps: [...path.steps],
  })))
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const submitting = useRef(false)
  const byId = new Map(subFindings.map(subFinding => [subFinding.id, subFinding]))
  const demonstrated = new Map(subFindings.map(subFinding => [subFinding.id, subFinding.demonstrated]))
  const canSave = drafts.every(draft => draft.steps.length > 0)

  const update = (key: string, change: (draft: PathDraft) => PathDraft): void => {
    setDrafts(current => current.map(draft => (draft.key === key ? change(draft) : draft)))
  }

  const moveStep = (key: string, from: number, to: number): void => {
    update(key, draft => {
      const steps = [...draft.steps]
      const [step] = steps.splice(from, 1)
      if (step === undefined) return draft
      steps.splice(to, 0, step)
      return { ...draft, steps }
    })
  }

  const save = async (): Promise<void> => {
    if (submitting.current || !canSave) return
    submitting.current = true
    setSaving(true)
    setError('')
    try {
      const saved = await operationsApi.replaceFindingPaths(operationId, finding.id, {
        paths: drafts.map(draft => ({
          ...(draft.id ? { id: draft.id } : {}),
          name: draft.name.trim() || null,
          description: draft.description,
          steps: draft.steps,
        })),
      })
      onSaved(saved)
    } catch (cause: unknown) {
      setError(toApiError(cause).detail)
    } finally {
      submitting.current = false
      setSaving(false)
    }
  }

  const stepTitle = (step: string): string => byId.get(step)?.title ?? 'Unknown sub-finding'

  const renderPath = (draft: PathDraft, index: number): React.ReactNode => {
    const label = findingPathName(draft, index)
    const complete = isFindingPathComplete(draft, demonstrated)
    const available = subFindings.filter(subFinding => !draft.steps.includes(subFinding.id))
    return (
      <fieldset key={draft.key} className={styles.path}>
        <legend className={styles.legend}>
          {label}
          {draft.steps.length > 0 && (
            <Badge appearance="tint" color={complete ? 'success' : 'warning'}>{complete ? 'Complete' : 'Partial'}</Badge>
          )}
        </legend>
        <Field label="Name" hint="Optional">
          <Input value={draft.name} disabled={saving} placeholder={`Path ${index + 1}`} autoComplete="off"
            onChange={(_, data) => { update(draft.key, value => ({ ...value, name: data.value })) }} />
        </Field>
        {draft.steps.length === 0 ? <Text className={styles.muted}>Add at least one step.</Text> : (
          <ol className={styles.steps} aria-label={`Steps of ${label}`}>
            {draft.steps.map((step, position) => {
              const title = stepTitle(step)
              return (
                <li key={step} className={styles.step}><div className={styles.stepRow}>
                  <Text className={styles.stepTitle}>
                    {title}
                    {byId.get(step)?.demonstrated === false && <span className={styles.missing}> (not demonstrated)</span>}
                  </Text>
                  <div className={styles.stepActions}>
                    <Button className={styles.button} appearance="subtle" icon={<ArrowUpRegular />}
                      aria-label={`Move up: ${title}`} title="Move up" disabled={saving || position === 0}
                      onClick={() => { moveStep(draft.key, position, position - 1) }} />
                    <Button className={styles.button} appearance="subtle" icon={<ArrowDownRegular />}
                      aria-label={`Move down: ${title}`} title="Move down"
                      disabled={saving || position === draft.steps.length - 1}
                      onClick={() => { moveStep(draft.key, position, position + 1) }} />
                    <Button className={styles.button} appearance="subtle" icon={<DismissRegular />}
                      aria-label={`Remove step: ${title}`} title="Remove step" disabled={saving}
                      onClick={() => {
                        update(draft.key, value => ({ ...value, steps: value.steps.filter(item => item !== step) }))
                      }} />
                  </div>
                </div></li>
              )
            })}
          </ol>
        )}
        <div className={styles.row}>
          <Field className={styles.grow} label="Add step"
            hint={available.length === 0 ? 'Every sub-finding is already a step of this path.' : undefined}>
            <Select value="" disabled={saving || available.length === 0} onChange={event => {
              const step = event.target.value
              if (byId.has(step)) update(draft.key, value => ({ ...value, steps: [...value.steps, step] }))
            }}>
              <option value="">Choose a sub-finding…</option>
              {available.map(subFinding => <option key={subFinding.id} value={subFinding.id}>{subFinding.title}</option>)}
            </Select>
          </Field>
          <Button className={styles.button} disabled={saving} aria-label={`Remove path: ${label}`}
            onClick={() => { setDrafts(current => current.filter(item => item.key !== draft.key)) }}>Remove path</Button>
        </div>
      </fieldset>
    )
  }

  return (
    <Dialog open onOpenChange={(_, data) => { if (!data.open && !submitting.current) onClose() }}>
      <DialogSurface><DialogBody>
        <DialogTitle>Edit paths</DialogTitle>
        <DialogContent>
          <form className={styles.form} onSubmit={event => { event.preventDefault(); void save() }}>
            <Text>
              Each path is one ordered way the sub-findings of “{finding.title}” demonstrate it. A path is complete
              when every step was demonstrated; a partial path shows which steps are missing.
            </Text>
            {error && <MessageBar intent="error" aria-live="polite"><MessageBarBody>
              Could not save paths: {error}
            </MessageBarBody></MessageBar>}
            {drafts.length === 0 && <Text className={styles.muted}>This finding has no paths.</Text>}
            {drafts.map(renderPath)}
            <Button className={styles.button} icon={<AddRegular />} disabled={saving} onClick={() => {
              setDrafts(current => [...current, { key: generateClientId(), name: '', description: '', steps: [] }])
            }}>Add path</Button>
            <div className={styles.actions}>
              <Button className={styles.button} disabled={saving} onClick={onClose}>Cancel</Button>
              <Button type="submit" appearance="primary" className={styles.button} disabledFocusable={saving}
                disabled={!canSave}>{saving ? 'Saving…' : 'Save paths'}</Button>
            </div>
          </form>
        </DialogContent>
      </DialogBody></DialogSurface>
    </Dialog>
  )
}
