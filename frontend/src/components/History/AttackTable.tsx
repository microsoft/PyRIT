import { useState, type KeyboardEvent } from 'react'

import {
  Text,
  Badge,
  Tooltip,
  Table,
  TableBody,
  TableCell,
  TableHeader,
  TableHeaderCell,
  TableRow,
} from '@fluentui/react-components'

import OutcomeBadge from '@/components/OutcomeBadge'
import RegistryIdentityRow from '@/components/Registry/RegistryIdentityRow'
import type { AttackSummary, TargetInstance } from '@/types'
import { resolveTargetByIdentifierHash } from '@/utils/targetIdentity'

import { useAttackHistoryStyles } from './AttackHistory.styles'

const COLUMN_COUNT = 7

interface AttackTableProps {
  readonly attacks: AttackSummary[]
  readonly targets?: TargetInstance[]
  readonly onOpenAttack: (attackResultId: string) => void
  readonly formatDate: (dateStr: string) => string
}

export default function AttackTable({ attacks, targets = [], onOpenAttack, formatDate }: AttackTableProps) {
  const styles = useAttackHistoryStyles()
  const [expandedTargets, setExpandedTargets] = useState<Set<string>>(new Set())

  const toggleTarget = (attackResultId: string): void => {
    setExpandedTargets((current: Set<string>) => {
      const next = new Set(current)
      if (next.has(attackResultId)) next.delete(attackResultId)
      else next.add(attackResultId)
      return next
    })
  }

  return (
    <Table className={styles.table} data-testid="attacks-table">
      <TableHeader>
        <TableRow>
          <TableHeaderCell className={styles.colStatus}>Status</TableHeaderCell>
          <TableHeaderCell className={styles.colObjective}>Objective</TableHeaderCell>
          <TableHeaderCell className={styles.colTarget}>Target</TableHeaderCell>
          <TableHeaderCell className={styles.colOperator}>Operator</TableHeaderCell>
          <TableHeaderCell className={styles.colOperation}>Operation</TableHeaderCell>
          <TableHeaderCell className={styles.colLabels}>Labels</TableHeaderCell>
          <TableHeaderCell className={styles.colDate}>Last Update</TableHeaderCell>
        </TableRow>
      </TableHeader>
      <TableBody>
        {attacks.map((attack: AttackSummary) => {
          const target = attack.target
          const resolution = target ? resolveTargetByIdentifierHash(target.identifier_hash, targets) : null
          const registeredTarget = resolution?.status === 'resolved' ? resolution.target : null
          const targetName = target
            ? target.target_registry_name || target.binding?.source_name
              || registeredTarget?.target_registry_name || 'Unnamed target'
            : '—'
          const otherLabels = Object.entries(attack.labels ?? {}).filter(([key]: [string, string]) => key !== 'source')

          return (
            <RegistryIdentityRow
              key={attack.attack_result_id}
              name={targetName}
              identity={target?.identifier ?? registeredTarget?.identifier ?? target ?? null}
              columns={COLUMN_COUNT}
              expanded={expandedTargets.has(attack.attack_result_id)}
              onToggle={() => { toggleTarget(attack.attack_result_id) }}
              nameCellClassName={styles.colTarget}
              rowClassName={styles.clickableRow}
              rowTestId={`attack-row-${attack.attack_result_id}`}
              rowProps={{
                onClick: () => { onOpenAttack(attack.attack_result_id) },
                onKeyDown: (event: KeyboardEvent<HTMLTableRowElement>) => {
                  if (event.target !== event.currentTarget) return
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault()
                    onOpenAttack(attack.attack_result_id)
                  }
                },
                tabIndex: 0,
                'aria-label': `Open attack: ${attack.objective || 'No objective'}`,
              }}
              leadingCell={<>
                <TableCell>
                  <OutcomeBadge outcome={attack.outcome} testId={`outcome-badge-${attack.attack_result_id}`} />
                </TableCell>
                <TableCell className={styles.colObjective}>
                  <Text size={200} className={styles.objectiveCell}>{attack.objective || '—'}</Text>
                </TableCell>
              </>}
            >
              <TableCell>
                <Text size={200} className={styles.nowrap}>{attack.operator || '—'}</Text>
              </TableCell>
              <TableCell>
                <Text size={200} className={styles.nowrap}>{attack.operation_name || '—'}</Text>
              </TableCell>
              <TableCell>
                {otherLabels.length > 0 ? (
                  <div className={styles.badgeGroup}>
                    {otherLabels.slice(0, 2).map(([key, value]: [string, string]) => (
                      <Badge key={key} appearance="tint" size="small" color="brand">{key}: {value}</Badge>
                    ))}
                    {otherLabels.length > 2 && (
                      <Tooltip
                        content={otherLabels.map(([key, value]: [string, string]) => `${key}: ${value}`).join(', ')}
                        relationship="label"
                      >
                        <Badge appearance="tint" size="small" color="brand">
                          +{otherLabels.length - 2}
                        </Badge>
                      </Tooltip>
                    )}
                  </div>
                ) : (
                  <Text size={200} className={styles.emptyValue}>—</Text>
                )}
              </TableCell>
              <TableCell>
                <Text size={200} className={styles.nowrap}>{formatDate(attack.updated_at)}</Text>
              </TableCell>
            </RegistryIdentityRow>
          )
        })}
      </TableBody>
    </Table>
  )
}
