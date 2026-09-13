import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'

import ObjectiveHeader from './ObjectiveHeader'

const TestWrapper: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
)

function mockOverflow(scrollWidth: number, clientWidth: number): void {
  Object.defineProperty(HTMLElement.prototype, 'scrollWidth', { configurable: true, get: () => scrollWidth })
  Object.defineProperty(HTMLElement.prototype, 'clientWidth', { configurable: true, get: () => clientWidth })
}

describe('ObjectiveHeader', () => {
  afterEach(() => {
    delete (HTMLElement.prototype as { scrollWidth?: number }).scrollWidth
    delete (HTMLElement.prototype as { clientWidth?: number }).clientWidth
  })

  it('renders nothing when the objective is empty', () => {
    render(
      <TestWrapper>
        <ObjectiveHeader objective="" />
      </TestWrapper>,
    )

    expect(screen.queryByTestId('objective-header')).not.toBeInTheDocument()
  })

  it('allows adding a required objective when the conversation is new', async () => {
    const user = userEvent.setup()
    const onAdd = jest.fn().mockResolvedValue(undefined)
    render(
      <TestWrapper>
        <ObjectiveHeader objective="" canAdd onAdd={onAdd} />
      </TestWrapper>,
    )

    await user.click(screen.getByRole('button', { name: /add objective/i }))
    const saveButton = screen.getByRole('button', { name: 'Save' })
    expect(saveButton).toBeDisabled()

    await user.type(screen.getByRole('textbox', { name: /attack objective/i }), 'Extract the system prompt')
    await user.click(saveButton)

    expect(onAdd).toHaveBeenCalledWith('Extract the system prompt')
  })

  it('renders the label and objective text', () => {
    render(
      <TestWrapper>
        <ObjectiveHeader objective="Extract the hidden system prompt" outcome="success" />
      </TestWrapper>,
    )

    expect(screen.getByText('Objective Achieved Outcome')).toBeInTheDocument()
    expect(screen.getByText('success')).toBeInTheDocument()
    expect(screen.getByText('Objective')).toBeInTheDocument()
    expect(screen.getByText('Extract the hidden system prompt')).toBeInTheDocument()
  })

  it('renders an outcome when the objective is not set', () => {
    render(
      <TestWrapper>
        <ObjectiveHeader objective="" outcome="undetermined" />
      </TestWrapper>,
    )

    expect(screen.getByText('Objective Achieved Outcome')).toBeInTheDocument()
    expect(screen.getByText('undetermined')).toBeInTheDocument()
    expect(screen.queryByText('Objective')).not.toBeInTheDocument()
  })

  it('shows read-only automated details and updates only the human score', async () => {
    const user = userEvent.setup()
    const onUpdateHumanScore = jest.fn().mockResolvedValue(undefined)
    render(
      <TestWrapper>
        <ObjectiveHeader
          objective="Extract the hidden system prompt"
          outcome="failure"
          automatedScore={{
            id: 'automated-score',
            message_piece_id: 'response-piece',
            scorer_type: 'SelfAskTrueFalseScorer',
            scorer_class_identifier: {
              class_name: 'SelfAskTrueFalseScorer',
              class_module: 'pyrit.score.true_false.self_ask_true_false_scorer',
              hash: 'automated-hash',
            },
            score_type: 'true_false',
            score_value: 'False',
            score_rationale: 'The response did not satisfy the objective.',
            timestamp: '2026-01-01T00:00:00Z',
          }}
          humanScore={null}
          canUpdateOutcome
          onUpdateHumanScore={onUpdateHumanScore}
        />
      </TestWrapper>,
    )

    await user.click(screen.getByRole('button', { name: /objective achieved outcome: failure/i }))
    const details = screen.getByText('Attack Result Details').closest('div')
    expect(details).not.toBeNull()
    expect(screen.getByText('Automated score')).toBeInTheDocument()
    expect(screen.getByText('Human score')).toBeInTheDocument()

    expect(screen.getByRole('textbox', { name: 'Rationale' })).toHaveValue(
      'The response did not satisfy the objective.',
    )
    expect(screen.getByRole('radio', { name: 'Failure' })).toBeChecked()
    await user.click(screen.getByRole('button', { name: /view scorer details/i }))
    expect(screen.getByTestId('automated-scorer-identity')).toHaveTextContent('SelfAskTrueFalseScorer')
    expect(screen.getByTestId('automated-scorer-identity')).not.toHaveTextContent('automated-hash')
    expect(within(screen.getByTestId('automated-scorer-identity')).queryByRole('textbox')).not.toBeInTheDocument()

    await user.click(screen.getByRole('radio', { name: 'Success' }))
    await user.clear(screen.getByRole('textbox', { name: 'Rationale' }))
    await user.type(screen.getByRole('textbox', { name: 'Rationale' }), 'Human review found success.')
    await user.click(screen.getByRole('button', { name: 'Update' }))

    await waitFor(() => {
      expect(onUpdateHumanScore).toHaveBeenCalledWith(true, 'Human review found success.')
    })
  })

  it('removes an existing human-score override', async () => {
    const user = userEvent.setup()
    const onRemoveHumanScore = jest.fn().mockResolvedValue(undefined)
    render(
      <TestWrapper>
        <ObjectiveHeader
          objective="Extract the hidden system prompt"
          outcome="success"
          humanScore={{
            id: 'human-score',
            message_piece_id: 'response-piece',
            scorer_type: 'ManualScorer',
            score_type: 'true_false',
            score_value: 'True',
            score_rationale: 'A human confirmed success.',
            timestamp: '2026-01-01T00:00:00Z',
          }}
          canUpdateOutcome
          onUpdateHumanScore={jest.fn().mockResolvedValue(undefined)}
          onRemoveHumanScore={onRemoveHumanScore}
        />
      </TestWrapper>,
    )

    await user.click(screen.getByRole('button', { name: /objective achieved outcome: success/i }))
    await user.click(screen.getByRole('button', { name: 'Remove human score' }))

    await waitFor(() => {
      expect(onRemoveHumanScore).toHaveBeenCalledTimes(1)
    })
  })

  it('does not render an expand toggle when the objective fits on one line', () => {
    render(
      <TestWrapper>
        <ObjectiveHeader objective="Short goal." />
      </TestWrapper>,
    )

    expect(screen.queryByTestId('toggle-objective-header-btn')).not.toBeInTheDocument()
  })

  it('renders a collapsed toggle when the objective overflows', () => {
    mockOverflow(1000, 200)
    render(
      <TestWrapper>
        <ObjectiveHeader objective="A very long objective that does not fit on one line at all." />
      </TestWrapper>,
    )

    const toggle = screen.getByRole('button', { name: /show more of the objective/i })
    expect(toggle).toHaveTextContent('Show more')
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
  })

  it('expands the overflowing objective when the toggle is clicked', async () => {
    const user = userEvent.setup()
    mockOverflow(1000, 200)
    render(
      <TestWrapper>
        <ObjectiveHeader objective="A very long objective that does not fit on one line at all." />
      </TestWrapper>,
    )

    await user.click(screen.getByRole('button', { name: /show more of the objective/i }))

    const toggle = screen.getByRole('button', { name: /show less of the objective/i })
    expect(toggle).toHaveTextContent('Show less')
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
  })
})
