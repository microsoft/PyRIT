import { mergeClasses, Text } from '@fluentui/react-components'

import type { AttackOutcome, ScenarioProgressResult } from '@/types'
import {
  OUTCOME_LABELS,
  OUTCOME_ORDER,
  summarizeAttackOutcomes,
} from '@/utils/attackOutcomeSummary'

import { useOutcomeSummaryBarStyles } from './OutcomeSummaryBar.styles'

interface OutcomeSummaryBarProps {
  readonly results: readonly Pick<ScenarioProgressResult, 'outcome'>[]
  readonly testId?: string
}

function formatPercent(value: number): string {
  return `${value.toFixed(1)}%`
}

export default function OutcomeSummaryBar({ results, testId }: OutcomeSummaryBarProps) {
  const styles = useOutcomeSummaryBarStyles()
  const { counts, total, percentages } = summarizeAttackOutcomes(results)

  // Fill class per outcome, keyed here so the bar and legend stay in sync.
  const colorClasses: Record<AttackOutcome, string> = {
    success: styles.colorSuccess,
    failure: styles.colorFailure,
    undetermined: styles.colorUndetermined,
    error: styles.colorError,
  }

  if (total === 0) {
    return (
      <Text className={styles.hint} data-testid={testId}>
        No completed executions to summarize yet.
      </Text>
    )
  }

  const presentOutcomes = OUTCOME_ORDER.filter((outcome) => counts[outcome] > 0)

  const ariaLabel = `${total} execution${total === 1 ? '' : 's'}: ${presentOutcomes
    .map(
      (outcome) =>
        `${counts[outcome]} ${OUTCOME_LABELS[outcome].toLowerCase()} (${formatPercent(percentages[outcome])})`,
    )
    .join(', ')}`

  return (
    <div className={styles.root} data-testid={testId}>
      <div className={styles.bar} role="img" aria-label={ariaLabel}>
        {presentOutcomes.map((outcome) => (
          <div
            key={outcome}
            className={mergeClasses(styles.segment, colorClasses[outcome])}
            data-testid={testId ? `${testId}-segment-${outcome}` : undefined}
            style={{ width: `${percentages[outcome]}%` }}
          />
        ))}
      </div>
      <ul className={styles.legend} aria-hidden="true">
        {presentOutcomes.map((outcome) => (
          <li key={outcome} className={styles.legendItem}>
            <span className={mergeClasses(styles.swatch, colorClasses[outcome])} />
            <Text size={200} weight="semibold">
              {OUTCOME_LABELS[outcome]}
            </Text>
            <Text size={200} className={styles.legendCount}>
              {counts[outcome]}
            </Text>
            <Text size={200} className={styles.legendPercent}>
              {formatPercent(percentages[outcome])}
            </Text>
          </li>
        ))}
      </ul>
    </div>
  )
}
