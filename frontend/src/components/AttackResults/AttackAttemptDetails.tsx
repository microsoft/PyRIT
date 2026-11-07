import { Button, MessageBar, MessageBarBody, Text, mergeClasses } from '@fluentui/react-components'
import { Link } from 'react-router'

import AttachToFindingsButton from '@/components/Chat/AttachToFindingsButton'
import DetailFacts, { DetailFact } from '@/components/DetailFacts'
import FindingSeverityBadge from '@/components/Operations/FindingSeverityBadge'
import OutcomeBadge from '@/components/OutcomeBadge'
import IdentityDisclosure from '@/components/Registry/IdentityDisclosure'
import { useAttackFindings } from '@/hooks/useAttackFindings'
import type {
  AttackDisplayOutcome,
  ScenarioActiveConversation,
  ScenarioAttackTechniqueDetails,
  ScenarioHierarchyNode,
  ScenarioObjectiveScorer,
  ScenarioProgressResult,
  ScenarioRunPlanAtomicGroup,
} from '@/types'
import { scoreSummaryFromProgressScore } from '@/utils/scoreSummary'

import { formatAttemptTiming, formatScoreRationale } from './attackAttemptFormatting'
import { useAttackAttemptDetailsStyles } from './AttackAttemptDetails.styles'

interface AttackAttemptDetailsProps {
  /** A saved result or hierarchy node, a running attack, or null before the attack starts. */
  readonly attempt: ScenarioProgressResult | ScenarioActiveConversation | ScenarioHierarchyNode | null
  /** The attack has not started yet but will; otherwise a missing attempt is unavailable. */
  readonly preparing?: boolean
  readonly objective?: string | null
  readonly atomicGroup?: ScenarioRunPlanAtomicGroup | null
  readonly techniqueDetails?: ScenarioAttackTechniqueDetails | null
  readonly objectiveScorer?: ScenarioObjectiveScorer | null
  /** Route to the attempt's conversation. Supplied by the host so this stays route-agnostic. */
  readonly conversationPath: string | null
  readonly conversationState?: unknown
  /** Offered first when attaching this attack to a finding. */
  readonly findingOperationId?: string | null
  /** Frame the details as a standalone card when they are shown inline on a page. */
  readonly framed?: boolean
}

/** One attack's objective and outcome, the components it ran with, its findings, and its conversation. */
export default function AttackAttemptDetails({
  attempt,
  preparing = false,
  objective,
  atomicGroup,
  techniqueDetails,
  objectiveScorer,
  conversationPath,
  conversationState,
  findingOperationId,
  framed = false,
}: AttackAttemptDetailsProps) {
  const styles = useAttackAttemptDetailsStyles()
  const completed = attempt && 'outcome' in attempt ? attempt : null
  const { findings, refresh: refreshFindings } = useAttackFindings(completed?.attack_result_id)
  const techniqueName = atomicGroup?.technique_name ?? atomicGroup?.display_group ?? attempt?.atomic_attack_name ?? 'Not recorded'
  const seeds = attempt && 'seed_identifiers' in attempt ? attempt.seed_identifiers ?? [] : []
  const datasetNames = [...new Set(seeds.flatMap((seed) =>
    typeof seed.parameters.dataset_name === 'string' ? [seed.parameters.dataset_name] : []))]
  const outcome: AttackDisplayOutcome = completed ? completed.outcome
    : attempt ? 'in_progress' : preparing ? 'preparing' : 'undetermined'
  const rationale = completed
    ? completed.outcome === 'error' ? 'This attack did not produce a scored outcome.' : formatScoreRationale(completed.score?.score_rationale)
    : attempt || preparing ? 'The outcome will appear after scoring.' : 'Attack details are not available.'

  return (
    <div className={mergeClasses(styles.content, framed && styles.framed)}>
      <DetailFacts label="Attack outcome">
        <DetailFact label="Objective:" tone="primary" valueClassName={styles.objective}>
          {objective ?? 'Objective text is not available.'}
        </DetailFact>
        <DetailFact label="Outcome:" tone="primary">
          <OutcomeBadge appearance="tint" outcome={outcome} labelStyle="success"
            label={!attempt && !preparing ? 'Not available' : undefined}
            score={completed?.score ? scoreSummaryFromProgressScore(completed.score) : null}
            scoreButtonLabel="View attack score" />
          <Text className={styles.rationale}>{rationale}</Text>
          {completed && <Text size={200} className={styles.meta} aria-label="Attack timing">
            {formatAttemptTiming(completed)}
          </Text>}
          {completed?.outcome === 'error' && <MessageBar intent="error" className={styles.error}>
            <MessageBarBody>
              {completed.error_type ? `${completed.error_type}: ` : ''}{completed.error_message ?? 'No error detail was persisted.'}
            </MessageBarBody>
          </MessageBar>}
        </DetailFact>
      </DetailFacts>
      <DetailFacts label="Attack components" className={mergeClasses(styles.divided, styles.components)}>
        <DetailFact label="Technique"><IdentityDisclosure name={techniqueName} identity={techniqueDetails} /></DetailFact>
        <DetailFact label="Objective scorer">
          <IdentityDisclosure name={objectiveScorer?.component_name ?? 'Not recorded'} identity={objectiveScorer} />
        </DetailFact>
        <DetailFact label="Dataset">
          <IdentityDisclosure name={datasetNames.join(', ') || 'Not recorded'} identity={seeds.length > 0 ? seeds : null} />
        </DetailFact>
        {findings.length > 0 && <DetailFact label="Findings">
          <ul className={styles.findings} aria-label="Attached findings">
            {findings.map((finding) => <li key={finding.evidence_id} className={styles.finding}>
              <FindingSeverityBadge finding={finding} />
              <Text>{finding.title}</Text>
            </li>)}
          </ul>
        </DetailFact>}
      </DetailFacts>
      <div className={mergeClasses(styles.actions, styles.divided)}>
        {conversationPath ? (
          <Link className={styles.conversationLink} to={conversationPath} state={conversationState}>
            {completed ? 'Open conversation' : 'Watch live conversation'}
          </Link>
        ) : !attempt && preparing ? (
          <Button disabled>Watch live conversation</Button>
        ) : (
          <Text className={styles.meta}>No conversation was saved for this result.</Text>
        )}
        {attempt && <AttachToFindingsButton attackResultId={attempt.attack_result_id} defaultOperationId={findingOperationId}
          disabled={!completed} onAttached={refreshFindings} />}
      </div>
    </div>
  )
}
