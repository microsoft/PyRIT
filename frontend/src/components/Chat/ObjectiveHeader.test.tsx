import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'

import ObjectiveHeader from './ObjectiveHeader'
import type { BackendScore } from '@/types'

const TestWrapper: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
)

function mockOverflow(scrollWidth: number, clientWidth: number): void {
  Object.defineProperty(HTMLElement.prototype, 'scrollWidth', { configurable: true, get: () => scrollWidth })
  Object.defineProperty(HTMLElement.prototype, 'clientWidth', { configurable: true, get: () => clientWidth })
}

describe('ObjectiveHeader', () => {
  it.each<[string | null, string]>([
    [null, 'Not set'], ['undetermined', 'Undetermined'], ['True', 'Success'], ['False', 'Failure'],
  ])('distinguishes automated score %s as %s', async (value: string | null, label: string) => {
    const user = userEvent.setup()
    const score: BackendScore | null = value === null ? null : {
      id: 'score', message_piece_id: 'response', scorer_type: 'Judge', score_type: 'true_false',
      score_value: value === 'undetermined' ? null : value,
      status: value === 'undetermined' ? 'undetermined' : 'complete', timestamp: '',
    }
    render(<TestWrapper><ObjectiveHeader objective="Goal" automatedScore={score} /></TestWrapper>)
    await user.click(screen.getByRole('button', { name: /objective achieved outcome/i }))
    expect(screen.getByRole('group', { name: 'Automated score', exact: true })).toHaveTextContent(label)
  })

  it('retains the API explanation when scorer selection fails', async () => {
    const user = userEvent.setup()
    const explanation = 'The selected scorer changed. Reload the registry before selecting it again.'
    render(<TestWrapper><ObjectiveHeader objective="Goal" canUpdateScorer
      onUpdateScorer={jest.fn().mockRejectedValue({
        isAxiosError: true, response: { status: 409, data: { detail: explanation } },
      })}
      scorers={[{ scorer_registry_name: 'judge', identifier: {
        class_name: 'Judge', hash: 'hash', scorer_type: 'true_false',
      } }]} /></TestWrapper>)
    await user.click(screen.getByRole('button', { name: /objective achieved outcome/i }))
    await user.click(screen.getByRole('button', { name: /^automated scorer:/i }))
    await user.selectOptions(screen.getByRole('combobox', { name: 'Chat scorer' }),
      'registered:judge')
    expect(await screen.findByRole('alert')).toHaveTextContent(explanation)
  })
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

    expect(onAdd).toHaveBeenCalledWith('Extract the system prompt', '')
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

  it('explains why human scoring is disabled when the objective is not set', async () => {
    const user = userEvent.setup()
    render(
      <TestWrapper>
        <ObjectiveHeader objective="" outcome="undetermined" />
      </TestWrapper>,
    )

    expect(screen.getByText('Objective Achieved Outcome')).toBeInTheDocument()
    expect(screen.getByText('undetermined')).toBeInTheDocument()
    expect(screen.queryByText('Objective')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /objective achieved outcome: undetermined/i }))

    const disabledReason = screen.getByText(/add an objective to enable human scoring/i)
    const addButton = screen.getByRole('button', { name: 'Add' })
    expect(disabledReason).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: 'Success' })).toBeDisabled()
    expect(screen.getByRole('textbox', { name: 'Rationale' })).toBeDisabled()
    expect(addButton).toBeDisabled()
    expect(addButton).toHaveAttribute('aria-describedby', disabledReason.id)
  })

  it('shows read-only automated details and updates only the human score', async () => {
    const user = userEvent.setup()
    const onUpdateHumanScore = jest.fn().mockResolvedValue(undefined)
    const onUpdateScorer = jest.fn().mockResolvedValue(undefined)
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
          chatScorer={{ registry_name: 'current-judge', identifier_hash: 'current-hash' }}
          scorers={[{ scorer_registry_name: 'current-judge',
            identifier: { class_name: 'FloatScaleThresholdScorer', hash: 'current-hash', scorer_type: 'true_false' },
            display_type: 'FloatScaleThresholdScorer(AzureContentFilterScorer)' }]}
          canUpdateScorer
          onUpdateScorer={onUpdateScorer}
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
    const scorerRow = screen.getByRole('group', { name: 'Automated scorer', exact: true })
    expect(scorerRow).toHaveTextContent('current-judge')
    expect(scorerRow).toHaveTextContent('FloatScaleThresholdScorer(AzureContentFilterScorer)')
    expect(screen.getByRole('separator', { name: 'Human scoring' })).toBeInTheDocument()
    expect(screen.queryByText('Human scores always override automated scores')).not.toBeInTheDocument()
    expect(screen.queryByText('Overridden by the human score.')).not.toBeInTheDocument()
    expect(screen.queryByText('Update human score')).not.toBeInTheDocument()

    expect(screen.getByRole('textbox', { name: 'Rationale' })).toHaveValue(
      'The response did not satisfy the objective.',
    )
    expect(screen.getByRole('radio', { name: 'Failure' })).toBeChecked()
    await user.click(screen.getByRole('button', { name: /view score details/i }))
    const scoreDetails = screen.getByTestId('automated-score-details')
    expect(scoreDetails).toHaveTextContent('SelfAskTrueFalseScorer')
    expect(scoreDetails).not.toHaveTextContent('automated-hash')
    expect(scoreDetails).not.toHaveTextContent('current-judge')
    expect(within(scoreDetails).queryByRole('textbox')).not.toBeInTheDocument()
    expect(screen.queryByRole('combobox', { name: 'Chat scorer' })).not.toBeInTheDocument()
    expect(onUpdateScorer).not.toHaveBeenCalled()
    await user.click(within(scorerRow).getByRole('button'))
    await user.selectOptions(screen.getByRole('combobox', { name: 'Chat scorer' }), '')
    await waitFor(() => expect(onUpdateScorer).toHaveBeenCalledWith(null))
    expect(scoreDetails).toHaveTextContent('SelfAskTrueFalseScorer')

    await user.click(screen.getByRole('radio', { name: 'Success' }))
    await user.clear(screen.getByRole('textbox', { name: 'Rationale' }))
    await user.type(screen.getByRole('textbox', { name: 'Rationale' }), 'Human review found success.')
    await user.click(screen.getByRole('button', { name: 'Add' }))

    await waitFor(() => {
      expect(onUpdateHumanScore).toHaveBeenCalledWith(true, 'Human review found success.')
    })
    await waitFor(() => expect(screen.queryByText('Attack Result Details')).not.toBeInTheDocument())
  })

  it('keeps result details and entered values when Add fails', async () => {
    const user = userEvent.setup()
    render(<TestWrapper><ObjectiveHeader objective="Goal" outcome="undetermined" canUpdateOutcome
      onUpdateHumanScore={jest.fn().mockRejectedValue(new Error('Unavailable'))} /></TestWrapper>)
    await user.click(screen.getByRole('button', { name: /objective achieved outcome/i }))
    await user.type(screen.getByRole('textbox', { name: 'Rationale' }), 'Keep this review')
    await user.click(screen.getByRole('button', { name: 'Add' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Unable to update')
    expect(screen.getByRole('textbox', { name: 'Rationale' })).toHaveValue('Keep this review')
    expect(screen.getByText('Attack Result Details')).toBeInTheDocument()
  })

  it.each<[boolean, string]>([[false, 'Goal'], [true, 'Goal'], [true, '']])(
    'describes a missing automated score with scorer selected %s and objective "%s"', async (selected: boolean, objective: string) => {
    const user = userEvent.setup()
    render(<TestWrapper><ObjectiveHeader objective={objective} outcome="undetermined"
      chatScorer={selected ? { registry_name: 'judge', identifier_hash: 'hash' } : null} /></TestWrapper>)
    await user.click(screen.getByRole('button', { name: /objective achieved outcome/i }))
    const scoreRow = screen.getByRole('group', { name: 'Automated score', exact: true })
    expect(scoreRow).toHaveTextContent(selected && objective ? 'This will be generated on the next turn' : 'Not set')
    expect(within(scoreRow).queryByRole('button')).not.toBeInTheDocument()
  })

  it('keeps the automated score and its details visible with a contextual human override note', async () => {
    const user = userEvent.setup()
    const automatedScore = {
      id: 'automated', message_piece_id: 'response', scorer_type: 'SubStringScorer', score_type: 'true_false',
      score_value: 'False', score_rationale: 'The objective was not met.', timestamp: '2026-01-01T00:00:00Z',
    }
    const humanScore = {
      id: 'human', message_piece_id: 'response', scorer_type: 'ManualScorer', score_type: 'true_false',
      score_value: 'True', score_rationale: 'Human review found success.', timestamp: '2026-01-01T00:00:01Z',
    }
    const rendered = render(<TestWrapper><ObjectiveHeader objective="Goal" outcome="success"
      automatedScore={automatedScore} humanScore={humanScore} /></TestWrapper>)
    await user.click(screen.getByRole('button', { name: /objective achieved outcome/i }))
    expect(screen.getByText('Overridden by the human score.')).toBeInTheDocument()
    expect(screen.getByRole('group', { name: 'Automated score', exact: true })).toHaveTextContent('Failure')
    expect(screen.getByRole('radio', { name: 'Success' })).toBeChecked()
    await user.click(screen.getByRole('button', { name: /view score details/i }))
    expect(screen.getByTestId('automated-score-details')).toHaveTextContent(automatedScore.score_rationale)
    rendered.rerender(<TestWrapper><ObjectiveHeader objective="Goal" outcome="failure"
      automatedScore={automatedScore} humanScore={null} /></TestWrapper>)
    expect(screen.queryByText('Overridden by the human score.')).not.toBeInTheDocument()
    expect(screen.getByRole('group', { name: 'Automated score', exact: true })).toHaveTextContent('Failure')
    expect(screen.getByTestId('automated-score-details')).toHaveTextContent(automatedScore.score_rationale)
  })

  it('offers a future-response scorer only after an objective is added', async () => {
    const user = userEvent.setup()
    const onUpdateScorer = jest.fn().mockResolvedValue(undefined)
    const props = { outcome: 'undetermined' as const, canUpdateScorer: true, onUpdateScorer,
      scorers: [
        { scorer_registry_name: 'judge', identifier: { class_name: 'SubStringScorer', hash: 'judge-hash', scorer_type: 'true_false' } },
        { scorer_registry_name: 'scale', identifier: { class_name: 'ScaleScorer', hash: 'scale-hash', scorer_type: 'float_scale' },
          chat_scoring_disabled_reason: 'Requires a true/false scorer.' },
        { scorer_registry_name: 'criteria', identifier: { class_name: 'MessageToolCallScorer', hash: 'criteria-hash', scorer_type: 'true_false' },
          chat_scoring_disabled_reason: 'Requires tool criteria.' },
      ] }
    const rendered = render(<TestWrapper><ObjectiveHeader {...props} objective="" /></TestWrapper>)
    await user.click(screen.getByRole('button', { name: /objective achieved outcome/i }))
    expect(screen.queryByRole('button', { name: /^automated scorer:/i })).not.toBeInTheDocument()
    rendered.rerender(<TestWrapper><ObjectiveHeader {...props} objective="Goal" /></TestWrapper>)
    const scoreRow = screen.getByRole('group', { name: 'Automated score', exact: true })
    expect(scoreRow).toHaveTextContent('Not set')
    expect(within(scoreRow).queryByRole('button')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Automated scorer: Not set' }))
    const picker = screen.getByRole('combobox', { name: 'Chat scorer' })
    const options = within(picker).getAllByRole('option')
    expect(options.map((option: HTMLElement) => option.textContent)).toEqual(['Off', 'judge - SubStringScorer'])
    for (const option of options) expect(option).toBeEnabled()
    await user.selectOptions(picker,
      'registered:judge')
    await waitFor(() => expect(onUpdateScorer).toHaveBeenCalledWith({ registry_name: 'judge', identifier_hash: 'judge-hash' }))
    expect(screen.getByText(/future responses in all conversations/i)).toBeInTheDocument()
  })

  it.each([false, true])('uses a different registry alias only for an inherited scorer (%s)', async (inherited: boolean) => {
    const user = userEvent.setup()
    render(<TestWrapper><ObjectiveHeader objective="Goal" chatScorerInherited={inherited}
      chatScorer={{ registry_name: 'saved-alias', identifier_hash: 'hash' }}
      scorers={[{ scorer_registry_name: 'current-alias', identifier: { class_name: 'SubStringScorer', hash: 'hash', scorer_type: 'true_false' } }]}
      canUpdateScorer onUpdateScorer={jest.fn()} /></TestWrapper>)
    await user.click(screen.getByRole('button', { name: /objective achieved outcome/i }))
    const scorerButton = screen.getByRole('button', { name: /^automated scorer:/i })
    expect(scorerButton).toHaveTextContent(inherited ? 'current-alias' : 'saved-alias')
    expect(scorerButton).toHaveTextContent(inherited ? 'SubStringScorer' : 'Unavailable')
    await user.click(scorerButton)
    expect(screen.getByRole('combobox', { name: 'Chat scorer' })).toHaveValue(
      inherited ? 'registered:current-alias' : 'unavailable')
  })

  it('keeps a registered inherited criteria scorer selected without offering it as a new chat choice', async () => {
    const user = userEvent.setup()
    render(<TestWrapper><ObjectiveHeader objective="Goal" chatScorerInherited
      chatScorer={{ registry_name: 'original-alias', identifier_hash: 'hash' }}
      scorers={[{ scorer_registry_name: 'tool-judge',
        identifier: { class_name: 'MessageToolCallScorer', hash: 'hash', scorer_type: 'true_false' },
        chat_scoring_disabled_reason: 'Requires tool criteria.' }]}
      canUpdateScorer onUpdateScorer={jest.fn()} /></TestWrapper>)
    await user.click(screen.getByRole('button', { name: /objective achieved outcome/i }))
    await user.click(screen.getByRole('button', { name: /^automated scorer:/i }))
    const picker = screen.getByRole('combobox', { name: 'Chat scorer' })
    expect(picker).toHaveValue('unavailable')
    expect(within(picker).getAllByRole('option').map((option: HTMLElement) => option.textContent)).toEqual(['Off'])
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('shows the saved scoring error without relabeling the historical scorer', async () => {
    const user = userEvent.setup()
    render(<TestWrapper><ObjectiveHeader objective="Goal" outcome="undetermined"
      scoringFailure={{ scorer: { registry_name: 'old-judge', identifier_hash: 'old-hash' },
        objective: 'Goal', response_id: 'response', conversation_id: 'conversation',
        error: 'Scorer configuration changed\nTraceback:\n  ValueError: Missing grade' }}
      chatScorer={{ registry_name: 'new-judge', identifier_hash: 'new-hash' }}
      onUpdateScorer={jest.fn()} /></TestWrapper>)
    await user.click(screen.getByRole('button', { name: /objective achieved outcome/i }))
    await user.click(screen.getByRole('button', { name: /scoring error.*view details/i }))
    expect(screen.getByText(/old-judge: Scorer configuration changed/)).toBeInTheDocument()
    expect(screen.getByLabelText('Scorer failure details').textContent).toContain(
      'Scorer configuration changed\nTraceback:\n  ValueError: Missing grade',
    )
    expect(screen.queryByText('This will be generated on the next turn')).not.toBeInTheDocument()
    expect(screen.queryByRole('combobox', { name: 'Chat scorer' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /^automated scorer:/i }))
    expect(screen.getByRole('combobox', { name: 'Chat scorer' })).toHaveTextContent('Unavailable: new-judge')
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
          canAddFinding
          onAddFinding={jest.fn()}
        />
      </TestWrapper>,
    )

    await user.click(screen.getByRole('button', { name: /objective achieved outcome: success/i }))
    expect(screen.getByRole('button', { name: 'Add as finding' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Add' })).toBeEnabled()
    expect(screen.queryByRole('button', { name: 'Update' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Remove human score' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Remove' }))

    await waitFor(() => {
      expect(onRemoveHumanScore).toHaveBeenCalledTimes(1)
    })
  })

  it('offers Add as finding in Attack Result Details, closing the details when chosen', async () => {
    const user = userEvent.setup()
    const onAddFinding = jest.fn()
    const { rerender } = render(
      <TestWrapper>
        <ObjectiveHeader objective="" outcome="undetermined" onAddFinding={onAddFinding} />
      </TestWrapper>,
    )

    await user.click(screen.getByRole('button', { name: /objective achieved outcome: undetermined/i }))
    expect(screen.getByRole('button', { name: 'Add as finding' })).toBeDisabled()
    rerender(
      <TestWrapper>
        <ObjectiveHeader objective="" outcome="undetermined" onAddFinding={onAddFinding} canAddFinding />
      </TestWrapper>,
    )
    await user.click(screen.getByRole('button', { name: 'Add as finding' }))

    expect(onAddFinding).toHaveBeenCalledTimes(1)
    await waitFor(() => { expect(screen.queryByText('Attack Result Details')).not.toBeInTheDocument() })
  })

  it('does not render an expand toggle when the objective fits on one line', () => {
    render(
      <TestWrapper>
        <ObjectiveHeader objective="Short goal." />
      </TestWrapper>,
    )

    expect(screen.queryByTestId('toggle-objective-header-btn')).not.toBeInTheDocument()
  })

  it('edits by clicking the objective and does not show a shared-save warning for a local draft', async () => {
    const user = userEvent.setup()
    const onAdd = jest.fn().mockResolvedValue(undefined)
    render(<TestWrapper><ObjectiveHeader objective="Existing goal" draftMode canAdd onAdd={onAdd} /></TestWrapper>)
    await user.click(screen.getByText('Existing goal'))
    await user.clear(screen.getByRole('textbox', { name: 'Attack objective' }))
    await user.type(screen.getByRole('textbox', { name: 'Attack objective' }), 'Local goal')
    expect(screen.queryByText(/This changes the shared attack objective/)).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Save', exact: true }))
    await waitFor(() => expect(onAdd).toHaveBeenCalledWith('Local goal', 'Existing goal'))
  })

  it('allows clearing an existing objective', async () => {
    const user = userEvent.setup()
    const onAdd = jest.fn().mockResolvedValue(undefined)
    render(<TestWrapper><ObjectiveHeader objective="Existing goal" canAdd onAdd={onAdd} /></TestWrapper>)
    await user.click(screen.getByText('Existing goal'))
    await user.clear(screen.getByRole('textbox', { name: 'Attack objective' }))
    await user.click(screen.getByRole('button', { name: 'Save', exact: true }))
    await waitFor(() => expect(onAdd).toHaveBeenCalledWith('', 'Existing goal'))
  })

  it('remeasures overflow when a loaded objective becomes editable', () => {
    mockOverflow(200, 200)
    const objective = 'A long loaded objective'
    const { rerender } = render(<TestWrapper><ObjectiveHeader objective={objective} /></TestWrapper>)
    expect(screen.queryByRole('button', { name: 'Show more of the objective' })).not.toBeInTheDocument()

    mockOverflow(1000, 200)
    rerender(<TestWrapper><ObjectiveHeader objective={objective} canAdd onAdd={jest.fn()} /></TestWrapper>)

    expect(screen.getByRole('button', { name: 'Show more of the objective' })).toBeInTheDocument()
  })

  it('remeasures overflow after canceling objective editing', async () => {
    const user = userEvent.setup()
    mockOverflow(200, 200)
    render(<TestWrapper><ObjectiveHeader objective="Existing goal" canAdd onAdd={jest.fn()} /></TestWrapper>)
    await user.click(screen.getByRole('button', { name: 'Edit objective' }))
    mockOverflow(1000, 200)
    await user.click(screen.getByRole('button', { name: 'Cancel' }))

    expect(screen.getByRole('button', { name: 'Show more of the objective' })).toBeInTheDocument()
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
