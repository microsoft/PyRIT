import { useState, useEffect, useCallback, useRef } from 'react'
import {
  tokens,
  Text,
  Button,
  Link,
  Spinner,
  Dialog,
  DialogSurface,
  DialogBody,
  DialogTitle,
  DialogContent,
  DialogActions,
  MessageBar,
  MessageBarBody,
  useRestoreFocusTarget,
} from '@fluentui/react-components'
import { AddRegular, ArrowSyncRegular } from '@fluentui/react-icons'
import { useRuntime } from '@/hooks/useRuntime'
import { targetsApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import { listRegisteredTargets } from '@/services/targetRegistry'
import type { TargetInstance } from '@/types'
import CreateTargetDialog from './CreateTargetDialog'
import TargetTable from './TargetTable'
import { useTargetConfigStyles } from './TargetConfig.styles'

interface TargetConfigProps {
  defaultObjectiveTarget: TargetInstance | null
  defaultAdversarialTarget: TargetInstance | null
  onSetDefaultObjectiveTarget: (target: TargetInstance | null) => void
  onSetDefaultAdversarialTarget: (target: TargetInstance | null) => void
  onTargetsLoaded?: (targets: TargetInstance[]) => void
  registeredTargets?: TargetInstance[]
}

export default function TargetConfig({
  defaultObjectiveTarget,
  defaultAdversarialTarget,
  onSetDefaultObjectiveTarget,
  onSetDefaultAdversarialTarget,
  onTargetsLoaded,
  registeredTargets,
}: TargetConfigProps) {
  const { generation, ready } = useRuntime()
  const styles = useTargetConfigStyles()
  const [loadedTargets, setTargets] = useState<TargetInstance[]>([])
  const targets = registeredTargets ?? loadedTargets
  const newTargetRef = useRef<HTMLButtonElement>(null)
  const restoreFocusTarget = useRestoreFocusTarget()
  const deleteTriggerRef = useRef<HTMLButtonElement | null>(null)
  const [focusRestore, setFocusRestore] = useState<{ trigger: HTMLButtonElement | null } | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState<TargetInstance | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  // Counter used to re-trigger the fetch effect from event handlers (Refresh,
  // dialog close) without invoking setState synchronously in the effect body.
  const [refetchCount, setRefetchCount] = useState(0)

  useEffect(() => {
    if (!focusRestore || deleteTarget) return
    const trigger = focusRestore.trigger
    const target = trigger?.isConnected ? trigger : newTargetRef.current
    target?.focus()
  }, [focusRestore, deleteTarget])

  // Retry fetching targets a few times with backoff. The Vite dev proxy
  // returns 502 while the backend is still starting, so a single failed
  // request on initial page load would show a confusing error to the user.
  useEffect(() => {
    if (!ready) return
    const maxRetries = 3
    let cancelled = false

    const attempt = async (n: number): Promise<void> => {
      try {
        const items = await listRegisteredTargets()
        if (cancelled) return
        setTargets(items)
        setError(null)
        setLoading(false)
        onTargetsLoaded?.(items)
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

  const fetchTargets = useCallback(() => {
    setLoading(true)
    setError(null)
    setRefetchCount(c => c + 1)
  }, [])

  const handleTargetCreated = useCallback(() => {
    setDialogOpen(false)
    fetchTargets()
  }, [fetchTargets])

  const closeDeleteDialog = (): void => {
    setDeleteTarget(null)
    setFocusRestore({ trigger: deleteTriggerRef.current })
  }

  const handleDeleteTarget = async (): Promise<void> => {
    if (!deleteTarget || deleting || !ready) return
    setDeleting(true)
    setDeleteError(null)
    try {
      try {
        await targetsApi.deleteTarget(deleteTarget.target_registry_name)
      } catch (cause: unknown) {
        if (toApiError(cause).status !== 404) throw cause
        // Another client already removed it; reconcile with the shared registry.
      }
      const remaining = targets.filter((target: TargetInstance) => (
        target.target_registry_name !== deleteTarget.target_registry_name
      ))
      setTargets(remaining)
      onTargetsLoaded?.(remaining)
      setDeleteTarget(null)
      setFocusRestore({ trigger: null })
      fetchTargets()
    } catch (cause: unknown) {
      setDeleteError(toApiError(cause).detail)
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
            {...restoreFocusTarget}
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
          onDeleteTarget={(target: TargetInstance, trigger: HTMLButtonElement | null) => {
            deleteTriggerRef.current = trigger
            setDeleteError(null)
            setDeleteTarget(target)
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
        open={deleteTarget !== null}
        onOpenChange={(_, data) => {
          if (!data.open && !deleting) closeDeleteDialog()
        }}
      >
        <DialogSurface>
          <DialogBody>
            <DialogTitle>Delete target?</DialogTitle>
            <DialogContent>
              <Text>
                Delete "{deleteTarget?.target_registry_name}"?
                This immediately removes the target from the shared registry for all users and cannot be undone.
                Saved conversations and results are kept,
                but using this target again requires adding it again.
                Targets in use cannot be deleted.
              </Text>
              {deleteError && (
                <MessageBar intent="error">
                  <MessageBarBody>{deleteError}</MessageBarBody>
                </MessageBar>
              )}
            </DialogContent>
            <DialogActions>
              <Button className={styles.touchTarget} disabled={deleting} onClick={closeDeleteDialog}>
                Cancel
              </Button>
              <Button
                className={styles.touchTarget}
                appearance="primary"
                disabled={deleting || !ready}
                onClick={() => { void handleDeleteTarget() }}
              >
                {deleting ? 'Deleting...' : 'Delete target'}
              </Button>
            </DialogActions>
          </DialogBody>
        </DialogSurface>
      </Dialog>
    </div>
  )
}
