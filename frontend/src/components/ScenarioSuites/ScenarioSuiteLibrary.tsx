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

import { scenarioSuitesApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { ScenarioSuiteResponse } from '@/types'

import { useScenarioSuiteLibraryStyles } from './ScenarioSuiteLibrary.styles'
import { NEW_SUITE_ROUTE, suiteEditorRoutePath } from './suiteRoutes'

const DEFINED_IN_CODE_HINT = 'Defined in code, so it cannot be edited or deleted here.'

interface ScenarioSuiteLibraryProps {
  /** Changes when presets change elsewhere on the page, so entry statuses are re-read. */
  presetsRevision: number
}

function issueCount(item: ScenarioSuiteResponse): number {
  return item.entry_statuses.reduce(
    (count, status) => count + status.issues.length,
    0,
  )
}

/** Lists the saved scenario suites. Suites launch from the Scanner, not from here. */
export default function ScenarioSuiteLibrary({ presetsRevision }: ScenarioSuiteLibraryProps) {
  const styles = useScenarioSuiteLibraryStyles()
  const navigate = useNavigate()
  const [items, setItems] = useState<ScenarioSuiteResponse[]>([])
  const [source, setSource] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [pendingDelete, setPendingDelete] = useState<ScenarioSuiteResponse | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [generation, setGeneration] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    let cancelled = false

    const loadSuites = async (): Promise<void> => {
      try {
        const response = await scenarioSuitesApi.list(controller.signal)
        if (cancelled) return
        setItems(response.items)
        setSource(response.source)
        setError(null)
      } catch (err: unknown) {
        if (cancelled || controller.signal.aborted) return
        setError(toApiError(err).detail)
      } finally {
        if (!cancelled) {
          setLoading(false)
        }
      }
    }

    void loadSuites()
    return () => {
      cancelled = true
      controller.abort()
    }
  }, [generation, presetsRevision])

  const refresh = useCallback((): void => {
    setLoading(true)
    setError(null)
    setGeneration((current) => current + 1)
  }, [])

  const handleDelete = async (): Promise<void> => {
    if (!pendingDelete) {
      return
    }
    setDeleting(true)
    setActionError(null)
    try {
      await scenarioSuitesApi.remove(pendingDelete.suite.name)
      setPendingDelete(null)
      refresh()
    } catch (err: unknown) {
      setActionError(toApiError(err).detail)
    } finally {
      setDeleting(false)
    }
  }

  return (
    <section
      className={styles.root}
      data-testid="scenario-suite-library"
      aria-labelledby="scenario-suite-library-title"
    >
      <div className={styles.header}>
        <div className={styles.headerText}>
          <Text id="scenario-suite-library-title" as="h2" size={500} weight="semibold">
            Scenario suites
          </Text>
          <Text size={300} className={styles.subtitle}>
            Ordered collections of reusable scenario preset definitions.
          </Text>
        </div>
        <div className={styles.headerActions}>
          <Button
            className={styles.touchTarget}
            appearance="primary"
            icon={<AddRegular />}
            onClick={() => navigate(NEW_SUITE_ROUTE)}
            data-testid="new-suite-btn"
          >
            New suite
          </Button>
          <Button
            className={styles.touchTarget}
            appearance="subtle"
            icon={<ArrowSyncRegular />}
            onClick={refresh}
            disabled={loading}
            aria-label="Refresh suites"
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

      {loading ? (
        <div className={styles.centeredState}>
          <Spinner label="Loading suites..." />
        </div>
      ) : error ? (
        <div className={styles.centeredState} data-testid="suite-error-state">
          <MessageBar intent="error">
            <MessageBarBody>{error}</MessageBarBody>
          </MessageBar>
          <Button
            className={styles.touchTarget}
            appearance="primary"
            icon={<ArrowSyncRegular />}
            onClick={refresh}
            data-testid="retry-suites-btn"
          >
            Retry
          </Button>
        </div>
      ) : items.length === 0 ? (
        <div className={styles.centeredState} data-testid="suite-empty-state">
          <Text size={400}>No suites saved yet</Text>
          <Text size={200}>Create one to keep an ordered set of scenario presets together.</Text>
        </div>
      ) : (
        <div className={styles.tableContainer}>
          <Table className={styles.table} aria-label="Saved suites">
            <TableHeader>
              <TableRow>
                <TableHeaderCell>Name</TableHeaderCell>
                <TableHeaderCell>Entries</TableHeaderCell>
                <TableHeaderCell>Status</TableHeaderCell>
                <TableHeaderCell>Author</TableHeaderCell>
                <TableHeaderCell>Actions</TableHeaderCell>
              </TableRow>
            </TableHeader>
            <TableBody>
              {items.map((item) => {
                const issues = issueCount(item)
                const editable = item.origin === 'stored'
                return (
                  <TableRow key={item.suite.name} data-testid={`suite-row-${item.suite.name}`}>
                    <TableCell className={styles.nameCell}>
                      <div className={styles.nameContent}>
                        <div className={styles.titleRow}>
                          <Link to={suiteEditorRoutePath(item.suite.name)}>
                            <Text weight="semibold">{item.suite.name}</Text>
                          </Link>
                          {issues > 0 && (
                            <Badge appearance="tint" color="warning">
                              Needs attention
                            </Badge>
                          )}
                          {!editable && (
                            <Badge appearance="tint" color="informative">
                              Defined in code
                            </Badge>
                          )}
                        </div>
                        {item.suite.description && (
                          <Text size={200} className={styles.meta}>
                            {item.suite.description}
                          </Text>
                        )}
                        {issues > 0 && (
                          <ul
                            className={styles.issueList}
                            data-testid={`suite-issues-${item.suite.name}`}
                          >
                            {item.entry_statuses.flatMap((entryStatus, entryIndex) => (
                              entryStatus.issues.map((issue) => (
                                <li key={`${entryStatus.entry_id}:${issue.field}:${issue.message}`}>
                                  <Text size={200}>
                                    Entry {entryIndex + 1} ({entryStatus.preset_name}): {issue.message}
                                  </Text>
                                </li>
                              ))
                            ))}
                          </ul>
                        )}
                      </div>
                    </TableCell>
                    <TableCell>
                      <Text>
                        {item.suite.entries.length} entr{item.suite.entries.length === 1 ? 'y' : 'ies'}
                      </Text>
                    </TableCell>
                    <TableCell data-testid={`suite-status-${item.suite.name}`}>
                      {item.suite.entries.length === 0
                        ? <Text>Empty suite</Text>
                        : issues === 0
                        ? <Text>No advisory issues</Text>
                        : <Text>{issues} advisory issue{issues === 1 ? '' : 's'}</Text>}
                    </TableCell>
                    <TableCell data-testid={`suite-author-${item.suite.name}`}>
                      {item.suite.author
                        ? <Text>{item.suite.author}</Text>
                        : <Text className={styles.meta}>—</Text>}
                    </TableCell>
                    <TableCell>
                      <div className={styles.actions}>
                        <Button
                          className={styles.touchTarget}
                          appearance="subtle"
                          icon={<EditRegular />}
                          onClick={() => navigate(suiteEditorRoutePath(item.suite.name))}
                          data-testid={`edit-suite-${item.suite.name}`}
                        >
                          {editable ? 'Edit' : 'View'}
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
                          data-testid={`delete-suite-${item.suite.name}`}
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
              <DialogTitle>Delete {pendingDelete.suite.name}?</DialogTitle>
              <DialogContent className={styles.dialogBody}>
                <Text>
                  This permanently removes the suite definition. Referenced presets are unaffected.
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
                  onClick={() => void handleDelete()}
                  disabled={deleting}
                  data-testid="confirm-delete-suite"
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
