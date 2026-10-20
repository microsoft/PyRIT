import { useRef, useState } from 'react'
import {
  Button, Dialog, DialogBody, DialogContent, DialogSurface, DialogTitle, DialogTrigger,
  Field, Input, MessageBar, MessageBarBody, Textarea,
} from '@fluentui/react-components'

import { operationsApi } from '@/services/api'
import { conflictingOperation, toApiError } from '@/services/errors'
import type { Operation, OperationUpdate } from '@/types'
import { useOperationsStyles } from './Operations.styles'

interface OperationEditDialogProps {
  operation: Operation
  onSaved: (operation: Operation) => void
}

export default function OperationEditDialog({ operation, onSaved }: OperationEditDialogProps) {
  const styles = useOperationsStyles()
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [error, setError] = useState('')
  const [existing, setExisting] = useState<Operation | null>(null)
  const [saving, setSaving] = useState(false)
  const submitting = useRef(false)

  const save = async (): Promise<void> => {
    if (submitting.current) return
    submitting.current = true
    setSaving(true)
    setError('')
    setExisting(null)
    try {
      // Sending only edited fields keeps a stale page from undoing someone else's change to the other field.
      const changes: OperationUpdate = {
        ...(name !== operation.name ? { name } : {}),
        ...(description !== (operation.description ?? '') ? { description } : {}),
      }
      onSaved(await operationsApi.update(operation.id, changes))
      setOpen(false)
    } catch (cause: unknown) {
      const conflict = conflictingOperation(cause)
      if (conflict) setExisting(conflict)
      else setError(toApiError(cause).detail)
    } finally {
      submitting.current = false
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={(_, data) => {
      if (saving) return
      if (data.open) {
        setName(operation.name)
        setDescription(operation.description ?? '')
        setError('')
        setExisting(null)
      }
      setOpen(data.open)
    }}>
      <DialogTrigger disableButtonEnhancement>
        <Button className={styles.button}>Edit</Button>
      </DialogTrigger>
      <DialogSurface>
        <DialogBody>
          <DialogTitle>Edit operation</DialogTitle>
          <DialogContent>
            <form className={styles.form} onSubmit={event => { event.preventDefault(); void save() }}>
              {error && <MessageBar intent="error" aria-live="polite"><MessageBarBody>
                Could not save operation: {error}
              </MessageBarBody></MessageBar>}
              {existing && <MessageBar intent="warning" aria-live="polite"><MessageBarBody>
                Another operation is already named “{existing.name}”. Choose a different name.
              </MessageBarBody></MessageBar>}
              <Field label="Name" required hint="Unique, ignoring case.">
                <Input className={styles.input} value={name} disabled={saving} maxLength={128}
                  name="operation-name" autoComplete="off"
                  onChange={(_, data) => { setName(data.value); setExisting(null) }} />
              </Field>
              <Field label="Description" hint="The engagement's purpose and scope. Leave empty to remove it.">
                <Textarea value={description} disabled={saving} resize="vertical" name="operation-description"
                  autoComplete="off" onChange={(_, data) => { setDescription(data.value) }} />
              </Field>
              <div className={styles.actions}>
                <DialogTrigger disableButtonEnhancement>
                  <Button className={styles.button} disabled={saving}>Cancel</Button>
                </DialogTrigger>
                <Button type="submit" appearance="primary" className={styles.button}
                  disabledFocusable={saving} disabled={!name.trim()}>
                  {saving ? 'Saving…' : 'Save operation'}
                </Button>
              </div>
            </form>
          </DialogContent>
        </DialogBody>
      </DialogSurface>
    </Dialog>
  )
}
