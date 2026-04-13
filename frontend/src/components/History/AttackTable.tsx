import {
  tokens,
  Text,
  Button,
  Badge,
  Tooltip,
  Table,
  TableBody,
  TableCell,
  TableHeader,
  TableHeaderCell,
  TableRow,
} from '@fluentui/react-components'
import {
  OpenRegular,
  CheckmarkCircleRegular,
  DismissCircleRegular,
  QuestionCircleRegular,
} from '@fluentui/react-icons'
import type { AttackSummary } from '../../types'
import { useAttackHistoryStyles } from './AttackHistory.styles'

const OUTCOME_ICONS: Record<string, React.ReactElement> = {
  success: <CheckmarkCircleRegular style={{ color: tokens.colorPaletteGreenForeground1 }} />,
  failure: <DismissCircleRegular style={{ color: tokens.colorPaletteRedForeground1 }} />,
  undetermined: <QuestionCircleRegular style={{ color: tokens.colorNeutralForeground3 }} />,
}

const OUTCOME_COLORS: Record<string, 'success' | 'danger' | 'informative'> = {
  success: 'success',
  failure: 'danger',
  undetermined: 'informative',
}

interface AttackTableProps {
  attacks: AttackSummary[]
  onOpenAttack: (attackResultId: string) => void
  formatDate: (dateStr: string) => string
}

export default function AttackTable({ attacks, onOpenAttack, formatDate }: AttackTableProps) {
  const styles = useAttackHistoryStyles()

  return (
    <Table className={styles.table} data-testid="attacks-table">
      <TableHeader>
        <TableRow>
          <TableHeaderCell className={styles.colStatus}>Status</TableHeaderCell>
          <TableHeaderCell className={styles.colAttackType}>Attack Type</TableHeaderCell>
          <TableHeaderCell className={styles.colTarget}>Target</TableHeaderCell>
          <TableHeaderCell className={styles.colOperator}>Operator</TableHeaderCell>
          <TableHeaderCell className={styles.colOperation}>Operation</TableHeaderCell>
          <TableHeaderCell className={styles.colMessages}>Msgs</TableHeaderCell>
          <TableHeaderCell className={styles.colConversations}>Convs</TableHeaderCell>
          <TableHeaderCell className={styles.colConverters}>Converters</TableHeaderCell>
          <TableHeaderCell className={styles.colLabels}>Labels</TableHeaderCell>
          <TableHeaderCell className={styles.colDate}>Created</TableHeaderCell>
          <TableHeaderCell className={styles.colDate}>Updated</TableHeaderCell>
          <TableHeaderCell>Last Message</TableHeaderCell>
          <TableHeaderCell className={styles.colAction} />
        </TableRow>
      </TableHeader>
      <TableBody>
        {attacks.map(attack => (
          <TableRow
            key={attack.attack_result_id}
            className={styles.clickableRow}
            onClick={() => onOpenAttack(attack.attack_result_id)}
            data-testid={`attack-row-${attack.attack_result_id}`}
          >
            <TableCell>
              <Badge
                appearance="filled"
                color={OUTCOME_COLORS[attack.outcome ?? 'undetermined'] ?? 'informative'}
                icon={OUTCOME_ICONS[attack.outcome ?? 'undetermined']}
                data-testid={`outcome-badge-${attack.attack_result_id}`}
              >
                {attack.outcome ?? 'undetermined'}
              </Badge>
            </TableCell>
            <TableCell>
              <Text size={200} weight="semibold" truncate>{attack.attack_type}</Text>
            </TableCell>
            <TableCell>
              {attack.target ? (
                <Tooltip content={`${attack.target.target_type}${attack.target.model_name ? ` (${attack.target.model_name})` : ''}`} relationship="label">
                  <Badge appearance="outline" size="small">
                    {attack.target.model_name || attack.target.target_type}
                  </Badge>
                </Tooltip>
              ) : (
                <Text size={200} style={{ color: tokens.colorNeutralForeground3 }}>—</Text>
              )}
            </TableCell>
            <TableCell>
              <Text size={200} className={styles.nowrap}>{attack.labels.operator || '—'}</Text>
            </TableCell>
            <TableCell>
              <Text size={200} className={styles.nowrap}>{attack.labels.operation || '—'}</Text>
            </TableCell>
            <TableCell>
              <Text size={200}>{attack.message_count}</Text>
            </TableCell>
            <TableCell>
              <Text size={200}>{(attack.related_conversation_ids?.length ?? 0) + 1}</Text>
            </TableCell>
            <TableCell>
              {attack.converters.length > 0 ? (
                <div className={styles.badgeGroup}>
                  {attack.converters.slice(0, 2).map(c => (
                    <Badge key={c} appearance="tint" size="small">{c}</Badge>
                  ))}
                  {attack.converters.length > 2 && (
                    <Tooltip content={attack.converters.join(', ')} relationship="label">
                      <Badge appearance="tint" size="small">+{attack.converters.length - 2}</Badge>
                    </Tooltip>
                  )}
                </div>
              ) : (
                <Text size={200} style={{ color: tokens.colorNeutralForeground3 }}>—</Text>
              )}
            </TableCell>
            <TableCell>
              {(() => {
                const otherLabels = Object.entries(attack.labels ?? {}).filter(([k]) => k !== 'operator' && k !== 'operation' && k !== 'source')
                return otherLabels.length > 0 ? (
                  <div className={styles.badgeGroup}>
                    {otherLabels.slice(0, 2).map(([k, v]) => (
                      <Badge key={k} appearance="tint" size="small" color="brand">{k}: {v}</Badge>
                    ))}
                    {otherLabels.length > 2 && (
                      <Tooltip
                        content={otherLabels.map(([k, v]) => `${k}: ${v}`).join(', ')}
                        relationship="label"
                      >
                        <Badge appearance="tint" size="small" color="brand">
                          +{otherLabels.length - 2}
                        </Badge>
                      </Tooltip>
                    )}
                  </div>
                ) : (
                  <Text size={200} style={{ color: tokens.colorNeutralForeground3 }}>—</Text>
                )
              })()}
            </TableCell>
            <TableCell>
              <Text size={200} className={styles.nowrap}>{formatDate(attack.created_at)}</Text>
            </TableCell>
            <TableCell>
              <Text size={200} className={styles.nowrap}>{formatDate(attack.updated_at)}</Text>
            </TableCell>
            <TableCell>
              <Text size={200} className={styles.previewCell}>
                {attack.last_message_preview || '—'}
              </Text>
            </TableCell>
            <TableCell>
              <Tooltip content="Open attack" relationship="label">
                <Button
                  appearance="subtle"
                  size="small"
                  icon={<OpenRegular />}
                  onClick={(e) => {
                    e.stopPropagation()
                    onOpenAttack(attack.attack_result_id)
                  }}
                  data-testid={`open-attack-${attack.attack_result_id}`}
                />
              </Tooltip>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}
