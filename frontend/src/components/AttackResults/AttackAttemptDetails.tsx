import { useState } from 'react'

import { Accordion, AccordionHeader, AccordionItem, AccordionPanel, Badge, MessageBar, MessageBarBody, Table, TableBody, TableCell, TableHeader, TableHeaderCell, TableRow, Text } from '@fluentui/react-components'
import { Link } from 'react-router'

import RegistryIdentityRow from '@/components/Registry/RegistryIdentityRow'
import type {
  ScenarioActiveConversation,
  ScenarioAttackTechniqueDetails,
  ScenarioHierarchyNode,
  ScenarioObjectiveScorer,
  ScenarioProgressResult,
  ScenarioRunPlanAtomicGroup,
} from '@/types'

import ComponentIdentityDetails, { DetailMetric } from './ComponentIdentityDetails'
import ObjectiveScorerDetails from './ObjectiveScorerDetails'
import {
  formatAttackSuccess,
  formatDuration,
  formatScore,
  formatScoreRationale,
  formatTimestamp,
} from './attackAttemptFormatting'
import { useAttackAttemptDetailsStyles } from './AttackAttemptDetails.styles'

interface AttackAttemptDetailsProps {
  readonly attempt: ScenarioProgressResult | ScenarioActiveConversation | ScenarioHierarchyNode
  readonly objective?: string | null
  readonly atomicGroup?: ScenarioRunPlanAtomicGroup | null
  readonly techniqueDetails?: ScenarioAttackTechniqueDetails | null
  readonly objectiveScorer?: ScenarioObjectiveScorer | null
  /** Route to the attempt's conversation. Supplied by the host so this stays route-agnostic. */
  readonly conversationPath: string | null
  readonly conversationState?: unknown
}

export default function AttackAttemptDetails({
  attempt,
  objective,
  atomicGroup,
  techniqueDetails,
  objectiveScorer,
  conversationPath,
  conversationState,
}: AttackAttemptDetailsProps) {
  const styles = useAttackAttemptDetailsStyles()
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const completed = 'outcome' in attempt ? attempt : null
  const techniqueName = atomicGroup?.technique_name ?? atomicGroup?.display_group ?? attempt.atomic_attack_name
  const seeds = 'seed_identifiers' in attempt ? attempt.seed_identifiers ?? [] : []
  const datasetNames = [...new Set(seeds.flatMap((seed) =>
    typeof seed.parameters.dataset_name === 'string' ? [seed.parameters.dataset_name] : []))]
  const identities = [
    { key: 'technique', name: techniqueName, kind: 'Technique', type: techniqueDetails?.component_name ?? attempt.atomic_attack_name, identity: techniqueDetails },
    { key: 'scorer', name: objectiveScorer?.component_name ?? 'Not recorded', kind: 'Scorer', type: 'Objective scorer', identity: objectiveScorer },
    { key: 'dataset', name: datasetNames.join(', ') || 'Dataset seeds', kind: 'Dataset', type: 'Seed group', identity: seeds.length ? seeds : null },
  ]
  const techniqueDescription = atomicGroup?.description ?? 'No description is available for this attack technique.'
  const techniqueTags = atomicGroup?.tags ?? []
  const errorMessage = completed?.outcome === 'error'
    ? completed.error_message ?? 'No error detail was persisted.'
    : null

  function toggle(key: string): void {
    setExpanded((current: Set<string>) => {
      const next = new Set(current)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  return (
    <div className={styles.content}>
      <section className={styles.section}>
        <Text size={200} className={styles.sectionLabel}>Objective</Text>
        <Text as="p" className={styles.bodyText}>{objective ?? 'Objective text is not available.'}</Text>
      </section>
      <section className={styles.surface} aria-label="Attack outcome">
        <div><Badge appearance="tint" color={!completed ? 'brand' : completed.outcome === 'success' ? 'success' : completed.outcome === 'error' ? 'danger' : 'informative'}>
          {completed ? formatAttackSuccess(completed.outcome) : 'In progress'}
        </Badge></div>
        <Text as="p" className={styles.bodyText}>
          {!completed ? 'The outcome will appear after scoring.' : completed.outcome === 'error'
            ? 'This attack did not produce a scored outcome.' : formatScoreRationale(completed.score?.score_rationale)}
        </Text>
      </section>
      {completed && <div className={styles.summary}>
        <DetailMetric label="Attack Success" value={formatAttackSuccess(completed.outcome)} />
        <DetailMetric label="Score" value={formatScore(completed.score)} />
        <DetailMetric label="Execution time" value={formatDuration(completed.execution_time_ms)} />
        <DetailMetric label="Retries" value={String(completed.total_retries)} />
        <DetailMetric label="Timestamp" value={formatTimestamp(completed.timestamp)} />
      </div>}
      <section className={styles.section}>
        <Text size={200} className={styles.sectionLabel}>Attack technique</Text>
        <div className={styles.surface}>
          <Text weight="semibold">{techniqueName}</Text>
          <Text as="p" className={styles.bodyText}>{techniqueDescription}</Text>
          {techniqueTags.length > 0 && (
            <div className={styles.badgeList} aria-label="Technique tags">
              {techniqueTags.map((tag) => <Badge key={tag} appearance="tint">{tag}</Badge>)}
            </div>
          )}
          {techniqueDetails && <ComponentIdentityDetails identity={techniqueDetails} hideComponentName />}
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
          <Text as="p" className={styles.bodyText}>{formatScoreRationale(completed?.score?.score_rationale)}</Text>
        </div>
      </section>
      {conversationPath ? (
        <Link className={styles.conversationLink} to={conversationPath} state={conversationState}>
          {completed ? 'Open conversation' : 'Watch live conversation'}
        </Link>
      ) : (
        <Text>No conversation was saved for this result.</Text>
      )}
      <Table size="small" aria-label="Attack components">
        <TableHeader><TableRow><TableHeaderCell>Name</TableHeaderCell><TableHeaderCell>Component</TableHeaderCell><TableHeaderCell>Type</TableHeaderCell></TableRow></TableHeader>
        <TableBody>{identities.map((item: typeof identities[number]) => item.identity ? (
          <RegistryIdentityRow key={item.key} name={item.name} identity={item.identity} columns={3}
            expanded={expanded.has(item.key)} onToggle={() => { toggle(item.key) }}>
            <TableCell>{item.kind}</TableCell><TableCell>{item.type}</TableCell>
          </RegistryIdentityRow>
        ) : (
          <TableRow key={item.key}><TableCell>{item.name}</TableCell><TableCell>{item.kind}</TableCell><TableCell>No saved identity</TableCell></TableRow>
        ))}</TableBody>
      </Table>
      {completed?.outcome === 'error' && <Accordion collapsible><AccordionItem value="error">
        <AccordionHeader>Error details{completed.error_type ? ` · ${completed.error_type}` : ''}</AccordionHeader>
        <AccordionPanel><Text className={styles.bodyText}>{completed.error_message ?? 'No error detail is available.'}</Text></AccordionPanel>
      </AccordionItem></Accordion>}
      {completed && <Text size={200} className={styles.sectionLabel}>
        {formatDuration(completed.execution_time_ms)} · {completed.total_retries} retries · {formatTimestamp(completed.timestamp)}
      </Text>}
      {errorMessage && (
        <MessageBar intent="error">
          <MessageBarBody>{completed?.error_type ? `${completed.error_type}: ` : ''}{errorMessage}</MessageBarBody>
        </MessageBar>
      )}
    </div>
  )
}
