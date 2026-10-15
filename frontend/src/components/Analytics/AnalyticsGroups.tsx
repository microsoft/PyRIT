import { useId } from 'react'

import { Button, mergeClasses, Text } from '@fluentui/react-components'

import OutcomeSummaryBar from '@/components/AttackResults/OutcomeSummaryBar'
import type { AttackAnalyticsDenominator, AttackAnalyticsFilter, AttackAnalyticsGroup, AttackAnalyticsReport, AttackOutcome } from '@/types'
import {
  analyticsDimensionLabel, analyticsOptionLabel, analyticsStatisticsLabel, analyticsSuccessCountsLabel, analyticsSuccessRate, analyticsValueKey,
  formatAnalyticsCount, formatAnalyticsPercent,
} from '@/utils/attackAnalytics'

import { AnalyticsAsrLabel } from './AnalyticsStats'
import { useAnalyticsGroupsStyles } from './AnalyticsGroups.styles'

interface AnalyticsGroupsProps {
  readonly report: AttackAnalyticsReport
  readonly successRate: boolean
  readonly denominator?: AttackAnalyticsDenominator
  readonly onDrilldown: (filters: AttackAnalyticsFilter[], outcome?: AttackOutcome) => void
}

/**
 * Two presentations of the same server group page: outcome shares or ASR.
 * Widths use SDK ratios, never a sum of visible rows. Both bars and labeled
 * counts drill down with the supplied predicates; labels keep tiny segments usable.
 */
export default function AnalyticsGroups({ report, successRate, denominator = 'decided', onDrilldown }: AnalyticsGroupsProps) {
  const styles = useAnalyticsGroupsStyles()
  const reasonId = useId()
  const drilldownUnavailable = report.drilldown_unavailable_reason !== null
  const drilldownDescription = drilldownUnavailable ? reasonId : undefined
  const marked = report.outcome_filter_applied
  const title = `${successRate ? `Success rate${marked ? '*' : ''}` : 'Outcome breakdown'} by ${analyticsDimensionLabel(report.group_by)}`

  return (
    <div className={styles.root}>
      <div className={styles.legend}>
        {successRate ? (
          <><AnalyticsAsrLabel marked={marked} denominator={denominator} /><Text>Fixed scale: 0% to 100%. Compare sample sizes, not rates alone.</Text></>
        ) : <Text>Bar width shows each outcome's share of all results in that group, not ASR.</Text>}
      </div>
      {drilldownUnavailable && <Text id={reasonId} role="note">
        {report.drilldown_unavailable_reason}
      </Text>}
      <section className={styles.groups} aria-label={title} aria-describedby={drilldownDescription} tabIndex={0}>
        {report.groups.map((group: AttackAnalyticsGroup) => {
          const label = analyticsOptionLabel(group)
          const statistics = group.statistics
          const rate = analyticsSuccessRate(statistics, denominator)
          return (
            <div className={styles.group} key={analyticsValueKey(group.key)}>
              <div className={styles.groupLabel}>
                <Button className={styles.groupButton} appearance="subtle"
                  disabled={drilldownUnavailable || statistics.total_results === 0}
                  aria-label={`Filter to ${label}`}
                  aria-describedby={drilldownDescription}
                  onClick={() => { onDrilldown(group.drilldown_filters) }}>{label}</Button>
                <Text size={200}>{formatAnalyticsCount(statistics.total_results)} results</Text>
              </div>
              <div className={styles.visualization}>
                {successRate && (
                  <>
                    <Button
                      appearance="transparent"
                      className={mergeClasses(styles.bar, styles.rateButton, rate === null && styles.unavailable)}
                      disabled={drilldownUnavailable || statistics.total_results === 0}
                      aria-label={`Inspect ${label} success rate: ${analyticsStatisticsLabel(statistics, marked, denominator)}`}
                      aria-describedby={drilldownDescription}
                      onClick={() => { onDrilldown(group.drilldown_filters) }}
                    >
                      {rate !== null && (
                        <span aria-hidden="true" className={mergeClasses(styles.segment, styles.rate)} style={{ width: `${rate * 100}%` }} />
                      )}
                    </Button>
                    <Text className={styles.number}>
                      {formatAnalyticsPercent(rate)}{marked ? '*' : ''}{' '}
                      ({analyticsSuccessCountsLabel(statistics, denominator)}, {formatAnalyticsCount(statistics.total_results)} total)
                    </Text>
                  </>
                )}
                <OutcomeSummaryBar statistics={statistics} label={label} showBar={!successRate}
                  disabled={drilldownUnavailable} disabledReason={report.drilldown_unavailable_reason}
                  describedBy={drilldownDescription}
                  onOutcome={(outcome: AttackOutcome) => { onDrilldown(group.drilldown_filters, outcome) }} />
              </div>
            </div>
          )
        })}
        {report.groups.length === 0 && <Text>No groups on this page. Return to the first groups or narrow the filters.</Text>}
      </section>
    </div>
  )
}
