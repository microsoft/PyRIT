import { Badge, Table, TableBody, TableCell, TableHeader, TableHeaderCell, TableRow, Text } from '@fluentui/react-components'

import {
  formatAttackSuccess,
  formatDuration,
  formatTimestamp,
  objectivePreview,
} from '@/components/AttackResults/attackAttemptFormatting'
import type { ScenarioProgressResult } from '@/types'

import { useScenarioRunPageStyles } from './ScenarioRunPage.styles'

const OUTCOME_BADGE_COLORS: Record<
  ScenarioProgressResult['outcome'],
  'success' | 'danger' | 'warning' | 'informative'
> = {
  success: 'success',
  failure: 'danger',
  error: 'warning',
  undetermined: 'informative',
}

interface AttackExecutionTableProps {
  readonly attempts: ScenarioProgressResult[]
  readonly seedObjectives: Map<string, string>
  readonly onOpenDetails: (attempt: ScenarioProgressResult, trigger: HTMLElement) => void
  /** Total attempts in this group, when more exist than are rendered. */
  readonly totalAttempts: number
}

export default function AttackExecutionTable({
  attempts,
  seedObjectives,
  onOpenDetails,
  totalAttempts,
}: AttackExecutionTableProps) {
  const styles = useScenarioRunPageStyles()

  if (attempts.length === 0) {
    return (
      <div className={styles.emptyState}>
        <Text>No execution details are available for this display group.</Text>
      </div>
    )
  }

  return (
    <div className={styles.tableScroll}>
      {totalAttempts > attempts.length && (
        <Text size={200} className={styles.sectionHint}>
          Showing the latest {attempts.length} of {totalAttempts} executions in this group.
        </Text>
      )}
      <Table size="small" className={styles.attemptsTable} aria-label="Attack executions">
        <TableHeader>
          <TableRow>
            <TableHeaderCell>Attack</TableHeaderCell>
            <TableHeaderCell>Attack Success</TableHeaderCell>
            <TableHeaderCell>Objective</TableHeaderCell>
            <TableHeaderCell>Execution</TableHeaderCell>
            <TableHeaderCell>Retries / error</TableHeaderCell>
            <TableHeaderCell>Timestamp</TableHeaderCell>
          </TableRow>
        </TableHeader>
        <TableBody>
          {attempts.map((attempt) => (
            <TableRow
              key={attempt.attack_result_id}
              className={styles.clickableAttemptRow}
              tabIndex={0}
              aria-label={`View details for ${attempt.atomic_attack_name}`}
              onClick={(event) => onOpenDetails(attempt, event.currentTarget)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' || event.key === ' ') {
                  event.preventDefault()
                  onOpenDetails(attempt, event.currentTarget)
                }
              }}
            >
              <TableCell>
                <Text>{attempt.atomic_attack_name}</Text>
              </TableCell>
              <TableCell>
                <Badge appearance="tint" color={OUTCOME_BADGE_COLORS[attempt.outcome]}>
                  {formatAttackSuccess(attempt.outcome)}
                </Badge>
              </TableCell>
              <TableCell>
                <Text className={styles.preview}>
                  {objectivePreview(seedObjectives.get(attempt.seed_group_id) ?? null, attempt.seed_group_id)}
                </Text>
              </TableCell>
              <TableCell className={styles.nowrap}>{formatDuration(attempt.execution_time_ms)}</TableCell>
              <TableCell>
                {attempt.outcome === 'error'
                  ? attempt.error_message ?? attempt.error_type ?? 'Error'
                  : `${attempt.total_retries} retries`}
              </TableCell>
              <TableCell className={styles.nowrap}>{formatTimestamp(attempt.timestamp)}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}
