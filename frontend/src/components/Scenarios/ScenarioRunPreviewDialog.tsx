import {
  Badge,
  Button,
  Dialog,
  DialogActions,
  DialogBody,
  DialogContent,
  DialogSurface,
  DialogTitle,
  MessageBar,
  MessageBarBody,
  Text,
} from '@fluentui/react-components'

import type { ParameterFormValue } from '@/components/Parameters/parameterForm'
import type { Parameter, ScenarioRunEstimateState, TechniqueOverride } from '@/types'

import { useScenarioDetailStyles } from './ScenarioDetail.styles'
import { ScenarioRunEstimateDetails } from './ScenarioRunEstimate'
import { estimateFromState, formatEffectiveParameterPreview } from './scenarioLaunchPresentation'

/** Which datasets a confirmed run would replace, and how sure the backend answer is. */
export interface DatasetRefreshPreview {
  checking: boolean
  unconfirmed: boolean
  refreshed: string[]
  edited: string[]
  unchecked: string[]
}

/** The datasets section of the run preview, or null when the scenario takes no dataset input. */
export interface DatasetSelectionPreview {
  names: string[]
  custom: boolean
  sizeLabel: string
  harmCategories: string[]
  dataTypes: string[]
}

interface ScenarioRunPreviewDialogProps {
  open: boolean
  submitting: boolean
  confirmDisabled: boolean
  onOpenChange: (open: boolean) => void
  onCancel: () => void
  onConfirm: () => void
  refresh: DatasetRefreshPreview
  /** The objective target name, or null when the scenario takes no target. */
  objectiveTarget: string | null
  /** The adversarial target shown, or null when the scenario has no adversarial target. */
  adversarialTarget: string | null
  /** The selected techniques, or null when the scenario takes neither techniques nor a baseline. */
  techniques: { baseline: boolean, names: string[] } | null
  techniqueOverride?: TechniqueOverride
  datasets: DatasetSelectionPreview | null
  dynamicParameters: Parameter[]
  scenarioParamValues: Record<string, ParameterFormValue>
  estimateState: ScenarioRunEstimateState
}

export default function ScenarioRunPreviewDialog({
  open,
  submitting,
  confirmDisabled,
  onOpenChange,
  onCancel,
  onConfirm,
  refresh,
  objectiveTarget,
  adversarialTarget,
  techniques,
  techniqueOverride,
  datasets,
  dynamicParameters,
  scenarioParamValues,
  estimateState,
}: ScenarioRunPreviewDialogProps) {
  const styles = useScenarioDetailStyles()
  const estimate = estimateFromState(estimateState)
  return (
    <Dialog open={open} onOpenChange={(_, data) => onOpenChange(data.open)}>
      <DialogSurface>
        <DialogBody>
          <DialogTitle>Run preview</DialogTitle>
          <DialogContent className={styles.dialogContent}>
            {refresh.checking && <Text role="status">Checking dataset refresh policies...</Text>}
            {refresh.unconfirmed && <MessageBar intent="warning">
              <MessageBarBody role="alert">
                Cannot confirm which datasets will refresh. The backend settings could not be checked for this
                request. A refresh replaces the full stored dataset, can remove manual edits, and can prevent
                older runs from resuming. Filters and objective limits do not limit replacement.
              </MessageBarBody>
            </MessageBar>}
            {refresh.refreshed.length > 0 && <MessageBar intent="warning">
              <MessageBarBody role="alert">
                Refresh will replace the full stored datasets: {refresh.refreshed.join(', ')}.
                Filters and objective limits do not limit replacement. This can remove manual edits
                and prevent older runs from resuming. Use a new dataset name to keep both batches.
                {refresh.edited.length > 0 && <Text block>
                  User or unknown-origin data will be replaced: {refresh.edited.join(', ')}.
                </Text>}
                {refresh.unchecked.length > 0 && <Text block>
                  Existing data could not be checked for: {refresh.unchecked.join(', ')}.
                </Text>}
              </MessageBarBody>
            </MessageBar>}
            <dl className={styles.previewList}>
              <div className={styles.previewGroup}>
                {objectiveTarget !== null && <>
                  <dt>Objective Target</dt>
                  <dd>{objectiveTarget}</dd>
                </>}
                {adversarialTarget !== null && (
                  <>
                    <dt>Adversarial Target</dt>
                    <dd>{adversarialTarget}</dd>
                  </>
                )}
              </div>
              {techniques && <div className={styles.previewGroup}>
                <dt>Techniques</dt>
                <dd>
                  <div className={styles.previewBadges}>
                    {techniques.baseline && <Badge appearance="outline">baseline</Badge>}
                    {techniques.names.map((name) => (
                      <Badge key={name} appearance="outline">{name}</Badge>
                    ))}
                  </div>
                </dd>
              </div>}
              {techniqueOverride && <div className={styles.previewGroup}>
                <dt>Run-local technique edits</dt>
                <dd><Text>The registered {techniqueOverride.source_name} stays unchanged.</Text>
                  <pre className={styles.techniquePreview} aria-label="Technique edit preview">{JSON.stringify(techniqueOverride.changes, null, 2)}</pre>
                </dd>
              </div>}
              {datasets && <>
              <div className={styles.previewGroup}>
                <dt>Datasets</dt>
                <dd>
                  <div className={styles.previewStack}>
                    <Text>
                      {datasets.names.length > 0
                        ? datasets.names.join(', ')
                        : 'No datasets declared'}
                    </Text>
                    <Text size={200} className={styles.hint}>
                      {datasets.custom ? 'Custom override' : 'Scenario defaults'}
                      {` - dataset size: ${datasets.sizeLabel}`}
                    </Text>
                  </div>
                </dd>
              </div>
              <div className={styles.previewGroup}>
                <dt>Dataset filters</dt>
                <dd>
                  {datasets.harmCategories.length > 0 || datasets.dataTypes.length > 0 ? (
                    <dl className={styles.parameterPreview}>
                      {datasets.harmCategories.length > 0 && (
                        <div className={styles.parameterPreviewRow}>
                          <dt>Harm categories</dt>
                          <dd>{datasets.harmCategories.join(', ')}</dd>
                        </div>
                      )}
                      {datasets.dataTypes.length > 0 && (
                        <div className={styles.parameterPreviewRow}>
                          <dt>Data types</dt>
                          <dd>{datasets.dataTypes.join(', ')}</dd>
                        </div>
                      )}
                    </dl>
                  ) : (
                    'None'
                  )}
                </dd>
              </div>
              </>}
              <div className={styles.previewGroup}>
                <dt>Parameters</dt>
                <dd>
                  {dynamicParameters.length > 0 ? (
                    <dl className={styles.parameterPreview}>
                      {dynamicParameters.map((parameter) => (
                        <div className={styles.parameterPreviewRow} key={parameter.name}>
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
                  ) : (
                    'No scenario-specific parameters'
                  )}
                </dd>
              </div>
            </dl>
            <ScenarioRunEstimateDetails state={estimateState} />
          </DialogContent>
          <DialogActions>
            <Button
              appearance="secondary"
              disabled={submitting}
              onClick={onCancel}
            >
              Cancel
            </Button>
            <Button
              appearance="primary"
              disabled={confirmDisabled}
              onClick={onConfirm}
              data-testid="confirm-launch-scenario-btn"
            >
              {submitting ? 'Launching...' : 'Launch scan'}
            </Button>
          </DialogActions>
        </DialogBody>
      </DialogSurface>
    </Dialog>
  )
}
