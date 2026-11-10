import {
  Accordion,
  AccordionHeader,
  AccordionItem,
  AccordionPanel,
  Badge,
  Button,
  MessageBar,
  MessageBarBody,
  Spinner,
  Table,
  TableBody,
  TableCell,
  TableHeader,
  TableHeaderCell,
  TableRow,
  Text,
} from '@fluentui/react-components'

import { useAssertPolicyMetrics } from '@/hooks/useAssertPolicyMetrics'
import type {
  AssertGradeDistribution, AssertNotJudgedCounts, AssertPolicyMetrics, AssertTechniqueMetrics, BinaryRate,
} from '@/types'

import { useAssertPolicyMetricsPanelStyles } from './AssertPolicyMetricsPanel.styles'

const NODE_JUDGMENTS_UNAVAILABLE = 'node_judgments_unavailable'
const MIN_SUPPORT = 5
const NOT_JUDGED_LABELS: Record<keyof AssertNotJudgedCounts, string> = {
  target_input_refused: 'target blocked',
  target_error: 'target error',
  judge_no_verdict: 'no judge verdict',
  attack_error: 'attack error',
  not_scored: 'not scored',
}

interface RateColumn {
  key: string
  label: string
  rate: (technique: AssertTechniqueMetrics) => BinaryRate | null
  grades?: (technique: AssertTechniqueMetrics) => AssertGradeDistribution | undefined
}

interface AssertPolicyMetricsPanelProps {
  scenarioResultId: string
}

/** ASSERT's permissible/impermissible split, judge-dimension rates, and category rates for a completed run. */
export default function AssertPolicyMetricsPanel({ scenarioResultId }: AssertPolicyMetricsPanelProps) {
  const styles = useAssertPolicyMetricsPanelStyles()
  const { status, metrics, error, retry } = useAssertPolicyMetrics(scenarioResultId)
  return (
    <section className={styles.root} aria-labelledby="assert-metrics-heading" data-testid="assert-policy-metrics">
      <Text as="h2" id="assert-metrics-heading" size={500} weight="semibold">ASSERT policy metrics</Text>
      {status === 'loading' && <Spinner size="small" label="Loading ASSERT metrics..." />}
      {status === 'error' && (
        <MessageBar intent="error">
          <MessageBarBody>
            {`Couldn't load ASSERT metrics. ${error ?? ''}`}
            <Button appearance="transparent" onClick={retry}>Retry</Button>
          </MessageBarBody>
        </MessageBar>
      )}
      {status === 'success' && metrics && <MetricsBody metrics={metrics} />}
    </section>
  )
}

function MetricsBody({ metrics }: { metrics: AssertPolicyMetrics }) {
  const styles = useAssertPolicyMetricsPanelStyles()
  const techniques = Object.values(metrics.techniques)
  if (techniques.length === 0) {
    return <Text className={styles.hint}>No conversations were saved for this run.</Text>
  }
  const splitAvailable = metrics.split_unavailable_reason !== NODE_JUDGMENTS_UNAVAILABLE
  const dimensionNames = [...new Set(techniques.flatMap((technique) => Object.keys(technique.dimensions)))]
  const columns: RateColumn[] = [
    { key: 'impermissible', label: 'Impermissible violated', rate: (technique) => technique.policy_violation_on_not_permissible },
    { key: 'permissible', label: 'Permissible violated', rate: (technique) => technique.policy_violation_on_permissible },
    ...dimensionNames.map((name): RateColumn => ({
      key: name,
      label: name.replace(/_/g, ' '),
      rate: (technique) => technique.dimensions[name] ?? null,
      grades: (technique) => technique.dimension_grades[name],
    })),
  ]
  const notes = techniques.flatMap((technique) => exclusionNote(technique, splitAvailable))
  return (
    <>
      <Text className={styles.hint}>
        The impermissible and permissible rates count each judged conversation once for each stance whose policy
        categories the judge found relevant. Each cell shows violated / applicable conversations.
      </Text>
      {!splitAvailable && (
        <MessageBar intent="info">
          <MessageBarBody>
            The impermissible and permissible rates need per-category judgments. Run with the category_validated
            judgment mode to report them.
          </MessageBarBody>
        </MessageBar>
      )}
      {notes.length > 0 && (
        <MessageBar intent="warning">
          <MessageBarBody>{`Some conversations are left out of these rates. ${notes.join(' ')}`}</MessageBarBody>
        </MessageBar>
      )}
      <div className={styles.tableScroll}>
        <Table aria-label="ASSERT rates" size="small">
          <TableHeader>
            <TableRow>
              <TableHeaderCell>Technique</TableHeaderCell>
              <TableHeaderCell>Judged</TableHeaderCell>
              {columns.map((column) => <TableHeaderCell key={column.key}>{column.label}</TableHeaderCell>)}
            </TableRow>
          </TableHeader>
          <TableBody>
            {techniques.map((technique) => (
              <TableRow key={technique.technique}>
                <TableCell className={styles.label}>{techniqueLabel(technique.technique)}</TableCell>
                <TableCell>{`${technique.scored_total} of ${technique.total}`}</TableCell>
                {columns.map((column) => (
                  <TableCell key={column.key}>
                    <RateCell rate={column.rate(technique)} grades={column.grades?.(technique)} />
                  </TableCell>
                ))}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
      {techniques.some((technique) => technique.categories.length > 0) && (
        <Accordion collapsible>
          <AccordionItem value="categories">
            <AccordionHeader>Rates by policy category</AccordionHeader>
            <AccordionPanel><CategoryTable techniques={techniques} /></AccordionPanel>
          </AccordionItem>
        </Accordion>
      )}
    </>
  )
}

function exclusionNote(technique: AssertTechniqueMetrics, splitAvailable: boolean): string[] {
  const notJudged = (Object.keys(NOT_JUDGED_LABELS) as Array<keyof AssertNotJudgedCounts>)
    .filter((reason) => technique.not_judged[reason] > 0)
    .map((reason) => `${technique.not_judged[reason]} ${NOT_JUDGED_LABELS[reason]}`)
  const splitOnly = [
    technique.unresolved_policy > 0 ? `${technique.unresolved_policy} without a stored policy` : null,
    splitAvailable && technique.rows_without_node_judgments > 0
      ? `${technique.rows_without_node_judgments} without per-category judgments`
      : null,
  ].filter((reason): reason is string => reason !== null)
  const parts = [
    ...(notJudged.length > 0 ? [`not judged: ${notJudged.join(', ')}`] : []),
    ...(splitOnly.length > 0 ? [`left out of the stance rates only: ${splitOnly.join(', ')}`] : []),
  ]
  return parts.length > 0 ? [`${techniqueLabel(technique.technique)} ${parts.join('; ')}.`] : []
}

function CategoryTable({ techniques }: { techniques: AssertTechniqueMetrics[] }) {
  const styles = useAssertPolicyMetricsPanelStyles()
  const rows = new Map<string, { behavior: string; name: string; permissible: boolean }>()
  for (const technique of techniques) {
    for (const category of technique.categories) {
      rows.set(`${category.behavior_name}:${category.name}`, {
        behavior: category.behavior_name, name: category.name, permissible: category.permissible,
      })
    }
  }
  const qualify = new Set([...rows.values()].map((row) => row.behavior)).size > 1
  return (
    <div className={styles.tableScroll}>
      <Table aria-label="Rates by policy category" size="small">
        <TableHeader>
          <TableRow>
            <TableHeaderCell>Category</TableHeaderCell>
            <TableHeaderCell>Stance</TableHeaderCell>
            {techniques.map((technique) => (
              <TableHeaderCell key={technique.technique}>{techniqueLabel(technique.technique)}</TableHeaderCell>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {[...rows.entries()].map(([key, row]) => (
            <TableRow key={key}>
              <TableCell>{qualify ? `${row.behavior}: ${row.name}` : row.name}</TableCell>
              <TableCell>{row.permissible ? 'Permissible' : 'Impermissible'}</TableCell>
              {techniques.map((technique) => {
                const category = technique.categories.find(
                  (entry) => entry.behavior_name === row.behavior && entry.name === row.name,
                )
                return <TableCell key={technique.technique}><RateCell rate={category?.node_violation ?? null} /></TableCell>
              })}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}

function RateCell({ rate, grades }: { rate: BinaryRate | null; grades?: AssertGradeDistribution }) {
  const styles = useAssertPolicyMetricsPanelStyles()
  if (!rate || rate.rate === null) return <Text className={styles.hint}>—</Text>
  const notApplicable = rate.not_applicable_count > 0 ? ` · ${rate.not_applicable_count} n/a` : ''
  return (
    <div className={styles.rateCell}>
      <Text weight="semibold">{`${(rate.rate * 100).toFixed(1)}%`}</Text>
      <Text size={200} className={styles.hint}>{`${rate.flagged_count} / ${rate.applicable_count}${notApplicable}`}</Text>
      {!rate.sufficient_support && (
        <Badge appearance="outline" color="warning" size="small">{`n < ${MIN_SUPPORT}`}</Badge>
      )}
      {grades && (
        <Text size={200} className={styles.hint}>
          {`median ${grades.median ?? '—'} · ${Object.entries(grades.counts).map(([grade, count]) => `${grade}: ${count}`).join(', ')}`}
        </Text>
      )}
    </div>
  )
}

function techniqueLabel(technique: string): string {
  return technique === 'baseline' ? 'Baseline' : technique
}
