import { useState, useEffect, useCallback, useRef } from 'react'
import {
  tokens,
  Text,
  Button,
  Dialog,
  DialogActions,
  DialogBody,
  DialogContent,
  DialogSurface,
  DialogTitle,
  Link,
  MessageBar,
  MessageBarBody,
  Spinner,
} from '@fluentui/react-components'
import { AddRegular, ArrowSyncRegular } from '@fluentui/react-icons'
import UnrestorableInstances from '@/components/Registry/UnrestorableInstances'
import { useRuntime } from '@/hooks/useRuntime'
import { targetsApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import { listTargetRegistry } from '@/services/targetRegistry'
import type { TargetInstance, UnrestorableInstance } from '@/types'
import CreateTargetDialog from './CreateTargetDialog'
import TargetTable from './TargetTable'
import { useTargetConfigStyles } from './TargetConfig.styles'

interface TargetConfigProps {
  defaultObjectiveTarget: TargetInstance | null
  defaultAdversarialTarget: TargetInstance | null
  onSetDefaultObjectiveTarget: (target: TargetInstance | null) => void
  onSetDefaultAdversarialTarget: (target: TargetInstance | null) => void
  onTargetsLoaded?: (targets: TargetInstance[]) => void
}

/** A saved target the user asked to delete, with the version it was read at. */
interface SavedTargetDeletion {
  name: string
  version: string
}

export default function TargetConfig({
  defaultObjectiveTarget,
  defaultAdversarialTarget,
  onSetDefaultObjectiveTarget,
  onSetDefaultAdversarialTarget,
  onTargetsLoaded,
}: TargetConfigProps) {
  const { generation, ready } = useRuntime()
  const styles = useTargetConfigStyles()
  const [targets, setTargets] = useState<TargetInstance[]>([])
  const [unrestorable, setUnrestorable] = useState<UnrestorableInstance[]>([])
  const [restoreError, setRestoreError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [targetToDelete, setTargetToDelete] = useState<SavedTargetDeletion | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  // Deleting a target unmounts the control that opened the confirmation, so
  // focus moves to New Target once the render that closes the dialog commits.
  const [focusNewTargetRequest, setFocusNewTargetRequest] = useState(0)
  const newTargetRef = useRef<HTMLButtonElement>(null)
  // Counter used to re-trigger the fetch effect from event handlers (Refresh,
  // dialog close) without invoking setState synchronously in the effect body.
  const [refetchCount, setRefetchCount] = useState(0)

  // Retry fetching targets a few times with backoff. The Vite dev proxy
  // returns 502 while the backend is still starting, so a single failed
  // request on initial page load would show a confusing error to the user.
  useEffect(() => {
    if (!ready) return
    const maxRetries = 3
    let cancelled = false

    const attempt = async (n: number): Promise<void> => {
      try {
        const snapshot = await listTargetRegistry()
        if (cancelled) return
        setTargets(snapshot.targets)
        setUnrestorable(snapshot.unrestorable)
        setRestoreError(snapshot.restoreError)
        setError(null)
        setLoading(false)
        onTargetsLoaded?.(snapshot.targets)
      } catch (err) {
        if (cancelled) return
        if (n < maxRetries) {
          await new Promise(r => setTimeout(r, (n + 1) * 1000))
          if (cancelled) return
          return attempt(n + 1)
        }
        setError(toApiError(err).detail)
        setLoading(false)
      }
    }

    attempt(0)
    return () => {
      cancelled = true
    }
  }, [refetchCount, generation, ready, onTargetsLoaded])

  useEffect(() => {
    if (focusNewTargetRequest > 0) newTargetRef.current?.focus()
  }, [focusNewTargetRequest])

  const fetchTargets = useCallback(() => {
    setLoading(true)
    setError(null)
    setRefetchCount(c => c + 1)
  }, [])

  const handleTargetCreated = useCallback(() => {
    setDialogOpen(false)
    fetchTargets()
  }, [fetchTargets])

  const requestDeletion = (name: string, version: string) => {
    setDeleteError(null)
    setTargetToDelete({ name, version })
  }

  const deleteTarget = async () => {
    if (!targetToDelete) return
    setDeleting(true)
    setDeleteError(null)
    try {
      await targetsApi.deleteTarget(targetToDelete.name, targetToDelete.version)
      setTargetToDelete(null)
      setFocusNewTargetRequest((count) => count + 1)
      fetchTargets()
    } catch (err) {
      setDeleteError(toApiError(err).detail)
    } finally {
      setDeleting(false)
    }
  }

  return (
    <div className={styles.root} data-testid="target-config">
      <div className={styles.header}>
        <div className={styles.headerLeft}>
          <Text as="h1" size={600} weight="semibold">Target Registry</Text>
          <Text size={300} style={{ color: tokens.colorNeutralForeground3 }}>
            Manage targets and choose defaults for new chats and scanner runs. Existing chats and runs are unchanged.
          </Text>
        </div>
        <div className={styles.headerActions}>
          <Button
            className={styles.headerAction}
            appearance="subtle"
            icon={<ArrowSyncRegular />}
            onClick={fetchTargets}
            disabled={loading}
          >
            Refresh
          </Button>
          <Button
            ref={newTargetRef}
            className={styles.headerAction}
            appearance="primary"
            icon={<AddRegular />}
            onClick={() => setDialogOpen(true)}
          >
            New Target
          </Button>
        </div>
      </div>

      {loading && (
        <div className={styles.loadingState}>
          <Spinner label="Loading targets..." />
        </div>
      )}

      {error && (
        <div className={styles.errorState}>
          <Text>Error: {error}</Text>
        </div>
      )}

      {!loading && !error && (
        <UnrestorableInstances
          noun="target"
          instances={unrestorable}
          restoreError={restoreError}
          onDelete={(_event, instance) => {
            if (instance.version) requestDeletion(instance.name, instance.version)
          }}
        />
      )}

      {!loading && !error && targets.length === 0 && (
        <div className={styles.emptyState}>
          <Text size={500} weight="semibold">No Targets Configured</Text>
          <Text size={300} style={{ color: tokens.colorNeutralForeground3 }}>
            Add a target manually, or configure an initializer in your <code>~/.pyrit/.pyrit_conf</code> file
            to auto-populate targets from your <code>.env</code> and <code>.env.local</code> files.
            For example, add <code>target</code> to the <code>initializers</code> list to register
            available prompt targets automatically. See the{' '}
            <Link
              href="https://github.com/microsoft/PyRIT/blob/main/.pyrit_conf_example"
              target="_blank"
              rel="noopener noreferrer"
              inline
            >
              .pyrit_conf_example
            </Link>{' '}
            for details.
          </Text>
          <Button
            className={styles.touchTarget}
            appearance="primary"
            icon={<AddRegular />}
            onClick={() => setDialogOpen(true)}
          >
            Create First Target
          </Button>
        </div>
      )}

      {!loading && !error && targets.length > 0 && (
        <TargetTable
          targets={targets}
          defaultObjectiveTarget={defaultObjectiveTarget}
          defaultAdversarialTarget={defaultAdversarialTarget}
          onSetDefaultObjectiveTarget={onSetDefaultObjectiveTarget}
          onSetDefaultAdversarialTarget={onSetDefaultAdversarialTarget}
          onDeleteTarget={(target) => {
            if (target.version) requestDeletion(target.target_registry_name, target.version)
          }}
        />
      )}

      <CreateTargetDialog
        open={dialogOpen}
        onClose={() => setDialogOpen(false)}
        onCreated={handleTargetCreated}
        existingTargets={targets}
      />

      <Dialog
        open={targetToDelete !== null}
        onOpenChange={(_, data) => { if (!data.open && !deleting) setTargetToDelete(null) }}
      >
        <DialogSurface>
          <DialogBody>
            <DialogTitle>Delete saved target?</DialogTitle>
            <DialogContent className={styles.deleteDialogContent}>
              {deleteError && (
                <MessageBar intent="error">
                  <MessageBarBody>{deleteError}</MessageBarBody>
                </MessageBar>
              )}
              <Text>
                {targetToDelete
                  ? `Delete "${targetToDelete.name}"? It is removed now and is not restored when PyRIT restarts.`
                  : ''}
              </Text>
            </DialogContent>
            <DialogActions>
              <Button disabled={deleting} onClick={() => setTargetToDelete(null)}>Cancel</Button>
              <Button appearance="primary" disabled={deleting} onClick={() => void deleteTarget()}>
                {deleting ? 'Deleting...' : 'Delete'}
              </Button>
            </DialogActions>
          </DialogBody>
        </DialogSurface>
      </Dialog>
    </div>
  )
}
