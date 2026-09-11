import { Badge, Spinner, Text } from '@fluentui/react-components'

import type {
  ScenarioRunEstimate,
  ScenarioRunEstimateComponent,
  ScenarioRunEstimateState,
} from '@/types'

import { useScenarioRunEstimateStyles } from './ScenarioRunEstimate.styles'

interface ScenarioRunEstimateSummaryProps {
  state: ScenarioRunEstimateState
  compact?: boolean
}

interface ScenarioRunEstimateDetailsProps {
  state: ScenarioRunEstimateState
}

function stateEstimate(state: ScenarioRunEstimateState): ScenarioRunEstimate | undefined {
  switch (state.status) {
    case 'available':
    case 'conditional':
    case 'refreshing':
    case 'stale':
      return state.estimate
    default:
      return undefined
  }
}

function scopeLabel(state: ScenarioRunEstimateState): string {
  const scope = state.status === 'loading' || state.status === 'unavailable'
    ? state.scope
    : state.estimate.scope
  return scope === 'default' ? 'Default configuration' : 'Current configuration'
}

function statusLabel(state: ScenarioRunEstimateState): string {
  switch (state.status) {
    case 'loading':
      return 'Loading estimate'
    case 'available':
      return 'Backend estimate'
    case 'conditional':
      return 'Conditional estimate'
    case 'refreshing':
      return 'Updating estimate'
    case 'stale':
      return 'Previous estimate'
    case 'unavailable':
      return 'Estimate unavailable'
  }
}

function statusColor(state: ScenarioRunEstimateState): 'brand' | 'warning' | 'subtle' {
  switch (state.status) {
    case 'available':
    case 'refreshing':
      return 'brand'
    case 'conditional':
    case 'stale':
      return 'warning'
    default:
      return 'subtle'
  }
}

function formatEstimateValue(value: number): string {
  return value.toLocaleString()
}

function countLabel(value: number, singular: string, plural: string): string {
  return `${formatEstimateValue(value)} ${value === 1 ? singular : plural}`
}

function formatPlannedAttackSummary(estimate: ScenarioRunEstimate): string {
  if (estimate.total !== null) {
    return countLabel(estimate.total, 'attack', 'attacks')
  }
  if (estimate.minimum != null && estimate.maximum != null) {
    return estimate.minimum === estimate.maximum
      ? countLabel(estimate.minimum, 'attack', 'attacks')
      : `${formatEstimateValue(estimate.minimum)}-${formatEstimateValue(estimate.maximum)} attacks`
  }
  if (estimate.maximum != null) {
    return `Up to ${countLabel(estimate.maximum, 'attack', 'attacks')}`
  }
  if (estimate.minimum != null) {
    return `At least ${countLabel(estimate.minimum, 'attack', 'attacks')}`
  }
  return 'Attack count varies'
}

function formatComponentFormula(component: ScenarioRunEstimateComponent): string {
  return `${component.label}: ${formatEstimateValue(component.count)}`
}

function formatBackendFormula(estimate: ScenarioRunEstimate): string {
  const components = estimate.components.length > 0
    ? estimate.components.map(formatComponentFormula).join(' + ')
    : 'No additive components supplied'
  const total = estimate.total === null
    ? 'conditional total'
    : formatEstimateValue(estimate.total)
  return `${components} = ${total}`
}

export function ScenarioRunEstimateSummary({ state, compact = false }: ScenarioRunEstimateSummaryProps) {
  const styles = useScenarioRunEstimateStyles()
  const estimate = stateEstimate(state)

  return (
    <div className={styles.summary} aria-live="polite">
      <div className={styles.summaryHeader}>
        {!compact && <Badge appearance="tint" color={statusColor(state)}>{statusLabel(state)}</Badge>}
        {estimate && (
          <Text className={styles.total} weight="semibold">
            {formatPlannedAttackSummary(estimate)}
          </Text>
        )}
        {compact && !estimate && <Text className={styles.total}>{statusLabel(state)}</Text>}
      </div>
      {!compact && <Text size={200} className={styles.muted}>{scopeLabel(state)}</Text>}
    </div>
  )
}

export function ScenarioRunEstimateDetails({ state }: ScenarioRunEstimateDetailsProps) {
  const styles = useScenarioRunEstimateStyles()

  if (state.status === 'loading') {
    return (
      <div className={styles.details} aria-live="polite">
        <Spinner size="tiny" label="Calculating run estimate..." />
      </div>
    )
  }

  if (state.status === 'unavailable') {
    return (
      <div className={styles.details} aria-live="polite">
        <Text weight="semibold">{state.label}</Text>
        {state.note && <Text size={200} className={styles.muted}>{state.note}</Text>}
      </div>
    )
  }

  const { estimate } = state
  return (
    <div className={styles.details} aria-live="polite">
      <Text className={styles.total} weight="semibold">
        {formatPlannedAttackSummary(estimate)}
      </Text>
      <code className={styles.formula}>{formatBackendFormula(estimate)}</code>
    </div>
  )
}
