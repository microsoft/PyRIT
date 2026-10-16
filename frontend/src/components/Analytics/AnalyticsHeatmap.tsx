import { useId, useState } from 'react'

import {
  Button, mergeClasses, Table, TableBody, TableCell, TableHeader,
  TableHeaderCell, TableRow, Text, Tooltip,
} from '@fluentui/react-components'

import type {
  AttackAnalyticsCell, AttackAnalyticsDenominator, AttackAnalyticsFilter, AttackAnalyticsHeatmapMetric,
  AttackAnalyticsOption, AttackAnalyticsReport, AttackAnalyticsValue,
} from '@/types'
import {
  ANALYTICS_AXIS_LIMIT, analyticsDimensionLabel, analyticsOptionLabel, analyticsStatisticsLabel, analyticsSuccessCountsLabel, analyticsSuccessRate, analyticsValueKey,
  formatAnalyticsCount, formatAnalyticsPercent,
} from '@/utils/attackAnalytics'

import { AnalyticsAsrLabel } from './AnalyticsStats'
import { useAnalyticsHeatmapStyles } from './AnalyticsHeatmap.styles'

interface AnalyticsHeatmapProps {
  readonly report: AttackAnalyticsReport
  readonly metric: AttackAnalyticsHeatmapMetric
  readonly denominator?: AttackAnalyticsDenominator
  readonly onDrilldown: (filters: AttackAnalyticsFilter[]) => void
}

/** Join typed axis identities, not friendly labels; different absence kinds may share a displayed label. */
function cellKey(row: AttackAnalyticsValue, column: AttackAnalyticsValue): string {
  return JSON.stringify([analyticsValueKey(row), analyticsValueKey(column)])
}

/**
 * Render the SDK's bounded matrix without regrouping results or filling absent
 * cells with invented zeroes. The color metric and optional data table are local
 * presentation; every drill-down uses the cell's server-provided predicates.
 */
export default function AnalyticsHeatmap({ report, metric, denominator = 'decided', onDrilldown }: AnalyticsHeatmapProps) {
  const styles = useAnalyticsHeatmapStyles()
  const reasonId = useId()
  const drilldownUnavailable = report.drilldown_unavailable_reason !== null
  const drilldownDescription = drilldownUnavailable ? reasonId : undefined
  const [showData, setShowData] = useState(false)
  const marked = report.outcome_filter_applied
  const cells = new Map(report.cells.map((cell: AttackAnalyticsCell) => [cellKey(cell.row, cell.column), cell]))
  const rowLabels = new Map(report.rows.map((option: AttackAnalyticsOption) => [analyticsValueKey(option.key), analyticsOptionLabel(option)]))
  const columnLabels = new Map(report.columns.map((option: AttackAnalyticsOption) => [analyticsValueKey(option.key), analyticsOptionLabel(option)]))
  const rowName = analyticsDimensionLabel(report.group_by)
  const columnName = report.compare_by ? analyticsDimensionLabel(report.compare_by) : 'Comparison'

  /** Fixed bands stay comparable across cohorts; empty, unavailable, and zero remain distinct. */
  function cellColorClass(cell: AttackAnalyticsCell): string {
    const statistics = cell.statistics
    if (statistics.total_results === 0) return styles.empty
    const value = metric === 'success_rate' ? analyticsSuccessRate(statistics, denominator) : statistics.total_results
    if (value === null) return styles.unavailable
    if (value === 0) return styles.level0
    if (value <= (metric === 'success_rate' ? 0.25 : 9)) return styles.level1
    if (value <= (metric === 'success_rate' ? 0.5 : 99)) return styles.level2
    if (value <= (metric === 'success_rate' ? 0.75 : 999)) return styles.level3
    return styles.level4
  }

  return (
    <div className={styles.root}>
      <div className={styles.legend} aria-label="Heatmap legend">
        {metric === 'success_rate' ? (
          <><AnalyticsAsrLabel marked={marked} denominator={denominator} /><Text>Darker: higher ASR{marked ? '*' : ''}, on a fixed 0% to 100% scale.</Text></>
        ) : <Text>Count colors: 1-9, 10-99, 100-999, 1,000+ results. Darker means more results.</Text>}
        <Text>No results, unavailable ASR, and 0% ASR are distinct. The selected denominator applies to every cell.</Text>
      </div>
      {drilldownUnavailable && <Text id={reasonId} role="note">
        {report.drilldown_unavailable_reason}
      </Text>}
      <div className={styles.scroll} role="region" aria-label="Heatmap" aria-describedby={drilldownDescription} tabIndex={0}>
        <Table className={styles.table} size="small">
          <caption className={styles.caption}>{rowName} by {columnName}</caption>
          <TableHeader><TableRow>
            <TableHeaderCell className={styles.heading}>{rowName} / {columnName}</TableHeaderCell>
            {report.columns.map((column: AttackAnalyticsOption) => (
              <TableHeaderCell className={styles.heading} key={analyticsValueKey(column.key)}>
                {analyticsOptionLabel(column)}
              </TableHeaderCell>
            ))}
          </TableRow></TableHeader>
          <TableBody>
            {report.rows.map((row: AttackAnalyticsOption) => (
              <TableRow key={analyticsValueKey(row.key)}>
                <TableHeaderCell scope="row" className={styles.heading}>{analyticsOptionLabel(row)}</TableHeaderCell>
                {report.columns.map((column: AttackAnalyticsOption) => {
                  const cell = cells.get(cellKey(row.key, column.key))
                  const label = `${analyticsOptionLabel(row)} / ${analyticsOptionLabel(column)}`
                  const description = cell ? analyticsStatisticsLabel(cell.statistics, marked, denominator) : 'No cell data'
                  const value = !cell ? 'No cell data' : cell.statistics.total_results === 0 ? 'No results'
                    : metric === 'success_rate' ? `${formatAnalyticsPercent(analyticsSuccessRate(cell.statistics, denominator))}${marked ? '*' : ''}`
                      : `${formatAnalyticsCount(cell.statistics.total_results)} total`
                  return (
                    <TableCell className={styles.cell} key={analyticsValueKey(column.key)}>
                      <Tooltip content={report.drilldown_unavailable_reason ?? `${label}: ${description}`} relationship="description">
                        <Button
                          appearance="transparent"
                          className={mergeClasses(styles.button, cell ? cellColorClass(cell) : styles.empty)}
                          disabledFocusable={drilldownUnavailable || !cell || cell.statistics.total_results === 0}
                          aria-label={`${label}: ${description}`}
                          aria-describedby={drilldownDescription}
                          onClick={() => {
                            if (!drilldownUnavailable && cell && cell.statistics.total_results > 0) onDrilldown(cell.drilldown_filters)
                          }}
                        >
                          <Text weight="semibold">{value}</Text>
                          {cell && cell.statistics.total_results > 0 && (
                            <>
                              <Text size={200}>{analyticsSuccessCountsLabel(cell.statistics, denominator)}</Text>
                              {metric === 'success_rate' && (
                                <Text size={200}>{formatAnalyticsCount(cell.statistics.total_results)} total</Text>
                              )}
                            </>
                          )}
                        </Button>
                      </Tooltip>
                    </TableCell>
                  )
                })}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
      {report.axes_truncated && <Text>Axes are limited to {ANALYTICS_AXIS_LIMIT} values each. Some values are omitted; narrow the filters to inspect them.</Text>}
      <div>
        <Button className={styles.touchTarget} aria-expanded={showData}
          onClick={() => { setShowData(!showData) }}>{showData ? 'Hide cell data' : 'Show cell data'}</Button>
      </div>
      {showData && (
        <div className={styles.scrollTable} role="region" aria-label="Heatmap aggregate data" tabIndex={0}>
          <Table className={styles.dataTable} size="small">
            <caption className={styles.caption}>Cell outcome counts and denominators</caption>
            <TableHeader><TableRow>
              <TableHeaderCell>Row</TableHeaderCell><TableHeaderCell>Column</TableHeaderCell>
              <TableHeaderCell>Total results</TableHeaderCell><TableHeaderCell><AnalyticsAsrLabel marked={marked} denominator={denominator} /></TableHeaderCell>
              <TableHeaderCell>Decided</TableHeaderCell><TableHeaderCell>Decided share</TableHeaderCell>
              <TableHeaderCell>Successes</TableHeaderCell><TableHeaderCell>Failures</TableHeaderCell>
              <TableHeaderCell>Errors</TableHeaderCell><TableHeaderCell>Undetermined</TableHeaderCell>
            </TableRow></TableHeader>
            <TableBody>
              {report.cells.map((cell: AttackAnalyticsCell) => (
                <TableRow key={cellKey(cell.row, cell.column)}>
                  <TableCell>{rowLabels.get(analyticsValueKey(cell.row))}</TableCell>
                  <TableCell>{columnLabels.get(analyticsValueKey(cell.column))}</TableCell>
                  <TableCell>{formatAnalyticsCount(cell.statistics.total_results)}</TableCell>
                  <TableCell>{formatAnalyticsPercent(analyticsSuccessRate(cell.statistics, denominator))}{marked ? '*' : ''}</TableCell>
                  <TableCell>{formatAnalyticsCount(cell.statistics.total_decided)}</TableCell>
                  <TableCell>{formatAnalyticsPercent(cell.statistics.decided_share)}</TableCell>
                  <TableCell>{formatAnalyticsCount(cell.statistics.successes)}</TableCell>
                  <TableCell>{formatAnalyticsCount(cell.statistics.failures)}</TableCell>
                  <TableCell>{formatAnalyticsCount(cell.statistics.errors)}</TableCell>
                  <TableCell>{formatAnalyticsCount(cell.statistics.undetermined)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </div>
  )
}
