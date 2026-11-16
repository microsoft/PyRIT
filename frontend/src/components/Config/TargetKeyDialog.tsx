import { useEffect, useRef, useState, type FormEvent } from 'react'
import {
  Button,
  Dialog,
  DialogActions,
  DialogBody,
  DialogContent,
  DialogSurface,
  DialogTitle,
  Field,
  Input,
  MessageBar,
  MessageBarBody,
  Text,
} from '@fluentui/react-components'

import { targetsApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { TargetInstance } from '@/types'
import { targetNeedsCallerKey } from '@/utils/targetAccount'

import { useTargetKeyDialogStyles } from './TargetKeyDialog.styles'

interface TargetKeyDialogProps {
  /** A target that uses each person's own key. */
  target: TargetInstance
  onClose: () => void
  /** Called after the key is saved, so the caller can refresh the targets. */
  onSaved: () => void
}

/** Sets or replaces the user's own key for a target that uses each person's own key. */
export default function TargetKeyDialog({ target, onClose, onSaved }: TargetKeyDialogProps) {
  const styles = useTargetKeyDialogStyles()
  const [credential, setCredential] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const errorRef = useRef<HTMLDivElement>(null)
  const replacing = !targetNeedsCallerKey(target)
  const canSave = !saving && credential.trim() !== ''

  // Saving disables the button that had focus, so move focus to the error that explains the failure.
  useEffect(() => {
    if (error) errorRef.current?.focus()
  }, [error])

  const handleClose = (): void => {
    if (!saving) onClose()
  }

  const handleSubmit = async (event: FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault()
    if (!canSave) return
    setSaving(true)
    setError(null)
    try {
      await targetsApi.setTargetCredential(target.target_registry_name, credential)
      onSaved()
    } catch (err: unknown) {
      setError(toApiError(err).detail)
      setSaving(false)
    }
  }

  return (
    <Dialog open onOpenChange={(_, data) => { if (!data.open) handleClose() }}>
      <DialogSurface>
        <form onSubmit={(event: FormEvent<HTMLFormElement>) => { void handleSubmit(event) }}>
          <DialogBody>
            <DialogTitle>{replacing ? 'Replace your key' : 'Set your key'}</DialogTitle>
            <DialogContent className={styles.content}>
              <Text className={styles.targetName}>
                Your requests to <Text weight="semibold">{target.target_registry_name}</Text> will use this key.
              </Text>
              {error && (
                <div ref={errorRef} role="alert" tabIndex={-1}>
                  <MessageBar intent="error">
                    <MessageBarBody>{error}</MessageBarBody>
                  </MessageBar>
                </div>
              )}
              <Field
                label="Your key"
                hint="Stored only for you, in server memory, until the server restarts."
                required
              >
                <Input
                  type="password"
                  value={credential}
                  onChange={(_, data) => setCredential(data.value)}
                />
              </Field>
            </DialogContent>
            <DialogActions>
              <Button appearance="secondary" onClick={handleClose} disabled={saving}>
                Cancel
              </Button>
              <Button appearance="primary" type="submit" disabled={!canSave}>
                {saving ? 'Saving...' : 'Save'}
              </Button>
            </DialogActions>
          </DialogBody>
        </form>
      </DialogSurface>
    </Dialog>
  )
}
