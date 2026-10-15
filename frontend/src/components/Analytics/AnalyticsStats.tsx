import { Button, Text, Tooltip } from '@fluentui/react-components'
import { InfoRegular } from '@fluentui/react-icons'

import OutcomeBadge from '@/components/OutcomeBadge'
import type { AttackAnalyticsDenominator, AttackAnalyticsStatistics, AttackOutcome } from '@/types'
import {
  ANALYTICS_ASR_NOTE, analyticsAsrDefinition, analyticsSuccessCountsLabel, analyticsSuccessRate,
  formatAnalyticsCount, formatAnalyticsPercent,
} from '@/utils/attackAnalytics'

import { useAnalyticsStatsStyles } from './AnalyticsStats.styles'

interface AnalyticsAsrLabelProps {
  readonly marked: boolean
  readonly denominator?: AttackAnalyticsDenominator
}

/** A focusable explanation shared by summary/charts; the SDK's outcome-filter flag controls the asterisk. */
export function AnalyticsAsrLabel({ marked, denominator = 'decided' }: AnalyticsAsrLabelProps) {
  const styles = useAnalyticsStatsStyles()
  const explanation = `${marked ? `${ANALYTICS_ASR_NOTE} ` : ''}${analyticsAsrDefinition(denominator)}`
  return (
    <Tooltip content={explanation} relationship="description">
      <Button
        appearance="transparent"
        size="small"
        className={styles.asrLabel}
        icon={<InfoRegular />}
        iconPosition="after"
        aria-label={`ASR${marked ? '*' : ''}: ${explanation}`}
      >
        ASR{marked ? '*' : ''} ({denominator === 'decided' ? 'decided results' : 'all results'})
      </Button>
    </Tooltip>
  )
}

interface AnalyticsStatsProps {
  readonly statistics: AttackAnalyticsStatistics
  readonly marked: boolean
  readonly denominator?: AttackAnalyticsDenominator
  readonly onOutcome: (outcome: AttackOutcome) => void
}

/** Display SDK population statistics; outcome buttons change the whole cohort, not just this summary. */
export default function AnalyticsStats({ statistics, marked, denominator = 'decided', onOutcome }: AnalyticsStatsProps) {
  const styles = useAnalyticsStatsStyles()
  const outcomes: Array<{ outcome: AttackOutcome; count: number }> = [
    { outcome: 'success', count: statistics.successes },
    { outcome: 'failure', count: statistics.failures },
    { outcome: 'error', count: statistics.errors },
    { outcome: 'undetermined', count: statistics.undetermined },
  ]
  return (
    <section aria-label="Outcome summary" className={styles.root}>
      <div className={styles.metric}>
        <Text>Total results</Text>
        <Text size={600} weight="semibold" className={styles.number}>{formatAnalyticsCount(statistics.total_results)}</Text>
      </div>
      <div className={styles.metric}>
        <AnalyticsAsrLabel marked={marked} denominator={denominator} />
        <Text size={500} weight="semibold" className={styles.number}>
          {formatAnalyticsPercent(analyticsSuccessRate(statistics, denominator))}{marked ? '*' : ''}
        </Text>
        <Text size={200}>{analyticsSuccessCountsLabel(statistics, denominator)}</Text>
        <Text size={200}>{formatAnalyticsCount(statistics.total_results)} total</Text>
      </div>
      <div className={styles.metric}>
        <Text>Decided share</Text>
        <Text size={500} className={styles.number}>{formatAnalyticsPercent(statistics.decided_share)}</Text>
        <Text size={200}>{formatAnalyticsCount(statistics.total_decided)} decided / {formatAnalyticsCount(statistics.total_results)} total</Text>
      </div>
      <div className={styles.outcomes} aria-label="Filter entire dashboard by outcome">
        {outcomes.map((item: { outcome: AttackOutcome; count: number }) => (
          <Button
            key={item.outcome}
            className={styles.button}
            appearance="subtle"
            aria-label={`Filter to ${item.outcome}: ${formatAnalyticsCount(item.count)}`}
            onClick={() => { onOutcome(item.outcome) }}
          >
            <OutcomeBadge outcome={item.outcome} appearance="filled" /> {formatAnalyticsCount(item.count)}
          </Button>
        ))}
      </div>
    </section>
  )
}
