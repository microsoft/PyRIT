import { useEffect, useMemo, useRef, useState } from 'react'

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
  MessageBarActions,
  MessageBarBody,
  mergeClasses,
  ProgressBar,
  Skeleton,
  SkeletonItem,
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
  ChevronDownRegular,
  ChevronRightRegular,
  DismissCircleRegular,
  ErrorCircleRegular,
  StopRegular,
} from '@fluentui/react-icons'
import { Link, useNavigate, useParams } from 'react-router'

import AttackAttemptDetails from '@/components/AttackResults/AttackAttemptDetails'
import ObjectiveScorerDetails from '@/components/AttackResults/ObjectiveScorerDetails'
import {
  formatDuration,
  formatTimestamp,
  objectivePreview,
} from '@/components/AttackResults/attackAttemptFormatting'
import { useScenarioRunProgress } from '@/hooks/useScenarioRunProgress'
import { scenariosApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type {
  ScenarioProgressCounts,
  ScenarioProgressHeader,
  ScenarioProgressResult,
  ScenarioRunPlanSeedGroup,
  ScenarioRunState,
  ScenarioTechniqueProgress,
} from '@/types'
import {
  attackConversationRoutePath,
  routerPathParamValue,
  scenarioRunAttackRoutePath,
  scenarioRunRoutePath,
} from '@/utils/routeParams'
import {
  getElapsedMilliseconds,
  getEtaMilliseconds,
  isTerminalRunState,
} from '@/utils/scenarioRunProgress'

import AttackExecutionTable from './AttackExecutionTable'
import { ObjectiveDetailsDialog, TechniqueDetailsDialog } from './ScenarioRunDialogs'
import { useScenarioRunPageStyles } from './ScenarioRunPage.styles'

const CLOCK_REFRESH_INTERVAL_MS = 1_000
const MAX_VISIBLE_ATTEMPTS_PER_GROUP = 100

const RUN_BADGE_COLORS: Record<ScenarioRunState, 'informative' | 'brand' | 'success' | 'danger' | 'warning'> = {
  CREATED: 'informative',
  IN_PROGRESS: 'brand',
  COMPLETED: 'success',
  FAILED: 'danger',
  CANCELLED: 'warning',
}

export default function ScenarioRunPage() {
  const {
    scenarioResultId: encodedScenarioResultId,
    attackResultId: encodedAttackResultId,
  } = useParams<{ scenarioResultId: string; attackResultId?: string }>()
  return (
    <ScenarioRunPageContent
      key={encodedScenarioResultId}
      scenarioResultId={routerPathParamValue(encodedScenarioResultId)}
      attackResultId={encodedAttackResultId ? routerPathParamValue(encodedAttackResultId) : null}
    />
  )
}

interface ScenarioRunPageContentProps {
  readonly scenarioResultId: string
  readonly attackResultId: string | null
}

function ScenarioRunPageContent({ scenarioResultId, attackResultId }: ScenarioRunPageContentProps) {
  const styles = useScenarioRunPageStyles()
  const navigate = useNavigate()
  const { state, retry, applyRunSummary } = useScenarioRunProgress(scenarioResultId)
  const [cancelDialogOpen, setCancelDialogOpen] = useState(false)
  const [cancelling, setCancelling] = useState(false)
  const [cancelError, setCancelError] = useState<string | null>(null)
  const [selectedTechnique, setSelectedTechnique] = useState<ScenarioTechniqueProgress | null>(null)
  const [selectedObjective, setSelectedObjective] = useState<ScenarioRunPlanSeedGroup | null>(null)
  const [expandedGroupIds, setExpandedGroupIds] = useState<Set<string>>(new Set())
  const detailsTriggerRef = useRef<HTMLElement | null>(null)

  const seedObjectives = useMemo(
    () => new Map(state.plan?.seed_groups.map((seed) => [seed.id, seed.objective]) ?? []),
    [state.plan],
  )
  const plannedAtomicGroups = useMemo(
    () => new Map(state.plan?.atomic_groups.map((group) => [group.id, group]) ?? []),
    [state.plan],
  )
  const progressAtomicGroups = useMemo(
    () => new Map(state.summary?.atomic_groups.map((group) => [group.id, group]) ?? []),
    [state.summary],
  )
  const selectedAttempt = useMemo(
    () => state.results.find((attempt) => attempt.attack_result_id === attackResultId) ?? null,
    [attackResultId, state.results],
  )
  const selectedAttemptGroup = selectedAttempt
    ? plannedAtomicGroups.get(selectedAttempt.atomic_group_id)
    : undefined
  // Attempts are grouped by atomic group id, then truncated per group, so expanding an
  // older group still shows its own executions rather than an empty table.
  const attemptsByGroupId = useMemo(() => {
    const groupedAttempts = new Map<string, ScenarioProgressResult[]>()
    for (let index = state.results.length - 1; index >= 0; index -= 1) {
      const attempt = state.results[index]
      const existing = groupedAttempts.get(attempt.atomic_group_id)
      if (existing) {
        existing.push(attempt)
      } else {
        groupedAttempts.set(attempt.atomic_group_id, [attempt])
      }
    }
    return groupedAttempts
  }, [state.results])

  const closeAttemptDetails = (): void => {
    navigate(scenarioRunRoutePath(scenarioResultId), { replace: true })
    requestAnimationFrame(() => detailsTriggerRef.current?.focus())
  }

  const openAttemptDetails = (
    attempt: ScenarioProgressResult,
    trigger: HTMLElement,
  ): void => {
    detailsTriggerRef.current = trigger
    navigate(scenarioRunAttackRoutePath(scenarioResultId, attempt.attack_result_id))
  }

  const toggleDisplayGroup = (groupId: string): void => {
    setExpandedGroupIds((current) => {
      const updated = new Set(current)
      if (updated.has(groupId)) {
        updated.delete(groupId)
      } else {
        updated.add(groupId)
      }
      return updated
    })
  }

  const handleCancel = async (): Promise<void> => {
    setCancelling(true)
    setCancelError(null)
    try {
      const run = await scenariosApi.cancelRun(scenarioResultId)
      applyRunSummary(run)
      setCancelDialogOpen(false)
    } catch (error: unknown) {
      setCancelError(toApiError(error).detail)
    } finally {
      setCancelling(false)
    }
  }

  if (state.loadStatus === 'loading' && !state.run) {
    return (
      <main className={styles.root} data-testid="scenario-run-page">
        <div className={styles.content}>
          <Link to="/scanner" className={styles.backLink}>
            <ArrowLeftRegular /> Back to scanners
          </Link>
          <div className={styles.centeredState} aria-label="Loading scenario run">
            <Skeleton className={styles.loadingBlock}>
              <SkeletonItem size={32} />
              <br />
              <SkeletonItem size={16} />
              <br />
              <SkeletonItem size={96} />
            </Skeleton>
            <Text>Loading scenario run...</Text>
          </div>
        </div>
      </main>
    )
  }

  if (state.loadStatus === 'not-found' && !state.run) {
    return (
      <main className={styles.root} data-testid="scenario-run-page">
        <div className={styles.content}>
          <Link to="/scanner" className={styles.backLink}>
            <ArrowLeftRegular /> Back to scanners
          </Link>
          <div className={styles.centeredState}>
            <ErrorCircleRegular fontSize={32} />
            <Text as="h1" size={600} weight="semibold">Scenario run not found</Text>
            <Text>{state.error}</Text>
            <Button className={styles.touchTarget} icon={<ArrowSyncRegular />} onClick={retry}>
              Retry
            </Button>
          </div>
        </div>
      </main>
    )
  }

  if (state.loadStatus === 'error' && !state.run) {
    return (
      <main className={styles.root} data-testid="scenario-run-page">
        <div className={styles.content}>
          <Link to="/scanner" className={styles.backLink}>
            <ArrowLeftRegular /> Back to scanners
          </Link>
          <div className={styles.centeredState}>
            <ErrorCircleRegular fontSize={32} />
            <Text as="h1" size={600} weight="semibold">Unable to load scenario run</Text>
            <Text>{state.error}</Text>
            <Button appearance="primary" className={styles.touchTarget} icon={<ArrowSyncRegular />} onClick={retry}>
              Retry
            </Button>
          </div>
        </div>
      </main>
    )
  }

  if (!state.run || !state.summary) {
    return null
  }

  const run = state.run
  const {
    overall,
    objective_scorer: objectiveScorer,
    display_groups: summarizedDisplayGroups,
    techniques,
    seed_groups: seedGroups,
  } = state.summary
  const displayGroups = summarizedDisplayGroups ?? techniques
  const unattributedAttempts = state.summary.unattributed_attempts ?? 0
  const canCancel = run.status === 'CREATED' || run.status === 'IN_PROGRESS'
  const progressText = overall.planned === null
    ? `${overall.completed} known completed units; planned total unavailable`
    : `${overall.completed} of ${overall.planned} executable units completed`

  return (
    <main className={styles.root} data-testid="scenario-run-page">
      <div className={styles.content}>
        <Link to="/scanner" className={styles.backLink}>
          <ArrowLeftRegular /> Back to scanners
        </Link>

        <header className={styles.header}>
          <div className={styles.headerIdentity}>
            <div className={styles.titleRow}>
              <Text as="h1" size={700} weight="semibold">
                {run.scenario_registry_name ?? run.scenario_name}
              </Text>
              <Badge
                appearance="filled"
                color={RUN_BADGE_COLORS[run.status]}
                icon={statusIcon(run.status)}
                data-testid="run-state-badge"
              >
                {formatRunState(run.status)}
              </Badge>
            </div>
            {run.scenario_registry_name && run.scenario_registry_name !== run.scenario_name && (
              <Text size={300}>{run.scenario_name}</Text>
            )}
            <Text size={200} className={styles.runId}>
              Run ID: <code>{run.scenario_result_id}</code>
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
                Cancel run
              </Button>
            </div>
          )}
        </header>

        <div className={styles.metadata} aria-label="Run metadata">
          <div className={styles.metadataItem}>
            <Text size={200} className={styles.metadataLabel}>Scenario version</Text>
            <Text weight="semibold">{run.scenario_version}</Text>
          </div>
          <div className={styles.metadataItem}>
            <Text size={200} className={styles.metadataLabel}>Created</Text>
            <Text weight="semibold">{formatTimestamp(run.created_at)}</Text>
          </div>
          <div className={styles.metadataItem}>
            <Text size={200} className={styles.metadataLabel}>Completed</Text>
            <Text weight="semibold">{run.completed_at ? formatTimestamp(run.completed_at) : 'Not yet'}</Text>
          </div>
        </div>

        {state.stale && (
          <MessageBar intent="warning">
            <MessageBarBody>
              Live updates paused. Showing the last successfully loaded progress. {state.error}
            </MessageBarBody>
            <MessageBarActions>
              <Button className={styles.touchTarget} icon={<ArrowSyncRegular />} onClick={retry}>
                Retry
              </Button>
            </MessageBarActions>
          </MessageBar>
        )}

        {run.status === 'FAILED' && (
          <MessageBar intent="error">
            <MessageBarBody>
              This run ended before all planned executable units completed. Finished executions remain available below.
            </MessageBarBody>
          </MessageBar>
        )}

        {!state.planComplete && (
          <MessageBar intent="info">
            <MessageBarBody>
              This legacy run has no complete persisted execution plan. Known groups and executions are shown, but planned totals and ETA are unavailable.
            </MessageBarBody>
          </MessageBar>
        )}

        {unattributedAttempts > 0 && (
          <MessageBar intent="warning">
            <MessageBarBody>
              {unattributedAttempts} execution{unattributedAttempts === 1 ? '' : 's'} could not be matched to a planned unit, so the totals below understate what ran.
            </MessageBarBody>
          </MessageBar>
        )}

        <section className={styles.section} aria-labelledby="overall-progress-heading">
          <div className={styles.sectionHeading}>
            <Text as="h2" id="overall-progress-heading" size={500} weight="semibold">
              Overall progress
            </Text>
            <Text className={styles.sectionHint}>{progressText}</Text>
          </div>
          <div className={styles.progressSurface}>
            <div className={styles.progressPrimary}>
              <div className={styles.progressText}>
                <Text weight="semibold">{progressText}</Text>
                {overall.planned !== null && (
                  <Text weight="semibold">
                    {overall.planned > 0 ? `${Math.round((overall.completed / overall.planned) * 100)}%` : '0%'}
                  </Text>
                )}
              </div>
              {overall.planned !== null ? (
                <ProgressBar
                  value={overall.planned > 0 ? overall.completed / overall.planned : 0}
                  aria-label="Overall scenario run progress"
                  aria-valuetext={progressText}
                />
              ) : (
                <Text className={styles.sectionHint}>Progress percentage unavailable</Text>
              )}
            </div>
            <RunTiming run={run} overall={overall} />
          </div>
          <span className={styles.liveStatus} aria-live="polite">
            {isTerminalRunState(run.status) ? `Run ${formatRunState(run.status)}` : ''}
          </span>
        </section>

        <section className={styles.section} aria-labelledby="atomic-groups-heading">
          <div className={styles.sectionHeading}>
            <Text as="h2" id="atomic-groups-heading" size={500} weight="semibold">
              Atomic attack groups
            </Text>
            <Text className={styles.sectionHint}>
              {`${state.results.length} executions`}
            </Text>
          </div>
          {displayGroups.length === 0 ? (
            <EmptyState text="No atomic attack groups have been persisted yet." />
          ) : (
            <div className={styles.displayGroupList}>
              {displayGroups.map((group) => {
                const attempts = (group.atomic_group_ids ?? [])
                  .flatMap((atomicGroupId) => attemptsByGroupId.get(atomicGroupId) ?? [])
                const expanded = expandedGroupIds.has(group.id)
                const panelId = `atomic-group-${group.id.replace(/[^a-zA-Z0-9_-]/g, '-')}`
                return (
                  <article key={group.id} className={styles.displayGroup}>
                    <div className={styles.displayGroupSummary}>
                      <Button
                        appearance="subtle"
                        className={styles.expandButton}
                        icon={expanded ? <ChevronDownRegular /> : <ChevronRightRegular />}
                        aria-expanded={expanded}
                        aria-controls={panelId}
                        aria-label={`${expanded ? 'Collapse' : 'Expand'} attacks in ${group.display_group}`}
                        onClick={() => toggleDisplayGroup(group.id)}
                      />
                      <span className={styles.displayGroupIdentity}>
                        <Text size={400} weight="semibold">{group.display_group}</Text>
                        <Text size={200} className={styles.sectionHint}>
                          {group.atomic_attack_names.join(', ')}
                        </Text>
                      </span>
                      <span className={styles.displayGroupMetrics}>
                        <DisplayGroupMetric
                          label="Completed"
                          value={formatCompletion(group.completed, group.planned)}
                        />
                        <DisplayGroupMetric
                          label="Attack success"
                          value={formatSuccess(group.succeeded, group.completed, group.success_percentage)}
                        />
                        <DisplayGroupMetric label="Errors" value={String(group.errors)} />
                        <DisplayGroupMetric label="Retries" value={String(group.retries)} />
                      </span>
                    </div>
                    {expanded && (
                      <div
                        id={panelId}
                        className={styles.displayGroupPanel}
                        aria-label={`${group.display_group} attack executions`}
                      >
                        <AttackExecutionTable
                          attempts={attempts.slice(0, MAX_VISIBLE_ATTEMPTS_PER_GROUP)}
                          totalAttempts={attempts.length}
                          seedObjectives={seedObjectives}
                          onOpenDetails={openAttemptDetails}
                        />
                      </div>
                    )}
                  </article>
                )
              })}
            </div>
          )}
        </section>

        <section className={styles.section} aria-labelledby="objective-scorer-heading">
          <Text as="h2" id="objective-scorer-heading" size={500} weight="semibold">
            Objective Scorer
          </Text>
          {!objectiveScorer ? (
            <EmptyState text="Objective scorer information is unavailable for this run." />
          ) : (
            <ObjectiveScorerDetails scorer={objectiveScorer} />
          )}
        </section>

        <section className={styles.section} aria-labelledby="techniques-heading">
          <Text as="h2" id="techniques-heading" size={500} weight="semibold">Techniques</Text>
          {techniques.length === 0 ? (
            <EmptyState text="Technique results will appear when the first attack is persisted." />
          ) : (
            <div className={styles.summaryGrid}>
              {techniques.map((technique) => (
                <article key={technique.id} className={styles.summaryItem}>
                  <div className={styles.summaryTitle}>
                    <Button
                      appearance="transparent"
                      className={styles.detailLinkButton}
                      onClick={() => setSelectedTechnique(technique)}
                    >
                      {technique.display_group}
                    </Button>
                    <Metric
                      label="Attack success"
                      value={formatSuccess(
                        technique.succeeded,
                        technique.completed,
                        technique.success_percentage,
                      )}
                    />
                  </div>
                  <div className={styles.summaryStats}>
                    <Metric
                      label="Progress"
                      value={formatCompletion(technique.completed, technique.planned)}
                    />
                    <Metric label="Errors" value={String(technique.errors)} />
                    <Metric label="Retries" value={String(technique.retries)} />
                  </div>
                </article>
              ))}
            </div>
          )}
        </section>

        <section className={styles.section} aria-labelledby="objectives-heading">
          <div className={styles.objectivesHeading}>
            <Text as="h2" id="objectives-heading" size={500} weight="semibold">Objectives</Text>
            <Text className={styles.sectionHint}>Aggregated across techniques.</Text>
          </div>
          {seedGroups.length === 0 ? (
            <EmptyState text="No objectives have been persisted yet." />
          ) : (
            <div className={styles.tableScroll}>
              <Table size="small" aria-label="Objectives">
                <TableHeader className={styles.highlightedTableHeader}>
                  <TableRow>
                    <TableHeaderCell>Objective</TableHeaderCell>
                    <TableHeaderCell className={styles.objectiveSuccessColumn}>Attack Success</TableHeaderCell>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {seedGroups.map((seed) => {
                    const planSeed = state.plan?.seed_groups.find((candidate) => candidate.id === seed.id)
                    return (
                      <TableRow key={seed.id}>
                        <TableCell>
                          <Button
                            appearance="transparent"
                            className={styles.objectiveLinkButton}
                            onClick={() => setSelectedObjective(planSeed ?? {
                              id: seed.id,
                              objective_sha256: '',
                              objective: seed.objective ?? 'Objective text unavailable.',
                              prompts: [],
                            })}
                          >
                            {objectivePreview(seed.objective ?? null, seed.id)}
                          </Button>
                        </TableCell>
                        <TableCell className={styles.objectiveSuccessColumn}>
                          {formatSuccess(seed.succeeded, seed.completed, seed.success_percentage)}
                        </TableCell>
                      </TableRow>
                    )
                  })}
                </TableBody>
              </Table>
            </div>
          )}
        </section>
      </div>

      <Dialog
        open={cancelDialogOpen}
        onOpenChange={(_, data) => {
          if (!cancelling) {
            setCancelDialogOpen(data.open)
          }
        }}
      >
        <DialogSurface>
          <DialogBody>
            <DialogTitle>Cancel this scenario run?</DialogTitle>
            <DialogContent className={styles.dialogContent}>
              <Text>
                In-flight work will be stopped. Finished executions will remain available in this dashboard.
              </Text>
              {cancelError && (
                <MessageBar intent="error">
                  <MessageBarBody>{cancelError}</MessageBarBody>
                </MessageBar>
              )}
            </DialogContent>
            <DialogActions>
              <Button disabled={cancelling} onClick={() => setCancelDialogOpen(false)}>Keep running</Button>
              <Button
                appearance="primary"
                disabled={cancelling}
                icon={<StopRegular />}
                onClick={() => void handleCancel()}
              >
                {cancelling ? 'Cancelling...' : 'Cancel run'}
              </Button>
            </DialogActions>
          </DialogBody>
        </DialogSurface>
      </Dialog>

      <Dialog
        open={selectedAttempt !== null}
        onOpenChange={(_, data) => {
          if (!data.open) {
            closeAttemptDetails()
          }
        }}
      >
        <DialogSurface className={styles.attackDetailDialogSurface}>
          <DialogBody className={styles.attackDetailDialogBody}>
            <DialogTitle>{selectedAttempt?.atomic_attack_name || 'Attack attempt'}</DialogTitle>
            {selectedAttempt && (
              <DialogContent className={styles.attackDetailDialogContent}>
                <AttackAttemptDetails
                  attempt={selectedAttempt}
                  objective={seedObjectives.get(selectedAttempt.seed_group_id) ?? null}
                  atomicGroup={selectedAttemptGroup}
                  techniqueDetails={progressAtomicGroups.get(selectedAttempt.atomic_group_id)?.technique_details}
                  objectiveScorer={objectiveScorer}
                  conversationPath={attackConversationRoutePath(
                    selectedAttempt.attack_result_id,
                    selectedAttempt.conversation_id,
                    scenarioResultId,
                  )}
                />
              </DialogContent>
            )}
            <DialogActions>
              <Button appearance="primary" onClick={closeAttemptDetails}>Close</Button>
            </DialogActions>
          </DialogBody>
        </DialogSurface>
      </Dialog>

      <TechniqueDetailsDialog technique={selectedTechnique} onClose={() => setSelectedTechnique(null)} />
      <ObjectiveDetailsDialog objective={selectedObjective} onClose={() => setSelectedObjective(null)} />
    </main>
  )
}

interface MetricProps {
  readonly label: string
  readonly value: string
}

function DisplayGroupMetric({ label, value }: MetricProps) {
  const styles = useScenarioRunPageStyles()
  return (
    <span className={styles.displayGroupMetric}>
      <Text size={200} className={styles.metricLabel}>{label}</Text>
      <Text weight="semibold" className={styles.metricValue}>{value}</Text>
    </span>
  )
}

function Metric({ label, value }: MetricProps) {
  const styles = useScenarioRunPageStyles()
  return (
    <div className={styles.metric} role="group" aria-label={label}>
      <Text size={200} className={styles.metricLabel}>{label}</Text>
      <Text weight="semibold" className={styles.metricValue}>{value}</Text>
    </div>
  )
}

interface RunTimingProps {
  readonly run: ScenarioProgressHeader
  readonly overall: ScenarioProgressCounts
}

function RunTiming({ run, overall }: RunTimingProps) {
  const [nowMilliseconds, setNowMilliseconds] = useState(() => Date.now())

  useEffect(() => {
    if (isTerminalRunState(run.status)) {
      return
    }
    const timer = setInterval(() => setNowMilliseconds(Date.now()), CLOCK_REFRESH_INTERVAL_MS)
    return () => clearInterval(timer)
  }, [run.status])

  const elapsed = getElapsedMilliseconds(run, nowMilliseconds)
  const eta = getEtaMilliseconds(run, overall, nowMilliseconds)
  const estimatedRemaining = isTerminalRunState(run.status)
    ? 'Completed'
    : eta === null
      ? 'Unavailable'
      : formatDuration(eta)
  return (
    <>
      <Metric label="Elapsed" value={formatDuration(elapsed)} />
      <Metric label="Estimated remaining" value={estimatedRemaining} />
    </>
  )
}

interface EmptyStateProps {
  readonly text: string
}

function EmptyState({ text }: EmptyStateProps) {
  const styles = useScenarioRunPageStyles()
  return (
    <div className={styles.emptyState}>
      <Text>{text}</Text>
    </div>
  )
}

function formatRunState(status: string): string {
  return status.toLowerCase().replace('_', ' ').replace(/^\w/, (letter) => letter.toUpperCase())
}

function statusIcon(status: ScenarioRunState): React.ReactElement {
  if (status === 'COMPLETED') {
    return <CheckmarkCircleRegular />
  }
  if (status === 'FAILED') {
    return <DismissCircleRegular />
  }
  if (status === 'CANCELLED') {
    return <StopRegular />
  }
  return <ArrowSyncRegular />
}

function formatSuccess(succeeded: number, evaluated: number, percent: number | null): string {
  return percent === null ? `${succeeded}/${evaluated} —` : `${succeeded}/${evaluated} (${percent}%)`
}

function formatCompletion(completed: number, planned: number | null): string {
  return planned === null ? `${completed}/total unavailable` : `${completed}/${planned}`
}
