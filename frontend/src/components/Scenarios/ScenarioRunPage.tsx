import { useMemo, useRef, useState } from 'react'

import {
  Accordion, AccordionHeader, AccordionItem, AccordionPanel, Badge, Button,
  Dialog, DialogActions, DialogBody, DialogContent, DialogSurface, DialogTitle,
  MessageBar, MessageBarBody, ProgressBar, Spinner, Table, TableBody, TableCell,
  TableHeader, TableHeaderCell, TableRow, Text,
} from '@fluentui/react-components'
import { ArrowLeftRegular, ArrowClockwiseRegular, ChevronDownRegular, ChevronRightRegular, DismissRegular, PlayRegular, StopRegular } from '@fluentui/react-icons'
import { Link, useLocation, useNavigate, useParams } from 'react-router'

import AttackAttemptDetails from '@/components/AttackResults/AttackAttemptDetails'
import { formatProgressSuccess, objectivePreview } from '@/components/AttackResults/attackAttemptFormatting'
import RegistryIdentityRow from '@/components/Registry/RegistryIdentityRow'
import { suiteExecutionRoutePath } from '@/components/ScenarioSuites/suiteRoutes'
import { useScenarioRunProgress } from '@/hooks/useScenarioRunProgress'
import { useScenarioResultHierarchy } from '@/hooks/useScenarioResultHierarchy'
import { useScenarioRunResume } from '@/hooks/useScenarioRunResume'
import { useScenarioQueue } from '@/hooks/useScenarioQueue'
import { scenariosApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type {
  ScenarioActiveConversation,
  ScenarioComponentIdentity,
  ScenarioHierarchyNode,
  ScenarioProgressResult,
  ScenarioRunPlanSeedGroup,
  ScenarioRunState,
} from '@/types'
import {
  attackConversationRoutePath,
  routerPathParamValue,
  scenarioConversationRoutePath,
  scenarioRerunRoutePath,
  scenarioRunAttackRoutePath,
  scenarioRunRoutePath,
} from '@/utils/routeParams'
import { isTerminalRunState } from '@/utils/scenarioRunProgress'

import AttackExecutionTable from './AttackExecutionTable'
import { ObjectiveDetailsDialog } from './ScenarioRunDialogs'
import { useScenarioRunPageStyles } from './ScenarioRunPage.styles'
import ScenarioRunTiming from './ScenarioRunTiming'
import ScenarioResultHierarchy from './ScenarioResultHierarchy'
import { hierarchyDisplayGroups } from './scenarioHierarchy'

const MAX_VISIBLE_ATTEMPTS_PER_GROUP = 100
const AUTO_EXPAND_GROUP_LIMIT = 20
const STATE_COLORS: Record<ScenarioRunState, 'informative' | 'brand' | 'success' | 'danger' | 'warning'> = {
  CREATED: 'informative', QUEUED: 'informative', IN_PROGRESS: 'brand', COMPLETED: 'success', FAILED: 'danger', CANCELLED: 'warning',
}

export default function ScenarioRunPage() {
  const { scenarioResultId, attackResultId } = useParams<{ scenarioResultId: string; attackResultId?: string }>()
  return <ScenarioRunContent key={scenarioResultId} scenarioResultId={routerPathParamValue(scenarioResultId)}
    attackResultId={attackResultId ? routerPathParamValue(attackResultId) : null} />
}

interface ScenarioRunContentProps {
  readonly scenarioResultId: string
  readonly attackResultId: string | null
}

function seedDatasetNames(identities: ScenarioComponentIdentity[]): string {
  return [...new Set(identities.flatMap((identity: ScenarioComponentIdentity) =>
    typeof identity.parameters.dataset_name === 'string' ? [identity.parameters.dataset_name] : []))].join(', ')
}

function ScenarioRunContent({ scenarioResultId, attackResultId }: ScenarioRunContentProps) {
  const styles = useScenarioRunPageStyles()
  const navigate = useNavigate()
  const location = useLocation()
  const navigationState = location.state as {
    fromScenarioHistory?: boolean
    scenarioHistorySearch?: string
    scenarioName?: string
    suiteExecutionId?: string
    suiteName?: string
  } | null
  const backPath = navigationState?.suiteExecutionId
    ? suiteExecutionRoutePath(navigationState.suiteExecutionId)
    : navigationState?.fromScenarioHistory || navigationState?.scenarioHistorySearch
      ? `/scanner/history${navigationState.scenarioHistorySearch ?? ''}`
      : navigationState?.scenarioName
        ? `/scanner/launch/${encodeURIComponent(navigationState.scenarioName)}`
        : '/scanner/launch'
  const backLabel = navigationState?.suiteExecutionId
    ? `Back to ${navigationState.suiteName ?? 'suite execution'}`
    : navigationState?.fromScenarioHistory || navigationState?.scenarioHistorySearch
      ? 'Back to scans'
      : navigationState?.scenarioName ? 'Back to scenario' : 'Back to scanners'
  const { state, retry, applyRunSummary } = useScenarioRunProgress(scenarioResultId)
  const hierarchy = useScenarioResultHierarchy(scenarioResultId, state.run !== null && state.summary !== null)
  const queue = useScenarioQueue()
  const [identityExpanded, setIdentityExpanded] = useState(false)
  const [objective, setObjective] = useState<ScenarioRunPlanSeedGroup | null>(null)
  const [cancelOpen, setCancelOpen] = useState(false)
  const [cancelling, setCancelling] = useState(false)
  const [cancelError, setCancelError] = useState<string | null>(null)
  const [rerunPending, setRerunPending] = useState(false)
  const [rerunError, setRerunError] = useState<string | null>(null)
  const detailsTrigger = useRef<HTMLElement | null>(null)
  const objectiveTrigger = useRef<HTMLElement | null>(null)
  const overviewTarget = useRef<HTMLElement | null>(null)
  const active = Boolean(state.run && !isTerminalRunState(state.run.status))
  const resume = useScenarioRunResume({
    onResumed: applyRunSummary,
    onRefresh: (succeeded: boolean): void => {
      queue.retry()
      if (!succeeded) retry()
    },
  })
  const [atomicGroupsExpandedChoice, setAtomicGroupsExpandedChoice] = useState<boolean | null>(null)
  const [expandedGroupChoices, setExpandedGroupChoices] = useState<Map<string, boolean>>(new Map())
  const detailsTriggerRef = useRef<HTMLElement | null>(null)
  const atomicGroupsToggleRef = useRef<HTMLButtonElement | null>(null)
  const [hierarchySelection, setHierarchySelection] = useState<ScenarioHierarchyNode | null>(null)
  const seedObjectives = useMemo(
    () => new Map(state.plan?.seed_groups.map((seed) => [seed.id, seed.objective]) ?? []),
    [state.plan],
  )
  const planSeeds = useMemo(
    () => new Map(state.plan?.seed_groups.map((seed) => [seed.id, seed]) ?? []),
    [state.plan],
  )
  const attemptsBySeed = useMemo(() => {
    const groups = new Map<string, ScenarioProgressResult[]>()
    for (const attempt of state.results) {
      const attempts = groups.get(attempt.seed_group_id) ?? []
      attempts.push(attempt)
      groups.set(attempt.seed_group_id, attempts)
    }
    return groups
  }, [state.results])
  const plannedAtomicGroups = useMemo(
    () => new Map(state.plan?.atomic_groups.map((group) => [group.id, group]) ?? []),
    [state.plan],
  )
  const progressAtomicGroups = useMemo(
    () => new Map(state.summary?.atomic_groups.map((group) => [group.id, group]) ?? []),
    [state.summary],
  )
  // A hierarchy node can be opened before the paged progress feed has delivered it.
  const selectedAttempt = useMemo<ScenarioProgressResult | ScenarioHierarchyNode | ScenarioActiveConversation | null>(
    () =>
      state.results.find((attempt) => attempt.attack_result_id === attackResultId) ??
      state.activeConversations.find((conversation) => conversation.attack_result_id === attackResultId) ??
      (hierarchySelection?.attack_result_id === attackResultId ? hierarchySelection : null),
    [attackResultId, state.results, state.activeConversations, hierarchySelection],
  )
  const selectedAtomicGroupId = selectedAttempt && 'atomic_group_id' in selectedAttempt
    ? selectedAttempt.atomic_group_id
    : null
  const selectedAttemptGroup = selectedAtomicGroupId
    ? plannedAtomicGroups.get(selectedAtomicGroupId)
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
  const datasetsBySeed = useMemo(() => {
    const identities = new Map<string, ScenarioComponentIdentity[]>()
    for (const attempt of [...state.results, ...state.activeConversations]) {
      if (!attempt.seed_group_id) continue
      const seeds = identities.get(attempt.seed_group_id) ?? []
      seeds.push(...(attempt.seed_identifiers ?? []))
      identities.set(attempt.seed_group_id, seeds)
    }
    return new Map([...identities].map(([id, seeds]) => [id, seedDatasetNames(seeds)]))
  }, [state.results, state.activeConversations])
  const savedConversations = useMemo(() => {
    const knownResults = new Set(state.results.map((attempt) => attempt.attack_result_id))
    const recorded = new Map<string, ScenarioActiveConversation>()
    for (const conversation of state.recordedConversations) {
      if (!knownResults.has(conversation.attack_result_id)) recorded.set(conversation.attack_result_id, conversation)
    }
    return [...recorded.values()]
  }, [state.results, state.recordedConversations])
  const selectedActive = state.activeConversations.find((conversation) => conversation.attack_result_id === attackResultId)
  const runPath = scenarioRunRoutePath(scenarioResultId)

  const closeAttemptDetails = (): void => {
    navigate(runPath, { replace: true, state: location.state })
    requestAnimationFrame(() => {
      const trigger = detailsTriggerRef.current ?? detailsTrigger.current
      const focusTarget = trigger?.isConnected ? trigger : atomicGroupsToggleRef.current ?? overviewTarget.current
      focusTarget?.focus()
    })
  }

  const openAttemptDetails = (
    attempt: ScenarioProgressResult | ScenarioHierarchyNode,
    trigger: HTMLElement,
  ): void => {
    detailsTriggerRef.current = trigger
    detailsTrigger.current = trigger
    setHierarchySelection('result_role' in attempt ? attempt : null)
    navigate(scenarioRunAttackRoutePath(scenarioResultId, attempt.attack_result_id), { state: location.state })
  }

  const toggleDisplayGroup = (groupId: string, defaultExpanded: boolean): void => {
    setExpandedGroupChoices((current) => new Map(current).set(
      groupId,
      !(current.get(groupId) ?? defaultExpanded),
    ))
  }

  function openDetails(attempt: ScenarioProgressResult, trigger: HTMLElement): void {
    const focusTarget = objective ? objectiveTrigger.current : trigger
    detailsTriggerRef.current = focusTarget
    detailsTrigger.current = focusTarget
    setObjective(null)
    setHierarchySelection(null)
    navigate(scenarioRunAttackRoutePath(scenarioResultId, attempt.attack_result_id), { state: location.state })
  }

  async function rerun(): Promise<void> {
    setRerunPending(true)
    setRerunError(null)
    try {
      const request = await scenariosApi.getLaunchConfiguration(scenarioResultId)
      navigate(scenarioRerunRoutePath(request.scenario_name, scenarioResultId))
    } catch (error: unknown) {
      setRerunError(toApiError(error).detail)
    } finally {
      setRerunPending(false)
    }
  }

  async function cancel(): Promise<void> {
    setCancelling(true)
    setCancelError(null)
    try {
      applyRunSummary(await scenariosApi.cancelRun(scenarioResultId))
      setCancelOpen(false)
    } catch (error: unknown) {
      setCancelError(toApiError(error).detail)
    } finally {
      setCancelling(false)
    }
  }

  if (!state.run || !state.summary) return (
    <main className={styles.root} data-testid="scenario-run-page"><div className={styles.content}>
      <Link to={backPath} className={styles.backLink}><ArrowLeftRegular /> {backLabel}</Link>
      {state.loadStatus === 'loading' ? <Spinner label="Loading scan..." /> : (
        <MessageBar intent="error"><MessageBarBody>{state.error ?? 'Scan not found.'} <Button onClick={retry}>Retry</Button></MessageBarBody></MessageBar>
      )}
    </div></main>
  )

  const run = state.run
  const summary = state.summary
  const overall = summary.overall
  const outcomes = overall.outcomes
  const groups = summary.display_groups ?? summary.techniques
  const firstError = state.results.find((attempt) => attempt.outcome === 'error')
  const controlError = rerunError ?? resume.error
    ?? (resume.executionError !== run.error ? resume.executionError : null)
  const notices: string[] = []
  if (state.overloadSummaries.length > 0) notices.push('Service overload detected.')
  const unattributedAttempts = summary.unattributed_attempts ?? 0
  const unmatched = unattributedAttempts
  if (unmatched > 0) notices.push(`${unmatched} execution${unmatched === 1 ? '' : 's'} could not be matched to the saved plan.`)
  if (!state.planComplete) notices.push('The saved execution plan is incomplete; some totals cannot be verified.')
  const objectiveScorer = summary.objective_scorer
  const displayGroups = hierarchyDisplayGroups(groups, hierarchy.roots.groups)
  const producerCounts = overall.producer_counts
  const executionCount = producerCounts
    ? producerCounts.target_facing.attempts + producerCounts.orchestration.attempts + producerCounts.unknown.attempts
    : state.results.length
  const atomicGroupsExpanded = atomicGroupsExpandedChoice ?? displayGroups.length <= AUTO_EXPAND_GROUP_LIMIT
  return (
    <main className={styles.root} data-testid="scenario-run-page">
      <div className={styles.content}>
        <Link to={backPath} className={styles.backLink}><ArrowLeftRegular /> {backLabel}</Link>
        <Text role="status" aria-label="Run status updates" aria-live="polite" className={styles.liveStatus}>
          {!active ? `${run.scenario_registry_name ?? run.scenario_name}: ${run.status.toLowerCase()}.` : ''}
        </Text>
        <header className={styles.header} ref={overviewTarget} tabIndex={-1}>
          <div className={styles.headerIdentity}>
            <div className={styles.titleRow}>
              <Text as="h1" size={700} weight="semibold">{run.scenario_registry_name ?? run.scenario_name}</Text>
              <Badge appearance="tint" color={STATE_COLORS[run.status]} data-testid="run-state-badge">
                {run.status === 'IN_PROGRESS' ? 'Executing' : run.status.charAt(0) + run.status.slice(1).toLowerCase()}
              </Badge>
            </div>
            {run.description && <Text className={styles.sectionHint}>{run.description}</Text>}
          </div>
          <div className={styles.headerActions}>
            {active && <Link className={styles.backLink} to="/scanner/queue">View queue</Link>}
            <Button icon={<ArrowClockwiseRegular />} disabled={rerunPending} onClick={() => { void rerun() }}>{rerunPending ? 'Loading...' : 'Rerun'}</Button>
            {active ? <Button icon={<StopRegular />} className={styles.touchTarget} onClick={() => { setCancelError(null); setCancelOpen(true) }}>Cancel</Button>
              : (run.status === 'FAILED' || run.status === 'CANCELLED') && <Button appearance="primary" icon={<PlayRegular />}
                disabled={resume.pendingRunId !== null} onClick={() => { setRerunError(null); resume.requestResume(scenarioResultId) }}>Resume</Button>}
          </div>
        </header>
        {controlError && <MessageBar intent="error"><MessageBarBody>
          {controlError}
        </MessageBarBody></MessageBar>}
        <section className={styles.metadata} aria-label="Scan summary">
          <div className={styles.metadataItem}><Text size={200} className={styles.metadataLabel}>Operation</Text>
            {run.operation_id ? <Link to={`/operations/${encodeURIComponent(run.operation_id)}`}>{run.operation_name ?? 'Operation'}</Link> : <Text>Unassigned</Text>}
          </div>
          <div className={styles.metadataItem}><Text size={200} className={styles.metadataLabel}>Target</Text><Text weight="semibold">{run.target?.model_name ?? run.target?.target_type ?? 'Not recorded'}</Text></div>
          <div className={styles.metadataItem}><Text size={200} className={styles.metadataLabel}>Timing</Text><ScenarioRunTiming run={run} /></div>
          <div className={styles.metadataItem}><Text size={200} className={styles.metadataLabel}>Attack success</Text>
            <Text weight="semibold">{formatProgressSuccess(overall)}</Text>
            <Text size={200} className={styles.sectionHint}>{outcomes ? `${outcomes.successes} / ${outcomes.total_decided} decided` : `${overall.succeeded} / ${overall.completed} complete`}</Text>
          </div>
          <div className={styles.metadataItem}><Text size={200} className={styles.metadataLabel}>Errors / retries</Text><Text weight="semibold">{overall.errors} / {overall.retries}</Text></div>
          <div className={styles.metadataItem}><Text size={200} className={styles.metadataLabel}>Labels</Text>
            <div className={styles.badgeList}>{Object.entries(run.labels ?? {}).map(([key, value]) => <Badge key={key} appearance="outline" className={styles.labelBadge}>{key}: {value}</Badge>)}</div>
          </div>
        </section>
        {(run.status === 'FAILED' || run.error || overall.errors > 0) && <MessageBar
          intent={run.status === 'FAILED' ? 'error' : run.status === 'CANCELLED' && overall.errors === 0 ? 'info' : 'warning'}>
          <MessageBarBody>{run.status === 'FAILED' ? 'Scan failed. Resume to continue remaining attacks.'
            : run.status === 'CANCELLED' ? 'Scan cancelled.'
            : `${overall.errors} attack${overall.errors === 1 ? '' : 's'} ended with an error.`}
            {firstError && <Button appearance="transparent" onClick={(event) => { openDetails(firstError, event.currentTarget) }}>View attack</Button>}
            {run.error && <Accordion collapsible><AccordionItem value="error">
              <AccordionHeader>Error details</AccordionHeader>
              <AccordionPanel><Text className={styles.objective}>{run.error_type ? `${run.error_type}: ` : ''}{run.error}</Text></AccordionPanel>
            </AccordionItem></Accordion>}
          </MessageBarBody>
        </MessageBar>}
        {state.stale && <MessageBar intent="warning"><MessageBarBody>Live updates paused. {state.error} <Button onClick={retry}>Retry</Button></MessageBarBody></MessageBar>}
        {notices.length > 0 && <MessageBar aria-label="Scan notices"
          intent={state.overloadSummaries.length > 0 || unmatched > 0 ? 'warning' : 'info'}>
          <MessageBarBody>
            {notices.join(' ')}
            {state.overloadSummaries.length > 0 && <Accordion collapsible><AccordionItem value="overload">
              <AccordionHeader>Overload details</AccordionHeader>
              <AccordionPanel>{state.overloadSummaries.map((overload) => (
                <Text block key={overload.component_role}>
                  {overload.component_role.replace(/_/g, ' ')}: {overload.count}
                  {overload.status_codes.length > 0 ? ` (HTTP ${overload.status_codes.join(', ')})` : ''}
                </Text>
              ))}</AccordionPanel>
            </AccordionItem></Accordion>}
          </MessageBarBody>
        </MessageBar>}
        {active && <section className={styles.progressSurface} aria-label="Scan progress">
          <div className={styles.progressPrimary}>
            <Text weight="semibold">{run.status === 'QUEUED' ? `Queued${run.queue_position ? ` · Position ${run.queue_position}` : ''}` : `${overall.completed}${overall.planned === null ? '' : ` of ${overall.planned}`} attacks complete`}</Text>
            <ProgressBar value={overall.planned ? overall.completed / overall.planned : undefined} aria-label="Scan progress" />
          </div>
          <ScenarioRunTiming run={run} overall={overall} />
        </section>}
        {state.activeConversations.length > 0 && <section className={styles.section} aria-labelledby="live-conversations-heading">
          <Text as="h2" id="live-conversations-heading" size={500} weight="semibold">Executing</Text>
          {state.activeConversations.map((conversation) => (
            <div key={conversation.conversation_id} className={styles.liveConversation}>
              <Button appearance="transparent" onClick={(event) => {
                detailsTrigger.current = event.currentTarget
                navigate(scenarioRunAttackRoutePath(scenarioResultId, conversation.attack_result_id), { state: location.state })
              }}>{conversation.atomic_attack_name}</Button>
              <Text className={styles.preview}>{conversation.objective}</Text>
              <Link to={scenarioConversationRoutePath(scenarioResultId, conversation.conversation_id)}>Watch live conversation</Link>
            </div>
          ))}
        </section>}
        {savedConversations.length > 0 && <section className={styles.section} aria-label="Saved conversations">
          <Text as="h2" size={500} weight="semibold">Saved conversations</Text>
          {savedConversations.map((conversation) => <div key={conversation.attack_result_id} className={styles.liveConversation}>
            <Text>{conversation.atomic_attack_name}</Text>
            <Text className={styles.preview}>{conversation.objective}</Text>
            <Link to={scenarioConversationRoutePath(scenarioResultId, conversation.conversation_id)}>Open saved conversation</Link>
          </div>)}
        </section>}
        <section className={styles.section} aria-labelledby="atomic-groups-heading">
          <div className={styles.sectionHeading}>
            <div className={styles.objectivesHeading}>
              <Text as="h2" id="atomic-groups-heading" size={500} weight="semibold">
                Atomic attack groups
              </Text>
              <Button
                appearance="subtle"
                className={styles.touchTarget}
                icon={atomicGroupsExpanded ? <ChevronDownRegular /> : <ChevronRightRegular />}
                aria-expanded={atomicGroupsExpanded}
                aria-controls="atomic-groups-panel"
                aria-label={`${atomicGroupsExpanded ? 'Collapse' : 'Expand'} atomic attack groups`}
                data-testid="toggle-atomic-groups-btn"
                ref={atomicGroupsToggleRef}
                onClick={() => setAtomicGroupsExpandedChoice(!atomicGroupsExpanded)}
              >
              </Button>
            </div>
            <Text className={styles.sectionHint}>
              {`${displayGroups.length.toLocaleString()} group${displayGroups.length === 1 ? '' : 's'}, ${executionCount.toLocaleString()} ${producerCounts ? '' : 'loaded '}execution${executionCount === 1 ? '' : 's'}`}
            </Text>
          </div>
          <div id="atomic-groups-panel" hidden={!atomicGroupsExpanded}>
            {atomicGroupsExpanded && (
              <ScenarioResultHierarchy hierarchy={hierarchy} runStatus={run.status}>
                {displayGroups.length === 0 ? (
                  <EmptyState text="No atomic attack groups have been persisted yet." />
                ) : (
                  <div className={styles.displayGroupList}>
                    {displayGroups.map((group) => {
                      const attempts = (group.atomic_group_ids ?? [])
                        .flatMap((atomicGroupId) => attemptsByGroupId.get(atomicGroupId) ?? [])
                      const defaultExpanded = displayGroups.length <= AUTO_EXPAND_GROUP_LIMIT
                      const expanded = expandedGroupChoices.get(group.id) ?? defaultExpanded
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
                              onClick={() => toggleDisplayGroup(group.id, defaultExpanded)}
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
                                scenarioResultId={scenarioResultId}
                                label={`${group.display_group} attack results`}
                                attempts={attempts.slice(0, MAX_VISIBLE_ATTEMPTS_PER_GROUP)}
                                totalAttempts={attempts.length}
                                roots={hierarchy.roots.groups
                                  .filter((savedGroup) => group.atomic_group_ids.includes(savedGroup.atomic_group_id))
                                  .flatMap((savedGroup) => savedGroup.roots)}
                                hierarchy={hierarchy}
                                seedObjectives={seedObjectives}
                                onOpenDetails={openAttemptDetails}
                                showTimestamp={false}
                                showConversationLink
                                booleanOutcome
                              />
                            </div>
                          )}
                        </article>
                      )
                    })}
                  </div>
                )}
              </ScenarioResultHierarchy>
            )}
          </div>
        </section>
        <section className={styles.section} aria-labelledby="objectives-heading">
          <Text as="h2" id="objectives-heading" size={500} weight="semibold">Datasets &amp; objectives</Text>
          <div className={styles.tableScroll}><Table size="small" aria-label="Datasets and objectives">
            <TableHeader><TableRow><TableHeaderCell>Dataset</TableHeaderCell><TableHeaderCell>Objective</TableHeaderCell><TableHeaderCell>Attack success</TableHeaderCell><TableHeaderCell>Completed</TableHeaderCell></TableRow></TableHeader>
            <TableBody>{summary.seed_groups.map((seed) => {
              const planSeed = planSeeds.get(seed.id)
              return <TableRow key={seed.id}>
                <TableCell>{datasetsBySeed.get(seed.id) || 'Not recorded'}</TableCell>
                <TableCell><Button appearance="transparent" className={styles.objectiveLinkButton} onClick={(event) => {
                  objectiveTrigger.current = event.currentTarget
                  setObjective(planSeed ?? { id: seed.id, objective_sha256: '', objective: seed.objective ?? 'Not recorded', prompts: [] })
                }}>
                  {objectivePreview(seed.objective ?? null, seed.id)}
                </Button></TableCell>
                <TableCell>{formatProgressSuccess(seed)}</TableCell>
                <TableCell>{seed.completed}{seed.planned === null ? '' : ` / ${seed.planned}`}</TableCell>
              </TableRow>
            })}</TableBody>
          </Table></div>
        </section>
        <section className={styles.section} aria-labelledby="configuration-heading">
          <Text as="h2" id="configuration-heading" size={500} weight="semibold">Scenario configuration</Text>
          {run.scenario_identity ? <Table size="small" aria-label="Scenario configuration"><TableBody>
            <RegistryIdentityRow name={run.scenario_registry_name ?? run.scenario_name} identity={run.scenario_identity}
              columns={1} expanded={identityExpanded} onToggle={() => { setIdentityExpanded((value: boolean) => !value) }} />
          </TableBody></Table> : <Text className={styles.sectionHint}>No saved scenario identity.</Text>}
        </section>
      </div>
      <Dialog open={cancelOpen} onOpenChange={(_, data) => { if (!cancelling) setCancelOpen(data.open) }}>
        <DialogSurface><DialogBody><DialogTitle>Cancel scan?</DialogTitle><DialogContent className={styles.dialogContent}>
          <Text>Stop remaining attacks. Saved conversations and results stay available.</Text>
          {cancelError && <MessageBar intent="error"><MessageBarBody>{cancelError}</MessageBarBody></MessageBar>}
        </DialogContent><DialogActions><Button disabled={cancelling} onClick={() => { setCancelOpen(false) }}>Keep running</Button>
          <Button appearance="primary" disabled={cancelling} onClick={() => { void cancel() }}>{cancelling ? 'Cancelling...' : 'Cancel scan'}</Button>
        </DialogActions></DialogBody></DialogSurface>
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
            <DialogTitle
              action={(
                <Button
                  appearance="subtle"
                  icon={<DismissRegular />}
                  aria-label="Close attack details"
                  onClick={closeAttemptDetails}
                />
              )}
            >
              {selectedAttempt?.atomic_attack_name || 'Attack attempt'}
            </DialogTitle>
            {selectedAttempt && (
              <DialogContent className={styles.attackDetailDialogContent}>
                <AttackAttemptDetails
                  attempt={selectedAttempt}
                  objective={
                    (hierarchySelection?.attack_result_id === selectedAttempt.attack_result_id
                      ? hierarchySelection.objective
                      : null) ??
                    ('objective' in selectedAttempt ? selectedAttempt.objective : null) ??
                    (selectedAttempt.seed_group_id ? seedObjectives.get(selectedAttempt.seed_group_id) : null) ??
                    null
                  }
                  atomicGroup={selectedAttemptGroup}
                  techniqueDetails={selectedActive?.technique_details
                    ?? (selectedAtomicGroupId ? progressAtomicGroups.get(selectedAtomicGroupId)?.technique_details : null)}
                  objectiveScorer={objectiveScorer}
                  conversationPath={selectedActive
                    ? scenarioConversationRoutePath(scenarioResultId, selectedActive.conversation_id)
                    : selectedAttempt.conversation_id
                      ? attackConversationRoutePath(selectedAttempt.attack_result_id, selectedAttempt.conversation_id, scenarioResultId)
                      : null}
                  conversationState={location.state}
                />
              </DialogContent>
            )}
            <DialogActions>
              <Button appearance="primary" onClick={closeAttemptDetails}>Close</Button>
            </DialogActions>
          </DialogBody>
        </DialogSurface>
      </Dialog>
      <ObjectiveDetailsDialog objective={objective} onClose={() => { setObjective(null) }}
        attempts={objective ? attemptsBySeed.get(objective.id) ?? [] : []}
        onOpenAttempt={openDetails} scenarioResultId={scenarioResultId} />
    </main>
  )
}

function EmptyState({ text }: { readonly text: string }) {
  return <Text className={useScenarioRunPageStyles().sectionHint}>{text}</Text>
}

function DisplayGroupMetric({ label, value }: { readonly label: string; readonly value: string }) {
  return (
    <div>
      <Text size={200}>{label}</Text>
      <Text weight="semibold">{value}</Text>
    </div>
  )
}

function formatCompletion(completed: number, planned: number | null): string {
  return planned === null ? `${completed}/total unavailable` : `${completed}/${planned}`
}

function formatSuccess(succeeded: number, completed: number, percentage: number | null): string {
  if (percentage === null) return completed === 0 ? 'Unavailable' : `${succeeded}/${completed}`
  return `${percentage}%`
}
