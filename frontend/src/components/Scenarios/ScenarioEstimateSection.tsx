import { Button, Spinner, Text, Tooltip, mergeClasses } from '@fluentui/react-components'
import { InfoRegular } from '@fluentui/react-icons'

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
  const notes = estimateNotes(state)
  const estimate = estimateFromState(state)
  return (
    <section
      className={styles.section}
      aria-labelledby="run-estimate-title"
      data-testid="run-estimate"
    >
      <div className={styles.estimateHeader}>
        <Text id="run-estimate-title" as="h2" size={400} weight="semibold">
          Run estimate
        </Text>
        {notes && (
          <Tooltip
            content={notes}
            relationship="description"
            positioning="above"
          >
            <Button
              appearance="subtle"
              icon={<InfoRegular />}
              aria-label="Estimate notes"
              size="small"
            />
          </Tooltip>
        )}
      </div>
      <dl className={styles.costEstimateList}>
        <div className={mergeClasses(styles.costEstimateRow, styles.totalEstimateRow)}>
          <dt>Total atomic attacks</dt>
          <dd>{formatAtomicAttackCount(state)}</dd>
        </div>
        {showDatasetSize && <div className={styles.costEstimateRow}>
          <dt>Dataset size</dt>
          <dd>
            {datasetSizeLabel}
          </dd>
        </div>}
        {showTechniqueCount && <div className={styles.costEstimateRow}>
          <dt>Number techniques</dt>
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
      {state.status === 'refreshing' && (
        <div className={styles.inlineStatus}>
          <Spinner size="tiny" />
          <Text size={200} className={styles.hint}>Updating estimate...</Text>
        </div>
      )}
      {state.status === 'stale' && (
        <Text size={200} className={styles.warningText}>
          {state.error}
        </Text>
      )}
    </section>
  )
}
