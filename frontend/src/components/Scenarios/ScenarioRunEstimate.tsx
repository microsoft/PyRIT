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

function countLabel(value: number, singular: string, plural: string): string {
  return `${formatCount(value)} ${value === 1 ? singular : plural}`
}

function formatPlannedAttackSummary(estimate: ScenarioRunEstimate): string {
  if (estimate.total !== null) {
    return countLabel(estimate.total, 'planned attack', 'planned attacks')
  }
  if (estimate.minimum !== null && estimate.maximum !== null) {
    return estimate.minimum === estimate.maximum
      ? countLabel(estimate.minimum, 'planned attack', 'planned attacks')
      : `${formatCount(estimate.minimum)}–${formatCount(estimate.maximum)} planned attacks`
  }
  if (estimate.maximum !== null) {
    return `Up to ${countLabel(estimate.maximum, 'planned attack', 'planned attacks')}`
  }
  if (estimate.minimum !== null) {
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
    const attackAttemptUpperBound = estimate.adaptiveDetails.techniqueAttemptCountUpperBound
      + baselineCount(estimate)
    const attemptSummary = `up to ${countLabel(
      attackAttemptUpperBound,
      'attack attempt',
      'attack attempts',
    )}`
    const hasPlannedAttackBound = estimate.total !== null
      || estimate.minimum !== null
      || estimate.maximum !== null
    return hasPlannedAttackBound
      ? `${attemptSummary} · ${formatProgressUnitSummary(estimate)}`
      : `${countLabel(estimate.adaptiveDetails.objectiveCount, 'objective', 'objectives')} · ${attemptSummary}`
  }
  return formatPlannedAttackSummary(estimate)
}

function formatProgressUnitSummary(estimate: ScenarioRunEstimate): string {
  if (estimate.total !== null) {
    return countLabel(estimate.total, 'progress unit', 'progress units')
  }
  if (estimate.minimum !== null && estimate.maximum !== null) {
    return estimate.minimum === estimate.maximum
      ? countLabel(estimate.minimum, 'progress unit', 'progress units')
      : `${formatCount(estimate.minimum)}–${formatCount(estimate.maximum)} progress units`
  }
  if (estimate.maximum !== null) {
    return `Up to ${countLabel(estimate.maximum, 'progress unit', 'progress units')}`
  }
  if (estimate.minimum !== null) {
    return `At least ${countLabel(estimate.minimum, 'progress unit', 'progress units')}`
  }
  return 'Progress units are confirmed at launch.'
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
  if (estimate.minimum !== null && estimate.maximum !== null) {
    return {
      id: 'result',
      value: estimate.minimum === estimate.maximum
        ? formatCount(estimate.minimum)
        : `${formatCount(estimate.minimum)}–${formatCount(estimate.maximum)}`,
      label: estimate.minimum === 1 && estimate.maximum === 1 ? 'planned attack' : 'planned attacks',
      result: true,
    }
  }
  if (estimate.maximum !== null) {
    return {
      id: 'result',
      value: `up to ${formatCount(estimate.maximum)}`,
      label: estimate.maximum === 1 ? 'planned attack' : 'planned attacks',
      result: true,
    }
  }
  if (estimate.minimum !== null) {
    return {
      id: 'result',
      value: `at least ${formatCount(estimate.minimum)}`,
      label: estimate.minimum === 1 ? 'planned attack' : 'planned attacks',
      result: true,
    }
  }
  return estimate.scope === 'default'
    ? { id: 'result', value: 'Select targets', label: 'to calculate', result: true }
    : { id: 'result', value: 'Confirmed', label: 'at launch', result: true }
}

function adaptiveAttemptCalculation(estimate: ScenarioRunEstimate): RunCalculation {
  const details = estimate.adaptiveDetails
  if (!details) {
    throw new Error('Adaptive attempt calculation requires adaptive details.')
  }
  const directBaselineCount = baselineCount(estimate)
  const attemptUpperBound = details.techniqueAttemptCountUpperBound + directBaselineCount
  const objectiveLabel = details.objectiveCount === 1 ? 'objective' : 'objectives'
  const techniqueLabel = details.techniquesPerObjectiveUpperBound === 1
    ? 'Adaptive technique per objective'
    : 'Adaptive techniques per objective'
  const capProvenance = {
    selectedCandidateCount: details.selectedCandidateTechniqueCount,
    compatibleCandidateCount: details.candidateTechniqueCount,
    limit: details.maxAttemptsPerObjective,
    effectiveMaximum: details.techniquesPerObjectiveUpperBound,
  }
  const effectiveCapRule = formatAdaptiveCapMetadata(capProvenance)
  const accessibleCapRule = formatAdaptiveCapAccessibleRule(capProvenance)
  const baselinePerObjective = details.objectiveCount > 0
    && directBaselineCount === details.objectiveCount
  const parts: CalculationPart[] = [
    operand('attack-objectives', formatCount(details.objectiveCount), objectiveLabel),
    operator('attack-multiply', '×'),
  ]
  if (baselinePerObjective) {
    parts.push(operator('attempt-open', '('))
    parts.push(operand('baseline-factor', '1', 'direct baseline per objective'))
    parts.push(operator('attempt-plus', '+'))
  }
  parts.push(operand(
    'adaptive-techniques',
    `up to ${formatCount(details.techniquesPerObjectiveUpperBound)}`,
    techniqueLabel,
    false,
    effectiveCapRule,
  ))
  if (baselinePerObjective) {
    parts.push(operator('attempt-close', ')'))
  }
  if (directBaselineCount > 0 && !baselinePerObjective) {
    parts.push(operator('partial-baseline-plus', '+'))
    parts.push(operand(
      'partial-baseline',
      formatCount(directBaselineCount),
      directBaselineCount === 1 ? 'direct baseline attempt' : 'direct baseline attempts',
    ))
  }
  parts.push(operator('attack-equals', '='))
  parts.push(operand(
    'attack-result',
    `up to ${formatCount(attemptUpperBound)}`,
    attemptUpperBound === 1 ? 'attack attempt' : 'attack attempts',
    true,
  ))

  const baselinePhrase = baselinePerObjective
    ? '1 direct baseline plus '
    : ''
  const partialBaselinePhrase = directBaselineCount > 0 && !baselinePerObjective
    ? ` plus ${countLabel(directBaselineCount, 'direct baseline attempt', 'direct baseline attempts')}`
    : ''
  const baselineContext = directBaselineCount === 0
    ? ' Direct baseline comparison is not included.'
    : ' Direct baseline comparison is included.'
  return {
    parts,
    accessibleLabel: `${countLabel(details.objectiveCount, 'objective', 'objectives')} multiplied by ${
      baselinePhrase
    }up to ${countLabel(
      details.techniquesPerObjectiveUpperBound,
      'Adaptive technique per objective',
      'Adaptive techniques per objective',
    )}, ${accessibleCapRule}${partialBaselinePhrase}, equals up to ${
      countLabel(attemptUpperBound, 'attack attempt', 'attack attempts')
    }.${baselineContext}`,
  }
}

function adaptiveProgressCalculation(estimate: ScenarioRunEstimate): RunCalculation {
  const summary = formatProgressUnitSummary(estimate)
  const summarySentence = summary.endsWith('.') ? summary : `${summary}.`
  const hasBound = estimate.total !== null || estimate.minimum !== null || estimate.maximum !== null
  const value = hasBound ? summary.replace(/ progress units?$/, '') : 'Confirmed at launch'
  return {
    parts: [
      operand(
        'progress-result',
        value,
        estimate.total === 1 || (estimate.minimum === 1 && estimate.maximum === 1)
          ? 'progress unit'
          : 'progress units',
        true,
      ),
    ],
    accessibleLabel: `${summarySentence} Progress units track resumable evaluation groups, not every persisted attack attempt.`,
    context: 'Progress units track resumable evaluation groups, not every persisted attack attempt.',
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
  return `Attack-attempt totals exclude multi-turn target exchanges and retries. Adaptive techniques run sequentially and stop each objective after the first successful technique.${compatibilityContext}`
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
    context: estimate.total === null && estimate.minimum === null && estimate.maximum === null
      ? formatEstimateSummary(estimate)
      : undefined,
  }
}

export function ScenarioRunEstimateSummary({ state }: ScenarioRunEstimateSummaryProps) {
  const styles = useScenarioRunEstimateStyles()
  const estimate = stateEstimate(state)
  const label = statusLabel(state)

  return (
    <div className={styles.summary} aria-live="polite">
      <div className={styles.summaryHeader}>
        {label && <Badge appearance="tint" color={statusColor(state)}>{label}</Badge>}
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
            calculation={adaptiveAttemptCalculation(estimate)}
            heading="Attack attempts"
            idPrefix={`${idPrefix}-attempts`}
            testId="run-calculation"
          />
          <RunCalculationView
            calculation={adaptiveProgressCalculation(estimate)}
            heading="Progress planning"
            idPrefix={`${idPrefix}-progress`}
            testId="progress-unit-calculation"
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
