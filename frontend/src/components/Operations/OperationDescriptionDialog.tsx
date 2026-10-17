import { useRef, useState } from 'react'
import {
  Button, Dialog, DialogBody, DialogContent, DialogSurface, DialogTitle, DialogTrigger,
  Field, MessageBar, MessageBarBody, Textarea,
} from '@fluentui/react-components'

import { operationsApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { Operation } from '@/types'
import { useOperationsStyles } from './Operations.styles'

interface OperationDescriptionDialogProps {
  operation: Operation
  onSaved: (operation: Operation) => void
}

export default function OperationDescriptionDialog({ operation, onSaved }: OperationDescriptionDialogProps) {
  const styles = useOperationsStyles()
  const [open, setOpen] = useState(false)
  const [description, setDescription] = useState('')
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const submitting = useRef(false)

  const save = async (): Promise<void> => {
    if (submitting.current) return
    submitting.current = true
    setSaving(true)
    setError('')
    try {
      onSaved(await operationsApi.update(operation.id, { description }))
      setOpen(false)
    } catch (cause: unknown) {
      setError(toApiError(cause).detail)
    } finally {
      submitting.current = false
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={(_, data) => {
      if (saving) return
      if (data.open) { setDescription(operation.description ?? ''); setError('') }
      setOpen(data.open)
    }}>
      <DialogTrigger disableButtonEnhancement>
        <Button className={styles.button}>{operation.description ? 'Edit description' : 'Add description'}</Button>
      </DialogTrigger>
      <DialogSurface>
        <DialogBody>
          <DialogTitle>Operation description</DialogTitle>
          <DialogContent>
            <form className={styles.form} onSubmit={event => { event.preventDefault(); void save() }}>
              {error && <MessageBar intent="error" aria-live="polite"><MessageBarBody>
                Could not save description: {error}
              </MessageBarBody></MessageBar>}
              <Field label="Description" hint="The engagement's purpose and scope. Leave empty to remove it.">
                <Textarea value={description} disabled={saving} resize="vertical" name="operation-description"
                  autoComplete="off" onChange={(_, data) => { setDescription(data.value) }} />
              </Field>
              <div className={styles.actions}>
                <DialogTrigger disableButtonEnhancement>
                  <Button className={styles.button} disabled={saving}>Cancel</Button>
                </DialogTrigger>
                <Button type="submit" appearance="primary" className={styles.button} disabledFocusable={saving}>
                  {saving ? 'Saving…' : 'Save description'}
                </Button>
              </div>
            </form>
          </DialogContent>
        </DialogBody>
      </DialogSurface>
    </Dialog>
  )
}
