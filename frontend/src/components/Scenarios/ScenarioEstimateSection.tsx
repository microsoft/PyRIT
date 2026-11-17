import { Spinner, Text, mergeClasses } from '@fluentui/react-components'

import ContextualHelpLabel from '@/components/ContextualHelpLabel'
import type { ParameterFormValue } from '@/components/Parameters/parameterForm'
import type { Parameter, ScenarioRunEstimateState } from '@/types'

import { useScenarioDetailStyles } from './ScenarioDetail.styles'
import {
  estimateFromState,
  estimateNotes,
  formatAtomicAttackCount,
  formatEffectiveParameterPreview,
} from './scenarioLaunchPresentation'

interface ScenarioEstimateSectionProps {
  state: ScenarioRunEstimateState
  showDatasetSize: boolean
  datasetSizeLabel: string
  showTechniqueCount: boolean
  techniqueCount: number
  dynamicParameters: Parameter[]
  scenarioParamValues: Record<string, ParameterFormValue>
}

export default function ScenarioEstimateSection({
  state,
  showDatasetSize,
  datasetSizeLabel,
  showTechniqueCount,
  techniqueCount,
  dynamicParameters,
  scenarioParamValues,
}: ScenarioEstimateSectionProps) {
  const styles = useScenarioDetailStyles()
  const notes = state.status === 'unavailable' ? null : estimateNotes(state)
  const estimate = estimateFromState(state)
  return (
    <section
      className={styles.section}
      aria-labelledby="run-estimate-title"
      data-testid="run-estimate"
    >
      <div className={styles.estimateHeader}>
        <Text id="run-estimate-title" as="h2" aria-label="Run estimate" size={400} weight="semibold">
          <ContextualHelpLabel term="Run estimate" labelProps={{ weight: 'semibold' }}>
            {`Estimated work in this scan, not its cost or duration.${notes ? ` ${notes}` : ''}`}
          </ContextualHelpLabel>
        </Text>
      </div>
      <dl className={styles.costEstimateList}>
        <div className={mergeClasses(styles.costEstimateRow, styles.totalEstimateRow)}>
          <dt><ContextualHelpLabel term="Total atomic attacks">
            Individual attack executions in this scan, across its objectives and techniques.
          </ContextualHelpLabel></dt>
          <dd>{formatAtomicAttackCount(state)}</dd>
        </div>
        {showDatasetSize && <div className={styles.costEstimateRow}>
          <dt><ContextualHelpLabel term="Dataset selection limit">
            The configured maximum number of examples to select. The actual selection may be smaller after filtering or because fewer examples are available.
          </ContextualHelpLabel></dt>
          <dd>
            {datasetSizeLabel}
          </dd>
        </div>}
        {showTechniqueCount && <div className={styles.costEstimateRow}>
          <dt><ContextualHelpLabel term="Number techniques">
            How many techniques are included. Each technique can produce multiple individual attacks.
          </ContextualHelpLabel></dt>
          <dd>{techniqueCount}</dd>
        </div>}
        {dynamicParameters.map((parameter) => (
          <div className={styles.costEstimateRow} key={parameter.name}>
            <dt>{parameter.name}</dt>
            <dd>
              {formatEffectiveParameterPreview(
                parameter.name,
                scenarioParamValues[parameter.name],
                estimate,
              )}
            </dd>
          </div>
        ))}
      </dl>
      {state.status === 'unavailable' && (
        <Text size={200} className={styles.warningText} role="status">
          {state.label}
          {state.note && state.note !== state.label ? ` ${state.note}` : ''}
        </Text>
      )}
      {state.status === 'refreshing' && (
        <div className={styles.inlineStatus} role="status">
          <Spinner size="tiny" />
          <Text size={200} className={styles.hint}>Updating estimate...</Text>
        </div>
      )}
      {state.status === 'stale' && (
        <Text size={200} className={styles.warningText} role="status">
          {state.label} {state.error}
        </Text>
      )}
    </section>
  )
}
