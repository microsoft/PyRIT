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
  it('renders nothing when there is neither an outcome nor a score', () => {
    render(
      <TestWrapper>
        <AttackVerdictChip outcome={null} score={null} />
      </TestWrapper>
    )

    expect(screen.queryByTestId('attack-verdict-chip')).not.toBeInTheDocument()
    expect(screen.queryByTestId('attack-score-chip')).not.toBeInTheDocument()
  })

  it('renders an outcome-only badge when there is an outcome but no score', () => {
    render(
      <TestWrapper>
        <AttackVerdictChip outcome="failure" score={null} />
      </TestWrapper>
    )

    // No score means no interactive score chip/popover, just the outcome badge.
    expect(screen.queryByTestId('attack-score-chip')).not.toBeInTheDocument()
    const chip = screen.getByTestId('attack-verdict-chip')
    expect(within(chip).getByText('failure')).toBeInTheDocument()
  })

  it('shows a single chip labeled with the outcome, tinted by the score', () => {
    render(
      <TestWrapper>
        <AttackVerdictChip outcome="failure" score={sampleScore} />
      </TestWrapper>
    )

    const chip = screen.getByRole('button', { name: /verdict failure, score true/i })
    // The chip shows the outcome; the raw score value lives in the popover
    expect(within(chip).getByText('failure')).toBeInTheDocument()
    expect(within(chip).queryByText('true')).not.toBeInTheDocument()
  })

  it('opens a popover with full verdict details when clicked', async () => {
    const user = userEvent.setup()
    render(
      <TestWrapper>
        <AttackVerdictChip outcome="failure" score={sampleScore} />
      </TestWrapper>
    )

    await user.click(screen.getByRole('button', { name: /verdict failure, score true/i }))

    const details = await screen.findByTestId('attack-score-details')
    expect(within(details).getByText('true_false')).toBeInTheDocument()
    expect(within(details).getByText('SelfAskRefusalScorer')).toBeInTheDocument()
    expect(within(details).getByText('refusal')).toBeInTheDocument()
    expect(within(details).getByText('The model refused the request.')).toBeInTheDocument()
  })

  it('shows the underlying scale score when a thresholded verdict provides one', async () => {
    const user = userEvent.setup()
    const thresholdScore: ScoreView = {
      id: 'score-2',
      scorer_type: 'FloatScaleThresholdScorer',
      score_type: 'true_false',
      score_value: 'false',
      score_category: ['humor'],
      score_rationale: 'Normalized scale score: 0.5 < threshold 0.6',
      scale_score: 0.5,
      timestamp: '2026-01-15T11:00:00Z',
    }
    render(
      <TestWrapper>
        <AttackVerdictChip outcome="failure" score={thresholdScore} />
      </TestWrapper>
    )

    await user.click(screen.getByRole('button', { name: /verdict failure, score false/i }))

    const details = await screen.findByTestId('attack-score-details')
    expect(within(details).getByText('Scale score')).toBeInTheDocument()
    expect(within(details).getByText('0.50')).toBeInTheDocument()
  })

  it('omits the scale score row for a plain true/false score', async () => {
    const user = userEvent.setup()
    render(
      <TestWrapper>
        <AttackVerdictChip outcome="failure" score={sampleScore} />
      </TestWrapper>
    )

    await user.click(screen.getByRole('button', { name: /verdict failure, score true/i }))

    const details = await screen.findByTestId('attack-score-details')
    expect(within(details).queryByText('Scale score')).not.toBeInTheDocument()
  })

  it('marks the verdict as attack-level when a related conversation is active', async () => {
    const user = userEvent.setup()
    render(
      <TestWrapper>
        <AttackVerdictChip outcome="failure" score={sampleScore} appliesToActiveConversation={false} />
      </TestWrapper>
    )

    // The accessible name signals the verdict is attack-level, not this conversation's.
    const chip = screen.getByRole('button', { name: /attack-level verdict failure/i })
    await user.click(chip)

    const details = await screen.findByTestId('attack-score-details')
    expect(within(details).getByTestId('attack-level-note')).toBeInTheDocument()
  })

  it('does not mark the verdict as attack-level on the main conversation', () => {
    render(
      <TestWrapper>
        <AttackVerdictChip outcome="failure" score={sampleScore} appliesToActiveConversation={true} />
      </TestWrapper>
    )

    expect(screen.queryByRole('button', { name: /attack-level verdict/i })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /verdict failure, score true/i })).toBeInTheDocument()
  })
})
