import { useState } from 'react'

import {
  Button,
  Field,
  Text,
} from '@fluentui/react-components'
import { AddRegular, DeleteRegular } from '@fluentui/react-icons'

import ConfirmDialog from '@/components/ConfirmDialog'
import EditorWorkspace from '@/components/EditorWorkspace'
import { PythonCodeEditor } from '@/components/Configuration/PythonCode'
import type { CustomInitializer } from '@/types'

import AddCustomInitializerDialog from './AddCustomInitializerDialog'
import { useCustomInitializersStyles } from './CustomInitializers.styles'

interface CustomInitializersProps {
  items: CustomInitializer[]
  registering: boolean
  deletingName: string | null
  onRegister: (name: string, scriptContent: string) => Promise<boolean>
  onDelete: (name: string) => Promise<void>
}

export default function CustomInitializers({
  items,
  registering,
  deletingName,
  onRegister,
  onDelete,
}: CustomInitializersProps) {
  const styles = useCustomInitializersStyles()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [selectedName, setSelectedName] = useState<string | null>(items[0]?.initializer_name ?? null)
  const selectedInitializer = items.find((item) => item.initializer_name === selectedName) ?? items[0] ?? null
  const [initializerToDelete, setInitializerToDelete] = useState<CustomInitializer | null>(null)

  const selectInitializer = (initializer: CustomInitializer): void => {
    setSelectedName(initializer.initializer_name)
  }

  const handleDelete = async (initializer: CustomInitializer): Promise<void> => {
    const nextInitializer = items.find((item) => item.initializer_name !== initializer.initializer_name) ?? null
    setInitializerToDelete(null)
    await onDelete(initializer.initializer_name)
    setSelectedName(nextInitializer?.initializer_name ?? null)
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
        emptyMessage="No custom initializer scripts stored."
        description="Python initializers loaded when PyRIT starts."
        actions={(
          <div className={styles.editorActions}>
            <Button appearance="subtle" icon={<AddRegular />} onClick={() => setDialogOpen(true)}>
              Add initializer
            </Button>
            <Button
              appearance="subtle"
              icon={<DeleteRegular />}
              disabled={!selectedInitializer || deletingName !== null}
              onClick={() => selectedInitializer && setInitializerToDelete(selectedInitializer)}
            >
              {deletingName === selectedInitializer?.initializer_name ? 'Removing...' : 'Remove'}
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
              label={selectedInitializer.source}
            >
              <PythonCodeEditor
                source={selectedInitializer.script_content}
                disabled
                onChange={() => undefined}
              />
            </Field>
          </>
        )}
      </EditorWorkspace>

      <AddCustomInitializerDialog
        open={dialogOpen}
        registering={registering}
        onOpenChange={setDialogOpen}
        onRegister={onRegister}
      />

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
