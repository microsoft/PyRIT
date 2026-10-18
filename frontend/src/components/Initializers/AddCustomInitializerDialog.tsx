import { useState } from 'react'
import type { FormEvent } from 'react'

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
} from '@fluentui/react-components'

import { PythonCodeEditor } from '@/components/Configuration/PythonCode'

import { useCustomInitializersStyles } from './CustomInitializers.styles'

interface AddCustomInitializerDialogProps {
  open: boolean
  registering: boolean
  onOpenChange: (open: boolean) => void
  onRegister: (name: string, scriptContent: string) => Promise<boolean>
}

export default function AddCustomInitializerDialog({
  open,
  registering,
  onOpenChange,
  onRegister,
}: AddCustomInitializerDialogProps) {
  const styles = useCustomInitializersStyles()
  const [name, setName] = useState('')
  const [scriptContent, setScriptContent] = useState('')

  const handleSubmit = async (event: FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault()
    if (await onRegister(name.trim(), scriptContent)) {
      onOpenChange(false)
      setName('')
      setScriptContent('')
    }
  }

  return (
    <Dialog open={open} onOpenChange={(_, data) => onOpenChange(data.open)}>
      <DialogSurface className={styles.sourceDialog}>
        <form onSubmit={handleSubmit}>
          <DialogBody>
            <DialogTitle>Add custom initializer</DialogTitle>
            <DialogContent className={styles.dialogBody}>
              <Field label="Initializer name" required>
                <Input
                  value={name}
                  onChange={(_, data) => setName(data.value)}
                  disabled={registering}
                  autoComplete="off"
                />
              </Field>
              <Field label="Python source" required>
                <PythonCodeEditor source={scriptContent} onChange={setScriptContent} disabled={registering} />
              </Field>
            </DialogContent>
            <DialogActions>
              <Button appearance="secondary" disabled={registering} onClick={() => onOpenChange(false)}>
                Cancel
              </Button>
              <Button
                type="submit"
                appearance="primary"
                disabled={registering || name.trim() === '' || scriptContent.trim() === ''}
              >
                {registering ? 'Adding...' : 'Add'}
              </Button>
            </DialogActions>
          </DialogBody>
        </form>
      </DialogSurface>
    </Dialog>
  )
}
