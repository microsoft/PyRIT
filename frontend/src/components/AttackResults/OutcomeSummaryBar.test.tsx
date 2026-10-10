import { render, screen } from '@testing-library/react'

import OutcomeSummaryBar from './OutcomeSummaryBar'

describe('OutcomeSummaryBar', () => {
  it('renders a segment per present outcome and an accessible summary label', () => {
    render(
      <OutcomeSummaryBar
        testId="outcome-summary"
        results={[
          { outcome: 'success' },
          { outcome: 'success' },
          { outcome: 'failure' },
          { outcome: 'error' },
        ]}
      />,
    )

    expect(screen.getByTestId('outcome-summary-segment-success')).toBeInTheDocument()
    expect(screen.getByTestId('outcome-summary-segment-failure')).toBeInTheDocument()
    expect(screen.getByTestId('outcome-summary-segment-error')).toBeInTheDocument()
    // Undetermined has no attempts, so it gets no segment.
    expect(screen.queryByTestId('outcome-summary-segment-undetermined')).not.toBeInTheDocument()

    const bar = screen.getByRole('img')
    expect(bar).toHaveAccessibleName(/4 executions/i)
    expect(bar).toHaveAccessibleName(/2 success \(50\.0%\)/i)
  })

  it('shows counts and percentages in the legend', () => {
    render(
      <OutcomeSummaryBar
        results={[{ outcome: 'success' }, { outcome: 'failure' }]}
      />,
    )

    expect(screen.getByText('Success')).toBeInTheDocument()
    expect(screen.getByText('Failure')).toBeInTheDocument()
    expect(screen.getAllByText('50.0%')).toHaveLength(2)
  })

  it('renders a hint when there are no results', () => {
    render(<OutcomeSummaryBar testId="outcome-summary" results={[]} />)

    expect(screen.getByTestId('outcome-summary')).toHaveTextContent(/no completed executions/i)
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
  })
})
