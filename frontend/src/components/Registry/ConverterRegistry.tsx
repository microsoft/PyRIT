import { useCallback, useEffect, useRef, useState } from 'react'
import type { MouseEvent } from 'react'

import {
  Badge,
  Button,
  Dialog,
  DialogActions,
  DialogBody,
  DialogContent,
  DialogSurface,
  DialogTitle,
  Spinner,
  Table,
  TableBody,
  TableCell,
  TableHeader,
  TableHeaderCell,
  TableRow,
  Text,
  useRestoreFocusTarget,
} from '@fluentui/react-components'
import { AddRegular, ArrowSyncRegular, DeleteRegular } from '@fluentui/react-icons'

import { useRuntime } from '@/hooks/useRuntime'
import { convertersApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { ConverterIdentifier, ConverterInstance } from '@/types'

import CreateConverterDialog from './CreateConverterDialog'
import { useConverterRegistryStyles } from './Registry.styles'

const IDENTIFIER_FIELDS = new Set([
  'class_name',
  'class_module',
  'hash',
  'pyrit_version',
  'eval_hash',
  'children',
  'attributes',
  'supported_input_types',
  'supported_output_types',
])

function formatParameters(identifier: ConverterIdentifier): string {
  const parameters = Object.entries(identifier)
    .filter(([key, value]) => !IDENTIFIER_FIELDS.has(key) && value != null)
    .map(([key, value]) => `${key}: ${typeof value === 'object' ? JSON.stringify(value) : String(value)}`)
  return parameters.join('\n') || '—'
}

// One token per dialog opening. A create response can land after the dialog it
// was submitted from is gone, and by then another dialog may be open, so the
// response is matched by token identity; a single "a dialog is open" boolean
// cannot tell the dialog in front of the user from the one it belongs to.
interface DialogToken {
  dialog: 'create' | 'remove'
}

// A request to hand the keyboard back, honoured after React commits the render
// that asked for it. `trigger` is the control the dialog was opened from. Each
// request is a fresh object so two dismissals in a row are two restores, even
// when they aim at the same control.
interface FocusRestore {
  trigger: HTMLElement | null
}

interface DataTypeBadgesProps {
  dataTypes: string[] | null | undefined
}

function DataTypeBadges({ dataTypes }: DataTypeBadgesProps) {
  const styles = useConverterRegistryStyles()
  if (!dataTypes?.length) return <Text>—</Text>
  return (
    <div className={styles.typeList}>
      {dataTypes.map((dataType) => (
        <Badge key={dataType} appearance="tint">{dataType.replace('_path', '')}</Badge>
      ))}
    </div>
  )
}

export default function ConverterRegistry() {
  const { generation } = useRuntime()
  const styles = useConverterRegistryStyles()
  const [converters, setConverters] = useState<ConverterInstance[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [createToken, setCreateToken] = useState<DialogToken | null>(null)
  const [converterToRemove, setConverterToRemove] = useState<ConverterInstance | null>(null)
  const [removing, setRemoving] = useState(false)
  const [focusRestore, setFocusRestore] = useState<FocusRestore | null>(null)
  // Both dialogs open from state rather than from a DialogTrigger, so mark the
  // opening controls as restore targets the way FeedbackDialog does; that is what
  // hands focus back when a dialog is dismissed. Tabster cannot restore to a
  // control that unmounted along with the dialog, so the paths that remove the
  // trigger restore focus explicitly as well.
  const restoreFocusTarget = useRestoreFocusTarget()
  const newConverterRef = useRef<HTMLButtonElement>(null)
  const createTriggerRef = useRef<HTMLElement | null>(null)
  const removeTriggerRef = useRef<HTMLElement | null>(null)
  // The token of the dialog on screen, or null when none is. Kept in a ref
  // because an in-flight create request outlives the render that started it,
  // and because the restore below has to read it as of the commit it runs in
  // rather than as of the render that requested it.
  const openDialogRef = useRef<DialogToken | null>(null)

  const openDialog = (dialog: DialogToken['dialog']): DialogToken => {
    const token: DialogToken = { dialog }
    openDialogRef.current = token
    return token
  }

  const restoreFocus = (trigger: HTMLElement | null) => {
    setFocusRestore({ trigger })
  }

  // Hand the keyboard back once React has committed the render that closed the
  // dialog, the way a submission failure waits for the render that adds its
  // message bar. A frame callback could not be trusted with this: React
  // schedules its own commit, so the callback could land first, and from there
  // the control that opened the dialog still looked connected even though the
  // same handler had already started the refresh that unmounts it — focus went
  // to a doomed node and ended up on <body>. An effect cannot run before that
  // commit, so the tree it inspects is the one the user is about to see.
  //
  // New Converter is always mounted, so it is the fallback whenever the control
  // that opened the dialog is gone: a removed row, or the empty-state button
  // once the registry holds a converter. Nothing clears `focusRestore` here —
  // a synchronous setState in an effect is what `react-hooks/set-state-in-effect`
  // forbids, and a stale value is harmless because only a new request can
  // re-run this.
  useEffect(() => {
    if (!focusRestore) return
    if (openDialogRef.current) return
    const target = focusRestore.trigger?.isConnected
      ? focusRestore.trigger
      : newConverterRef.current
    target?.focus()
  }, [focusRestore])

  const loadConverters = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const response = await convertersApi.listConverters()
      setConverters(response.items)
    } catch (err) {
      setError(toApiError(err).detail)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    Promise.resolve().then(() => {
      void loadConverters()
    })
  }, [loadConverters, generation])

  const openCreateDialog = (event: MouseEvent<HTMLButtonElement>) => {
    createTriggerRef.current = event.currentTarget
    setCreateToken(openDialog('create'))
  }

  const closeCreateDialog = () => {
    openDialogRef.current = null
    setCreateToken(null)
    restoreFocus(createTriggerRef.current)
  }

  const openRemoveDialog = (event: MouseEvent<HTMLButtonElement>, converter: ConverterInstance) => {
    removeTriggerRef.current = event.currentTarget
    openDialog('remove')
    setConverterToRemove(converter)
  }

  const dismissRemoveDialog = () => {
    openDialogRef.current = null
    setConverterToRemove(null)
    restoreFocus(removeTriggerRef.current)
  }

  const removeConverter = async () => {
    if (!converterToRemove) return
    setRemoving(true)
    setError(null)
    try {
      await convertersApi.deleteConverter(converterToRemove.converter_id)
      // The row that opened the dialog is about to unmount, so this restores to
      // New Converter. Doing it before the refresh keeps focus off <body> while
      // the list reloads. No token check is needed: Cancel is disabled and the
      // Escape handler is ignored while `removing`, so this dialog is still the
      // one on screen.
      dismissRemoveDialog()
      await loadConverters()
    } catch (err) {
      setError(toApiError(err).detail)
    } finally {
      setRemoving(false)
    }
  }

  return (
    <div className={styles.root} data-testid="converter-registry">
      <div className={styles.header}>
        <div className={styles.headerText}>
          <Text as="h1" size={600} weight="semibold">Converter Registry</Text>
          <Text>Manage configured converter instances.</Text>
        </div>
        <div className={styles.actions}>
          <Button
            className={styles.action}
            appearance="subtle"
            icon={<ArrowSyncRegular />}
            disabled={loading}
            onClick={() => void loadConverters()}
          >
            Refresh
          </Button>
          <Button
            {...restoreFocusTarget}
            ref={newConverterRef}
            className={styles.action}
            appearance="primary"
            icon={<AddRegular />}
            onClick={openCreateDialog}
          >
            New Converter
          </Button>
        </div>
      </div>

      {loading && (
        <div className={styles.state}>
          <Spinner label="Loading converters..." />
        </div>
      )}
      {!loading && error && (
        <div className={`${styles.state} ${styles.error}`}>
          <Text>Error: {error}</Text>
        </div>
      )}
      {!loading && !error && converters.length === 0 && (
        <div className={styles.state}>
          <Text size={500} weight="semibold">No Converters Registered</Text>
          <Text>Add a configured converter to the registry.</Text>
          <Button
            {...restoreFocusTarget}
            appearance="primary"
            icon={<AddRegular />}
            onClick={openCreateDialog}
          >
            Create First Converter
          </Button>
        </div>
      )}
      {!loading && !error && converters.length > 0 && (
        <div className={styles.tableContainer}>
          <Table className={styles.table} aria-label="Converter instances">
            <TableHeader>
              <TableRow>
                <TableHeaderCell>Registry Name</TableHeaderCell>
                <TableHeaderCell>Type</TableHeaderCell>
                <TableHeaderCell>Inputs</TableHeaderCell>
                <TableHeaderCell>Outputs</TableHeaderCell>
                <TableHeaderCell>Parameters</TableHeaderCell>
                <TableHeaderCell>Actions</TableHeaderCell>
              </TableRow>
            </TableHeader>
            <TableBody>
              {converters.map((converter) => (
                <TableRow key={converter.converter_id}>
                  <TableCell className={styles.nameCell}>{converter.converter_id}</TableCell>
                  <TableCell>
                    <Text>{converter.identifier.class_name}</Text>
                    {converter.is_llm_based && <Badge appearance="tint">LLM</Badge>}
                  </TableCell>
                  <TableCell>
                    <DataTypeBadges dataTypes={converter.identifier.supported_input_types} />
                  </TableCell>
                  <TableCell>
                    <DataTypeBadges dataTypes={converter.identifier.supported_output_types} />
                  </TableCell>
                  <TableCell className={styles.parameters}>
                    {formatParameters(converter.identifier)}
                  </TableCell>
                  <TableCell>
                    <Button
                      {...restoreFocusTarget}
                      className={styles.deleteButton}
                      appearance="subtle"
                      icon={<DeleteRegular />}
                      aria-label={`Remove ${converter.converter_id}`}
                      onClick={(event) => openRemoveDialog(event, converter)}
                    >
                      Remove
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}

      <CreateConverterDialog
        open={createToken !== null}
        onClose={closeCreateDialog}
        onCreated={() => {
          // This closure holds the token the create request was submitted
          // under. A response that outlived its own dialog still refreshes the
          // list, but only the opening it belongs to may clear the dialog state
          // and move focus, so it cannot pull focus out of a dialog the user
          // opened in the meantime or close one they reopened.
          if (createToken && openDialogRef.current === createToken) closeCreateDialog()
          void loadConverters()
        }}
      />

      <Dialog
        open={converterToRemove !== null}
        onOpenChange={(_, data) => { if (!data.open && !removing) dismissRemoveDialog() }}
      >
        <DialogSurface>
          <DialogBody>
            <DialogTitle>Remove converter?</DialogTitle>
            <DialogContent>
              {converterToRemove
                ? `Remove "${converterToRemove.converter_id}" from the converter registry?`
                : ''}
            </DialogContent>
            <DialogActions>
              <Button disabled={removing} onClick={dismissRemoveDialog}>Cancel</Button>
              <Button
                appearance="primary"
                disabled={removing}
                onClick={() => void removeConverter()}
              >
                {removing ? 'Removing...' : 'Remove'}
              </Button>
            </DialogActions>
          </DialogBody>
        </DialogSurface>
      </Dialog>
    </div>
  )
}
