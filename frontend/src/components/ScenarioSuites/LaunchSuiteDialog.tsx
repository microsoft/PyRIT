import { type FormEvent, useState } from 'react'

import {
  Badge,
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
import { scenarioSuitesApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type {
  LaunchScenarioSuiteRequest,
  ScenarioSuiteResponse,
  TargetInstance,
} from '@/types'

import { useLaunchSuiteDialogStyles } from './LaunchSuiteDialog.styles'
import { suiteExecutionRoutePath } from './suiteRoutes'

interface LaunchSuiteDialogProps {
  suite: ScenarioSuiteResponse
  targets: TargetInstance[]
  defaultObjectiveTarget: TargetInstance | null
  defaultAdversarialTarget: TargetInstance | null
  labels: Record<string, string>
  /** The saved Operation that every child run joins, or null for none. */
  operationId: string | null
  /** False while server defaults or the global Operation selection are unresolved. */
  defaultsReady: boolean
  onDismiss: () => void
  onSuiteChanged: () => void
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

export default function LaunchSuiteDialog({
  suite,
  targets,
  defaultObjectiveTarget,
  defaultAdversarialTarget,
  labels,
  operationId,
  defaultsReady,
  onDismiss,
  onSuiteChanged,
}: LaunchSuiteDialogProps) {
  const styles = useLaunchSuiteDialogStyles()
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
    if (targetName === '' || submitting || !defaultsReady) {
      return
    }

    const request: LaunchScenarioSuiteRequest = {
      expected_version: suite.version,
      entries: suite.entry_statuses.map((entry) => ({
        entry_id: entry.entry_id,
        preset_name: entry.preset_name,
        origin: entry.origin,
        preset_version: entry.preset_version,
      })),
      target_name: targetName,
      ...(adversarialTargetName === '' ? {} : { adversarial_target_name: adversarialTargetName }),
      max_concurrency: maxConcurrency,
      max_retries: maxRetries,
      ...(operationId ? { operation_id: operationId } : {}),
      ...(Object.keys(labels).length > 0 ? { labels } : {}),
    }

    setSubmitting(true)
    setError(null)
    try {
      const execution = await scenarioSuitesApi.launch(suite.suite.name, request)
      navigate(suiteExecutionRoutePath(execution.suite_execution_id))
    } catch (err: unknown) {
      const apiError = toApiError(err)
      if (apiError.status === 409) {
        onSuiteChanged()
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
            <DialogTitle>Launch {suite.suite.name}</DialogTitle>
            <DialogContent className={styles.body}>
              <div className={styles.summary}>
                <Text size={200} className={styles.summaryText}>
                  Queues {suite.suite.entries.length} scenario run
                  {suite.suite.entries.length === 1 ? '' : 's'} in this order.
                </Text>
                <ol className={styles.entryList} data-testid="launch-suite-entries">
                  {suite.entry_statuses.map((entry) => (
                    <li key={entry.entry_id}>
                      <span className={styles.entryIdentity}>
                        <Text size={200}>{entry.preset_name}</Text>
                        {entry.origin === 'registered' && (
                          <Badge appearance="tint" color="informative" size="small">
                            Defined in code
                          </Badge>
                        )}
                      </span>
                    </li>
                  ))}
                </ol>
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
                hint="The registered target every suite entry attacks."
                placeholder="Select a target"
                disabled={submitting}
              />
              <TargetSelect
                targets={adversarialTargets}
                value={adversarialTargetName}
                onChange={(target) => setAdversarialTargetName(target?.target_registry_name ?? '')}
                label="Adversarial Target"
                hint="Only used by suite entries whose scenarios require a second target."
                placeholder="Use server default"
                disabled={submitting}
              />
              <Field label="Max concurrency per run">
                <SingleStepSpinButton
                  className={styles.numberInput}
                  value={maxConcurrency}
                  min={MIN_MAX_CONCURRENCY}
                  max={MAX_MAX_CONCURRENCY}
                  disabled={submitting}
                  onChange={(_, data) => setMaxConcurrency(resolveSpinButtonValue(data, maxConcurrency))}
                  data-testid="suite-max-concurrency-input"
                />
              </Field>
              <Field label="Max retries per run">
                <SingleStepSpinButton
                  className={styles.numberInput}
                  value={maxRetries}
                  min={MIN_MAX_RETRIES}
                  max={MAX_MAX_RETRIES}
                  disabled={submitting}
                  onChange={(_, data) => setMaxRetries(resolveSpinButtonValue(data, maxRetries))}
                  data-testid="suite-max-retries-input"
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
                disabled={targetName === '' || submitting || !defaultsReady}
                data-testid="confirm-launch-suite"
              >
                {submitting ? 'Launching...' : 'Launch suite'}
              </Button>
            </DialogActions>
          </DialogBody>
        </form>
      </DialogSurface>
    </Dialog>
  )
}
