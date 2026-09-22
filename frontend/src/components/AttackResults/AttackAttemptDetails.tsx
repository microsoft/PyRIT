import { useId, useState } from 'react'

import { Badge, Button, MessageBar, MessageBarBody, Text, mergeClasses } from '@fluentui/react-components'
import { ChevronDownRegular, ChevronUpRegular } from '@fluentui/react-icons'
import { Link } from 'react-router'

import type {
  ScenarioAttackTechniqueDetails,
  ScenarioComponentIdentity,
  ScenarioObjectiveScorer,
  ScenarioProgressResult,
  ScenarioRunPlanAtomicGroup,
} from '@/types'
import {
  basenameFromValue,
  buildMediaUrl,
  dataTypeToAttachmentKind,
  isPathDataType,
} from '@/utils/media'

import ComponentIdentityDetails, {
  DetailMetric,
  type IdentityChildRenderContext,
} from './ComponentIdentityDetails'
import ObjectiveScorerDetails from './ObjectiveScorerDetails'
import {
  formatAttackSuccess,
  formatDuration,
  formatScore,
  formatScoreRationale,
  formatTimestamp,
} from './attackAttemptFormatting'
import { useAttackAttemptDetailsStyles } from './AttackAttemptDetails.styles'

const COLLAPSIBLE_SEED_LENGTH = 320

interface AttackAttemptDetailsProps {
  /** The persisted attempt to describe. */
  readonly attempt: ScenarioProgressResult
  /** Objective text, when the run plan still has it. */
  readonly objective?: string | null
  /** Planned group the attempt belongs to, used for technique identity. */
  readonly atomicGroup?: ScenarioRunPlanAtomicGroup | null
  /** Projected technique details shared by every attempt in the atomic group. */
  readonly techniqueDetails?: ScenarioAttackTechniqueDetails | null
  /** Scorer that decided attack success. */
  readonly objectiveScorer?: ScenarioObjectiveScorer | null
  /** Route to the attempt's conversation. Supplied by the host so this stays route-agnostic. */
  readonly conversationPath: string
}

export default function AttackAttemptDetails({
  attempt,
  objective,
  atomicGroup,
  techniqueDetails,
  objectiveScorer,
  conversationPath,
}: AttackAttemptDetailsProps) {
  const styles = useAttackAttemptDetailsStyles()

  const techniqueName = atomicGroup?.technique_name
    ?? atomicGroup?.display_group
    ?? attempt.atomic_attack_name
  const techniqueDescription = atomicGroup?.description
    ?? 'No description is available for this attack technique.'
  const techniqueTags = atomicGroup?.tags ?? []
  const errorMessage = attempt.outcome === 'error'
    ? attempt.error_message ?? 'No error detail was persisted.'
    : null

  const renderTechniqueSeed = ({ childName, identity }: IdentityChildRenderContext) => {
    if (childName !== 'technique_seeds') {
      return undefined
    }
    return <TechniqueSeed identity={identity} />
  }

  return (
    <div className={styles.content}>
      <section className={styles.section}>
        <Text size={200} className={styles.sectionLabel}>Objective</Text>
        <Text as="p" className={styles.bodyText}>
          {objective ?? 'Objective text unavailable for this legacy attempt.'}
        </Text>
      </section>

      <div className={styles.summary}>
        <DetailMetric label="Attack Success" value={formatAttackSuccess(attempt.outcome)} />
        <DetailMetric label="Score" value={formatScore(attempt.score)} />
        <DetailMetric label="Execution time" value={formatDuration(attempt.execution_time_ms)} />
        <DetailMetric label="Retries" value={String(attempt.total_retries)} />
        <DetailMetric label="Timestamp" value={formatTimestamp(attempt.timestamp)} />
      </div>

      <section className={styles.section}>
        <Text size={200} className={styles.sectionLabel}>Attack technique</Text>
        <div className={styles.surface}>
          <Text weight="semibold">{techniqueName}</Text>
          <Text as="p" className={styles.bodyText}>{techniqueDescription}</Text>
          {techniqueTags.length > 0 && (
            <div className={styles.badgeList} aria-label="Technique tags">
              {techniqueTags.map((tag) => (
                <Badge key={tag} appearance="tint">{tag}</Badge>
              ))}
            </div>
          )}
          {techniqueDetails && (
            <ComponentIdentityDetails
              identity={techniqueDetails}
              hideComponentName
              renderChild={renderTechniqueSeed}
            />
          )}
        </div>
      </section>

      <section className={styles.section}>
        <Text size={200} className={styles.sectionLabel}>Objective Scorer</Text>
        {objectiveScorer
          ? <ObjectiveScorerDetails scorer={objectiveScorer} />
          : <Text>Objective scorer information is unavailable for this attempt.</Text>}
      </section>

      <section className={styles.section}>
        <Text size={200} className={styles.sectionLabel}>Score rationale</Text>
        <div className={styles.surface}>
          <Text as="p" className={styles.bodyText}>
            {formatScoreRationale(attempt.score?.score_rationale)}
          </Text>
        </div>
      </section>

      <Link className={styles.conversationLink} to={conversationPath}>
        View conversation
      </Link>

      {errorMessage && (
        <MessageBar intent="error">
          <MessageBarBody>
            {attempt.error_type ? `${attempt.error_type}: ` : ''}
            {errorMessage}
          </MessageBarBody>
        </MessageBar>
      )}
    </div>
  )
}

interface TechniqueSeedProps {
  readonly identity: ScenarioComponentIdentity
}

function TechniqueSeed({ identity }: TechniqueSeedProps) {
  const styles = useAttackAttemptDetailsStyles()
  const value = typeof identity.parameters.value === 'string' ? identity.parameters.value : ''
  const dataType = typeof identity.parameters.data_type === 'string'
    ? identity.parameters.data_type
    : 'text'

  return (
    <div className={styles.seed}>
      <Text weight="semibold">{identity.component_name}</Text>
      <TechniqueSeedValue componentName={identity.component_name} dataType={dataType} value={value} />
    </div>
  )
}

interface TechniqueSeedValueProps {
  readonly componentName: string
  readonly dataType: string
  readonly value: string
}

function TechniqueSeedValue({ componentName, dataType, value }: TechniqueSeedValueProps) {
  const styles = useAttackAttemptDetailsStyles()
  if (!value) {
    return <Text>No seed content is available.</Text>
  }
  if (!isPathDataType(dataType)) {
    return <TextTechniqueSeed componentName={componentName} value={value} />
  }

  const kind = dataTypeToAttachmentKind(dataType)
  const mediaUrl = buildMediaUrl(value)
  if (kind === 'image') {
    return <img className={styles.seedImage} src={mediaUrl} alt={`${componentName} technique seed`} />
  }
  if (kind === 'audio') {
    return <audio className={styles.seedMedia} src={mediaUrl} controls aria-label={`${componentName} technique seed`} />
  }
  if (kind === 'video') {
    return <video className={styles.seedMedia} src={mediaUrl} controls aria-label={`${componentName} technique seed`} />
  }
  return (
    <a className={styles.fileLink} href={mediaUrl} target="_blank" rel="noopener noreferrer">
      {basenameFromValue(value, 'Technique seed')}
    </a>
  )
}

interface TextTechniqueSeedProps {
  readonly componentName: string
  readonly value: string
}

function TextTechniqueSeed({ componentName, value }: TextTechniqueSeedProps) {
  const styles = useAttackAttemptDetailsStyles()
  const contentId = useId()
  const [expanded, setExpanded] = useState(false)
  const canExpand = value.length > COLLAPSIBLE_SEED_LENGTH

  return (
    <>
      <Text
        as="p"
        id={contentId}
        className={mergeClasses(styles.bodyText, canExpand && !expanded && styles.collapsedSeedText)}
      >
        {value}
      </Text>
      {canExpand && (
        <Button
          appearance="transparent"
          size="small"
          className={styles.seedToggle}
          icon={expanded ? <ChevronUpRegular /> : <ChevronDownRegular />}
          aria-controls={contentId}
          aria-expanded={expanded}
          aria-label={expanded ? `Collapse ${componentName}` : `Show full ${componentName}`}
          onClick={() => setExpanded((current) => !current)}
        >
          {expanded ? 'Show less' : 'Show more'}
        </Button>
      )}
    </>
  )
}
