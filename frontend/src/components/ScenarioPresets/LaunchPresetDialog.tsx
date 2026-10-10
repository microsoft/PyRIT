import { type FormEvent, useState } from 'react'

import {
  Button,
  Dialog,
  DialogActions,
  DialogBody,
  DialogContent,
  DialogSurface,
  DialogTitle,
  Field,
  MessageBar,
  MessageBarBody,
  Text,
} from '@fluentui/react-components'
import { useNavigate } from 'react-router'

import TargetSelect from '@/components/Config/TargetSelect'
import SingleStepSpinButton from '@/components/Parameters/SingleStepSpinButton'
import {
  DEFAULT_MAX_CONCURRENCY,
  DEFAULT_MAX_RETRIES,
  MAX_MAX_CONCURRENCY,
  MAX_MAX_RETRIES,
  MIN_MAX_CONCURRENCY,
  MIN_MAX_RETRIES,
  resolveSpinButtonValue,
} from '@/components/Scenarios/scenarioRunLimits'
import { scenarioPresetsApi, scenariosApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { ScenarioPreset, TargetInstance } from '@/types'
import { scenarioRunRoutePath } from '@/utils/routeParams'

import { useLaunchPresetDialogStyles } from './LaunchPresetDialog.styles'

interface LaunchPresetDialogProps {
  preset: ScenarioPreset
  version: string
  targets: TargetInstance[]
  defaultObjectiveTarget: TargetInstance | null
  defaultAdversarialTarget: TargetInstance | null
  labels: Record<string, string>
  onDismiss: () => void
  onPresetChanged: () => void
}

function initialTargetName(
  candidate: TargetInstance | null,
  available: TargetInstance[],
): string {
  if (!candidate) {
    return ''
  }
  const match = available.some(
    (target) => target.target_registry_name === candidate.target_registry_name,
  )
  return match ? candidate.target_registry_name : ''
}

/**
 * Restates what the preset pins. The dialog covers the library card, so without
 * this the operator commits to a run knowing only the preset and scenario name.
 * Omitted fields read as scenario defaults because that is what the server does
 * with them.
 */
function presetSummary(preset: ScenarioPreset): string {
  const techniques = preset.techniques ?? []
  const datasets = preset.dataset_names ?? []
  const parts = [
    techniques.length === 0
      ? 'Scenario default techniques'
      : `${techniques.length} technique${techniques.length === 1 ? '' : 's'}`,
    datasets.length === 0 ? 'scenario default datasets' : datasets.join(', '),
  ]
  if (preset.max_dataset_size != null) {
    parts.push(`max dataset size ${preset.max_dataset_size}`)
  }
  if (preset.include_baseline != null) {
    parts.push(preset.include_baseline ? 'baseline included' : 'baseline excluded')
  }
  return parts.join(' · ')
}

/**
 * Collects the launch-owned fields a preset deliberately omits, then asks the
 * server to merge them with the stored preset. Resolution stays server-side so
 * the browser never reimplements the preset-to-run mapping.
 *
 * The summary above describes the preset as it was read, so the launch sends that
 * version back. An edit landing in between is reported rather than launched, which
 * is the one case where a run would otherwise differ from what was confirmed.
 */
export default function LaunchPresetDialog({
  preset,
  version,
  targets,
  defaultObjectiveTarget,
  defaultAdversarialTarget,
  labels,
  onDismiss,
  onPresetChanged,
}: LaunchPresetDialogProps) {
  const styles = useLaunchPresetDialogStyles()
  const navigate = useNavigate()
  const adversarialTargets = targets.filter(
    (target) => target.capabilities?.supports_multi_turn === true,
  )
  const [targetName, setTargetName] = useState(
    () => initialTargetName(defaultObjectiveTarget, targets),
  )
  const [adversarialTargetName, setAdversarialTargetName] = useState(
    () => initialTargetName(defaultAdversarialTarget, adversarialTargets),
  )
  const [maxConcurrency, setMaxConcurrency] = useState(DEFAULT_MAX_CONCURRENCY)
  const [maxRetries, setMaxRetries] = useState(DEFAULT_MAX_RETRIES)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleSubmit = async (event: FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault()
    if (targetName === '' || submitting) {
      return
    }
    setSubmitting(true)
    setError(null)
    try {
      const request = await scenarioPresetsApi.resolve(preset.name, {
        expected_version: version,
        target_name: targetName,
        ...(adversarialTargetName === '' ? {} : { adversarial_target_name: adversarialTargetName }),
        max_concurrency: maxConcurrency,
        max_retries: maxRetries,
        ...(Object.keys(labels).length > 0 ? { labels } : {}),
      })
      const summary = await scenariosApi.startRun(request)
      navigate(scenarioRunRoutePath(summary.scenario_result_id), {
        state: { scenarioName: preset.scenario_name },
      })
    } catch (err) {
      const apiError = toApiError(err)
      if (apiError.status === 409) {
        // The summary on screen no longer describes the preset, so re-reading is the
        // only way forward; keeping the dialog open would invite a confirm-and-retry loop.
        onPresetChanged()
        return
      }
      setError(apiError.detail)
      setSubmitting(false)
    }
  }

  return (
    <Dialog open onOpenChange={(_, data) => { if (!data.open) onDismiss() }}>
      <DialogSurface>
        <form onSubmit={handleSubmit}>
          <DialogBody>
            <DialogTitle>Launch {preset.name}</DialogTitle>
            <DialogContent className={styles.body}>
              <div className={styles.summary}>
                <Text className={styles.scenarioLine} size={200}>
                  Runs <strong>{preset.scenario_name}</strong> with this preset&apos;s saved configuration.
                </Text>
                <Text
                  className={styles.scenarioLine}
                  size={200}
                  data-testid="launch-preset-summary"
                >
                  {presetSummary(preset)}
                </Text>
              </div>
              {error && (
                <MessageBar intent="error">
                  <MessageBarBody>{error}</MessageBarBody>
                </MessageBar>
              )}
              <TargetSelect
                targets={targets}
                value={targetName}
                onChange={(target) => setTargetName(target?.target_registry_name ?? '')}
                label="Target"
                hint="The registered target this run attacks."
                placeholder="Select a target"
                disabled={submitting}
              />
              <TargetSelect
                targets={adversarialTargets}
                value={adversarialTargetName}
                onChange={(target) => setAdversarialTargetName(target?.target_registry_name ?? '')}
                label="Adversarial Target"
                hint="Only used by scenarios that generate attacks with a second target."
                placeholder="Use server default"
                disabled={submitting}
              />
              <Field label="Max concurrency">
                <SingleStepSpinButton
                  className={styles.numberInput}
                  value={maxConcurrency}
                  min={MIN_MAX_CONCURRENCY}
                  max={MAX_MAX_CONCURRENCY}
                  disabled={submitting}
                  onChange={(_, data) => setMaxConcurrency(resolveSpinButtonValue(data, maxConcurrency))}
                  data-testid="preset-max-concurrency-input"
                />
              </Field>
              <Field label="Max retries">
                <SingleStepSpinButton
                  className={styles.numberInput}
                  value={maxRetries}
                  min={MIN_MAX_RETRIES}
                  max={MAX_MAX_RETRIES}
                  disabled={submitting}
                  onChange={(_, data) => setMaxRetries(resolveSpinButtonValue(data, maxRetries))}
                  data-testid="preset-max-retries-input"
                />
              </Field>
            </DialogContent>
            <DialogActions>
              <Button appearance="secondary" onClick={onDismiss} disabled={submitting}>
                Cancel
              </Button>
              <Button
                appearance="primary"
                type="submit"
                disabled={targetName === '' || submitting}
                data-testid="confirm-launch-preset"
              >
                {submitting ? 'Launching...' : 'Launch'}
              </Button>
            </DialogActions>
          </DialogBody>
        </form>
      </DialogSurface>
    </Dialog>
  )
}
