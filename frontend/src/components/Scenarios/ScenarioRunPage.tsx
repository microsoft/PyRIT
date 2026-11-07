import { useEffect, useMemo, useRef, useState } from 'react'

import { Button, MessageBar, MessageBarBody, Text } from '@fluentui/react-components'
import { ArrowLeftRegular } from '@fluentui/react-icons'
import { Link, useLocation, useParams } from 'react-router'

import { useScenarioQueue } from '@/hooks/useScenarioQueue'
import { useScenarioResultHierarchy } from '@/hooks/useScenarioResultHierarchy'
import { useScenarioRunAttackDetails } from '@/hooks/useScenarioRunAttackDetails'
import { useScenarioRunControls } from '@/hooks/useScenarioRunControls'
import { useScenarioRunProgress } from '@/hooks/useScenarioRunProgress'
import type { ScenarioProgressResult, ScenarioRunPlanSeedGroup } from '@/types'
import { routerPathParamValue } from '@/utils/routeParams'
import { isTerminalRunState } from '@/utils/scenarioRunProgress'

import AtomicAttackGroupsSection from './AtomicAttackGroupsSection'
import AttackActivity from './AttackActivity'
import AttackDetailsDialog from './AttackDetailsDialog'
import ScenarioAttackDetails, { type ScenarioRunDetailsContext } from './ScenarioAttackDetails'
import ScenarioRunConfigurationSection from './ScenarioRunConfigurationSection'
import { CancelScanDialog, ObjectiveDetailsDialog } from './ScenarioRunDialogs'
import ScenarioRunHeader from './ScenarioRunHeader'
import ScenarioRunNotices from './ScenarioRunNotices'
import ScenarioRunObjectivesSection from './ScenarioRunObjectivesSection'
import { useScenarioRunPageStyles } from './ScenarioRunPage.styles'
import ScenarioRunProgress from './ScenarioRunProgress'
import ScenarioRunSavedConversations from './ScenarioRunSavedConversations'
import { interruptedConversations, liveAttacks, singleAtomicGroupId, singleAttackAttempt } from './scenarioRunLayout'
import { scenarioRunBackLink, type ScenarioRunNavigationState } from './scenarioRunNavigation'

export default function ScenarioRunPage() {
  const { scenarioResultId, attackResultId } = useParams<{ scenarioResultId: string; attackResultId?: string }>()
  return <ScenarioRunContent key={scenarioResultId} scenarioResultId={routerPathParamValue(scenarioResultId)}
    attackResultId={attackResultId ? routerPathParamValue(attackResultId) : null} />
}

interface ScenarioRunContentProps {
  readonly scenarioResultId: string
  readonly attackResultId: string | null
}

function ScenarioRunContent({ scenarioResultId, attackResultId }: ScenarioRunContentProps) {
  const styles = useScenarioRunPageStyles()
  const location = useLocation()
  const backLink = scenarioRunBackLink(location.state as ScenarioRunNavigationState | null)
  const { state, retry, applyRunSummary } = useScenarioRunProgress(scenarioResultId)
  const hierarchy = useScenarioResultHierarchy(scenarioResultId, state.run !== null && state.summary !== null)
  const { roots: { status: hierarchyStatus, runStatus: hierarchyRunStatus }, reloadRoots } = hierarchy
  const runStatus = state.run?.status
  // Saved results load once; reload them when the run finishes so its final results replace the running snapshot.
  useEffect(() => {
    if (runStatus && isTerminalRunState(runStatus) && hierarchyStatus === 'ready'
      && hierarchyRunStatus !== null && runStatus !== hierarchyRunStatus) reloadRoots()
  }, [runStatus, hierarchyStatus, hierarchyRunStatus, reloadRoots])
  const queue = useScenarioQueue()
  const controls = useScenarioRunControls({
    scenarioResultId, applyRunSummary, retry, refreshQueue: queue.retry, runError: state.run?.error,
  })
  const headerRef = useRef<HTMLElement | null>(null)
  const groupsToggleRef = useRef<HTMLButtonElement | null>(null)
  const objectiveTrigger = useRef<HTMLElement | null>(null)
  const [objective, setObjective] = useState<ScenarioRunPlanSeedGroup | null>(null)
  const attackDetails = useScenarioRunAttackDetails({
    scenarioResultId,
    attackResultId,
    results: state.results,
    activeConversations: state.activeConversations,
    fallbackFocus: () => groupsToggleRef.current ?? headerRef.current,
  })
  const seedObjectives = useMemo(
    () => new Map(state.plan?.seed_groups.map((seed) => [seed.id, seed.objective]) ?? []),
    [state.plan],
  )
  const techniqueNames = useMemo(
    () => new Map(state.plan?.atomic_groups.map((group) => [group.id, group.technique_name ?? group.atomic_attack_name]) ?? []),
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
  const runningAttacks = useMemo(() => liveAttacks(state.activeConversations), [state.activeConversations])
  const savedConversations = useMemo(() => interruptedConversations(state), [state])

  const backLinkElement = <Link to={backLink.path} className={styles.backLink}><ArrowLeftRegular /> {backLink.label}</Link>

  if (!state.run || !state.summary) return (
    <main className={styles.root} data-testid="scenario-run-page"><div className={styles.content}>
      {backLinkElement}
      {state.loadStatus === 'loading' ? <AttackActivity loading /> : (
        <MessageBar intent="error"><MessageBarBody>{state.error ?? 'Scan not found.'} <Button onClick={retry}>Retry</Button></MessageBarBody></MessageBar>
      )}
    </div></main>
  )

  const run = state.run
  const summary = state.summary
  const active = !isTerminalRunState(run.status)
  const detailsContext: ScenarioRunDetailsContext = {
    scenarioResultId,
    operationId: run.operation_id,
    plan: state.plan,
    summary,
    activeConversations: state.activeConversations,
    hierarchyGroups: hierarchy.roots.groups,
    seedObjectives,
    conversationState: location.state,
  }
  // A run of one atomic attack shows that attack directly, from preparing through completion.
  const singleGroupId = singleAtomicGroupId(summary, hierarchy.roots.groups, state.results.length)
  const singleAttempt = singleGroupId
    ? singleAttackAttempt(singleGroupId, state.results, hierarchy.roots.groups, state.activeConversations)
    : null
  const openObjectiveAttempt = (attempt: ScenarioProgressResult): void => {
    attackDetails.open(attempt, objectiveTrigger.current)
    setObjective(null)
  }

  return (
    <main className={styles.root} data-testid="scenario-run-page">
      <div className={styles.content}>
        {backLinkElement}
        <Text role="status" aria-label="Run status updates" aria-live="polite" className={styles.liveStatus}>
          {!active ? `${run.scenario_registry_name ?? run.scenario_name}: ${run.status.toLowerCase()}.` : ''}
        </Text>
        <ScenarioRunHeader run={run} active={active} controls={controls} headerRef={headerRef} />
        <ScenarioRunNotices run={run} summary={summary} state={state} onRetry={retry} onOpenAttempt={attackDetails.open}
          inlineAttempt={singleAttempt && 'outcome' in singleAttempt ? singleAttempt : null} />
        <ScenarioRunProgress scenarioResultId={scenarioResultId} run={run} overall={summary.overall}
          liveAttacks={singleGroupId ? undefined : runningAttacks} conversationState={location.state}
          onOpenAttack={attackDetails.open} />
        <ScenarioRunSavedConversations scenarioResultId={scenarioResultId} conversations={savedConversations}
          conversationState={location.state} />
        {singleGroupId ? (
          <section className={styles.section} aria-label="Attack details">
            <ScenarioAttackDetails context={detailsContext} attempt={singleAttempt} atomicGroupId={singleGroupId}
              preparing={active} framed />
          </section>
        ) : <>
          <AtomicAttackGroupsSection
            scenarioResultId={scenarioResultId}
            runStatus={run.status}
            summary={summary}
            results={state.results}
            hierarchy={hierarchy}
            seedObjectives={seedObjectives}
            techniqueNames={techniqueNames}
            onOpenDetails={attackDetails.open}
            toggleRef={groupsToggleRef}
          />
          <ScenarioRunObjectivesSection
            summary={summary}
            plan={state.plan}
            results={state.results}
            activeConversations={state.activeConversations}
            onOpenObjective={(seed: ScenarioRunPlanSeedGroup, trigger: HTMLElement) => {
              objectiveTrigger.current = trigger
              setObjective(seed)
            }}
          />
          <ScenarioRunConfigurationSection run={run} />
        </>}
      </div>
      <CancelScanDialog open={controls.cancelOpen} cancelling={controls.cancelling} error={controls.cancelError}
        onOpenChange={controls.setCancelOpen} onConfirm={controls.cancel} />
      <AttackDetailsDialog
        run={run}
        context={detailsContext}
        attempt={attackDetails.selectedAttempt}
        hierarchySelection={attackDetails.hierarchySelection}
        onClose={attackDetails.close}
      />
      <ObjectiveDetailsDialog objective={objective} onClose={() => { setObjective(null) }}
        attempts={objective ? attemptsBySeed.get(objective.id) ?? [] : []} techniqueNames={techniqueNames}
        onOpenAttempt={openObjectiveAttempt} scenarioResultId={scenarioResultId} />
    </main>
  )
}
