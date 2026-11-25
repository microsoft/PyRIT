import { type FormEvent, useState } from 'react'

import {
  Button,
  Dialog,
  DialogActions,
  DialogBody,
  DialogContent,
  DialogSurface,
  DialogTitle,
  Field,
  MessageBar,
  MessageBarBody,
  Textarea,
} from '@fluentui/react-components'

import type { SuiteMutationOutcome } from '@/hooks/useScenarioSuiteMutations'

import { useSuiteDialogStyles } from './SuiteDialogs.styles'

interface EditSuiteDialogProps {
  suiteName: string
  description: string
  onSave: (description: string) => Promise<SuiteMutationOutcome>
  onDismiss: () => void
}

/** Edits a preset's description. Its name is its identity, so it does not change. */
export default function EditSuiteDialog({ suiteName, description, onSave, onDismiss }: EditSuiteDialogProps) {
  const styles = useSuiteDialogStyles()
  const [value, setValue] = useState(description)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  const handleSubmit = async (event: FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault()
    setSaving(true)
    setError(null)
    const outcome = await onSave(value)
    setSaving(false)
    if (outcome.status === 'done') {
      onDismiss()
    } else {
      setError(outcome.error)
    }
  }

  return (
    <Dialog open onOpenChange={(_, data) => { if (!data.open && !saving) onDismiss() }}>
      <DialogSurface>
        <form onSubmit={(event) => void handleSubmit(event)} noValidate>
          <DialogBody>
            <DialogTitle>Edit {suiteName}</DialogTitle>
            <DialogContent className={styles.body}>
              {error && (
                <MessageBar intent="error">
                  <MessageBarBody>{error}</MessageBarBody>
                </MessageBar>
              )}
              <Field label="Description">
                <Textarea
                  className={styles.control}
                  value={value}
                  disabled={saving}
                  onChange={(_, data) => setValue(data.value)}
                />
              </Field>
            </DialogContent>
            <DialogActions>
              <Button className={styles.touchTarget} appearance="secondary" disabled={saving} onClick={onDismiss}>
                Cancel
              </Button>
              <Button className={styles.touchTarget} appearance="primary" type="submit" disabled={saving}>
                {saving ? 'Saving...' : 'Save'}
              </Button>
            </DialogActions>
          </DialogBody>
        </form>
      </DialogSurface>
    </Dialog>
  )
}
