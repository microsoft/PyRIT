import { useState } from 'react'

import {
  Button, Dialog, DialogActions, DialogBody, DialogContent, DialogSurface, DialogTitle,
  MessageBar, MessageBarBody, Radio, RadioGroup, Text, Tooltip,
} from '@fluentui/react-components'

import type { SaveConversationRequest } from '@/types'

import { useSaveConversationDialogStyles } from './SaveConversationDialog.styles'

interface SaveConversationDialogProps {
  initialDestination?: SaveConversationRequest['destination']
  sameAttackDisabledReason?: string
  objectiveChanged: boolean
  saving: boolean
  error: string | null
  validationError?: string | null
  onClose: () => void
  onSave: (destination: SaveConversationRequest['destination']) => void
}

export default function SaveConversationDialog({
  initialDestination, sameAttackDisabledReason, objectiveChanged, saving, error, validationError, onClose, onSave,
}: SaveConversationDialogProps) {
  const styles = useSaveConversationDialogStyles()
  const [destination, setDestination] = useState<SaveConversationRequest['destination']>(
    sameAttackDisabledReason ? 'new_attack' : initialDestination ?? 'same_attack',
  )
  return (
    <Dialog open onOpenChange={(_event, data) => { if (!data.open && !saving) onClose() }}>
      <DialogSurface>
        <DialogBody>
          <DialogTitle>Save conversation to</DialogTitle>
          <DialogContent>
            <Text>This saves a new conversation. The original messages stay unchanged.</Text>
            <RadioGroup
              value={destination}
              disabled={saving}
              onChange={(_event, data) => {
                if (data.value === 'same_attack' || data.value === 'new_attack') setDestination(data.value)
              }}
            >
              <Tooltip content={sameAttackDisabledReason ?? 'Add a related conversation. Keep the main conversation.'} relationship="description">
                <span tabIndex={sameAttackDisabledReason ? 0 : undefined} aria-label={sameAttackDisabledReason}>
                  <Radio value="same_attack" label="Same attack" disabled={Boolean(sameAttackDisabledReason)} />
                </span>
              </Tooltip>
              <Radio value="new_attack" label="New attack" />
            </RadioGroup>
            {destination === 'same_attack' && objectiveChanged && (
              <MessageBar intent="warning"><MessageBarBody>
                This changes the objective for all conversations in this attack. The outcome resets to
                Undetermined. Old scores stay in history.
              </MessageBarBody></MessageBar>
            )}
            {error && <MessageBar intent="error"><MessageBarBody>{error}</MessageBarBody></MessageBar>}
            {validationError && <MessageBar intent="error"><MessageBarBody>{validationError}</MessageBarBody></MessageBar>}
          </DialogContent>
          <DialogActions>
            <Button className={styles.button} disabled={saving} onClick={onClose}>Back to editing</Button>
            <Button
              className={styles.button}
              appearance="primary"
              disabled={saving || Boolean(validationError) || (destination === 'same_attack' && Boolean(sameAttackDisabledReason))}
              onClick={() => onSave(destination)}
            >{saving ? 'Saving...' : 'Save conversation'}</Button>
          </DialogActions>
        </DialogBody>
      </DialogSurface>
    </Dialog>
  )
}
