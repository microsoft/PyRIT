import { useState } from 'react'

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
  Text,
} from '@fluentui/react-components'
import { AddRegular, DeleteRegular, SaveRegular } from '@fluentui/react-icons'

import ConfirmDialog from '@/components/ConfirmDialog'
import EditorWorkspace from '@/components/EditorWorkspace'
import { PythonCodeEditor } from '@/components/BackendConfiguration/PythonCode'
import type { CustomInitializer } from '@/types'

import { useCustomInitializersStyles } from './CustomInitializers.styles'

interface CustomInitializersProps {
  items: CustomInitializer[]
  registering: boolean
  updatingName: string | null
  deletingName: string | null
  onRegister: (name: string, scriptContent: string) => Promise<boolean>
  onUpdate: (name: string, scriptContent: string) => Promise<boolean>
  onDelete: (name: string) => Promise<void>
}

export default function CustomInitializers({
  items,
  registering,
  updatingName,
  deletingName,
  onRegister,
  onUpdate,
  onDelete,
}: CustomInitializersProps) {
  const styles = useCustomInitializersStyles()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [name, setName] = useState('')
  const [scriptContent, setScriptContent] = useState('')
  const [selectedName, setSelectedName] = useState<string | null>(items[0]?.initializer_name ?? null)
  const selectedInitializer = items.find((item) => item.initializer_name === selectedName) ?? items[0] ?? null
  const [editedSource, setEditedSource] = useState(selectedInitializer?.script_content ?? '')
  const [initializerToDelete, setInitializerToDelete] = useState<CustomInitializer | null>(null)

  const handleSubmit = async (event: React.FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault()
    if (await onRegister(name.trim(), scriptContent)) {
      setDialogOpen(false)
      setName('')
      setScriptContent('')
    }
  }

  const selectInitializer = (initializer: CustomInitializer): void => {
    setSelectedName(initializer.initializer_name)
    setEditedSource(initializer.script_content)
  }

  const handleUpdate = async (): Promise<void> => {
    if (selectedInitializer) {
      await onUpdate(selectedInitializer.initializer_name, editedSource)
    }
  }

  const handleDelete = async (initializer: CustomInitializer): Promise<void> => {
    const nextInitializer = items.find((item) => item.initializer_name !== initializer.initializer_name) ?? null
    setInitializerToDelete(null)
    await onDelete(initializer.initializer_name)
    setSelectedName(nextInitializer?.initializer_name ?? null)
    setEditedSource(nextInitializer?.script_content ?? '')
  }

  return (
    <section className={styles.root} aria-label="Custom initializers">
      <EditorWorkspace
        items={items.map((item) => ({
          id: item.initializer_name,
          label: item.initializer_name,
          secondaryText: item.source,
        }))}
        selectedId={selectedInitializer?.initializer_name ?? null}
        navigationLabel="Custom initializer files"
        emptyMessage="No custom initializers registered."
        description="Edit Python initializers loaded when the backend starts."
        actions={(
          <div className={styles.editorActions}>
            <Button appearance="subtle" icon={<AddRegular />} onClick={() => setDialogOpen(true)}>
              Register initializer
            </Button>
            <Button
              appearance="subtle"
              icon={<DeleteRegular />}
              disabled={!selectedInitializer || deletingName !== null || updatingName !== null}
              onClick={() => selectedInitializer && setInitializerToDelete(selectedInitializer)}
            >
              {deletingName === selectedInitializer?.initializer_name ? 'Removing...' : 'Remove'}
            </Button>
            <Button
              appearance="primary"
              icon={<SaveRegular />}
              disabled={
                !selectedInitializer
                || updatingName !== null
                || deletingName !== null
                || editedSource.trim() === ''
                || editedSource === selectedInitializer.script_content
              }
              onClick={() => void handleUpdate()}
            >
              {updatingName === selectedInitializer?.initializer_name ? 'Saving...' : 'Save'}
            </Button>
          </div>
        )}
        onSelect={(initializerName) => {
          const initializer = items.find((item) => item.initializer_name === initializerName)
          if (initializer) selectInitializer(initializer)
        }}
      >
        {selectedInitializer && (
          <>
            <Field
              className={styles.editorField}
              label={selectedInitializer.initializer_name}
              hint={editedSource === selectedInitializer.script_content ? 'No unsaved changes' : 'Unsaved changes'}
            >
              <PythonCodeEditor
                source={editedSource}
                disabled={updatingName !== null || deletingName !== null}
                onChange={setEditedSource}
              />
            </Field>
          </>
        )}
      </EditorWorkspace>

      <Dialog open={dialogOpen} onOpenChange={(_, data) => setDialogOpen(data.open)}>
        <DialogSurface className={styles.sourceDialog}>
          <form onSubmit={handleSubmit}>
            <DialogBody>
              <DialogTitle>Register custom initializer</DialogTitle>
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
                <Button appearance="secondary" disabled={registering} onClick={() => setDialogOpen(false)}>
                  Cancel
                </Button>
                <Button
                  type="submit"
                  appearance="primary"
                  disabled={registering || name.trim() === '' || scriptContent.trim() === ''}
                >
                  {registering ? 'Registering...' : 'Register'}
                </Button>
              </DialogActions>
            </DialogBody>
          </form>
        </DialogSurface>
      </Dialog>

      <ConfirmDialog
        open={initializerToDelete !== null}
        title="Remove custom initializer"
        confirmLabel="Remove"
        onConfirm={() => {
          if (initializerToDelete) {
            void handleDelete(initializerToDelete)
          }
        }}
        onCancel={() => setInitializerToDelete(null)}
      >
        Are you sure you want to remove the <Text weight="semibold">{initializerToDelete?.initializer_name}</Text>{' '}
        custom initializer? Its stored Python source will be permanently deleted.
      </ConfirmDialog>

    </section>
  )
}
