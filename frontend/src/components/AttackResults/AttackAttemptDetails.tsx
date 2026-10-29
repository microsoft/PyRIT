import { useState } from 'react'

import { Accordion, AccordionHeader, AccordionItem, AccordionPanel, Badge, Table, TableBody, TableCell, TableHeader, TableHeaderCell, TableRow, Text } from '@fluentui/react-components'
import { ChatRegular } from '@fluentui/react-icons'
import { Link } from 'react-router'

import RegistryIdentityRow from '@/components/Registry/RegistryIdentityRow'
import type {
  ScenarioActiveConversation, ScenarioAttackTechniqueDetails, ScenarioObjectiveScorer,
  ScenarioProgressResult, ScenarioRunPlanAtomicGroup,
} from '@/types'

import { formatAttackSuccess, formatDuration, formatScoreRationale, formatTimestamp } from './attackAttemptFormatting'
import { useAttackAttemptDetailsStyles } from './AttackAttemptDetails.styles'

interface AttackAttemptDetailsProps {
  readonly attempt: ScenarioProgressResult | ScenarioActiveConversation
  readonly objective?: string | null
  readonly atomicGroup?: ScenarioRunPlanAtomicGroup | null
  readonly techniqueDetails?: ScenarioAttackTechniqueDetails | null
  readonly objectiveScorer?: ScenarioObjectiveScorer | null
  readonly conversationPath: string
}

export default function AttackAttemptDetails({
  attempt, objective, atomicGroup, techniqueDetails, objectiveScorer, conversationPath,
}: AttackAttemptDetailsProps) {
  const styles = useAttackAttemptDetailsStyles()
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const completed = 'outcome' in attempt ? attempt : null
  const techniqueName = atomicGroup?.technique_name ?? atomicGroup?.display_group ?? attempt.atomic_attack_name
  const seeds = attempt.seed_identifiers ?? []
  const datasetNames = [...new Set(seeds.flatMap((seed) =>
    typeof seed.parameters.dataset_name === 'string' ? [seed.parameters.dataset_name] : []))]
  const identities = [
    { key: 'technique', name: techniqueName, kind: 'Technique', type: techniqueDetails?.component_name ?? attempt.atomic_attack_name, identity: techniqueDetails },
    { key: 'scorer', name: objectiveScorer?.component_name ?? 'Not recorded', kind: 'Scorer', type: 'Objective scorer', identity: objectiveScorer },
    { key: 'dataset', name: datasetNames.join(', ') || 'Dataset seeds', kind: 'Dataset', type: 'Seed group', identity: seeds.length ? seeds : null },
  ]

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
      <Link className={styles.conversationLink} to={conversationPath}>
        <ChatRegular /> {completed ? 'Open conversation' : 'Watch live conversation'}
      </Link>
      <section className={styles.surface} aria-label="Attack outcome">
        <div><Badge appearance="tint" color={!completed ? 'brand' : completed.outcome === 'success' ? 'success' : completed.outcome === 'error' ? 'danger' : 'informative'}>
          {completed ? formatAttackSuccess(completed.outcome) : 'In progress'}
        </Badge></div>
        <Text as="p" className={styles.bodyText}>
          {!completed ? 'The outcome will appear after scoring.' : completed.outcome === 'error'
            ? 'This attack did not produce a scored outcome.' : formatScoreRationale(completed.score?.score_rationale)}
        </Text>
      </section>
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
    </div>
  )
}
