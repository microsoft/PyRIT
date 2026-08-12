import { Badge, Spinner, Text } from '@fluentui/react-components'

import type {
  ScenarioRunEstimate,
  ScenarioRunEstimateComponent,
  ScenarioRunEstimateDatasetCap,
  ScenarioRunEstimateFactor,
  ScenarioRunEstimateState,
} from '@/types'

import {
  formatAdaptiveCapAccessibleRule,
  formatAdaptiveCapMetadata,
} from './scenarioAdaptiveCap'
import { normalizeDatasetCaps } from './scenarioDatasetCaps'
import { useScenarioRunEstimateStyles } from './ScenarioRunEstimate.styles'

interface ScenarioRunEstimateSummaryProps {
  state: ScenarioRunEstimateState
}

interface ScenarioRunEstimateDetailsProps {
  state: ScenarioRunEstimateState
  idPrefix?: string
}

interface CalculationOperand {
  id: string
  value: string
  label: string
  detail?: string
  result?: boolean
}

interface CalculationOperator {
  id: string
  symbol: '(' | ')' | '×' | '+' | '='
}

type CalculationPart =
  | { kind: 'operand'; operand: CalculationOperand }
  | { kind: 'operator'; operator: CalculationOperator }

interface RunCalculation {
  parts: CalculationPart[]
  accessibleLabel: string
  summary?: string
  context?: string
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

function statusLabel(state: ScenarioRunEstimateState): string | null {
  switch (state.status) {
    case 'loading':
      return 'Loading estimate'
    case 'available':
    case 'conditional':
      return null
    case 'refreshing':
      return state.label
    case 'stale':
      return 'Estimate may be out of date'
    case 'unavailable':
      return 'Estimate unavailable'
  }
}

function statusColor(state: ScenarioRunEstimateState): 'brand' | 'warning' | 'subtle' {
  switch (state.status) {
    case 'available':
      return 'brand'
    case 'conditional':
    case 'stale':
      return 'warning'
    default:
      return 'subtle'
  }
}

function formatCount(value: number): string {
  return value.toLocaleString()
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
  return estimate.scope === 'default'
    ? 'Select targets to calculate'
    : 'Run size is confirmed at launch.'
}

function baselineCount(estimate: ScenarioRunEstimate): number {
  return estimate.components
    .filter((component) => component.isBaseline)
    .reduce((sum, component) => sum + component.count, 0)
}

function formatEstimateSummary(estimate: ScenarioRunEstimate): string {
  if (estimate.adaptiveDetails) {
    const { objectiveCount, techniqueAttemptCountUpperBound } = estimate.adaptiveDetails
    const attemptSummary = `up to ${countLabel(
      techniqueAttemptCountUpperBound,
      'technique attempt',
      'technique attempts',
    )}`
    const hasPlannedAttackBound = estimate.total !== null
      || estimate.minimum != null
      || estimate.maximum != null
    return hasPlannedAttackBound
      ? `${formatPlannedAttackSummary(estimate)} · ${attemptSummary}`
      : `${countLabel(objectiveCount, 'objective', 'objectives')} · ${attemptSummary}`
  }
  return formatPlannedAttackSummary(estimate)
}

function operand(id: string, value: string, label: string, result = false, detail?: string): CalculationPart {
  return { kind: 'operand', operand: { id, value, label, detail, result } }
}

function operator(id: string, symbol: CalculationOperator['symbol']): CalculationPart {
  return { kind: 'operator', operator: { id, symbol } }
}

function humanizeLabel(label: string): string {
  const words = label.replace(/_/g, ' ').trim()
  return words.length > 0 ? `${words[0].toUpperCase()}${words.slice(1)}` : label
}

function formatDatasetCap(cap: ScenarioRunEstimateDatasetCap): string {
  const count = cap.configuredOn === 'dataset'
    ? countLabel(cap.count, 'objective', 'objectives')
    : formatCount(cap.count)
  return `${humanizeLabel(cap.label)}: ${count}`
}

function semanticFactorLabel(factor: ScenarioRunEstimateFactor): string {
  const label = factor.label.toLowerCase()
  if (label.includes('seed group') || label === 'objectives') {
    return factor.count === 1 ? 'objective' : 'objectives'
  }
  if (label.includes('technique')) {
    return factor.count === 1 ? 'technique' : 'techniques'
  }
  if (factor.count === 1 && label.endsWith('s')) {
    return label.slice(0, -1)
  }
  return label
}

function factorPriority(factor: ScenarioRunEstimateFactor): number {
  const label = semanticFactorLabel(factor)
  if (label === 'technique' || label === 'techniques') return 0
  if (label === 'objective' || label === 'objectives') return 1
  return 2
}

function objectiveFactor(component: ScenarioRunEstimateComponent): ScenarioRunEstimateFactor | undefined {
  return component.factors.find((factor) => {
    const label = semanticFactorLabel(factor)
    return label === 'objective' || label === 'objectives'
  })
}

function resultOperand(estimate: ScenarioRunEstimate): CalculationOperand {
  if (estimate.total !== null) {
    return {
      id: 'result',
      value: formatCount(estimate.total),
      label: estimate.total === 1 ? 'planned attack' : 'planned attacks',
      result: true,
    }
  }
  if (estimate.minimum != null && estimate.maximum != null) {
    return {
      id: 'result',
      value: estimate.minimum === estimate.maximum
        ? formatCount(estimate.minimum)
        : `${formatCount(estimate.minimum)}–${formatCount(estimate.maximum)}`,
      label: estimate.minimum === 1 && estimate.maximum === 1 ? 'planned attack' : 'planned attacks',
      result: true,
    }
  }
  if (estimate.maximum != null) {
    return {
      id: 'result',
      value: `up to ${formatCount(estimate.maximum)}`,
      label: estimate.maximum === 1 ? 'planned attack' : 'planned attacks',
      result: true,
    }
  }
  if (estimate.minimum != null) {
    return {
      id: 'result',
      value: `at least ${formatCount(estimate.minimum)}`,
      label: estimate.minimum === 1 ? 'planned attack' : 'planned attacks',
      result: true,
    }
  }
  return { id: 'result', value: 'Exact total', label: 'unavailable', result: true }
}

function adaptivePlannedCalculation(estimate: ScenarioRunEstimate): RunCalculation {
  const details = estimate.adaptiveDetails
  if (!details) {
    throw new Error('Adaptive planned calculation requires adaptive details.')
  }
  const directBaselineCount = baselineCount(estimate)
  const hasExactTotal = estimate.total !== null
    || (
      estimate.minimum != null
      && estimate.maximum != null
      && estimate.minimum === estimate.maximum
    )
  const hasPlannedTotal = estimate.total !== null
    || estimate.minimum != null
    || estimate.maximum != null
  const adaptiveAttackCount = hasExactTotal
    ? Math.max((estimate.total ?? estimate.maximum ?? 0) - directBaselineCount, 0)
    : estimate.maximum != null
      ? Math.max(estimate.maximum - directBaselineCount, 0)
      : estimate.minimum != null
        ? Math.max(estimate.minimum - directBaselineCount, 0)
        : details.objectiveCount
  const hasAdaptiveRange = directBaselineCount === 0
    && estimate.minimum != null
    && estimate.minimum > 0
    && estimate.maximum != null
    && estimate.minimum !== estimate.maximum
  const adaptiveValue = hasExactTotal
    ? formatCount(adaptiveAttackCount)
    : hasAdaptiveRange
      ? `${formatCount(estimate.minimum ?? 0)}–${formatCount(estimate.maximum ?? 0)}`
      : estimate.maximum != null || estimate.minimum == null
        ? `up to ${formatCount(adaptiveAttackCount)}`
        : `at least ${formatCount(adaptiveAttackCount)}`
  const adaptiveLabel = adaptiveAttackCount === 1 ? 'Adaptive attack' : 'Adaptive attacks'
  const result = resultOperand(estimate)
  const parts: CalculationPart[] = []
  if (directBaselineCount > 0) {
    parts.push(operand(
      'baseline',
      formatCount(directBaselineCount),
      directBaselineCount === 1 ? 'direct baseline attack' : 'direct baseline attacks',
    ))
    parts.push(operator('baseline-plus', '+'))
  }
  parts.push(operand('adaptive-attacks', adaptiveValue, adaptiveLabel))
  if (hasPlannedTotal) {
    parts.push(operator('planned-equals', '='))
    parts.push({ kind: 'operand', operand: result })
  }

  const adaptivePhrase = `${adaptiveValue} ${adaptiveLabel}`
  const resultPhrase = `${result.value} ${result.label}`
  const plannedResultPhrase = hasPlannedTotal
    ? ` equals ${resultPhrase}.`
    : '. Planned total is confirmed at launch.'
  const accessibleLabel = directBaselineCount > 0
    ? `Direct baseline comparison is included: ${countLabel(
      directBaselineCount,
      'direct baseline attack',
      'direct baseline attacks',
    )} plus ${adaptivePhrase}${plannedResultPhrase}`
    : `Direct baseline comparison is not included: ${adaptivePhrase}${plannedResultPhrase}`
  return { parts, accessibleLabel }
}

function adaptiveWorkCalculation(estimate: ScenarioRunEstimate): RunCalculation {
  const details = estimate.adaptiveDetails
  if (!details) {
    throw new Error('Adaptive work calculation requires adaptive details.')
  }
  const objectiveLabel = details.objectiveCount === 1 ? 'objective' : 'objectives'
  const techniqueLabel = details.techniquesPerObjectiveUpperBound === 1
    ? 'technique per objective'
    : 'techniques per objective'
  const attemptLabel = details.techniqueAttemptCountUpperBound === 1
    ? 'technique attempt'
    : 'technique attempts'
  const capProvenance = {
    selectedCandidateCount: details.selectedCandidateTechniqueCount,
    compatibleCandidateCount: details.candidateTechniqueCount,
    limit: details.maxAttemptsPerObjective,
    effectiveMaximum: details.techniquesPerObjectiveUpperBound,
  }
  const effectiveCapRule = formatAdaptiveCapMetadata(capProvenance)
  const accessibleCapRule = formatAdaptiveCapAccessibleRule(capProvenance)
  return {
    parts: [
      operand('adaptive-objectives', formatCount(details.objectiveCount), objectiveLabel),
      operator('adaptive-multiply', '×'),
      operand(
        'adaptive-techniques',
        `up to ${formatCount(details.techniquesPerObjectiveUpperBound)}`,
        techniqueLabel,
        false,
        effectiveCapRule,
      ),
      operator('adaptive-equals', '='),
      operand(
        'adaptive-result',
        `up to ${formatCount(details.techniqueAttemptCountUpperBound)}`,
        attemptLabel,
        true,
      ),
    ],
    accessibleLabel: `${countLabel(details.objectiveCount, 'objective', 'objectives')} multiplied by up to ${
      countLabel(details.techniquesPerObjectiveUpperBound, 'technique per objective', 'techniques per objective')
    }, ${accessibleCapRule}, equals up to ${
      countLabel(details.techniqueAttemptCountUpperBound, 'technique attempt', 'technique attempts')
    }.`,
  }
}

function adaptiveWorkContext(estimate: ScenarioRunEstimate): string {
  const details = estimate.adaptiveDetails
  if (!details) {
    throw new Error('Adaptive work context requires adaptive details.')
  }
  const compatibilityContext = details.compatibilityMayReduceAttempts
    ? ' Compatibility may reduce how many candidates each objective can try.'
    : ''
  return `Technique-attempt totals exclude multi-turn target exchanges and retries. Adaptive stops each objective after the first successful technique.${compatibilityContext}`
}

function homogeneousTechniqueCalculation(
  components: ScenarioRunEstimateComponent[],
): CalculationPart[] | null {
  if (components.length < 2 || components.some((component) => component.condition !== null)) {
    return null
  }
  const objectiveCounts = components.map((component) => objectiveFactor(component)?.count)
  if (objectiveCounts.some((count) => count === undefined)) {
    return null
  }
  const firstCount = objectiveCounts[0]
  if (!objectiveCounts.every((count) => count === firstCount)) {
    return null
  }
  return [
    operand(
      'technique-count',
      formatCount(components.length),
      components.length === 1 ? 'technique' : 'techniques',
    ),
    operator('technique-multiply', '×'),
    operand(
      'objective-count',
      formatCount(firstCount ?? 0),
      firstCount === 1 ? 'objective' : 'objectives',
    ),
  ]
}

function componentTerms(components: ScenarioRunEstimateComponent[]): CalculationPart[] {
  const homogeneous = homogeneousTechniqueCalculation(components)
  if (homogeneous) {
    return homogeneous
  }
  if (components.length === 1 && components[0].condition === null && components[0].factors.length > 0) {
    return [...components[0].factors]
      .sort((left, right) => factorPriority(left) - factorPriority(right))
      .flatMap((factor, index) => [
        ...(index > 0 ? [operator(`factor-${index}-multiply`, '×')] : []),
        operand(`factor-${factor.id}`, formatCount(factor.count), semanticFactorLabel(factor)),
      ])
  }
  return components.flatMap((component, index) => {
    const objectiveCount = objectiveFactor(component)?.count
    const value = formatCount(objectiveCount ?? component.count)
    const unit = objectiveCount === undefined
      ? component.count === 1 ? 'planned attack' : 'planned attacks'
      : objectiveCount === 1 ? 'objective' : 'objectives'
    const condition = component.condition ? ' · if supported' : ''
    return [
      ...(index > 0 ? [operator(`component-${index}-plus`, '+')] : []),
      operand(
        `component-${component.id}`,
        value,
        `${unit} · ${humanizeLabel(component.label)}${condition}`,
      ),
    ]
  })
}

function ordinaryCalculation(estimate: ScenarioRunEstimate): RunCalculation {
  const baselineCount = estimate.components
    .filter((component) => component.isBaseline)
    .reduce((sum, component) => sum + component.count, 0)
  const attackComponents = estimate.components.filter((component) => !component.isBaseline)
  const resultOnly = baselineCount === 0
    && attackComponents.length === 1
    && attackComponents[0].factors.length === 0
    && attackComponents[0].condition === null
  const attackParts = resultOnly ? [] : componentTerms(attackComponents)
  const hasMultiplication = attackParts.some(
    (part) => part.kind === 'operator' && part.operator.symbol === '×',
  )
  const parts: CalculationPart[] = []
  if (baselineCount > 0 && hasMultiplication) {
    parts.push(operator('attack-open', '('))
  }
  parts.push(...attackParts)
  if (baselineCount > 0 && hasMultiplication) {
    parts.push(operator('attack-close', ')'))
  }
  if (baselineCount > 0) {
    if (attackParts.length > 0) {
      parts.push(operator('baseline-plus', '+'))
    }
    parts.push(operand(
      'baseline',
      formatCount(baselineCount),
      baselineCount === 1 ? 'direct baseline attack' : 'direct baseline attacks',
    ))
  }
  const result = resultOperand(estimate)
  if (parts.length > 0) {
    parts.push(operator('total-equals', '='))
  }
  parts.push({ kind: 'operand', operand: result })

  const visibleExpression = parts.map((part) => part.kind === 'operator'
    ? part.operator.symbol
    : `${part.operand.value} ${part.operand.label}`).join(' ')
  return {
    parts,
    accessibleLabel: `${visibleExpression
      .replace(/×/g, 'multiplied by')
      .replace(/\+/g, 'plus')
      .replace(/=/g, 'equals')}.`,
    context: estimate.total === null && estimate.minimum == null && estimate.maximum == null
      ? formatEstimateSummary(estimate)
      : undefined,
  }
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
    </div>
  )
}

function RunCalculationView({
  calculation,
  heading,
  idPrefix,
  testId,
}: {
  calculation: RunCalculation
  heading: string
  idPrefix: string
  testId: string
}) {
  const styles = useScenarioRunEstimateStyles()
  const headingId = `${idPrefix}-calculation`

  return (
    <section className={styles.calculationSection} aria-labelledby={headingId}>
      <Text as="h4" id={headingId} weight="semibold">{heading}</Text>
      <div
        className={styles.equation}
        role="group"
        aria-label={calculation.accessibleLabel}
        data-testid={testId}
      >
        {calculation.parts.map((part) => part.kind === 'operator' ? (
          <Text
            aria-hidden="true"
            className={styles.operator}
            key={part.operator.id}
            weight="semibold"
          >
            {part.operator.symbol}
          </Text>
        ) : (
          <span
            aria-hidden="true"
            className={part.operand.result ? styles.resultOperand : styles.operand}
            key={part.operand.id}
          >
            <Text className={styles.operandValue} weight="semibold">{part.operand.value}</Text>
            <Text size={200}>{part.operand.label}</Text>
            {part.operand.detail && (
              <Text className={styles.operandDetail} size={100}>{part.operand.detail}</Text>
            )}
          </span>
        ))}
      </div>
      {calculation.summary && (
        <Text size={200} weight="semibold" className={styles.calculationContext}>
          {calculation.summary}
        </Text>
      )}
      {calculation.context && (
        <Text size={200} className={styles.calculationContext}>{calculation.context}</Text>
      )}
    </section>
  )
}

function EstimateSources({ estimate }: { estimate: ScenarioRunEstimate }) {
  const styles = useScenarioRunEstimateStyles()
  if (estimate.datasets.length === 0) {
    return null
  }
  const { commonCaps, residualCapsByDatasetId } = normalizeDatasetCaps(estimate.datasets)

  return (
    <div className={styles.sources} role="group" aria-label="Objective sources">
      {commonCaps.length > 0 && (
        <Text size={200} weight="semibold" className={styles.muted}>
          {commonCaps.map(formatDatasetCap).join(' · ')}
        </Text>
      )}
      {estimate.datasets.map((dataset) => {
        const residualCaps = residualCapsByDatasetId.get(dataset.id) ?? []
        return (
          <div
            className={styles.source}
            key={dataset.id}
            role="group"
            aria-label={`Objective source: ${dataset.name}`}
          >
            <Text size={200}>
              {countLabel(dataset.selectedSeedGroupCount, 'objective', 'objectives')} from {dataset.name}
              {dataset.logicalSeedGroupCount !== dataset.selectedSeedGroupCount
                ? ` · ${formatCount(dataset.logicalSeedGroupCount)} available`
                : ''}
            </Text>
            {dataset.selectionNote && (
              <Text size={200} className={styles.muted}>{dataset.selectionNote}</Text>
            )}
            {residualCaps.length > 0 && (
              <Text size={200} className={styles.muted}>
                {residualCaps.map(formatDatasetCap).join(' · ')}
              </Text>
            )}
          </div>
        )
      })}
    </div>
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
        <Spinner size="tiny" label="Calculating planned attacks..." />
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
  const hasAdaptiveDetails = estimate.adaptiveDetails !== null
  return (
    <div className={styles.details} aria-live="polite">
      {hasAdaptiveDetails ? (
        <>
          <RunCalculationView
            calculation={adaptivePlannedCalculation(estimate)}
            heading="Planned attacks"
            idPrefix={`${idPrefix}-planned`}
            testId="run-calculation"
          />
          <RunCalculationView
            calculation={adaptiveWorkCalculation(estimate)}
            heading="Adaptive work"
            idPrefix={`${idPrefix}-adaptive-work`}
            testId="adaptive-work-calculation"
          />
        </>
      ) : (
        <RunCalculationView
          calculation={ordinaryCalculation(estimate)}
          heading="Run calculation"
          idPrefix={idPrefix}
          testId="run-calculation"
        />
      )}
      <EstimateSources estimate={estimate} />
      <Text size={200} className={styles.muted}>
        {hasAdaptiveDetails
          ? adaptiveWorkContext(estimate)
          : `Retries are ${estimate.retriesIncluded ? 'included' : 'not included'}.`}
      </Text>
    </div>
  )
}
