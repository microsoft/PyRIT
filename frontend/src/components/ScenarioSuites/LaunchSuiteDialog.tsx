import { type FormEvent, useEffect, useMemo, useState } from 'react'

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
  mergeClasses,
} from '@fluentui/react-components'
import { useNavigate } from 'react-router'

import ChatScorerPicker from '@/components/Chat/ChatScorerPicker'
import TargetSelect from '@/components/Config/TargetSelect'
import HelpField from '@/components/HelpField'
import { HELP_DEFINITIONS } from '@/components/helpDefinitions'
import { useUserPreferences } from '@/hooks/useUserPreferences'
import { useRuntime } from '@/hooks/useRuntime'
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
  RegisteredComponentReference,
  ScenarioReadiness,
  ScenarioSuiteEntryStatus,
  ScenarioSuiteReadinessRequest,
  ScenarioSuiteResponse,
  ScorerInstance,
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
  scorers?: ScorerInstance[]
  scorersLoading?: boolean
  scorersError?: string | null
  onDismiss: () => void
  onSuiteChanged: () => void
}

type EntryReadiness =
  | { state: 'loading' }
  | { state: 'error'; detail: string }
  | { state: 'ready'; readiness: ScenarioReadiness }

/**
 * Present the library's resolution of one entry's objective scorer. The server owns precedence and
 * binding; this only names the value behind the source it reports.
 */
function entryScorerLabel(
  entry: ScenarioSuiteEntryStatus,
  readiness: EntryReadiness,
  override: RegisteredComponentReference | null,
): string {
  if (readiness.state === 'loading') return 'Resolving...'
  if (readiness.state === 'error') return `Could not resolve (${readiness.detail})`
  const forEntry = (item: { entry_id?: string | null }) => item.entry_id === entry.entry_id
  const matches = (item: { entry_id?: string | null; role: string }) =>
    forEntry(item) && item.role === 'objective_scorer'
  const binding = readiness.readiness.bindings.find(matches)
  const issue = readiness.readiness.issues.find(matches)
  if (!binding && !issue) {
    // A scenario that picks from compatible scorers reports candidates instead of one scorer.
    if (readiness.readiness.bindings.some(
      (item) => forEntry(item) && item.role.startsWith('objective_scorer_candidate:'),
    )) {
      return 'Scenario default scorer'
    }
    // Readiness could not configure the entry far enough to resolve a scorer.
    const reasons = readiness.readiness.issues.filter(forEntry).map((item) => item.reason)
    return reasons.length > 0 ? `Unresolved (${reasons.join('; ')})` : 'Unresolved'
  }
  const source = binding?.source ?? issue?.source
  if (source === 'unsupported') return 'Scenario default scorer (selected scorer not applicable)'
  const configured = source === 'launch'
    ? { name: override?.registry_name, origin: 'launch override' }
    : source === 'preset'
      ? { name: entry.launch_defaults?.objective_scorer, origin: 'from preset' }
      : null
  const name = configured?.name ?? 'Scenario default scorer'
  const details = [configured?.origin, issue ? `unavailable: ${issue.reason}` : undefined].filter(Boolean)
  return details.length > 0 ? `${name} (${details.join('; ')})` : name
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
  scorers,
  scorersLoading,
  scorersError,
  onDismiss,
  onSuiteChanged,
}: LaunchSuiteDialogProps) {
  const styles = useLaunchSuiteDialogStyles()
  const navigate = useNavigate()
  const runtime = useRuntime()
  const [openedGeneration] = useState(runtime.generation)
  const staleSelection = openedGeneration !== runtime.generation
  const { rememberScanTargets } = useUserPreferences()
  const adversarialTargets = targets.filter(
    (target) => target.capabilities?.supports_multi_turn === true,
  )
  const [selectedTargetName, setTargetName] = useState<string>()
  const [selectedAdversarialTargetName, setAdversarialTargetName] = useState<string>()
  const targetName = initialTargetName(
    selectedTargetName === undefined ? defaultObjectiveTarget
      : targets.find((target: TargetInstance) => target.target_registry_name === selectedTargetName) ?? null,
    targets,
  )
  const adversarialTargetName = initialTargetName(
    selectedAdversarialTargetName === undefined ? defaultAdversarialTarget
      : adversarialTargets.find((target: TargetInstance) => target.target_registry_name === selectedAdversarialTargetName) ?? null,
    adversarialTargets,
  )
  const [maxConcurrency, setMaxConcurrency] = useState(DEFAULT_MAX_CONCURRENCY)
  const [maxRetries, setMaxRetries] = useState(DEFAULT_MAX_RETRIES)
  const [scorerOverride, setScorerOverride] = useState<RegisteredComponentReference | null>(null)
  const readinessRequest = useMemo<ScenarioSuiteReadinessRequest>(() => ({
    expected_version: suite.version,
    entries: suite.entry_statuses.map((entry) => ({
      entry_id: entry.entry_id,
      preset_name: entry.preset_name,
      origin: entry.origin,
      preset_version: entry.preset_version,
    })),
    ...(targetName === '' ? {} : { target_name: targetName }),
    ...(adversarialTargetName === '' ? {} : { adversarial_target_name: adversarialTargetName }),
    ...(scorerOverride ? { objective_scorer: scorerOverride } : {}),
  }), [suite, targetName, adversarialTargetName, scorerOverride])
  const [readinessResult, setReadinessResult] = useState<{
    request: ScenarioSuiteReadinessRequest
    result: EntryReadiness
  } | null>(null)
  // A result for an earlier selection is stale; show it as resolving until the current one arrives.
  const readiness: EntryReadiness = readinessResult?.request === readinessRequest
    ? readinessResult.result
    : { state: 'loading' }
  // The library reports these for the current selection; launching would refuse them.
  const blockingIssues = readiness.state === 'ready' ? readiness.readiness.issues : []
  // A launch-level issue (e.g. a missing target) arrives once per entry; list each message once.
  const issueMessages = [...new Set(blockingIssues.map(
    (issue) => `${issue.component}: ${issue.reason} ${issue.remediation}`,
  ))]
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (staleSelection) onDismiss()
  }, [staleSelection, onDismiss])

  useEffect(() => {
    if (staleSelection || !runtime.ready) return
    const controller = new AbortController()
    scenarioSuitesApi.getReadiness(suite.suite.name, readinessRequest, controller.signal)
      .then((result: ScenarioReadiness) => {
        setReadinessResult({ request: readinessRequest, result: { state: 'ready', readiness: result } })
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return
        setReadinessResult({ request: readinessRequest, result: { state: 'error', detail: toApiError(err).detail } })
      })
    return () => controller.abort()
  }, [staleSelection, runtime.ready, suite.suite.name, readinessRequest])

  const handleSubmit = async (event: FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault()
    if (staleSelection || !runtime.ready || targetName === '' || submitting || !defaultsReady || blockingIssues.length > 0) {
      return
    }

    const request: LaunchScenarioSuiteRequest = {
      ...readinessRequest,
      target_name: targetName,
      max_concurrency: maxConcurrency,
      max_retries: maxRetries,
      ...(operationId ? { operation_id: operationId } : {}),
      ...(Object.keys(labels).length > 0 ? { labels } : {}),
    }

    setSubmitting(true)
    setError(null)
    try {
      const execution = await scenarioSuitesApi.launch(suite.suite.name, request)
      rememberScanTargets(targets, request)
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

  if (staleSelection) return null

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
                      <Text
                        as="p"
                        size={200}
                        className={mergeClasses(
                          styles.entryScorer,
                          blockingIssues.some((issue) => issue.entry_id === entry.entry_id && issue.role === 'objective_scorer')
                            ? styles.entryIssue : styles.summaryText,
                        )}
                        data-testid={`launch-suite-entry-scorer-${entry.entry_id}`}
                      >
                        Scorer: {entryScorerLabel(entry, readiness, scorerOverride)}
                      </Text>
                    </li>
                  ))}
                </ol>
              </div>
              {blockingIssues.length > 0 && (
                <MessageBar intent="warning" aria-label="Suite configuration issues" className={styles.issueBar}>
                  <MessageBarBody>
                    <ul className={styles.issueList}>
                      {issueMessages.map((message) => (
                        <li key={message}>{message}</li>
                      ))}
                    </ul>
                  </MessageBarBody>
                </MessageBar>
              )}
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
                help={HELP_DEFINITIONS.objectiveTarget}
                hint="The registered target every suite entry attacks."
                placeholder="Select a target"
                disabled={submitting}
              />
              <TargetSelect
                targets={adversarialTargets}
                value={adversarialTargetName}
                onChange={(target) => setAdversarialTargetName(target?.target_registry_name ?? '')}
                label="Adversarial Target"
                help={HELP_DEFINITIONS.adversarialTarget}
                hint="Only used by suite entries whose scenarios require a second target."
                placeholder="Use server default"
                disabled={submitting}
              />
              <ChatScorerPicker
                label="Objective scorer override"
                usage="scenario"
                emptyOptionLabel="Use each preset's scorer"
                help="Replaces the objective scorer of every entry whose scenario accepts one, for this launch only. Other entries use their scenario default."
                value={scorerOverride}
                scorers={scorers ?? []}
                loading={scorersLoading}
                error={scorersError}
                disabled={submitting}
                onChange={setScorerOverride}
              />
              <HelpField label="Max concurrency per run" help={HELP_DEFINITIONS.maxConcurrency}>
                <SingleStepSpinButton
                  className={styles.numberInput}
                  value={maxConcurrency}
                  min={MIN_MAX_CONCURRENCY}
                  max={MAX_MAX_CONCURRENCY}
                  disabled={submitting}
                  onChange={(_, data) => setMaxConcurrency(resolveSpinButtonValue(data, maxConcurrency))}
                  data-testid="suite-max-concurrency-input"
                />
              </HelpField>
              <HelpField label="Max retries per run" help={HELP_DEFINITIONS.maxRetries}>
                <SingleStepSpinButton
                  className={styles.numberInput}
                  value={maxRetries}
                  min={MIN_MAX_RETRIES}
                  max={MAX_MAX_RETRIES}
                  disabled={submitting}
                  onChange={(_, data) => setMaxRetries(resolveSpinButtonValue(data, maxRetries))}
                  data-testid="suite-max-retries-input"
                />
              </HelpField>
            </DialogContent>
            <DialogActions>
              <Button appearance="secondary" onClick={onDismiss} disabled={submitting}>
                Cancel
              </Button>
              <Button
                appearance="primary"
                type="submit"
                disabled={!runtime.ready || targetName === '' || submitting || !defaultsReady || blockingIssues.length > 0}
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
