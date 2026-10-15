import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import {
  ANALYTICS_OPERATION_FILTER, ANALYTICS_PREDICATE_LIMIT_REASON, makeAnalyticsReport,
} from '@/test-utils/analyticsFixtures'

import AnalyticsGroups from './AnalyticsGroups'

const TestWrapper: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
)

describe('AnalyticsGroups', () => {
  beforeEach(() => { jest.clearAllMocks() })

  it('should drill into an actual outcome segment using its server predicates and outcome', async () => {
    const user = userEvent.setup()
    const onDrilldown = jest.fn()
    render(<TestWrapper><AnalyticsGroups report={makeAnalyticsReport()} successRate={false} onDrilldown={onDrilldown} /></TestWrapper>)
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Show aggregate data' })).not.toBeInTheDocument()
    const segment = screen.getByRole('button', { name: 'Nightly: success segment; 4 results; filter dashboard' })
    segment.focus()
    await user.keyboard('{Enter}')
    expect(onDrilldown).toHaveBeenCalledWith([ANALYTICS_OPERATION_FILTER], 'success')
    await user.click(screen.getByRole('button', { name: 'Nightly: 4 success; filter dashboard' }))
    expect(onDrilldown).toHaveBeenCalledTimes(2)
  })

  it('should make the success-rate bar a keyboard-operable group drill-down', async () => {
    const user = userEvent.setup()
    const onDrilldown = jest.fn()
    render(<TestWrapper><AnalyticsGroups report={makeAnalyticsReport()} successRate onDrilldown={onDrilldown} /></TestWrapper>)
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
    const bar = screen.getByRole('button', { name: /Inspect Nightly success rate: 10 results; ASR 66.7%/ })
    expect(screen.getByText('66.7% (4 success / 6 decided, 10 total)')).toBeVisible()
    bar.focus()
    await user.keyboard(' ')
    expect(onDrilldown).toHaveBeenCalledWith([ANALYTICS_OPERATION_FILTER])
  })

  it.each([false, true])('should honor the SDK reason for every group action without hiding the chart (ASR: %s)', async (successRate: boolean) => {
    const user = userEvent.setup()
    const onDrilldown = jest.fn()
    const report = makeAnalyticsReport({ drilldown_unavailable_reason: ANALYTICS_PREDICATE_LIMIT_REASON })
    render(<TestWrapper><AnalyticsGroups report={report} successRate={successRate} onDrilldown={onDrilldown} /></TestWrapper>)
    const groups = screen.getByRole('region', { name: successRate ? 'Success rate by Operation' : 'Outcome breakdown by Operation' })
    expect(screen.getByRole('note')).toHaveTextContent(ANALYTICS_PREDICATE_LIMIT_REASON)
    expect(screen.getByRole('note')).toBeVisible()
    expect(groups).toHaveAccessibleDescription(ANALYTICS_PREDICATE_LIMIT_REASON)
    expect(within(groups).getByText('10 results')).toBeVisible()
    for (const button of within(groups).getAllByRole('button')) {
      expect(button).toBeDisabled()
      expect(button).toHaveAccessibleDescription(/maximum 16 predicates/)
      await user.click(button)
    }
    expect(onDrilldown).not.toHaveBeenCalled()
  })
})
