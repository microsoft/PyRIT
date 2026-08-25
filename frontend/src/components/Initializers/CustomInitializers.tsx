import { useState } from 'react'

import {
  Button,
  Dialog,
  DialogActions,
  DialogBody,
  DialogContent,
  DialogSurface,
  DialogTitle,
  DialogTrigger,
  Field,
  Input,
  Table,
  TableBody,
  TableCell,
  TableHeader,
  TableHeaderCell,
  TableRow,
  Text,
} from '@fluentui/react-components'
import { AddRegular, DeleteRegular, EyeRegular } from '@fluentui/react-icons'

import type { CustomInitializer } from '@/types'

import { useCustomInitializersStyles } from './CustomInitializers.styles'
import { PythonCodeBlock, PythonCodeEditor } from './PythonCode'

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
  const [name, setName] = useState('')
  const [scriptContent, setScriptContent] = useState('')
  const [viewingInitializer, setViewingInitializer] = useState<CustomInitializer | null>(null)

  const handleSubmit = async (event: React.FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault()
    if (await onRegister(name.trim(), scriptContent)) {
      setDialogOpen(false)
      setName('')
      setScriptContent('')
    }
  }

  return (
    <section className={styles.root} aria-labelledby="custom-initializers-heading">
      <div className={styles.header}>
        <div>
          <Text id="custom-initializers-heading" as="h2" size={500} weight="semibold">
            Custom initializers
          </Text>
          <Text block>Persisted Python definitions available to startup initializer configuration.</Text>
        </div>
        <Dialog open={dialogOpen} onOpenChange={(_, data) => setDialogOpen(data.open)}>
          <DialogTrigger disableButtonEnhancement>
            <Button appearance="primary" icon={<AddRegular />}>Register initializer</Button>
          </DialogTrigger>
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
                  <DialogTrigger disableButtonEnhancement>
                    <Button appearance="secondary" disabled={registering}>Cancel</Button>
                  </DialogTrigger>
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
      </div>

      {items.length === 0 ? (
        <Text className={styles.emptyState}>No custom initializers registered.</Text>
      ) : (
        <div className={styles.tableWrap}>
          <Table aria-label="Custom initializers">
            <TableHeader>
              <TableRow>
                <TableHeaderCell className={styles.nameCell}>Name</TableHeaderCell>
                <TableHeaderCell className={styles.actionCell}>Actions</TableHeaderCell>
              </TableRow>
            </TableHeader>
            <TableBody>
              {items.map((item) => (
                <TableRow
                  className={styles.clickableRow}
                  key={item.initializer_name}
                  data-testid={`custom-initializer-${item.initializer_name}`}
                  onClick={() => setViewingInitializer(item)}
                >
                  <TableCell className={styles.nameCell}>
                    <Button
                      appearance="transparent"
                      icon={<EyeRegular />}
                      onClick={() => setViewingInitializer(item)}
                    >
                      {item.initializer_name}
                    </Button>
                  </TableCell>
                  <TableCell className={styles.actionCell}>
                    <Button
                      appearance="subtle"
                      icon={<DeleteRegular />}
                      disabled={deletingName !== null}
                      onClick={(event) => {
                        event.stopPropagation()
                        void onDelete(item.initializer_name)
                      }}
                    >
                      {deletingName === item.initializer_name ? 'Removing...' : 'Remove'}
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}

      <Dialog
        open={viewingInitializer !== null}
        onOpenChange={(_, data) => {
          if (!data.open) {
            setViewingInitializer(null)
          }
        }}
      >
        <DialogSurface className={styles.sourceDialog}>
          <DialogBody>
            <DialogTitle>{viewingInitializer?.initializer_name}</DialogTitle>
            <DialogContent>
              <PythonCodeBlock source={viewingInitializer?.script_content ?? ''} ariaLabel="Python source" />
            </DialogContent>
            <DialogActions>
              <Button appearance="primary" onClick={() => setViewingInitializer(null)}>Close</Button>
            </DialogActions>
          </DialogBody>
        </DialogSurface>
      </Dialog>
    </section>
  )
}
