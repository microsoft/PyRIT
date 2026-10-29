import { Table, TableBody, TableCell, TableHeader, TableHeaderCell, TableRow, Text } from '@fluentui/react-components'

import OutcomeBadge from '@/components/OutcomeBadge'
import {
  formatAttackSuccess,
  formatDuration,
  objectivePreview,
} from '@/components/AttackResults/attackAttemptFormatting'
import type { ScenarioProgressResult } from '@/types'
import { Link } from 'react-router'
import { attackConversationRoutePath } from '@/utils/routeParams'

import { useScenarioRunPageStyles } from './ScenarioRunPage.styles'

interface AttackExecutionTableProps {
  readonly attempts: ScenarioProgressResult[]
  readonly seedObjectives: Map<string, string>
  readonly onOpenDetails: (attempt: ScenarioProgressResult, trigger: HTMLElement) => void
  /** Total attempts in this group, when more exist than are rendered. */
  readonly totalAttempts: number
  readonly scenarioResultId?: string
  readonly showObjective?: boolean
}

export default function AttackExecutionTable({
  attempts,
  seedObjectives,
  onOpenDetails,
  totalAttempts,
  scenarioResultId,
  showObjective = true,
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
            <TableHeaderCell>Outcome</TableHeaderCell>
            {showObjective && <TableHeaderCell>Objective</TableHeaderCell>}
            <TableHeaderCell>Duration</TableHeaderCell>
            <TableHeaderCell>Conversation</TableHeaderCell>
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
                <OutcomeBadge appearance="tint" outcome={attempt.outcome} label={formatAttackSuccess(attempt.outcome)} />
              </TableCell>
              {showObjective && <TableCell>
                <Text className={styles.preview}>
                  {objectivePreview(seedObjectives.get(attempt.seed_group_id) ?? null, attempt.seed_group_id)}
                </Text>
              </TableCell>}
              <TableCell className={styles.nowrap}>{formatDuration(attempt.execution_time_ms)}</TableCell>
              <TableCell>
                <Link to={attackConversationRoutePath(attempt.attack_result_id, attempt.conversation_id, scenarioResultId)}
                  onClick={(event) => { event.stopPropagation() }} onKeyDown={(event) => { event.stopPropagation() }}>
                  Conversation
                </Link>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}
