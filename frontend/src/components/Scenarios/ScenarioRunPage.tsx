import { useMemo, useRef, useState } from 'react'

import {
  Accordion, AccordionHeader, AccordionItem, AccordionPanel, Button, MessageBar, MessageBarBody, Spinner, Text,
} from '@fluentui/react-components'
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
import AttackDetailsDialog from './AttackDetailsDialog'
import PairedOutcomes from './PairedOutcomes'
import ScenarioRunConfigurationSection from './ScenarioRunConfigurationSection'
import ScenarioRunConversations from './ScenarioRunConversations'
import { CancelScanDialog, ObjectiveDetailsDialog } from './ScenarioRunDialogs'
import ScenarioRunHeader from './ScenarioRunHeader'
import ScenarioRunNotices from './ScenarioRunNotices'
import ScenarioRunObjectivesSection from './ScenarioRunObjectivesSection'
import ScenarioRunOverview from './ScenarioRunOverview'
import { useScenarioRunPageStyles } from './ScenarioRunPage.styles'
import ScenarioRunProgress from './ScenarioRunProgress'
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
  const queue = useScenarioQueue()
  const controls = useScenarioRunControls({
    scenarioResultId, applyRunSummary, retry, refreshQueue: queue.retry, runError: state.run?.error,
  })
  const overviewRef = useRef<HTMLElement | null>(null)
  const groupsToggleRef = useRef<HTMLButtonElement | null>(null)
  const objectiveTrigger = useRef<HTMLElement | null>(null)
  const [objective, setObjective] = useState<ScenarioRunPlanSeedGroup | null>(null)
  const [pairedExpanded, setPairedExpanded] = useState(false)
  const attackDetails = useScenarioRunAttackDetails({
    scenarioResultId,
    attackResultId,
    results: state.results,
    activeConversations: state.activeConversations,
    fallbackFocus: () => groupsToggleRef.current ?? overviewRef.current,
  })
  const seedObjectives = useMemo(
    () => new Map(state.plan?.seed_groups.map((seed) => [seed.id, seed.objective]) ?? []),
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

  const closeObjective = (): void => {
    setObjective(null)
    requestAnimationFrame(() => {
      const trigger = objectiveTrigger.current
      if (trigger?.isConnected) trigger.focus()
    })
  }

  const backLinkElement = <Link to={backLink.path} className={styles.backLink}><ArrowLeftRegular /> {backLink.label}</Link>

  if (!state.run || !state.summary) return (
    <main className={styles.root} data-testid="scenario-run-page"><div className={styles.content}>
      {backLinkElement}
      {state.loadStatus === 'loading' ? <Spinner label="Loading scan..." /> : (
        <MessageBar intent="error"><MessageBarBody>{state.error ?? 'Scan not found.'} <Button onClick={retry}>Retry</Button></MessageBarBody></MessageBar>
      )}
    </div></main>
  )

  const run = state.run
  const summary = state.summary
  const active = !isTerminalRunState(run.status)
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
        <ScenarioRunHeader run={run} active={active} controls={controls} headerRef={overviewRef} />
        <ScenarioRunOverview run={run} overall={summary.overall} />
        <ScenarioRunNotices run={run} summary={summary} state={state} onRetry={retry} onOpenAttempt={attackDetails.open} />
        {active && <ScenarioRunProgress run={run} overall={summary.overall} />}
        <ScenarioRunConversations
          scenarioResultId={scenarioResultId}
          results={state.results}
          activeConversations={state.activeConversations}
          recordedConversations={state.recordedConversations}
          onOpenAttempt={attackDetails.open}
        />
        <AtomicAttackGroupsSection
          scenarioResultId={scenarioResultId}
          runStatus={run.status}
          summary={summary}
          results={state.results}
          hierarchy={hierarchy}
          seedObjectives={seedObjectives}
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
        {run.status === 'COMPLETED' && (
          <Accordion collapsible onToggle={(_, data) => { setPairedExpanded(data.openItems.includes('paired')) }}>
            <AccordionItem value="paired">
              <AccordionHeader>Compare baseline and attack</AccordionHeader>
              <AccordionPanel>
                {pairedExpanded && (
                  <PairedOutcomes
                    scenarioResultId={scenarioResultId}
                    status={run.status}
                    plan={state.planComplete ? state.plan : null}
                  />
                )}
              </AccordionPanel>
            </AccordionItem>
          </Accordion>
        )}
        <ScenarioRunConfigurationSection run={run} />
      </div>
      <CancelScanDialog open={controls.cancelOpen} cancelling={controls.cancelling} error={controls.cancelError}
        onOpenChange={controls.setCancelOpen} onConfirm={controls.cancel} />
      <AttackDetailsDialog
        scenarioResultId={scenarioResultId}
        attempt={attackDetails.selectedAttempt}
        hierarchySelection={attackDetails.hierarchySelection}
        plan={state.plan}
        summary={summary}
        activeConversations={state.activeConversations}
        seedObjectives={seedObjectives}
        conversationState={location.state}
        onClose={attackDetails.close}
      />
      <ObjectiveDetailsDialog objective={objective} onClose={closeObjective}
        attempts={objective ? attemptsBySeed.get(objective.id) ?? [] : []}
        onOpenAttempt={openObjectiveAttempt} scenarioResultId={scenarioResultId} />
    </main>
  )
}
