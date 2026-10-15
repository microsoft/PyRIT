import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { ANALYTICS_TEST_STATISTICS } from '@/test-utils/analyticsFixtures'

import OutcomeSummaryBar from './OutcomeSummaryBar'

const TestWrapper: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
)

describe('OutcomeSummaryBar', () => {
  beforeEach(() => { jest.clearAllMocks() })

  it('should use supplied statistics and make a tiny outcome available through a labeled action', async () => {
    const user = userEvent.setup()
    const onOutcome = jest.fn()
    render(<TestWrapper>
      <OutcomeSummaryBar statistics={ANALYTICS_TEST_STATISTICS} label="Synthetic run" onOutcome={onOutcome} />
    </TestWrapper>)
    await user.click(screen.getByRole('button', { name: 'Synthetic run: 1 error; filter dashboard' }))
    expect(onOutcome).toHaveBeenCalledWith('error')
    expect(screen.getByRole('button', { name: 'Synthetic run: success segment; 4 results; filter dashboard' })).toBeVisible()
  })

  it('should show unavailable historical statistics without inferring counts from retries or errors', () => {
    render(<TestWrapper><OutcomeSummaryBar statistics={null} label="Historical run" /></TestWrapper>)
    expect(screen.getByText(/Outcome breakdown unavailable/)).toBeVisible()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })
})
