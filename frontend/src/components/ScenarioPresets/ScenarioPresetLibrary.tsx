import { useCallback, useEffect, useState } from 'react'

import {
  Badge,
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
  Switch,
  Table,
  TableBody,
  TableCell,
  TableHeader,
  TableHeaderCell,
  TableRow,
  Text,
} from '@fluentui/react-components'
import {
  AddRegular,
  ArrowSyncRegular,
  DeleteRegular,
  EditRegular,
} from '@fluentui/react-icons'
import { Link, useNavigate } from 'react-router'

import { ScenarioRunEstimateSummary } from '@/components/Scenarios/ScenarioRunEstimate'
import { mapScenarioRunEstimate } from '@/components/Scenarios/scenarioRunEstimateAdapter'
import { scenarioPresetsApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { ScenarioPresetResponse } from '@/types'

import ScannerVisibilityLabel from './ScannerVisibilityLabel'
import { useScenarioPresetLibraryStyles } from './ScenarioPresetLibrary.styles'
import { NEW_PRESET_ROUTE, presetEditorRoutePath } from './presetRoutes'
import { estimatePresetRunSizes } from './presetRunSizes'

const DEFINED_IN_CODE_HINT = 'Defined in code, so it cannot be edited or deleted here.'

interface ScenarioPresetLibraryProps {
  /** Lets sections that project preset state, such as suite entry statuses, reload. */
  onPresetDeleted: () => void
}

function techniqueSummary(item: ScenarioPresetResponse): string {
  const { techniques } = item.preset
  if (!techniques || techniques.length === 0) {
    return 'Scenario default techniques'
  }
  return `${techniques.length} technique${techniques.length === 1 ? '' : 's'}`
}

/**
 * Lists the saved scenario presets. Presets that reference something this
 * deployment does not have are still listed — the server reports the problems
 * as advisory issues. Presets launch from the Scanner, not from here.
 */
export default function ScenarioPresetLibrary({ onPresetDeleted }: ScenarioPresetLibraryProps) {
  const styles = useScenarioPresetLibraryStyles()
  const navigate = useNavigate()
  const [items, setItems] = useState<ScenarioPresetResponse[]>([])
  const [source, setSource] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [estimatesLoading, setEstimatesLoading] = useState(true)
  const [estimateError, setEstimateError] = useState<string | null>(null)
  const [pendingDelete, setPendingDelete] = useState<ScenarioPresetResponse | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [togglingVisibility, setTogglingVisibility] = useState(false)
  const [generation, setGeneration] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    let cancelled = false

    // Estimate the displayed definitions, without re-reading storage or changing
    // the versions that row actions send as their expected version.
    const loadPresets = async (): Promise<void> => {
      try {
        const response = await scenarioPresetsApi.list(controller.signal, false)
        if (cancelled) return
        setItems(response.items)
        setSource(response.source)
        setError(null)
        setLoading(false)

        if (response.items.length === 0) {
          setEstimatesLoading(false)
          return
        }

        const { runSizes, failures } = await estimatePresetRunSizes(response.items, controller.signal)
        if (cancelled) return
        // Merge by name so a row whose visibility changed while estimating keeps its new version.
        setItems((current) => current.map((item) => ({
          ...item,
          run_size: runSizes.get(item.preset.name) ?? null,
        })))
        setEstimateError(failures.length > 0 ? failures.join('; ') : null)
        setEstimatesLoading(false)
      } catch (err: unknown) {
        if (cancelled || controller.signal.aborted) return
        setError(toApiError(err).detail)
        setLoading(false)
        setEstimatesLoading(false)
      }
    }

    void loadPresets()

    return () => {
      cancelled = true
      controller.abort()
    }
  }, [generation])

  const refresh = useCallback(() => {
    setLoading(true)
    setEstimatesLoading(true)
    setError(null)
    setEstimateError(null)
    setGeneration((current) => current + 1)
  }, [])

  const handleDelete = async (): Promise<void> => {
    if (!pendingDelete) {
      return
    }
    setDeleting(true)
    setActionError(null)
    try {
      await scenarioPresetsApi.remove(pendingDelete.preset.name)
      setPendingDelete(null)
      refresh()
      onPresetDeleted()
    } catch (err) {
      setActionError(toApiError(err).detail)
    } finally {
      setDeleting(false)
    }
  }

  const handleScannerVisibilityChange = async (
    item: ScenarioPresetResponse,
    showInScanner: boolean,
  ): Promise<void> => {
    if (item.version === null) {
      return
    }
    setTogglingVisibility(true)
    setActionError(null)
    try {
      const updated = await scenarioPresetsApi.update(
        item.preset.name,
        { ...item.preset, show_in_scanner: showInScanner },
        item.version,
      )
      // Visibility does not change what the preset runs, so its run size still applies.
      setItems((current) => current.map((candidate) => (
        candidate.preset.name === updated.preset.name
          ? { ...updated, run_size: candidate.run_size }
          : candidate
      )))
    } catch (err: unknown) {
      const apiError = toApiError(err)
      if (apiError.status === 409) {
        refresh()
        setActionError(
          `Preset '${item.preset.name}' changed since it was loaded. Review it and try again.`,
        )
        return
      }
      setActionError(apiError.detail)
    } finally {
      setTogglingVisibility(false)
    }
  }

  return (
    <section
      className={styles.root}
      data-testid="scenario-preset-library"
      aria-labelledby="scenario-preset-library-title"
    >
      <div className={styles.header}>
        <div className={styles.headerText}>
          <Text id="scenario-preset-library-title" as="h2" size={500} weight="semibold">
            Individual presets
          </Text>
          <Text size={300} className={styles.subtitle}>
            Reusable scenario configurations. A preset pins what to test; you choose the target at
            launch.
          </Text>
        </div>
        <div className={styles.headerActions}>
          <Button
            className={styles.touchTarget}
            appearance="primary"
            icon={<AddRegular />}
            onClick={() => navigate(NEW_PRESET_ROUTE)}
            data-testid="new-preset-btn"
          >
            New preset
          </Button>
          <Button
            className={styles.touchTarget}
            appearance="subtle"
            icon={<ArrowSyncRegular />}
            onClick={refresh}
            disabled={loading}
            aria-label="Refresh presets"
          >
            Refresh
          </Button>
        </div>
      </div>

      {source !== '' && (
        <Text size={200} className={styles.sourceLine}>
          Stored in {source}
        </Text>
      )}

      {actionError && (
        <MessageBar intent="error">
          <MessageBarBody>{actionError}</MessageBarBody>
        </MessageBar>
      )}

      {estimateError && (
        <MessageBar intent="warning">
          <MessageBarBody>Run sizes could not be calculated: {estimateError}</MessageBarBody>
        </MessageBar>
      )}

      {loading ? (
        <div className={styles.centeredState}>
          <Spinner label="Loading presets..." />
        </div>
      ) : error ? (
        <div className={styles.centeredState} data-testid="error-state">
          <MessageBar intent="error">
            <MessageBarBody>{error}</MessageBarBody>
          </MessageBar>
          <Button
            className={styles.touchTarget}
            appearance="primary"
            icon={<ArrowSyncRegular />}
            onClick={refresh}
            data-testid="retry-btn"
          >
            Retry
          </Button>
        </div>
      ) : items.length === 0 ? (
        <div className={styles.centeredState} data-testid="empty-state">
          <Text size={400}>No presets saved yet</Text>
          <Text size={200}>
            Create one to reuse a scenario configuration across targets and runs.
          </Text>
        </div>
      ) : (
        <div className={styles.tableContainer}>
          <Table className={styles.table} aria-label="Saved scenario presets">
            <TableHeader>
              <TableRow>
                <TableHeaderCell>Name</TableHeaderCell>
                <TableHeaderCell>Scenario</TableHeaderCell>
                <TableHeaderCell>Run size</TableHeaderCell>
                <TableHeaderCell>Author</TableHeaderCell>
                <TableHeaderCell><ScannerVisibilityLabel /></TableHeaderCell>
                <TableHeaderCell>Actions</TableHeaderCell>
              </TableRow>
            </TableHeader>
            <TableBody>
              {items.map((item) => {
                const runnable = item.issues.length === 0
                const editable = item.origin === 'stored'
                const estimateState = item.run_size
                  ? mapScenarioRunEstimate(item.run_size, 'request')
                  : null
                return (
                  <TableRow
                    key={item.preset.name}
                    data-testid={`preset-row-${item.preset.name}`}
                  >
                    <TableCell className={styles.nameCell}>
                      <div className={styles.cardTitleRow}>
                        <Link to={presetEditorRoutePath(item.preset.name)}>
                          <Text weight="semibold">{item.preset.name}</Text>
                        </Link>
                        {!runnable && (
                          <Badge appearance="tint" color="warning">
                            Not runnable here
                          </Badge>
                        )}
                      </div>
                      {item.preset.description && (
                        <Text size={200} className={styles.cardMeta}>
                          {item.preset.description}
                        </Text>
                      )}
                      {!runnable && (
                        <ul
                          className={styles.issueList}
                          data-testid={`preset-issues-${item.preset.name}`}
                        >
                          {item.issues.map((issue) => (
                            <li key={`${issue.field}:${issue.message}`}>
                              <Text size={200}>{issue.message}</Text>
                            </li>
                          ))}
                        </ul>
                      )}
                    </TableCell>
                    <TableCell>
                      <Text>{item.preset.scenario_name}</Text>
                      <Text size={200} className={styles.cardMeta}>
                        {techniqueSummary(item)}
                      </Text>
                    </TableCell>
                    <TableCell data-testid={`preset-run-size-${item.preset.name}`}>
                      {estimatesLoading ? (
                        <Spinner size="tiny" label="Calculating..." labelPosition="after" />
                      ) : estimateState ? (
                        <ScenarioRunEstimateSummary state={estimateState} compact />
                      ) : (
                        <Text className={styles.cardMeta}>—</Text>
                      )}
                    </TableCell>
                    <TableCell data-testid={`preset-author-${item.preset.name}`}>
                      {item.preset.author
                        ? <Text>{item.preset.author}</Text>
                        : <Text className={styles.cardMeta}>—</Text>}
                    </TableCell>
                    <TableCell>
                      <Switch
                        className={styles.touchTarget}
                        checked={item.preset.show_in_scanner !== false}
                        disabled={!editable || item.version === null || togglingVisibility}
                        title={editable ? undefined : DEFINED_IN_CODE_HINT}
                        aria-label={`Add ${item.preset.name} to scanner`}
                        onChange={(_, data) => {
                          void handleScannerVisibilityChange(item, data.checked)
                        }}
                        data-testid={`preset-show-in-scanner-${item.preset.name}`}
                      />
                    </TableCell>
                    <TableCell>
                      <div className={styles.cardActions}>
                        <Button
                          className={styles.touchTarget}
                          appearance="subtle"
                          icon={<EditRegular />}
                          disabled={!editable}
                          title={editable ? undefined : DEFINED_IN_CODE_HINT}
                          onClick={() => navigate(presetEditorRoutePath(item.preset.name))}
                          data-testid={`edit-preset-${item.preset.name}`}
                        >
                          Edit
                        </Button>
                        <Button
                          className={styles.touchTarget}
                          appearance="subtle"
                          icon={<DeleteRegular />}
                          disabled={!editable}
                          title={editable ? undefined : DEFINED_IN_CODE_HINT}
                          onClick={() => {
                            setActionError(null)
                            setPendingDelete(item)
                          }}
                          data-testid={`delete-preset-${item.preset.name}`}
                        >
                          Delete
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        </div>
      )}

      {pendingDelete && (
        <Dialog
          open
          onOpenChange={(_, data) => { if (!data.open) setPendingDelete(null) }}
        >
          <DialogSurface>
            <DialogBody>
              <DialogTitle>Delete {pendingDelete.preset.name}?</DialogTitle>
              <DialogContent className={styles.dialogBody}>
                <Text>
                  This permanently removes the preset. Runs already started from it are unaffected.
                </Text>
              </DialogContent>
              <DialogActions>
                <Button
                  appearance="secondary"
                  onClick={() => setPendingDelete(null)}
                  disabled={deleting}
                >
                  Cancel
                </Button>
                <Button
                  appearance="primary"
                  onClick={handleDelete}
                  disabled={deleting}
                  data-testid="confirm-delete-preset"
                >
                  {deleting ? 'Deleting...' : 'Delete'}
                </Button>
              </DialogActions>
            </DialogBody>
          </DialogSurface>
        </Dialog>
      )}
    </section>
  )
}
