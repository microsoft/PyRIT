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
} from '@fluentui/react-components'
import { useNavigate } from 'react-router'

import TargetSelect from '@/components/Config/TargetSelect'
import CreateTargetDialog from '@/components/Config/CreateTargetDialog'
import CreateScorerDialog from '@/components/Scorers/CreateScorerDialog'
import ChatScorerPicker from '@/components/Chat/ChatScorerPicker'
import { useScorerRegistry } from '@/hooks/useScorerRegistry'
import { useRuntime } from '@/hooks/useRuntime'
import HelpField from '@/components/HelpField'
import { HELP_DEFINITIONS } from '@/components/helpDefinitions'
import { useUserPreferences } from '@/hooks/useUserPreferences'
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
import { resolvedRoleLabel } from '@/utils/targetIdentity'
import type {
  LaunchScenarioSuiteRequest,
  ScenarioSuiteResponse,
  TargetInstance,
  ScorerInstance,
  RegisteredComponentReference,
  ScenarioReadiness,
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
  const runtime = useRuntime()
  const scorerRegistry = useScorerRegistry()
  const [selectionGeneration, setSelectionGeneration] = useState(runtime.generation)
  const [createdTargets, setCreatedTargets] = useState<Array<{ generation: string; target: TargetInstance }>>([])
  const [createdScorers, setCreatedScorers] = useState<Array<{ generation: string; scorer: ScorerInstance }>>([])
  const availableTargets = [...targets, ...createdTargets.filter((entry) => entry.generation === runtime.generation)
    .map((entry) => entry.target).filter((entry) => !targets.some((target) => target.target_registry_name === entry.target_registry_name))]
  const availableScorers = [...scorerRegistry.scorers, ...createdScorers.filter((entry) => entry.generation === runtime.generation)
    .map((entry) => entry.scorer).filter((entry) => !scorerRegistry.scorers.some((scorer) => scorer.scorer_registry_name === entry.scorer_registry_name))]
  const [createRole, setCreateRole] = useState<'objective' | 'scoring' | 'adversarial' | null>(null)
  const [createScorer, setCreateScorer] = useState(false)
  const [creationGeneration, setCreationGeneration] = useState(runtime.generation)
  const [scoringTargetName, setScoringTargetName] = useState('')
  const [objectiveScorer, setObjectiveScorer] = useState<RegisteredComponentReference | null>(null)
  const { rememberScanTargets } = useUserPreferences()
  const adversarialTargets = availableTargets.filter(
    (target) => target.capabilities?.supports_multi_turn === true,
  )
  const [selectedTargetName, setTargetName] = useState<string>()
  const [selectedAdversarialTargetName, setAdversarialTargetName] = useState<string>()
  const targetName = initialTargetName(
    selectedTargetName === undefined ? defaultObjectiveTarget
      : availableTargets.find((target: TargetInstance) => target.target_registry_name === selectedTargetName) ?? null,
    availableTargets,
  )
  const adversarialTargetName = initialTargetName(
    selectedAdversarialTargetName === undefined ? defaultAdversarialTarget
      : adversarialTargets.find((target: TargetInstance) => target.target_registry_name === selectedAdversarialTargetName) ?? null,
    adversarialTargets,
  )
  const [maxConcurrency, setMaxConcurrency] = useState(DEFAULT_MAX_CONCURRENCY)
  const [maxRetries, setMaxRetries] = useState(DEFAULT_MAX_RETRIES)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const request = useMemo<LaunchScenarioSuiteRequest>(() => ({
    expected_version: suite.version,
    entries: suite.entry_statuses.map((entry) => ({
      entry_id: entry.entry_id, preset_name: entry.preset_name, origin: entry.origin, preset_version: entry.preset_version,
    })),
    target_name: targetName,
    ...(adversarialTargetName ? { adversarial_target_name: adversarialTargetName } : {}),
    ...(scoringTargetName ? { scoring_target_name: scoringTargetName } : {}),
    ...(objectiveScorer ? { objective_scorer: objectiveScorer } : {}),
    max_concurrency: maxConcurrency, max_retries: maxRetries,
    ...(operationId ? { operation_id: operationId } : {}),
    ...(Object.keys(labels).length > 0 ? { labels } : {}),
  }), [suite, targetName, adversarialTargetName, scoringTargetName, objectiveScorer,
    maxConcurrency, maxRetries, operationId, labels])
  const requestKey = JSON.stringify([runtime.generation, request.expected_version, request.entries,
    targetName, adversarialTargetName, scoringTargetName, objectiveScorer])
  const [roleState, setRoleState] = useState<{ key: string; result?: ScenarioReadiness; error?: string } | null>(null)
  useEffect(() => {
    if (!runtime.ready) return
    const controller = new AbortController()
    void scenarioSuitesApi.readiness(suite.suite.name,
      { ...request, target_name: targetName || undefined }, controller.signal).then((result) => {
        if (!controller.signal.aborted) setRoleState({ key: requestKey, result })
      }).catch((failure: unknown) => {
        if (!controller.signal.aborted) setRoleState({ key: requestKey, error: toApiError(failure).detail })
      })
    return () => { controller.abort() }
  }, [runtime.ready, request, requestKey, suite.suite.name, targetName])
  const readiness = roleState?.key === requestKey ? roleState.result : undefined
  const rolesReady = Boolean(readiness && readiness.issues.length === 0)
    && selectionGeneration === runtime.generation && runtime.ready
  const usesRole = (role: string): boolean => readiness
    ? [...readiness.bindings, ...readiness.issues].some((entry) => entry.role === role) : false
  const roleIssues = (role: string) => readiness?.issues.filter((issue) => issue.role === role)
    .map((issue) => <Text key={`${issue.component}:${issue.reason}`} role="alert">
      {issue.component}: {issue.reason} {issue.remediation}
    </Text>)
  const beginTargetCreation = (role: 'objective' | 'scoring' | 'adversarial'): void => {
    setCreationGeneration(runtime.generation)
    setCreateRole(role)
  }

  const handleSubmit = async (event: FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault()
    if (!rolesReady || targetName === '' || submitting || !defaultsReady) {
      return
    }

    setSubmitting(true)
    setError(null)
    try {
      const execution = await scenarioSuitesApi.launch(suite.suite.name, request)
      rememberScanTargets(availableTargets, request)
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
    <><Dialog open={!createRole && !createScorer} onOpenChange={(_, data) => {
      if (!data.open && !createRole && !createScorer) onDismiss()
    }}>
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
                targets={availableTargets}
                value={targetName}
                onChange={(target) => { setTargetName(target?.target_registry_name ?? ''); setSelectionGeneration(runtime.generation) }}
                label="Target"
                help={HELP_DEFINITIONS.objectiveTarget}
                hint="The registered target every suite entry attacks."
                placeholder="Select a target"
                disabled={submitting}
              />
              <Button disabled={submitting} onClick={() => beginTargetCreation('objective')}>Create objective target</Button>
              {roleIssues('objective_target')}
              <ChatScorerPicker value={objectiveScorer} scorers={availableScorers} onChange={setObjectiveScorer}
                usage="scenario" label="Objective scorer" loading={scorerRegistry.loading}
                error={scorerRegistry.error} disabled={submitting}
                defaultLabel={readiness?.bindings.some((binding) => binding.role === 'objective_scorer')
                  ? `Scenario rubrics: ${[...new Set(readiness.bindings.filter((entry) => entry.role === 'objective_scorer')
                    .map((entry) => entry.identifier.class_name))].join(', ')}` : 'No configured default scorer'} />
              <Button disabled={submitting} onClick={() => { setCreationGeneration(runtime.generation); setCreateScorer(true) }}>
                Create objective scorer
              </Button>
              {roleIssues('objective_scorer')}
              {usesRole('scoring_target') && <>
                <TargetSelect targets={availableTargets} value={scoringTargetName}
                  onChange={(target) => setScoringTargetName(target?.target_registry_name ?? '')}
                  label="Scoring Target"
                  placeholder={resolvedRoleLabel(readiness, 'scoring_target', availableTargets, 'No configured scoring target')}
                  disabled={submitting} />
                <Button disabled={submitting} onClick={() => beginTargetCreation('scoring')}>Create scoring target</Button>
                {roleIssues('scoring_target')}
              </>}
              {usesRole('adversarial_target') && <><TargetSelect
                targets={adversarialTargets}
                value={adversarialTargetName}
                onChange={(target) => setAdversarialTargetName(target?.target_registry_name ?? '')}
                label="Adversarial Target"
                help={HELP_DEFINITIONS.adversarialTarget}
                hint="Only used by suite entries whose scenarios require a second target."
                placeholder={resolvedRoleLabel(readiness, 'adversarial_target', availableTargets, 'No configured generation target')}
                disabled={submitting}
              />
              <Button disabled={submitting} onClick={() => beginTargetCreation('adversarial')}>Create adversarial target</Button>
              {roleIssues('adversarial_target')}</>}
              {roleState?.key === requestKey && roleState.error && <MessageBar intent="error">
                <MessageBarBody role="alert">{roleState.error}</MessageBarBody>
              </MessageBar>}
              {readiness && readiness.issues.length > 0 && <MessageBar intent="warning">
                <MessageBarBody role="alert" aria-label="Suite configuration issues">
                  No child can start until all suite entries are ready.
                  <ul>{readiness.issues.map((issue) => <li key={`${issue.component}:${issue.role}:${issue.reason}`}>
                    {issue.component}, {issue.role}: {issue.reason} {issue.remediation}
                  </li>)}</ul>
                </MessageBarBody>
              </MessageBar>}
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
                disabled={!rolesReady || targetName === '' || submitting || !defaultsReady}
                data-testid="confirm-launch-suite"
              >
                {submitting ? 'Launching...' : 'Launch suite'}
              </Button>
            </DialogActions>
          </DialogBody>
        </form>
      </DialogSurface>
    </Dialog>
      {createRole && <CreateTargetDialog open existingTargets={availableTargets} onClose={() => setCreateRole(null)}
        onCreated={(target) => {
          if (creationGeneration === runtime.generation) {
            setCreatedTargets((current) => [...current, { generation: runtime.generation, target }])
            if (createRole === 'objective') { setTargetName(target.target_registry_name); setSelectionGeneration(runtime.generation) }
            if (createRole === 'scoring') setScoringTargetName(target.target_registry_name)
            if (createRole === 'adversarial') setAdversarialTargetName(target.target_registry_name)
          }
          setCreateRole(null)
        }} />}
      {createScorer && <CreateScorerDialog targets={availableTargets} scorers={availableScorers}
        onClose={() => setCreateScorer(false)} onCreated={(scorer) => {
          if (creationGeneration === runtime.generation) {
            setCreatedScorers((current) => [...current, { generation: runtime.generation, scorer }])
            setObjectiveScorer({ registry_name: scorer.scorer_registry_name, identifier_hash: scorer.identifier.hash })
          }
          setCreateScorer(false)
        }} />}
    </>
  )
}
