import { Badge, Spinner, Text } from '@fluentui/react-components'

import type {
  ScenarioRunEstimate,
  ScenarioRunEstimateComponent,
  ScenarioRunEstimateState,
} from '@/types'

import { useScenarioRunEstimateStyles } from './ScenarioRunEstimate.styles'

interface ScenarioRunEstimateSummaryProps {
  state: ScenarioRunEstimateState
}

interface ScenarioRunEstimateDetailsProps {
  state: ScenarioRunEstimateState
  idPrefix?: string
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
    return countLabel(estimate.total, 'planned attack', 'planned attacks')
  }
  if (estimate.minimum != null && estimate.maximum != null) {
    return estimate.minimum === estimate.maximum
      ? countLabel(estimate.minimum, 'planned attack', 'planned attacks')
      : `${formatEstimateValue(estimate.minimum)}–${formatEstimateValue(estimate.maximum)} planned attacks`
  }
  if (estimate.maximum != null) {
    return `Up to ${countLabel(estimate.maximum, 'planned attack', 'planned attacks')}`
  }
  if (estimate.minimum != null) {
    return `At least ${countLabel(estimate.minimum, 'planned attack', 'planned attacks')}`
  }
  return 'Total depends on configuration'
}

function formatProgressUnitSummary(estimate: ScenarioRunEstimate): string {
  if (estimate.total !== null) {
    return countLabel(estimate.total, 'progress unit', 'progress units')
  }
  if (estimate.minimum != null && estimate.maximum != null) {
    return estimate.minimum === estimate.maximum
      ? countLabel(estimate.minimum, 'progress unit', 'progress units')
      : `${formatEstimateValue(estimate.minimum)}–${formatEstimateValue(estimate.maximum)} progress units`
  }
  if (estimate.maximum != null) {
    return `Up to ${countLabel(estimate.maximum, 'progress unit', 'progress units')}`
  }
  if (estimate.minimum != null) {
    return `At least ${countLabel(estimate.minimum, 'progress unit', 'progress units')}`
  }
  return 'Progress units are confirmed at launch.'
}

function baselineCount(estimate: ScenarioRunEstimate): number {
  return estimate.components
    .filter((component) => component.isBaseline)
    .reduce((sum, component) => sum + component.count, 0)
}

function formatEstimateSummary(estimate: ScenarioRunEstimate): string {
  if (!estimate.adaptiveDetails) {
    return formatPlannedAttackSummary(estimate)
  }
  const attackAttemptUpperBound = estimate.adaptiveDetails.techniqueAttemptCountUpperBound
    + baselineCount(estimate)
  const attemptSummary = `up to ${countLabel(
    attackAttemptUpperBound,
    'attack attempt',
    'attack attempts',
  )}`
  const hasPlannedAttackBound = estimate.total !== null
    || estimate.minimum != null
    || estimate.maximum != null
  return hasPlannedAttackBound
    ? `${attemptSummary} · ${formatProgressUnitSummary(estimate)}`
    : `${countLabel(estimate.adaptiveDetails.objectiveCount, 'objective', 'objectives')} · ${attemptSummary}`
}

function formatComponentFormula(component: ScenarioRunEstimateComponent): string {
  if (component.factors.length === 0) {
    return `${component.label}: ${formatEstimateValue(component.count)}`
  }
  const factors = component.factors
    .map((factor) => `${formatEstimateValue(factor.count)} ${factor.label}`)
    .join(' × ')
  return `${component.label}: ${factors} = ${formatEstimateValue(component.count)}`
}

function formatBackendFormula(estimate: ScenarioRunEstimate): string {
  const components = estimate.components.length > 0
    ? estimate.components.map(formatComponentFormula).join(' + ')
    : 'No additive components supplied'
  const total = estimate.total === null
    ? 'backend total is conditional'
    : `backend total = ${formatEstimateValue(estimate.total)}`
  return `${components}; ${total}`
}

export function ScenarioRunEstimateSummary({ state }: ScenarioRunEstimateSummaryProps) {
  const styles = useScenarioRunEstimateStyles()
  const estimate = stateEstimate(state)

  return (
    <div className={styles.summary} aria-live="polite">
      <div className={styles.summaryHeader}>
        <Badge appearance="tint" color={statusColor(state)}>{statusLabel(state)}</Badge>
        {estimate && (
          <Text className={styles.total} weight="semibold">
            {formatEstimateSummary(estimate)}
          </Text>
        )}
      </div>
      <Text size={200} className={styles.muted}>{scopeLabel(state)}</Text>
    </div>
  )
}

function EstimateComponents({
  estimate,
  idPrefix,
}: {
  estimate: ScenarioRunEstimate
  idPrefix: string
}) {
  const styles = useScenarioRunEstimateStyles()
  const headingId = `${idPrefix}-components`

  return (
    <section className={styles.detailGroup} aria-labelledby={headingId}>
      <Text as="h4" id={headingId} weight="semibold">
        Planned components
      </Text>
      {estimate.components.length === 0 ? (
        <Text size={200} className={styles.muted}>
          No additive components supplied by the backend.
        </Text>
      ) : (
        <ol className={styles.componentList}>
          {estimate.components.map((component) => (
            <li className={styles.component} key={component.id}>
              <div className={styles.componentHeader}>
                <Text weight="semibold">{component.label}</Text>
                <div className={styles.componentCount}>
                  {component.isBaseline && (
                    <Badge appearance="tint" color="informative">Baseline</Badge>
                  )}
                  <Text weight="semibold">{formatEstimateValue(component.count)}</Text>
                </div>
              </div>
              {component.factors.length > 0 && (
                <ul className={styles.factorList} aria-label={`${component.label} factors`}>
                  {component.factors.map((factor) => (
                    <li key={factor.id}>
                      <Text size={200}>
                        × {formatEstimateValue(factor.count)} {factor.label}
                      </Text>
                    </li>
                  ))}
                </ul>
              )}
              {component.note && (
                <Text size={200} className={styles.muted}>{component.note}</Text>
              )}
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}

function EstimateDatasets({
  estimate,
  idPrefix,
}: {
  estimate: ScenarioRunEstimate
  idPrefix: string
}) {
  const styles = useScenarioRunEstimateStyles()
  const headingId = `${idPrefix}-datasets`

  return (
    <section className={styles.detailGroup} aria-labelledby={headingId}>
      <Text as="h4" id={headingId} weight="semibold">
        Dataset populations
      </Text>
      {estimate.datasets.length === 0 ? (
        <Text size={200} className={styles.muted}>
          No dataset population details supplied by the backend.
        </Text>
      ) : (
        <div className={styles.datasetList}>
          {estimate.datasets.map((dataset) => (
            <article className={styles.dataset} key={dataset.id}>
              <div className={styles.datasetHeader}>
                <Text weight="semibold">{dataset.name}</Text>
                <Badge appearance="tint" color="informative">{dataset.kind}</Badge>
              </div>
              <dl className={styles.countList}>
                <div className={styles.countRow}>
                  <dt>Logical seed groups</dt>
                  <dd>{formatEstimateValue(dataset.logicalSeedGroupCount)}</dd>
                </div>
                <div className={styles.countRow}>
                  <dt>Selected seed groups</dt>
                  <dd>{formatEstimateValue(dataset.selectedSeedGroupCount)}</dd>
                </div>
              </dl>
              {dataset.configuredCaps.length > 0 && (
                <div className={styles.capGroup}>
                  <Text size={200} weight="semibold">Configured caps</Text>
                  <ul className={styles.capList}>
                    {dataset.configuredCaps.map((cap) => (
                      <li key={cap.id}>
                        <Text size={200}>
                          {cap.label}: {formatEstimateValue(cap.count)}
                          {' '}({cap.configuredOn}{cap.datasetName ? `: ${cap.datasetName}` : ''})
                        </Text>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {dataset.selectionNote && (
                <Text size={200} className={styles.muted}>{dataset.selectionNote}</Text>
              )}
            </article>
          ))}
        </div>
      )}
    </section>
  )
}

export function ScenarioRunEstimateDetails({
  state,
  idPrefix = 'scenario-run-estimate',
}: ScenarioRunEstimateDetailsProps) {
  const styles = useScenarioRunEstimateStyles()

  if (state.status === 'loading') {
    return (
      <div className={styles.details} aria-live="polite">
        <Spinner size="tiny" label="Loading backend run estimate..." />
        <Text size={200} className={styles.muted}>{scopeLabel(state)}</Text>
      </div>
    )
  }

  if (state.status === 'unavailable') {
    return (
      <div className={styles.details} aria-live="polite">
        <ScenarioRunEstimateSummary state={state} />
        <Text>{state.label}</Text>
        {state.note && <Text size={200} className={styles.muted}>{state.note}</Text>}
      </div>
    )
  }

  const { estimate } = state
  return (
    <div className={styles.details} aria-live="polite">
      <ScenarioRunEstimateSummary state={state} />
      {state.status === 'refreshing' && (
        <Text size={200} className={styles.muted}>{state.label}</Text>
      )}
      {state.status === 'stale' && (
        <div className={styles.staleNotice} role="status">
          <Text weight="semibold">{state.label}</Text>
          <Text size={200}>{state.error}</Text>
        </div>
      )}
      <EstimateComponents estimate={estimate} idPrefix={idPrefix} />
      <EstimateDatasets estimate={estimate} idPrefix={idPrefix} />
      <section className={styles.detailGroup} aria-labelledby={`${idPrefix}-formula`}>
        <Text as="h4" id={`${idPrefix}-formula`} weight="semibold">
          Backend formula
        </Text>
        <code className={styles.formula}>{formatBackendFormula(estimate)}</code>
      </section>
      <section className={styles.detailGroup} aria-labelledby={`${idPrefix}-notes`}>
        <Text as="h4" id={`${idPrefix}-notes`} weight="semibold">
          Estimate notes
        </Text>
        <Text size={200} className={styles.muted}>
          {estimate.note ?? 'No additional note supplied by the backend.'}
        </Text>
        <Text size={200} className={styles.muted}>
          Retries are {estimate.retriesIncluded ? 'included' : 'not included'}.
          {' '}Estimate schema v{estimate.version}.
        </Text>
      </section>
    </div>
  )
}
