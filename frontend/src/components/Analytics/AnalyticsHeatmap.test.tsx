import {
  createHighContrastTheme, FluentProvider, webDarkTheme, webLightTheme,
} from '@fluentui/react-components'
import type { Theme } from '@fluentui/react-components'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import {
  ANALYTICS_EMPTY_STATISTICS, ANALYTICS_PREDICATE_LIMIT_REASON, ANALYTICS_VALUE_LIMIT_REASON, makeAnalyticsReport,
} from '@/test-utils/analyticsFixtures'
import type { AttackAnalyticsFilter, AttackAnalyticsReport } from '@/types'

import AnalyticsHeatmap from './AnalyticsHeatmap'

const ROW_FILTER: AttackAnalyticsFilter = {
  dimension: { name: 'targeted_harm_category' },
  values: [{ kind: 'value', value: 'category-A' }],
  match_mode: 'any',
}
const COLUMN_FILTER: AttackAnalyticsFilter = {
  dimension: { name: 'attack_type' },
  values: [{ kind: 'value', value: 'A' }],
  match_mode: 'any',
}
const REPORT: AttackAnalyticsReport = makeAnalyticsReport({
  group_by: ROW_FILTER.dimension,
  compare_by: COLUMN_FILTER.dimension,
  outcome_filter_applied: true,
  axes_truncated: true,
  rows: [
    { key: { kind: 'value', value: 'category-A' }, label: 'category-A' },
    { key: { kind: 'missing', value: null }, label: 'Unknown' },
  ],
  columns: [
    { key: { kind: 'value', value: 'A' }, label: 'A' },
    { key: { kind: 'value', value: 'B' }, label: 'B' },
  ],
  cells: [
    {
      row: { kind: 'value', value: 'category-A' }, column: { kind: 'value', value: 'A' },
      statistics: {
        ...ANALYTICS_EMPTY_STATISTICS, total_results: 5, failures: 5, total_decided: 5,
        success_rate: 0, success_rate_decided: 0, success_rate_all: 0, decided_share: 1,
        outcome_shares: { success: 0, failure: 1, error: 0, undetermined: 0 },
      },
      drilldown_filters: [ROW_FILTER, COLUMN_FILTER],
    },
    {
      row: { kind: 'value', value: 'category-A' }, column: { kind: 'value', value: 'B' },
      statistics: {
        ...ANALYTICS_EMPTY_STATISTICS, total_results: 3, errors: 2, undetermined: 1,
        success_rate_all: 0, decided_share: 0,
        outcome_shares: { success: 0, failure: 0, error: 2 / 3, undetermined: 1 / 3 },
      },
      drilldown_filters: [],
    },
    {
      row: { kind: 'missing', value: null }, column: { kind: 'value', value: 'A' },
      statistics: ANALYTICS_EMPTY_STATISTICS, drilldown_filters: [],
    },
  ],
})

const TestWrapper: React.FC<{ children: React.ReactNode; theme?: Theme }> = ({ children, theme = webLightTheme }) => (
  <FluentProvider theme={theme}>{children}</FluentProvider>
)

describe('AnalyticsHeatmap', () => {
  beforeEach(() => { jest.clearAllMocks() })

  it.each([
    ['light', webLightTheme],
    ['dark', webDarkTheme],
    ['high contrast', createHighContrastTheme()],
  ] as const)('should distinguish missing, empty, no-decided and zero-success cells in %s', (_name: string, theme: Theme) => {
    render(<TestWrapper theme={theme}><AnalyticsHeatmap report={REPORT} metric="success_rate" onDrilldown={jest.fn()} /></TestWrapper>)
    const heatmap = screen.getByRole('region', { name: 'Heatmap' })
    expect(within(heatmap).getByText('0%*')).toBeInTheDocument()
    expect(within(heatmap).getByText('Unavailable*')).toBeInTheDocument()
    expect(within(heatmap).getByText('No results')).toBeInTheDocument()
    expect(within(heatmap).getByText('No cell data')).toBeInTheDocument()
    expect(within(heatmap).getByRole('rowheader', { name: 'Unknown (missing metadata)' })).toBeInTheDocument()
    expect(screen.getByText(/Some values are omitted/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /ASR\*:.*Outcome filter active/ })).toBeInTheDocument()
  })

  it('should drill down via keyboard using the supplied cell predicates and expose exact counts', async () => {
    const user = userEvent.setup()
    const onDrilldown = jest.fn()
    render(<TestWrapper><AnalyticsHeatmap report={REPORT} metric="success_rate" onDrilldown={onDrilldown} /></TestWrapper>)
    const cell = screen.getByRole('button', { name: /category-A \/ A: 5 results; ASR\* 0%/ })
    cell.focus()
    await user.keyboard('{Enter}')
    expect(onDrilldown).toHaveBeenCalledWith([ROW_FILTER, COLUMN_FILTER])
    const empty = screen.getByRole('button', { name: /Unknown \(missing metadata\) \/ A: 0 results/ })
    expect(empty).toHaveAttribute('aria-disabled', 'true')
    await user.click(empty)
    expect(onDrilldown).toHaveBeenCalledTimes(1)
    await user.click(screen.getByRole('button', { name: 'Show cell data' }))
    expect(screen.getByRole('region', { name: 'Heatmap aggregate data' })).toBeInTheDocument()
    expect(screen.getByText('Cell outcome counts and denominators')).toBeInTheDocument()
  })

  it('should show count mode independently of unavailable rates', () => {
    render(<TestWrapper><AnalyticsHeatmap report={REPORT} metric="total_results" onDrilldown={jest.fn()} /></TestWrapper>)
    const noDecided = screen.getByRole('button', { name: /category-A \/ B: 3 results/ })
    expect(within(noDecided).getByText('3 total')).toBeInTheDocument()
    expect(within(noDecided).getByText('0 success / 0 decided')).toBeInTheDocument()
    expect(screen.getByText(/Count colors: 1-9, 10-99, 100-999, 1,000\+/)).toBeInTheDocument()
  })

  it.each([ANALYTICS_PREDICATE_LIMIT_REASON, ANALYTICS_VALUE_LIMIT_REASON])(
    'should make blocked cells keyboard-readable but inert and retain the data table: %s',
    async (reason: string) => {
      const user = userEvent.setup()
      const onDrilldown = jest.fn()
      render(<TestWrapper><AnalyticsHeatmap
        report={{ ...REPORT, drilldown_unavailable_reason: reason }}
        metric="success_rate" onDrilldown={onDrilldown}
      /></TestWrapper>)
      expect(screen.getByRole('note')).toHaveTextContent(reason)
      expect(screen.getByRole('note')).toBeVisible()
      const heatmap = screen.getByRole('region', { name: 'Heatmap' })
      expect(heatmap).toHaveAccessibleDescription(reason)
      const cell = within(heatmap).getByRole('button', { name: /category-A \/ A: 5 results/ })
      expect(cell).toHaveAttribute('aria-disabled', 'true')
      expect(cell).toHaveAccessibleDescription(/maximum (16 predicates|500 values)/)
      cell.focus()
      expect(cell).toHaveFocus()
      await user.keyboard('{Enter} ')
      await user.click(cell)
      expect(onDrilldown).not.toHaveBeenCalled()
      expect(within(heatmap).getByText('0%*')).toBeVisible()
      await user.click(screen.getByRole('button', { name: 'Show cell data' }))
      expect(screen.getByRole('region', { name: 'Heatmap aggregate data' })).toBeVisible()
    },
  )

  it('labels successes and decided results separately from the total population', () => {
    const report = {
      ...REPORT,
      outcome_filter_applied: false,
      cells: [{
        ...REPORT.cells[0],
        statistics: {
          ...ANALYTICS_EMPTY_STATISTICS,
          total_results: 5, successes: 3, total_decided: 3, undetermined: 2,
          success_rate: 1, success_rate_decided: 1, success_rate_all: 0.6, decided_share: 0.6,
          outcome_shares: { success: 0.6, failure: 0, error: 0, undetermined: 0.4 },
        },
      }],
    }
    render(<TestWrapper><AnalyticsHeatmap report={report} metric="success_rate" onDrilldown={jest.fn()} /></TestWrapper>)
    const cell = screen.getByRole('button', { name: /category-A \/ A: 5 results; ASR 100%/ })
    expect(within(cell).getByText('100%', { exact: true })).toBeVisible()
    expect(within(cell).getByText('3 success / 3 decided')).toBeVisible()
    expect(within(cell).getByText('5 total')).toBeVisible()
    expect(within(cell).queryByText('3 / 3 decided')).not.toBeInTheDocument()
  })
})
