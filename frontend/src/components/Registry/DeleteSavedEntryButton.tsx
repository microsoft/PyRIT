import { useEffect, useState } from 'react'

import {
  Button,
  Dialog,
  DialogActions,
  DialogBody,
  DialogContent,
  DialogSurface,
  DialogTitle,
  MessageBar,
  MessageBarBody,
  Spinner,
  Text,
} from '@fluentui/react-components'
import { DeleteRegular } from '@fluentui/react-icons'

import { savedEntriesApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { RegistryRecipeKind, SavedEntry } from '@/types'

import { useDeleteSavedEntryStyles } from './DeleteSavedEntryButton.styles'

const SINGULAR: Record<RegistryRecipeKind, string> = {
  targets: 'target',
  converters: 'converter',
  scorers: 'scorer',
  techniques: 'technique',
}

interface DeleteSavedEntryDialogProps {
  kind: RegistryRecipeKind
  name: string
  onClose: () => void
  onDeleted: () => void
}

function DeleteSavedEntryDialog({ kind, name, onClose, onDeleted }: DeleteSavedEntryDialogProps) {
  const styles = useDeleteSavedEntryStyles()
  const singular = SINGULAR[kind]
  const [entry, setEntry] = useState<SavedEntry | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  // A file that changed since it was read can be looked at again, which also offers its new version.
  const [changed, setChanged] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [reads, setReads] = useState(0)

  useEffect(() => {
    let cancelled = false
    savedEntriesApi.get(kind, name)
      .then((loaded) => { if (!cancelled) setEntry(loaded) })
      .catch((error: unknown) => { if (!cancelled) setLoadError(toApiError(error).detail) })
    return () => { cancelled = true }
  }, [kind, name, reads])

  const readAgain = () => {
    setEntry(null)
    setLoadError(null)
    setDeleteError(null)
    setChanged(false)
    setReads((count) => count + 1)
  }

  const remove = async () => {
    if (!entry) return
    setDeleting(true)
    setDeleteError(null)
    try {
      await savedEntriesApi.delete(kind, name, entry.version)
      onDeleted()
    } catch (error) {
      const apiError = toApiError(error)
      setDeleteError(apiError.detail)
      setChanged(apiError.status === 409 && apiError.detail.includes('changed since it was read'))
    } finally {
      setDeleting(false)
    }
  }

  const savedBy = entry?.saved_by_me ? 'You saved it.' : entry?.saved_by ? `${entry.saved_by} saved it.` : null
  const permission = entry && !entry.can_change
    ? (entry.saved_by ? `Only ${entry.saved_by} or an administrator can delete it.` : 'Only an administrator can delete it.')
    : null

  return (
    <Dialog open onOpenChange={(_, data) => { if (!data.open && !deleting) onClose() }}>
      <DialogSurface>
        <DialogBody>
          <DialogTitle>{`Delete saved ${singular} '${name}'?`}</DialogTitle>
          <DialogContent className={styles.content}>
            {!entry && !loadError && <Spinner size="tiny" label={`Loading saved ${singular}...`} />}
            {loadError && (
              <div role="alert">
                <MessageBar intent="error"><MessageBarBody>{loadError}</MessageBarBody></MessageBar>
              </div>
            )}
            {entry && (
              <>
                <Text>It&apos;s deleted for everyone and doesn&apos;t come back after the backend restarts.</Text>
                {savedBy && <Text>{savedBy}</Text>}
                {entry.problem && <Text>Its saved file can&apos;t be used, so it&apos;s deleted as it is.</Text>}
                {permission && <Text weight="semibold">{permission}</Text>}
              </>
            )}
            {deleteError && (
              <div role="alert">
                <MessageBar intent="error"><MessageBarBody>{deleteError}</MessageBarBody></MessageBar>
              </div>
            )}
          </DialogContent>
          <DialogActions>
            {changed && <Button disabled={deleting} onClick={readAgain}>Look again</Button>}
            <Button disabled={deleting} onClick={onClose}>Cancel</Button>
            <Button
              appearance="primary"
              disabled={!entry || !entry.can_change || deleting || changed}
              onClick={() => void remove()}
            >
              {deleting ? 'Deleting...' : 'Delete'}
            </Button>
          </DialogActions>
        </DialogBody>
      </DialogSurface>
    </Dialog>
  )
}

interface DeleteSavedEntryButtonProps {
  kind: RegistryRecipeKind
  name: string
  /** Called once the saved file is deleted, so the list can be loaded again. */
  onDeleted: () => void
  /** Show only the icon, for a row's actions column. */
  iconOnly?: boolean
  className?: string
}

/** A button that deletes a saved entry's file, after a dialog that says who saved it and whether you may. */
export default function DeleteSavedEntryButton({
  kind, name, onDeleted, iconOnly = false, className,
}: DeleteSavedEntryButtonProps) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <Button
        className={className}
        appearance="subtle"
        size="small"
        icon={<DeleteRegular />}
        aria-label={`Delete saved ${SINGULAR[kind]} ${name}`}
        onClick={() => setOpen(true)}
      >
        {iconOnly ? undefined : 'Delete'}
      </Button>
      {open && (
        <DeleteSavedEntryDialog
          kind={kind}
          name={name}
          onClose={() => setOpen(false)}
          onDeleted={() => {
            setOpen(false)
            onDeleted()
          }}
        />
      )}
    </>
  )
}
