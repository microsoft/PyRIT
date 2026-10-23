import { Fragment, useId, useState } from 'react'

import { Button, Divider, mergeClasses, Table, TableBody, TableCell, TableHeader, TableHeaderCell, TableRow, Text, Tooltip } from '@fluentui/react-components'
import { ArrowSyncRegular, ChevronDownRegular, ChevronRightRegular } from '@fluentui/react-icons'

import ChatScorerPicker from '@/components/Chat/ChatScorerPicker'
import { useUserPreferences } from '@/hooks/useUserPreferences'
import type { RegisteredComponentReference, ScorerInstance } from '@/types'
import {
  formatScorerComparisonMetric, scorerComparisonValue, scorerEvidenceLabel, scorerMetricDescription, scorerMetricLabel,
} from '@/utils/scorerPresentation'

import { useConverterRegistryStyles } from './Registry.styles'
import { useScorerRegistryStyles } from './ScorerRegistry.styles'

interface ScorerRegistryProps {
  scorers: ScorerInstance[]
  loading: boolean
  error: string | null
  onRefresh: () => void
}

type ScorerFamily = 'true_false' | 'float_scale' | 'other'

interface ScorerTableSection {
  family: ScorerFamily
  title: string
  metrics: string[]
}

interface ScorerTableProps extends ScorerTableSection {
  scorers: ScorerInstance[]
}

interface ScorerTableSort {
  column: string
  direction: 'ascending' | 'descending'
}

const TABLE_SECTIONS: ScorerTableSection[] = [
  { family: 'true_false', title: 'True/false scorers', metrics: ['precision', 'recall', 'accuracy', 'f1_score'] },
  { family: 'float_scale', title: 'Float-scale scorers', metrics: ['mean_absolute_error', 'krippendorff_alpha_combined'] },
  { family: 'other', title: 'Other scorers', metrics: [] },
]

function scorerFamily(scorer: ScorerInstance): ScorerFamily {
  const type = scorer.identifier.scorer_type
  return type === 'true_false' || type === 'float_scale' ? type : 'other'
}

function MetricCell({ name, value }: { name: string; value: unknown }) {
  const styles = useScorerRegistryStyles()
  const display = formatScorerComparisonMetric(name, value)
  return (
    <TableCell>
      {display === 'n/a' ? (
        <span className={mergeClasses(styles.numeric, styles.subtitle)}>n/a</span>
      ) : (
        <Tooltip relationship="description"
          content={`${scorerMetricDescription(name)} Recorded value: ${String(value)}`}>
          <span className={styles.numeric} tabIndex={0}>{display}</span>
        </Tooltip>
      )}
    </TableCell>
  )
}

function ScorerTable({ title, family, metrics, scorers }: ScorerTableProps) {
  const styles = useConverterRegistryStyles()
  const scorerStyles = useScorerRegistryStyles()
  const detailsId = useId()
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [sort, setSort] = useState<ScorerTableSort>({ column: 'name', direction: 'ascending' })
  const toggleSort = (column: string): void => {
    setSort((current: ScorerTableSort) => ({
      column,
      direction: current.column === column
        ? current.direction === 'ascending' ? 'descending' : 'ascending'
        : column === 'name' || column === 'average_score_time_seconds' || column === 'mean_absolute_error'
          ? 'ascending' : 'descending',
    }))
  }
  const sorted = [...scorers].sort((left: ScorerInstance, right: ScorerInstance): number => {
    const byName = left.scorer_registry_name.localeCompare(right.scorer_registry_name, 'en', { numeric: true })
    const direction = sort.direction === 'ascending' ? 1 : -1
    if (sort.column === 'name') return direction * byName
    const a = scorerComparisonValue(sort.column, left.metrics?.[sort.column])
    const b = scorerComparisonValue(sort.column, right.metrics?.[sort.column])
    if (a === undefined) return b === undefined ? byName : 1
    if (b === undefined) return -1
    return direction * (a - b) || byName
  })
  const toggleDetails = (name: string): void => {
    setExpanded((current) => {
      const next = new Set(current)
      if (next.has(name)) next.delete(name)
      else next.add(name)
      return next
    })
  }
  return (
    <section className={scorerStyles.section}>
      <Text as="h2" size={400} weight="semibold" className={scorerStyles.heading}>{title}</Text>
      <div className={styles.tableContainer}>
        <Table className={mergeClasses(styles.table, scorerStyles.comparisonTable)} aria-label={title}>
          <TableHeader><TableRow>
            <TableHeaderCell className={scorerStyles.nameColumn} sortable
              sortDirection={sort.column === 'name' ? sort.direction : undefined}
              button={{ className: scorerStyles.sortButton, onClick: () => toggleSort('name') }}>Name</TableHeaderCell>
            <TableHeaderCell className={scorerStyles.typeColumn}>Type</TableHeaderCell>
            <TableHeaderCell className={scorerStyles.scorableColumn}>Scorable</TableHeaderCell>
            {metrics.map((name: string) => (
              <TableHeaderCell key={name}
                sortable sortDirection={sort.column === name ? sort.direction : undefined}
                button={{ className: scorerStyles.sortButton, onClick: () => toggleSort(name) }}
                className={family === 'float_scale' ? scorerStyles.scaleMetricColumn : scorerStyles.binaryMetricColumn}>
                <Tooltip content={scorerMetricDescription(name)} relationship="description">
                  <span className={scorerStyles.numeric}>
                    {name === 'f1_score' ? 'F1' : name === 'mean_absolute_error' ? 'MAE'
                      : name === 'krippendorff_alpha_combined' ? 'Agreement' : scorerMetricLabel(name)}
                  </span>
                </Tooltip>
              </TableHeaderCell>
            ))}
            <TableHeaderCell className={scorerStyles.timeColumn} sortable
              sortDirection={sort.column === 'average_score_time_seconds' ? sort.direction : undefined}
              button={{ className: scorerStyles.sortButton, onClick: () => toggleSort('average_score_time_seconds') }}>
              <span className={scorerStyles.numeric}>Avg time</span>
            </TableHeaderCell>
          </TableRow></TableHeader>
          <TableBody>{sorted.map((scorer: ScorerInstance) => (
            <Fragment key={scorer.scorer_registry_name}>
            <TableRow>
              <TableCell>
                <Button appearance="transparent" className={scorerStyles.name}
                  icon={expanded.has(scorer.scorer_registry_name) ? <ChevronDownRegular /> : <ChevronRightRegular />}
                  aria-expanded={expanded.has(scorer.scorer_registry_name)}
                  aria-controls={`${detailsId}-${encodeURIComponent(scorer.scorer_registry_name)}`}
                  aria-label={`Evaluation details for ${scorer.scorer_registry_name}`}
                  onClick={() => toggleDetails(scorer.scorer_registry_name)}>
                  {scorer.scorer_registry_name}
                </Button>
              </TableCell>
              <TableCell>
                <Text className={scorerStyles.type}>
                  {(scorer.display_type ?? scorer.identifier.class_name).split('(').map((part: string, index: number) => (
                    <Fragment key={`${index}-${part}`}>
                      {index > 0 && <>{'('}<wbr /></>}
                      {part}
                    </Fragment>
                  ))}
                </Text>
              </TableCell>
              <TableCell>{scorerEvidenceLabel(scorer)}</TableCell>
              {metrics.map((name: string) => <MetricCell key={name} name={name} value={scorer.metrics?.[name]} />)}
              <MetricCell name="average_score_time_seconds" value={scorer.metrics?.average_score_time_seconds} />
            </TableRow>
            {expanded.has(scorer.scorer_registry_name) && <TableRow>
              <TableCell colSpan={metrics.length + 4}>
                <div className={scorerStyles.details} id={`${detailsId}-${encodeURIComponent(scorer.scorer_registry_name)}`}
                  role="region" aria-label={`Evaluation details for ${scorer.scorer_registry_name}`}>
                  {scorer.evaluation_identifier
                    ? <pre className={scorerStyles.json}>{JSON.stringify(scorer.evaluation_identifier, null, 2)}</pre>
                    : <Text>n/a</Text>}
                </div>
              </TableCell>
            </TableRow>}
            </Fragment>
          ))}</TableBody>
        </Table>
      </div>
    </section>
  )
}

export default function ScorerRegistry({ scorers, loading, error, onRefresh }: ScorerRegistryProps) {
  const styles = useConverterRegistryStyles()
  const scorerStyles = useScorerRegistryStyles()
  const { preferences, updatePreferences } = useUserPreferences()
  return (
    <div className={styles.root}>
      <div className={styles.header}>
        <div className={styles.headerText}>
          <Text as="h1" size={600} weight="semibold">Scorer Registry</Text>
          <Text>Browse configured scorers.</Text>
        </div>
        <div className={styles.actions}>
          <Button className={styles.action} appearance="subtle" icon={<ArrowSyncRegular />}
            onClick={onRefresh} disabled={loading}>Refresh</Button>
        </div>
      </div>
      <div className={scorerStyles.defaultPicker}>
        <ChatScorerPicker label="Default objective scorer for chats" value={preferences.chatScorer ?? null}
          scorers={scorers} loading={loading} error={error}
          onChange={(chatScorer: RegisteredComponentReference | null) => updatePreferences((current) => ({ ...current, chatScorer }))} />
      </div>
      <Divider className={scorerStyles.separator} aria-label="Scorer registry" />
      {TABLE_SECTIONS.map(({ family, title, metrics }: ScorerTableSection) => {
        const items = scorers.filter((scorer: ScorerInstance) => scorerFamily(scorer) === family)
        return items.length > 0
          ? <ScorerTable key={family} family={family} title={title} metrics={metrics} scorers={items} /> : null
      })}
      {!loading && !error && scorers.length === 0 && <Text>No scorers are registered. Configure scorers through an initializer.</Text>}
    </div>
  )
}
