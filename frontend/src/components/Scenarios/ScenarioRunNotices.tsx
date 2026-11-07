import { Accordion, AccordionHeader, AccordionItem, AccordionPanel, Button, MessageBar, MessageBarBody, Text } from '@fluentui/react-components'

import type {
  ScenarioHierarchyNode,
  ScenarioOverloadSummary,
  ScenarioProgressHeader,
  ScenarioProgressResult,
  ScenarioProgressSummary,
} from '@/types'
import type { ScenarioRunProgressState } from '@/utils/scenarioRunProgress'

import { useScenarioRunPageStyles } from './ScenarioRunPage.styles'

interface ScenarioRunNoticesProps {
  readonly run: ScenarioProgressHeader
  readonly summary: ScenarioProgressSummary
  readonly state: ScenarioRunProgressState
  /** The attack shown inline on the page; its own error is already visible there. */
  readonly inlineAttempt?: ScenarioProgressResult | ScenarioHierarchyNode | null
  readonly onRetry: () => void
  readonly onOpenAttempt: (attempt: ScenarioProgressResult, trigger: HTMLElement) => void
}

/** Run failures, paused live updates, and conditions that limit how far the totals can be trusted. */
export default function ScenarioRunNotices({ run, summary, state, inlineAttempt, onRetry, onOpenAttempt }: ScenarioRunNoticesProps) {
  const styles = useScenarioRunPageStyles()
  const overall = summary.overall
  const inlineError = inlineAttempt?.outcome === 'error' ? inlineAttempt : null
  const firstError = inlineError ? undefined : state.results.find((attempt) => attempt.outcome === 'error')
  const runErrorShownInline = inlineError !== null && run.error === inlineError.error_message
    && (!run.error_type || run.error_type === inlineError.error_type)
  const runError = run.error && !runErrorShownInline ? run.error : null
  const showRunNotice = Boolean(runError) || (!inlineError && (run.status === 'FAILED' || overall.errors > 0))
  const unmatched = summary.unattributed_attempts ?? 0
  const overloaded = state.overloadSummaries.length > 0
  const notices: string[] = []
  if (overloaded) notices.push('Service overload detected.')
  if (unmatched > 0) notices.push(`${unmatched} execution${unmatched === 1 ? '' : 's'} could not be matched to the saved plan.`)
  if (!state.planComplete) notices.push('The saved execution plan is incomplete; some totals cannot be verified.')
  const runIntent = run.status === 'FAILED' ? 'error' : run.status === 'CANCELLED' && overall.errors === 0 ? 'info' : 'warning'

  return (
    <>
      {showRunNotice && <MessageBar intent={runIntent}>
        <MessageBarBody>{run.status === 'FAILED' ? 'Scan failed. Resume to continue remaining attacks.'
          : run.status === 'CANCELLED' ? 'Scan cancelled.'
          : `${overall.errors} attack${overall.errors === 1 ? '' : 's'} ended with an error.`}
          {firstError && <Button appearance="transparent" onClick={(event) => { onOpenAttempt(firstError, event.currentTarget) }}>
            View attack
          </Button>}
          {runError && <Accordion collapsible><AccordionItem value="error">
            <AccordionHeader>Error details</AccordionHeader>
            <AccordionPanel><Text className={styles.objective}>{run.error_type ? `${run.error_type}: ` : ''}{runError}</Text></AccordionPanel>
          </AccordionItem></Accordion>}
        </MessageBarBody>
      </MessageBar>}
      {state.stale && <MessageBar intent="warning"><MessageBarBody>
        Live updates paused. {state.error} <Button onClick={onRetry}>Retry</Button>
      </MessageBarBody></MessageBar>}
      {notices.length > 0 && <MessageBar aria-label="Scan notices" intent={overloaded || unmatched > 0 ? 'warning' : 'info'}>
        <MessageBarBody>
          {notices.join(' ')}
          {overloaded && <OverloadDetails overloads={state.overloadSummaries} />}
        </MessageBarBody>
      </MessageBar>}
    </>
  )
}

function OverloadDetails({ overloads }: { readonly overloads: readonly ScenarioOverloadSummary[] }) {
  return (
    <Accordion collapsible><AccordionItem value="overload">
      <AccordionHeader>Overload details</AccordionHeader>
      <AccordionPanel>{overloads.map((overload) => (
        <Text block key={overload.component_role}>
          {overload.component_role.replace(/_/g, ' ')}: {overload.count}
          {overload.status_codes.length > 0 ? ` (HTTP ${overload.status_codes.join(', ')})` : ''}
        </Text>
      ))}</AccordionPanel>
    </AccordionItem></Accordion>
  )
}
