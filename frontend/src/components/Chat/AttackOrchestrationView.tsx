import { useEffect, useMemo, useState } from 'react'
import {
  Badge,
  Breadcrumb,
  BreadcrumbDivider,
  BreadcrumbItem,
  MessageBar,
  MessageBarBody,
  Spinner,
  Text,
  mergeClasses,
} from '@fluentui/react-components'
import { Link } from 'react-router'
import { attacksApi } from '../../services/api'
import type { AttackSummary } from '../../types'
import { attackRoutePath, scenarioRunRoutePath } from '../../utils/routeParams'
import { childAttackResultIds } from './attackOrchestration'
import { useAttackOrchestrationViewStyles } from './AttackOrchestrationView.styles'

interface AttackOrchestrationViewProps {
  readonly attackSummary: AttackSummary
  readonly scenarioResultId?: string | null
}

interface ChildResultLoad {
  readonly attackResultId: string
  readonly summary: AttackSummary | null
}

interface ChildLoadState {
  readonly key: string
  readonly results: ChildResultLoad[]
}

type BadgeColor = 'success' | 'danger' | 'warning' | 'informative'

export default function AttackOrchestrationView({
  attackSummary,
  scenarioResultId,
}: AttackOrchestrationViewProps) {
  const styles = useAttackOrchestrationViewStyles()
  const childIds = useMemo(() => childAttackResultIds(attackSummary), [attackSummary])
  const childLoadKey = `${attackSummary.attack_result_id}:${childIds.join(',')}`
  const [childLoadState, setChildLoadState] = useState<ChildLoadState>({
    key: '',
    results: [],
  })

  useEffect(() => {
    if (childIds.length === 0) {
      return
    }

    let cancelled = false
    Promise.all(childIds.map(async (attackResultId): Promise<ChildResultLoad> => {
      try {
        const summary = await attacksApi.getAttack(attackResultId)
        return { attackResultId, summary }
      } catch {
        return { attackResultId, summary: null }
      }
    })).then((results) => {
      if (!cancelled) {
        setChildLoadState({ key: childLoadKey, results })
      }
    })

    return () => {
      cancelled = true
    }
  }, [childIds, childLoadKey])

  const isLoadingChildren = childIds.length > 0 && childLoadState.key !== childLoadKey
  const title = scenarioResultId ? 'Adaptive orchestration result' : 'Sequential attack result'
  const completionPolicy = attackSummary.metadata?.completion_policy

  return (
    <div className={styles.root} data-testid="attack-orchestration-view">
      {scenarioResultId && (
        <div className={styles.breadcrumbBar}>
          <Breadcrumb aria-label="Attack provenance" size="small">
            <BreadcrumbItem>
              <Text size={200}>Scenario History</Text>
            </BreadcrumbItem>
            <BreadcrumbDivider />
            <BreadcrumbItem>
              <Link
                className={styles.breadcrumbLink}
                to={scenarioRunRoutePath(scenarioResultId)}
                aria-label={`Return to scenario run ${scenarioResultId}`}
              >
                Scenario run {scenarioResultId.slice(0, 8)}
              </Link>
            </BreadcrumbItem>
          </Breadcrumb>
        </div>
      )}
      <div className={styles.scrollArea}>
        <main className={styles.content}>
          <section className={styles.summary} aria-labelledby="orchestration-result-heading">
            <div className={styles.titleRow}>
              <h1 id="orchestration-result-heading" className={styles.title}>{title}</h1>
              <Badge appearance="tint" color={outcomeColor(attackSummary.outcome)}>
                {formatOutcome(attackSummary.outcome)}
              </Badge>
            </div>
            <Text className={styles.description}>
              This record summarizes the ordered technique executions for one objective.
              It does not contain target messages itself; open an executed technique below
              to inspect its conversation.
            </Text>
            <dl className={styles.facts}>
              <div className={mergeClasses(styles.fact, styles.objectiveFact)}>
                <dt>Objective</dt>
                <dd>{attackSummary.objective || 'Unavailable'}</dd>
              </div>
              <div className={styles.fact}>
                <dt>Completion policy</dt>
                <dd>{formatCompletionPolicy(completionPolicy)}</dd>
              </div>
              <div className={styles.fact}>
                <dt>Executed techniques</dt>
                <dd>{childIds.length}</dd>
              </div>
              <div className={styles.fact}>
                <dt>Execution time</dt>
                <dd>{formatDuration(attackSummary.execution_time_ms)}</dd>
              </div>
            </dl>
          </section>

          <section className={styles.attemptsSection} aria-labelledby="technique-attempts-heading">
            <h2 id="technique-attempts-heading" className={styles.sectionHeading}>Technique attempts</h2>
            <Text className={styles.sectionDescription}>
              {completionPolicyDescription(completionPolicy)}
            </Text>
            {childIds.length === 0 ? (
              <MessageBar intent="warning">
                <MessageBarBody>
                  This legacy orchestration result does not contain persisted child-result links.
                </MessageBarBody>
              </MessageBar>
            ) : isLoadingChildren ? (
              <div className={styles.loading}>
                <Spinner label="Loading technique attempts..." />
              </div>
            ) : (
              <ol className={styles.attemptList}>
                {childLoadState.results.map((childResult, index) => (
                  <ChildResultRow
                    key={`${childResult.attackResultId}:${index}`}
                    childResult={childResult}
                    fallbackAttemptIndex={index + 1}
                    scenarioResultId={scenarioResultId}
                  />
                ))}
              </ol>
            )}
          </section>
        </main>
      </div>
    </div>
  )
}

interface ChildResultRowProps {
  readonly childResult: ChildResultLoad
  readonly fallbackAttemptIndex: number
  readonly scenarioResultId?: string | null
}

function ChildResultRow({
  childResult,
  fallbackAttemptIndex,
  scenarioResultId,
}: ChildResultRowProps) {
  const styles = useAttackOrchestrationViewStyles()
  const summary = childResult.summary
  const attemptIndex = summary?.labels?._adaptive_attempt ?? String(fallbackAttemptIndex)
  const techniqueName = summary?.labels?._adaptive_technique_name ?? summary?.attack_type ?? 'Unavailable technique'
  const outcome = formatOutcome(summary?.outcome)
  const messageCount = summary?.message_count
  const linkLabel = messageCount && messageCount > 0 ? 'Open conversation' : 'Open result'

  return (
    <li className={styles.attemptRow}>
      <div className={styles.attemptInfo}>
        <div className={styles.attemptTitleRow}>
          <Text className={styles.attemptName}>
            Attempt {attemptIndex}: {techniqueName}
          </Text>
          {summary && (
            <Badge appearance="tint" color={outcomeColor(summary.outcome)}>{outcome}</Badge>
          )}
        </div>
        <Text className={styles.attemptMeta}>
          {summary
            ? `${summary.attack_type} · ${formatMessageCount(summary.message_count)}`
            : `Result ${childResult.attackResultId} could not be loaded`}
        </Text>
      </div>
      <Link
        className={styles.childLink}
        to={attackRoutePath(childResult.attackResultId, scenarioResultId)}
        aria-label={`${linkLabel} for attempt ${attemptIndex}: ${techniqueName}`}
      >
        {linkLabel}
      </Link>
    </li>
  )
}

function formatCompletionPolicy(completionPolicy: string | undefined): string {
  if (completionPolicy === 'first_success') {
    return 'First success'
  }
  if (!completionPolicy) {
    return 'Unavailable'
  }
  return completionPolicy
    .replace(/_/g, ' ')
    .replace(/^\w/, (letter) => letter.toUpperCase())
}

function completionPolicyDescription(completionPolicy: string | undefined): string {
  switch (completionPolicy) {
    case 'first_success':
      return 'Techniques run in this stored order and stop after the first successful result.'
    case 'first_decisive':
      return 'Techniques run in this stored order and stop after the first success or error.'
    case 'strict_all':
      return 'Techniques run in this stored order and stop after the first non-successful result.'
    case 'exhaustive':
      return 'Every technique runs in this stored order regardless of intermediate outcomes.'
    case 'last_result':
      return 'Every technique runs in this stored order, and the final result determines the outcome.'
    default:
      return 'Techniques are shown in their persisted execution order.'
  }
}

function formatOutcome(outcome: AttackSummary['outcome'] | undefined): string {
  if (!outcome) {
    return 'Undetermined'
  }
  return outcome.replace(/^\w/, (letter) => letter.toUpperCase())
}

function outcomeColor(outcome: AttackSummary['outcome'] | undefined): BadgeColor {
  if (outcome === 'success') {
    return 'success'
  }
  if (outcome === 'failure' || outcome === 'error') {
    return 'danger'
  }
  if (outcome === 'undetermined') {
    return 'warning'
  }
  return 'informative'
}

function formatDuration(milliseconds: number | undefined): string {
  if (milliseconds === undefined || !Number.isFinite(milliseconds) || milliseconds < 0) {
    return 'Unavailable'
  }
  const totalSeconds = Math.floor(milliseconds / 1_000)
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  return minutes > 0 ? `${minutes}m ${seconds}s` : `${seconds}s`
}

function formatMessageCount(messageCount: number): string {
  return `${messageCount} ${messageCount === 1 ? 'message' : 'messages'}`
}
