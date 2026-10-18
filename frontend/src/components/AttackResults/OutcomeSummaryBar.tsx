import { Button, mergeClasses, Text, Tooltip } from '@fluentui/react-components'

import type { AttackOutcome, OutcomeStatistics } from '@/types'
import { formatAnalyticsCount, formatAnalyticsPercent } from '@/utils/attackAnalytics'

import { useOutcomeSummaryBarStyles } from './OutcomeSummaryBar.styles'

interface OutcomeSummaryBarProps {
  readonly statistics: OutcomeStatistics | null
  readonly label: string
  readonly showBar?: boolean
  readonly disabled?: boolean
  readonly disabledReason?: string | null
  readonly describedBy?: string
  readonly onOutcome?: (outcome: AttackOutcome) => void
}

/** Presentation only: callers supply SDK statistics, never paginated result rows. */
export default function OutcomeSummaryBar({
  statistics, label, showBar = true, disabled = false, disabledReason, describedBy, onOutcome,
}: OutcomeSummaryBarProps) {
  const styles = useOutcomeSummaryBarStyles()
  if (statistics === null) return <Text>Outcome breakdown unavailable for this historical result.</Text>
  const outcomes: Array<{ outcome: AttackOutcome; count: number }> = [
    { outcome: 'success', count: statistics.successes },
    { outcome: 'failure', count: statistics.failures },
    { outcome: 'error', count: statistics.errors },
    { outcome: 'undetermined', count: statistics.undetermined },
  ]
  const inactive = disabled || !onOutcome
  return (
    <div className={styles.root}>
      {showBar && (
        <div className={styles.bar} role="group" aria-label={`Outcome shares for ${label}`}>
          {outcomes.map(({ outcome, count }: { outcome: AttackOutcome; count: number }) =>
            statistics.outcome_shares[outcome] > 0 && (
              <Tooltip key={outcome} relationship="description" content={disabledReason ??
                `${formatAnalyticsPercent(statistics.outcome_shares[outcome])} of this group's results. The labeled outcome counts provide the same action.`}>
                <Button
                  appearance="transparent"
                  className={mergeClasses(styles.segment, styles[outcome])}
                  style={{ width: `${statistics.outcome_shares[outcome] * 100}%` }}
                  disabled={inactive}
                  aria-label={`${label}: ${outcome} segment; ${formatAnalyticsCount(count)} results; filter dashboard`}
                  aria-describedby={describedBy}
                  onClick={() => { onOutcome?.(outcome) }}
                />
              </Tooltip>
            ),
          )}
        </div>
      )}
      <div className={styles.legend} aria-label={`Outcome counts for ${label}`}>
        {outcomes.map(({ outcome, count }: { outcome: AttackOutcome; count: number }) => (
          <Tooltip key={outcome} relationship="description" content={disabledReason ??
            `${formatAnalyticsPercent(statistics.outcome_shares[outcome])} of this group's results. Filter the entire dashboard.`}>
            <Button className={styles.button} appearance="subtle" size="small"
              disabled={inactive || count === 0}
              aria-label={`${label}: ${formatAnalyticsCount(count)} ${outcome}; filter dashboard`}
              aria-describedby={describedBy}
              onClick={() => { onOutcome?.(outcome) }}>
              <span aria-hidden="true" className={mergeClasses(styles.swatch, styles[outcome])} />
              {outcome} {formatAnalyticsCount(count)}
            </Button>
          </Tooltip>
        ))}
      </div>
    </div>
  )
}
