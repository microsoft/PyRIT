import { type ReactElement, useState } from 'react'

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
  mergeClasses,
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
  ArrowLeftRegular,
  ArrowSyncRegular,
  CheckmarkCircleRegular,
  DismissCircleRegular,
  ErrorCircleRegular,
  StopRegular,
} from '@fluentui/react-icons'
import { Link, useParams } from 'react-router'

import { formatTimestamp } from '@/components/AttackResults/attackAttemptFormatting'
import {
  isTerminalSuiteExecutionStatus,
  useScenarioSuiteExecution,
} from '@/hooks/useScenarioSuiteExecution'
import { scenarioSuitesApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type {
  ScenarioSuiteExecutionEntryState,
  ScenarioSuiteExecutionEntrySummary,
  ScenarioSuiteExecutionStatus,
} from '@/types'
import { routerPathParamValue, scenarioRunRoutePath } from '@/utils/routeParams'

import { useScenarioSuiteExecutionPageStyles } from './ScenarioSuiteExecutionPage.styles'

const EXECUTION_BADGE_COLORS: Record<
  ScenarioSuiteExecutionStatus,
  'informative' | 'brand' | 'success' | 'danger' | 'warning'
> = {
  QUEUING: 'informative',
  IN_PROGRESS: 'brand',
  COMPLETED: 'success',
  FAILED: 'danger',
  CANCELLED: 'warning',
}

const ENTRY_BADGE_COLORS: Record<
  ScenarioSuiteExecutionEntryState,
  'informative' | 'brand' | 'success' | 'danger' | 'warning'
> = {
  PENDING: 'informative',
  SUBMITTING: 'informative',
  SUBMISSION_FAILED: 'danger',
  CREATED: 'informative',
  QUEUED: 'informative',
  IN_PROGRESS: 'brand',
  COMPLETED: 'success',
  FAILED: 'danger',
  CANCELLED: 'warning',
}

const ENTRY_SUBMISSION_FAILURE_MESSAGE = (
  'The scenario run could not be created. Check server logs before retrying.'
)
const ENTRY_STATUS_FAILURE_MESSAGE = (
  'The scenario run status could not be loaded. Refresh the page or check server logs.'
)

export default function ScenarioSuiteExecutionPage() {
  const { executionId } = useParams<{ executionId: string }>()
  const decodedExecutionId = routerPathParamValue(executionId)
  return (
    <ScenarioSuiteExecutionPageContent
      key={decodedExecutionId}
      executionId={decodedExecutionId}
    />
  )
}

interface ScenarioSuiteExecutionPageContentProps {
  readonly executionId: string
}

function ScenarioSuiteExecutionPageContent({
  executionId,
}: ScenarioSuiteExecutionPageContentProps) {
  const styles = useScenarioSuiteExecutionPageStyles()
  const { state, retry, applyExecution } = useScenarioSuiteExecution(executionId)
  const [cancelDialogOpen, setCancelDialogOpen] = useState(false)
  const [cancelling, setCancelling] = useState(false)
  const [cancelError, setCancelError] = useState<string | null>(null)

  const handleCancel = async (): Promise<void> => {
    setCancelling(true)
    setCancelError(null)
    try {
      const execution = await scenarioSuitesApi.cancelExecution(executionId)
      applyExecution(execution)
      setCancelDialogOpen(false)
    } catch (error: unknown) {
      setCancelError(toApiError(error).detail)
    } finally {
      setCancelling(false)
    }
  }

  if (state.loadStatus === 'loading' && !state.execution) {
    return (
      <main className={styles.root} data-testid="scenario-suite-execution-page">
        <div className={styles.content}>
          <Link to="/scanner" className={styles.backLink}>
            <ArrowLeftRegular /> Back to scanners
          </Link>
          <div className={styles.centeredState} aria-label="Loading suite execution">
            <Spinner label="Loading suite execution..." />
          </div>
        </div>
      </main>
    )
  }

  if (state.loadStatus === 'not-found' && !state.execution) {
    return (
      <main className={styles.root} data-testid="scenario-suite-execution-page">
        <div className={styles.content}>
          <Link to="/scanner" className={styles.backLink}>
            <ArrowLeftRegular /> Back to scanners
          </Link>
          <div className={styles.centeredState}>
            <ErrorCircleRegular fontSize={32} />
            <Text as="h1" size={600} weight="semibold">Suite execution not found</Text>
            <Text>{state.error}</Text>
            <Text size={200}>
              Suite progress is process-local, but successfully created scenario runs remain in scanner history.
            </Text>
            <Button
              appearance="primary"
              className={styles.touchTarget}
              icon={<ArrowSyncRegular />}
              onClick={retry}
            >
              Retry
            </Button>
          </div>
        </div>
      </main>
    )
  }

  if (state.loadStatus === 'error' && !state.execution) {
    return (
      <main className={styles.root} data-testid="scenario-suite-execution-page">
        <div className={styles.content}>
          <Link to="/scanner" className={styles.backLink}>
            <ArrowLeftRegular /> Back to scanners
          </Link>
          <div className={styles.centeredState}>
            <ErrorCircleRegular fontSize={32} />
            <Text as="h1" size={600} weight="semibold">Unable to load suite execution</Text>
            <Text>{state.error}</Text>
            <Button
              appearance="primary"
              className={styles.touchTarget}
              icon={<ArrowSyncRegular />}
              onClick={retry}
            >
              Retry
            </Button>
          </div>
        </div>
      </main>
    )
  }

  const execution = state.execution
  if (!execution) {
    return null
  }

  const canCancel = !isTerminalSuiteExecutionStatus(execution.status)
  const completedEntries = execution.entries.filter((entry) => entry.state === 'COMPLETED').length
  const failedEntries = execution.entries.filter(
    (entry) => entry.state === 'FAILED' || entry.state === 'SUBMISSION_FAILED',
  ).length
  const activeEntries = execution.entries.length - completedEntries - failedEntries
    - execution.entries.filter((entry) => entry.state === 'CANCELLED').length

  return (
    <main className={styles.root} data-testid="scenario-suite-execution-page">
      <div className={styles.content}>
        <Link to="/scanner" className={styles.backLink}>
          <ArrowLeftRegular /> Back to scanners
        </Link>

        <header className={styles.header}>
          <div className={styles.headerIdentity}>
            <div className={styles.titleRow}>
              <Text as="h1" size={700} weight="semibold">
                {execution.suite_name}
              </Text>
              <Badge
                appearance="filled"
                color={EXECUTION_BADGE_COLORS[execution.status]}
                icon={statusIcon(execution.status)}
                data-testid="suite-execution-state-badge"
              >
                {formatState(execution.status)}
              </Badge>
            </div>
            <Text size={200} className={styles.executionId}>
              Suite execution ID: <code>{execution.suite_execution_id}</code>
            </Text>
          </div>
          {canCancel && (
            <div className={styles.headerActions}>
              <Button
                appearance="secondary"
                className={mergeClasses(styles.touchTarget, styles.wideButton)}
                icon={<StopRegular />}
                onClick={() => {
                  setCancelError(null)
                  setCancelDialogOpen(true)
                }}
              >
                Cancel suite
              </Button>
            </div>
          )}
        </header>

        {state.stale && state.error && (
          <MessageBar intent="warning">
            <MessageBarBody>
              Live progress could not be refreshed: {state.error}
            </MessageBarBody>
          </MessageBar>
        )}

        <div className={styles.metadata} aria-label="Suite execution metadata">
          <div className={styles.metadataItem}>
            <Text size={200} className={styles.metadataLabel}>Suite origin</Text>
            <Text weight="semibold">
              {execution.suite_origin === 'registered' ? 'Defined in code' : 'Stored'}
            </Text>
          </div>
          <div className={styles.metadataItem}>
            <Text size={200} className={styles.metadataLabel}>Created</Text>
            <Text weight="semibold">{formatTimestamp(execution.created_at)}</Text>
          </div>
          <div className={styles.metadataItem}>
            <Text size={200} className={styles.metadataLabel}>Last updated</Text>
            <Text weight="semibold">{formatTimestamp(execution.updated_at)}</Text>
          </div>
        </div>

        <section className={styles.section} aria-labelledby="suite-progress-heading">
          <div className={styles.sectionHeading}>
            <Text as="h2" id="suite-progress-heading" size={500} weight="semibold">
              Suite progress
            </Text>
            <Text className={styles.sectionHint}>Each entry remains an ordinary scenario run.</Text>
          </div>
          <div className={styles.summaryGrid}>
            <SummaryItem label="Entries" value={execution.entries.length} />
            <SummaryItem label="Active" value={Math.max(0, activeEntries)} />
            <SummaryItem label="Completed" value={completedEntries} />
            <SummaryItem label="Failed" value={failedEntries} />
          </div>
          <div className={styles.tableScroll}>
            <Table className={styles.table} aria-label="Suite entry progress">
              <TableHeader>
                <TableRow>
                  <TableHeaderCell>Order</TableHeaderCell>
                  <TableHeaderCell>Preset</TableHeaderCell>
                  <TableHeaderCell>Status</TableHeaderCell>
                  <TableHeaderCell>Scenario run</TableHeaderCell>
                  <TableHeaderCell>Progress</TableHeaderCell>
                </TableRow>
              </TableHeader>
              <TableBody>
                {execution.entries.map((entry) => (
                  <TableRow
                    key={entry.entry_id}
                    data-testid={`suite-execution-entry-${entry.entry_id}`}
                  >
                    <TableCell>{entry.entry_position}</TableCell>
                    <TableCell>
                      <div className={styles.entryIdentity}>
                        <Text weight="semibold">{entry.preset_name}</Text>
                        <Text size={200} className={styles.entryMeta}>
                          {entry.preset_origin === 'registered' ? 'Defined in code' : 'Stored preset'}
                        </Text>
                        {entry.error && (
                          <Text size={200} className={styles.errorText}>
                            {entryErrorMessage(entry)}
                          </Text>
                        )}
                      </div>
                    </TableCell>
                    <TableCell>
                      <Badge
                        appearance="tint"
                        color={ENTRY_BADGE_COLORS[entry.state]}
                        icon={statusIcon(entry.state)}
                      >
                        {formatState(entry.state)}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      {entry.run ? (
                        <Link
                          to={scenarioRunRoutePath(entry.run.scenario_result_id)}
                          state={{
                            suiteExecutionId: execution.suite_execution_id,
                            suiteName: execution.suite_name,
                          }}
                        >
                          View run
                        </Link>
                      ) : (
                        <Text className={styles.entryMeta}>Not created</Text>
                      )}
                    </TableCell>
                    <TableCell>{entryProgress(entry)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
          <span className={styles.liveStatus} aria-live="polite">
            {isTerminalSuiteExecutionStatus(execution.status)
              ? `Suite ${formatState(execution.status)}`
              : `${completedEntries} of ${execution.entries.length} entries completed`}
          </span>
        </section>
      </div>

      <Dialog
        open={cancelDialogOpen}
        onOpenChange={(_, data) => { if (!data.open && !cancelling) setCancelDialogOpen(false) }}
      >
        <DialogSurface>
          <DialogBody>
            <DialogTitle>Cancel {execution.suite_name}?</DialogTitle>
            <DialogContent className={styles.dialogContent}>
              <Text>
                Pending submissions and running child scenarios will be cancelled where possible.
                Completed child runs are unaffected.
              </Text>
              {cancelError && (
                <MessageBar intent="error">
                  <MessageBarBody>{cancelError}</MessageBarBody>
                </MessageBar>
              )}
            </DialogContent>
            <DialogActions>
              <Button
                appearance="secondary"
                onClick={() => setCancelDialogOpen(false)}
                disabled={cancelling}
              >
                Keep running
              </Button>
              <Button
                appearance="primary"
                onClick={() => void handleCancel()}
                disabled={cancelling}
                data-testid="confirm-cancel-suite"
              >
                {cancelling ? 'Cancelling...' : 'Cancel suite'}
              </Button>
            </DialogActions>
          </DialogBody>
        </DialogSurface>
      </Dialog>
    </main>
  )
}

function SummaryItem({ label, value }: { readonly label: string; readonly value: number }) {
  const styles = useScenarioSuiteExecutionPageStyles()
  return (
    <div className={styles.summaryItem}>
      <Text size={200} className={styles.metadataLabel}>{label}</Text>
      <Text size={500} weight="semibold" className={styles.summaryValue}>{value}</Text>
    </div>
  )
}

function entryProgress(entry: ScenarioSuiteExecutionEntrySummary): string {
  if (!entry.run) {
    return entry.state === 'SUBMISSION_FAILED' ? 'Submission failed' : 'Waiting for run'
  }
  if (entry.run.status === 'QUEUED') {
    return entry.run.queue_position ? `Queue position ${entry.run.queue_position}` : 'Queued'
  }
  if (entry.run.total_attacks <= 0) {
    return formatState(entry.run.status)
  }
  return `${entry.run.completed_attacks} of ${entry.run.total_attacks} attacks`
}

function entryErrorMessage(entry: ScenarioSuiteExecutionEntrySummary): string {
  return entry.state === 'SUBMISSION_FAILED'
    ? ENTRY_SUBMISSION_FAILURE_MESSAGE
    : ENTRY_STATUS_FAILURE_MESSAGE
}

function statusIcon(
  status: ScenarioSuiteExecutionStatus | ScenarioSuiteExecutionEntryState,
): ReactElement | undefined {
  if (status === 'COMPLETED') {
    return <CheckmarkCircleRegular />
  }
  if (status === 'FAILED' || status === 'SUBMISSION_FAILED') {
    return <ErrorCircleRegular />
  }
  if (status === 'CANCELLED') {
    return <DismissCircleRegular />
  }
  return undefined
}

function formatState(status: string): string {
  return status.toLowerCase().replace(/_/g, ' ').replace(
    /^\w/,
    (letter: string) => letter.toUpperCase(),
  )
}
