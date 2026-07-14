import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import AttackVerdictChip from './AttackVerdictChip'
import type { ScoreView } from '../../types'

const TestWrapper: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
)

const sampleScore: ScoreView = {
  id: 'score-1',
  scorer_type: 'SelfAskRefusalScorer',
  score_type: 'true_false',
  score_value: 'true',
  score_category: ['refusal'],
  score_rationale: 'The model refused the request.',
  timestamp: '2026-01-15T11:00:00Z',
}

describe('AttackVerdictChip', () => {
  it('renders nothing when there is no score', () => {
    render(
      <TestWrapper>
        <AttackVerdictChip outcome="failure" score={null} />
      </TestWrapper>
    )

    expect(screen.queryByTestId('attack-score-chip')).not.toBeInTheDocument()
  })

  it('shows only the score value on the chip, labeled "Score"', () => {
    render(
      <TestWrapper>
        <AttackVerdictChip outcome="failure" score={sampleScore} />
      </TestWrapper>
    )

    const chip = screen.getByRole('button', { name: /score true/i })
    expect(within(chip).getByText('Score')).toBeInTheDocument()
    expect(within(chip).getByText('true')).toBeInTheDocument()
    // The outcome word must never appear on the chip itself.
    expect(within(chip).queryByText(/failure/i)).not.toBeInTheDocument()
  })

  it('opens a popover with full score details when clicked', async () => {
    const user = userEvent.setup()
    render(
      <TestWrapper>
        <AttackVerdictChip outcome="failure" score={sampleScore} />
      </TestWrapper>
    )

    await user.click(screen.getByRole('button', { name: /score true/i }))

    const details = await screen.findByTestId('attack-score-details')
    expect(within(details).getByText('true_false')).toBeInTheDocument()
    expect(within(details).getByText('SelfAskRefusalScorer')).toBeInTheDocument()
    expect(within(details).getByText('refusal')).toBeInTheDocument()
    expect(within(details).getByText('The model refused the request.')).toBeInTheDocument()
  })
})
