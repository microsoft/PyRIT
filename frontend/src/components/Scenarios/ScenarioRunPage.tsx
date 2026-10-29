import { useEffect, useMemo, useRef, useState } from 'react'

import {
  Accordion, AccordionHeader, AccordionItem, AccordionPanel, Badge, Button,
  Dialog, DialogActions, DialogBody, DialogContent, DialogSurface, DialogTitle,
  MessageBar, MessageBarBody, ProgressBar, Spinner, Table, TableBody, TableCell,
  TableHeader, TableHeaderCell, TableRow, Text,
} from '@fluentui/react-components'
import { ArrowLeftRegular, ArrowClockwiseRegular, ChevronDownRegular, ChevronRightRegular, DismissRegular, PlayRegular, StopRegular } from '@fluentui/react-icons'
import { Link, useLocation, useNavigate, useParams } from 'react-router'

import AttackAttemptDetails from '@/components/AttackResults/AttackAttemptDetails'
import { formatDuration, objectivePreview } from '@/components/AttackResults/attackAttemptFormatting'
import RegistryIdentityRow from '@/components/Registry/RegistryIdentityRow'
import { suiteExecutionRoutePath } from '@/components/ScenarioSuites/suiteRoutes'
import { useScenarioRunProgress } from '@/hooks/useScenarioRunProgress'
import { useScenarioRunResume } from '@/hooks/useScenarioRunResume'
import { scenariosApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { ScenarioComponentIdentity, ScenarioProgressResult, ScenarioRunPlanSeedGroup, ScenarioRunState } from '@/types'
import { attackConversationRoutePath, routerPathParamValue, scenarioConversationRoutePath, scenarioRerunRoutePath, scenarioRunAttackRoutePath, scenarioRunRoutePath } from '@/utils/routeParams'
import { getElapsedMilliseconds, getEtaMilliseconds, isTerminalRunState } from '@/utils/scenarioRunProgress'
import { formatAnalyticsPercent } from '@/utils/attackAnalytics'

import AttackExecutionTable from './AttackExecutionTable'
import { ObjectiveDetailsDialog } from './ScenarioRunDialogs'
import { useScenarioRunPageStyles } from './ScenarioRunPage.styles'

const CLOCK_INTERVAL_MS = 1_000
const MAX_VISIBLE_ATTEMPTS = 100
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
    scenarioHistorySearch?: string; suiteExecutionId?: string; suiteName?: string
  } | null
  const backPath = navigationState?.suiteExecutionId ? suiteExecutionRoutePath(navigationState.suiteExecutionId)
    : `/scanner/history${navigationState?.scenarioHistorySearch ?? ''}`
  const backLabel = navigationState?.suiteExecutionId ? `Back to ${navigationState.suiteName ?? 'suite execution'}` : 'Back to scans'
  const { state, retry, applyRunSummary } = useScenarioRunProgress(scenarioResultId)
  const [now, setNow] = useState(Date.now)
  const [expanded, setExpanded] = useState<Set<string> | null>(null)
  const [identityExpanded, setIdentityExpanded] = useState(false)
  const [objective, setObjective] = useState<ScenarioRunPlanSeedGroup | null>(null)
  const [cancelOpen, setCancelOpen] = useState(false)
  const [cancelling, setCancelling] = useState(false)
  const [cancelError, setCancelError] = useState<string | null>(null)
  const [rerunPending, setRerunPending] = useState(false)
  const [rerunError, setRerunError] = useState<string | null>(null)
  const detailsTrigger = useRef<HTMLElement | null>(null)
  const active = Boolean(state.run && !isTerminalRunState(state.run.status))
  const resume = useScenarioRunResume({
    onResumed: applyRunSummary,
    onRefresh: (succeeded: boolean): void => { if (!succeeded) retry() },
  })
  useEffect(() => {
    if (!active) return
    const timer = setInterval(() => { setNow(Date.now()) }, CLOCK_INTERVAL_MS)
    return () => { clearInterval(timer) }
  }, [active])
  const seedObjectives = useMemo(() => new Map(state.plan?.seed_groups.map((seed) => [seed.id, seed.objective]) ?? []), [state.plan])
  const attemptsByGroup = useMemo(() => {
    const groups = new Map<string, ScenarioProgressResult[]>()
    for (const attempt of [...state.results].reverse()) {
      const attempts = groups.get(attempt.atomic_group_id) ?? []
      attempts.push(attempt)
      groups.set(attempt.atomic_group_id, attempts)
    }
    return groups
  }, [state.results])
  const selected = state.results.find((attempt) => attempt.attack_result_id === attackResultId)
  const selectedActive = state.activeConversations.find((conversation) => conversation.attack_result_id === attackResultId)
  const selectedGroup = state.plan?.atomic_groups.find((group) => group.id === selected?.atomic_group_id)
  const techniqueDetails = selectedActive?.technique_details
    ?? state.summary?.atomic_groups.find((group) => group.id === selected?.atomic_group_id)?.technique_details
  const runPath = scenarioRunRoutePath(scenarioResultId)

  function closeDetails(): void {
    navigate(runPath, { replace: true, state: location.state })
    requestAnimationFrame(() => { detailsTrigger.current?.focus() })
  }

  function openDetails(attempt: ScenarioProgressResult, trigger: HTMLElement): void {
    setObjective(null)
    detailsTrigger.current = trigger
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
  const defaultExpanded = groups.find((group) => (group.atomic_group_ids ?? []).some((id: string) => attemptsByGroup.has(id)))?.id ?? groups[0]?.id
  const elapsed = run.started_at ? formatDuration(getElapsedMilliseconds(run, now))
    : run.status === 'CREATED' || run.status === 'QUEUED' ? 'Not started' : 'Not recorded'
  const eta = getEtaMilliseconds(run, overall, now)
  const selectedAttempt = selected ?? selectedActive
  const firstError = state.results.find((attempt) => attempt.outcome === 'error')
  const controlError = rerunError ?? resume.error
    ?? (resume.executionError !== run.error ? resume.executionError : null)

  return (
    <main className={styles.root} data-testid="scenario-run-page">
      <div className={styles.content}>
        <Link to={backPath} className={styles.backLink}><ArrowLeftRegular /> {backLabel}</Link>
        <header className={styles.header}>
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
          <div className={styles.metadataItem}><Text size={200} className={styles.metadataLabel}>Timing</Text><Text weight="semibold">{elapsed}{active && run.started_at ? ' elapsed' : ''}</Text></div>
          <div className={styles.metadataItem}><Text size={200} className={styles.metadataLabel}>Attack success</Text>
            <Text weight="semibold">{outcomes ? outcomes.success_rate_decided === null ? 'Undetermined' : formatAnalyticsPercent(outcomes.success_rate_decided)
              : overall.success_percentage === null ? 'Undetermined' : `${overall.success_percentage}%`}</Text>
            <Text size={200} className={styles.sectionHint}>{outcomes ? `${outcomes.successes} / ${outcomes.total_decided} decided` : `${overall.succeeded} / ${overall.completed} complete`}</Text>
          </div>
          <div className={styles.metadataItem}><Text size={200} className={styles.metadataLabel}>Errors / retries</Text><Text weight="semibold">{overall.errors} / {overall.retries}</Text></div>
          <div className={styles.metadataItem}><Text size={200} className={styles.metadataLabel}>Labels</Text>
            <div className={styles.badgeList}>{Object.entries(run.labels ?? {}).map(([key, value]) => <Badge key={key} appearance="outline" className={styles.labelBadge}>{key}: {value}</Badge>)}</div>
          </div>
        </section>
        {(run.status === 'FAILED' || run.error || overall.errors > 0) && <MessageBar intent={run.status === 'FAILED' ? 'error' : 'warning'}>
          <MessageBarBody>{run.status === 'FAILED' ? 'Scan failed. Resume to continue remaining attacks.'
            : `${overall.errors} attack${overall.errors === 1 ? '' : 's'} ended with an error.`}
            {firstError && <Button appearance="transparent" onClick={(event) => { openDetails(firstError, event.currentTarget) }}>View attack</Button>}
            {run.error && <Accordion collapsible><AccordionItem value="error">
              <AccordionHeader>Error details</AccordionHeader>
              <AccordionPanel><Text className={styles.objective}>{run.error_type ? `${run.error_type}: ` : ''}{run.error}</Text></AccordionPanel>
            </AccordionItem></Accordion>}
          </MessageBarBody>
        </MessageBar>}
        {state.stale && <MessageBar intent="warning"><MessageBarBody>Live updates paused. {state.error} <Button onClick={retry}>Retry</Button></MessageBarBody></MessageBar>}
        {active && <section className={styles.progressSurface} aria-label="Scan progress">
          <div className={styles.progressPrimary}>
            <Text weight="semibold">{run.status === 'QUEUED' ? `Queued${run.queue_position ? ` · Position ${run.queue_position}` : ''}` : `${overall.completed}${overall.planned === null ? '' : ` of ${overall.planned}`} attacks complete`}</Text>
            <ProgressBar value={overall.planned ? overall.completed / overall.planned : undefined} aria-label="Scan progress" />
          </div>
          {eta !== null && <Text className={styles.sectionHint}>About {formatDuration(eta)} left</Text>}
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
        <section className={styles.section} aria-labelledby="atomic-groups-heading">
          <div className={styles.sectionHeading}><Text as="h2" id="atomic-groups-heading" size={500} weight="semibold">Atomic attack groups</Text><Text size={200} className={styles.sectionHint}>{groups.length} groups</Text></div>
          <div className={styles.displayGroupList}>{groups.map((group) => {
            const attempts = (group.atomic_group_ids ?? []).flatMap((id: string) => attemptsByGroup.get(id) ?? [])
            const isExpanded = expanded?.has(group.id) ?? group.id === defaultExpanded
            return <article key={group.id} className={styles.displayGroup}>
              <div className={styles.compactGroupSummary}>
                <Button appearance="subtle" className={styles.expandButton} icon={isExpanded ? <ChevronDownRegular /> : <ChevronRightRegular />}
                  aria-label={`${isExpanded ? 'Collapse' : 'Expand'} ${group.display_group}`} aria-expanded={isExpanded}
                  onClick={() => { setExpanded((current: Set<string> | null) => { const next = new Set(current ?? (defaultExpanded ? [defaultExpanded] : [])); if (next.has(group.id)) next.delete(group.id); else next.add(group.id); return next }) }} />
                <Text weight="semibold">{group.display_group}</Text>
                <Text size={200} className={styles.sectionHint}>{group.completed}{group.planned === null ? '' : ` / ${group.planned}`} complete</Text>
              </div>
              {isExpanded && <AttackExecutionTable attempts={attempts.slice(0, MAX_VISIBLE_ATTEMPTS)} totalAttempts={attempts.length}
                seedObjectives={seedObjectives} onOpenDetails={openDetails} scenarioResultId={scenarioResultId} />}
            </article>
          })}</div>
          {groups.length === 0 && <Text className={styles.sectionHint}>Attack groups will appear when the plan is ready.</Text>}
        </section>
        <section className={styles.section} aria-labelledby="objectives-heading">
          <Text as="h2" id="objectives-heading" size={500} weight="semibold">Datasets &amp; objectives</Text>
          <div className={styles.tableScroll}><Table size="small" aria-label="Datasets and objectives">
            <TableHeader><TableRow><TableHeaderCell>Dataset</TableHeaderCell><TableHeaderCell>Objective</TableHeaderCell><TableHeaderCell>Completed</TableHeaderCell></TableRow></TableHeader>
            <TableBody>{summary.seed_groups.map((seed) => {
              const planSeed = state.plan?.seed_groups.find((candidate) => candidate.id === seed.id)
              const identities = [
                ...state.results.filter((attempt) => attempt.seed_group_id === seed.id).flatMap((attempt) => attempt.seed_identifiers ?? []),
                ...state.activeConversations.filter((attempt) => attempt.seed_group_id === seed.id).flatMap((attempt) => attempt.seed_identifiers),
              ]
              return <TableRow key={seed.id}>
                <TableCell>{seedDatasetNames(identities) || 'Not recorded'}</TableCell>
                <TableCell><Button appearance="transparent" className={styles.objectiveLinkButton} onClick={() => { setObjective(planSeed ?? { id: seed.id, objective_sha256: '', objective: seed.objective ?? 'Not recorded', prompts: [] }) }}>
                  {objectivePreview(seed.objective ?? null, seed.id)}
                </Button></TableCell>
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
      <Dialog open={Boolean(selectedAttempt)} onOpenChange={(_, data) => { if (!data.open) closeDetails() }}>
        <DialogSurface className={styles.attackDetailDialogSurface}><DialogBody className={styles.attackDetailDialogBody}>
          <DialogTitle action={<Button appearance="subtle" icon={<DismissRegular />} aria-label="Close attack details" onClick={closeDetails} />}>{selectedAttempt?.atomic_attack_name}</DialogTitle>
          <DialogContent className={styles.attackDetailDialogContent}>{selectedAttempt && <AttackAttemptDetails attempt={selectedAttempt}
            objective={selectedActive?.objective ?? seedObjectives.get(selected?.seed_group_id ?? '')}
            atomicGroup={selectedGroup} techniqueDetails={techniqueDetails} objectiveScorer={summary.objective_scorer}
            conversationPath={selectedActive ? scenarioConversationRoutePath(scenarioResultId, selectedActive.conversation_id)
              : attackConversationRoutePath(selectedAttempt.attack_result_id, selectedAttempt.conversation_id, scenarioResultId)} />}</DialogContent>
        </DialogBody></DialogSurface>
      </Dialog>
      <ObjectiveDetailsDialog objective={objective} onClose={() => { setObjective(null) }}
        attempts={state.results.filter((attempt) => attempt.seed_group_id === objective?.id)}
        onOpenAttempt={openDetails} scenarioResultId={scenarioResultId} />
    </main>
  )
}
