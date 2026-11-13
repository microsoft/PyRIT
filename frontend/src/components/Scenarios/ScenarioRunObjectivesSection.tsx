import { useMemo } from 'react'

import { Button, Table, TableBody, TableCell, TableHeader, TableHeaderCell, TableRow, Text } from '@fluentui/react-components'

import { formatProgressSuccess, objectivePreview } from '@/components/AttackResults/attackAttemptFormatting'
import type {
  ScenarioActiveConversation,
  ScenarioComponentIdentity,
  ScenarioProgressResult,
  ScenarioProgressSummary,
  ScenarioRunPlan,
  ScenarioRunPlanSeedGroup,
} from '@/types'

import { useScenarioRunPageStyles } from './ScenarioRunPage.styles'

interface ScenarioRunObjectivesSectionProps {
  readonly summary: ScenarioProgressSummary
  readonly plan: ScenarioRunPlan | null
  readonly results: readonly ScenarioProgressResult[]
  readonly activeConversations: readonly ScenarioActiveConversation[]
  readonly onOpenObjective: (objective: ScenarioRunPlanSeedGroup, trigger: HTMLElement) => void
}

function seedDatasetNames(identities: ScenarioComponentIdentity[]): string {
  return [...new Set(identities.flatMap((identity: ScenarioComponentIdentity) =>
    typeof identity.parameters.dataset_name === 'string' ? [identity.parameters.dataset_name] : []))].join(', ')
}

/** Each objective's dataset, attack success, and completion, opening its seed prompts and attacks. */
export default function ScenarioRunObjectivesSection({
  summary, plan, results, activeConversations, onOpenObjective,
}: ScenarioRunObjectivesSectionProps) {
  const styles = useScenarioRunPageStyles()
  const planSeeds = useMemo(
    () => new Map(plan?.seed_groups.map((seed) => [seed.id, seed]) ?? []),
    [plan],
  )
  const datasetsBySeed = useMemo(() => {
    const identities = new Map<string, ScenarioComponentIdentity[]>()
    for (const attempt of [...results, ...activeConversations]) {
      if (!attempt.seed_group_id) continue
      const seeds = identities.get(attempt.seed_group_id) ?? []
      seeds.push(...(attempt.seed_identifiers ?? []))
      identities.set(attempt.seed_group_id, seeds)
    }
    return new Map([...identities].map(([id, seeds]) => [id, seedDatasetNames(seeds)]))
  }, [results, activeConversations])

  return (
    <section className={styles.section} aria-labelledby="objectives-heading">
      <Text as="h2" id="objectives-heading" size={500} weight="semibold">Datasets &amp; objectives</Text>
      <div className={styles.tableScroll}><Table size="small" aria-label="Datasets and objectives">
        <TableHeader><TableRow>
          <TableHeaderCell>Dataset</TableHeaderCell><TableHeaderCell>Objective</TableHeaderCell>
          <TableHeaderCell>Attack success</TableHeaderCell><TableHeaderCell>Completed</TableHeaderCell>
        </TableRow></TableHeader>
        <TableBody>{summary.seed_groups.map((seed) => (
          <TableRow key={seed.id}>
            <TableCell>{datasetsBySeed.get(seed.id) || 'Not recorded'}</TableCell>
            <TableCell><Button appearance="transparent" className={styles.objectiveLinkButton} onClick={(event) => {
              onOpenObjective(
                planSeeds.get(seed.id) ?? { id: seed.id, objective_sha256: '', objective: seed.objective ?? 'Not recorded', prompts: [] },
                event.currentTarget,
              )
            }}>
              {objectivePreview(seed.objective ?? null, seed.id)}
            </Button></TableCell>
            <TableCell>{formatProgressSuccess(seed)}</TableCell>
            <TableCell>{seed.completed}{seed.planned === null ? '' : ` / ${seed.planned}`}</TableCell>
          </TableRow>
        ))}</TableBody>
      </Table></div>
    </section>
  )
}
