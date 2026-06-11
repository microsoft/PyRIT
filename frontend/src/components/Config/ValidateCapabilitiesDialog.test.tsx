import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import ValidateCapabilitiesDialog from './ValidateCapabilitiesDialog'
import { targetsApi } from '@/services/api'
import type { TargetInstance, ValidateCapabilitiesResponse } from '@/types'

jest.mock('@/services/api', () => ({
  targetsApi: {
    validateCapabilities: jest.fn(),
  },
}))

const mockedApi = targetsApi as jest.Mocked<typeof targetsApi>

const TestWrapper: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
)

const sampleTarget: TargetInstance = {
  target_registry_name: 'azure_chat_test',
  target_type: 'OpenAIChatTarget',
  endpoint: 'https://example.openai.azure.com',
  model_name: 'gpt-4',
  capabilities: {
    supports_multi_turn: true,
    supports_multi_message_pieces: true,
    supports_json_schema: true,
    supports_json_output: true,
    supports_editable_history: true,
    supports_system_prompt: true,
    supported_input_modalities: ['text'],
    supported_output_modalities: ['text'],
  },
}

const allMatchResponse: ValidateCapabilitiesResponse = {
  target_registry_name: 'azure_chat_test',
  declared: sampleTarget.capabilities!,
  observed: sampleTarget.capabilities!,
  non_probeable_input_modalities: [],
  non_probeable_only_types: [],
  warnings: [
    'Validation sent live requests to the target; this may incur cost and produce real side effects.',
    'Test prompts written to memory are tagged with `capability_probe`.',
    'Output modalities are reported as declared (not actively probed).',
    'Capability probes confirm request acceptance, not semantic enforcement.',
    'Do not run Validate while an attack or scenario is actively using this target.',
  ],
}

const mismatchResponse: ValidateCapabilitiesResponse = {
  target_registry_name: 'azure_chat_test',
  declared: {
    ...sampleTarget.capabilities!,
    supports_json_schema: true,
    supported_input_modalities: ['image_path', 'text'],
  },
  observed: {
    ...sampleTarget.capabilities!,
    supports_json_schema: false,
    supported_input_modalities: ['text'],
  },
  non_probeable_input_modalities: [],
  non_probeable_only_types: [],
  warnings: ['Validation sent live requests to the target; ...'],
}

const notProbedResponse: ValidateCapabilitiesResponse = {
  target_registry_name: 'openai_response_test',
  declared: {
    ...sampleTarget.capabilities!,
    supported_input_modalities: ['function_call', 'reasoning', 'text', 'tool_call'],
  },
  observed: {
    ...sampleTarget.capabilities!,
    supported_input_modalities: ['function_call', 'reasoning', 'text', 'tool_call'],
  },
  non_probeable_input_modalities: ['function_call', 'reasoning', 'tool_call'],
  non_probeable_only_types: ['function_call', 'reasoning', 'tool_call'],
  warnings: [
    'Validation sent live requests to the target; ...',
    'Some declared input modalities are reported as declared/not-probed (no packaged probe asset): function_call, reasoning, tool_call.',
  ],
}

describe('ValidateCapabilitiesDialog', () => {
  beforeEach(() => {
    mockedApi.validateCapabilities.mockReset()
  })

  it('renders nothing when closed', () => {
    render(
      <TestWrapper>
        <ValidateCapabilitiesDialog open={false} target={sampleTarget} onClose={jest.fn()} />
      </TestWrapper>,
    )
    expect(screen.queryByText(/Validate capabilities/i)).not.toBeInTheDocument()
    expect(mockedApi.validateCapabilities).not.toHaveBeenCalled()
  })

  it('renders nothing when target is null', () => {
    render(
      <TestWrapper>
        <ValidateCapabilitiesDialog open={true} target={null} onClose={jest.fn()} />
      </TestWrapper>,
    )
    expect(screen.queryByText(/Validate capabilities/i)).not.toBeInTheDocument()
    expect(mockedApi.validateCapabilities).not.toHaveBeenCalled()
  })

  it('renders spinner while the request is in flight', async () => {
    // Never-resolving promise so we observe the spinner state.
    mockedApi.validateCapabilities.mockReturnValue(new Promise(() => {}))
    render(
      <TestWrapper>
        <ValidateCapabilitiesDialog open={true} target={sampleTarget} onClose={jest.fn()} />
      </TestWrapper>,
    )
    await waitFor(() => expect(mockedApi.validateCapabilities).toHaveBeenCalledWith('azure_chat_test'))
    expect(screen.getByText(/Probing target/i)).toBeInTheDocument()
  })

  it('renders error message when the API call rejects', async () => {
    mockedApi.validateCapabilities.mockRejectedValue(new Error('boom'))
    render(
      <TestWrapper>
        <ValidateCapabilitiesDialog open={true} target={sampleTarget} onClose={jest.fn()} />
      </TestWrapper>,
    )
    await waitFor(() => expect(screen.getByText('boom')).toBeInTheDocument())
  })

  it('renders the capabilities table once the request resolves', async () => {
    mockedApi.validateCapabilities.mockResolvedValue(allMatchResponse)
    render(
      <TestWrapper>
        <ValidateCapabilitiesDialog open={true} target={sampleTarget} onClose={jest.fn()} />
      </TestWrapper>,
    )
    await waitFor(() => expect(screen.getByText('Multi-turn')).toBeInTheDocument())
    expect(screen.getByText('JSON Schema')).toBeInTheDocument()
    expect(screen.getByText('Input modalities')).toBeInTheDocument()
    expect(screen.getByText('Output modalities')).toBeInTheDocument()
  })

  it('shows red mismatch indicator when declared differs from observed', async () => {
    mockedApi.validateCapabilities.mockResolvedValue(mismatchResponse)
    render(
      <TestWrapper>
        <ValidateCapabilitiesDialog open={true} target={sampleTarget} onClose={jest.fn()} />
      </TestWrapper>,
    )
    await waitFor(() => expect(screen.getByText('Multi-turn')).toBeInTheDocument())
    // At least one mismatch indicator (JSON Schema differs, input modalities differ).
    const mismatches = screen.getAllByText('mismatch')
    expect(mismatches.length).toBeGreaterThanOrEqual(2)
  })

  it('shows green match indicator when declared equals observed', async () => {
    mockedApi.validateCapabilities.mockResolvedValue(allMatchResponse)
    render(
      <TestWrapper>
        <ValidateCapabilitiesDialog open={true} target={sampleTarget} onClose={jest.fn()} />
      </TestWrapper>,
    )
    await waitFor(() => expect(screen.getByText('Multi-turn')).toBeInTheDocument())
    const matches = screen.getAllByText('match')
    // 6 boolean rows + 1 input-modalities row, all match.
    expect(matches.length).toBeGreaterThanOrEqual(7)
  })

  it('renders all warning messages', async () => {
    mockedApi.validateCapabilities.mockResolvedValue(allMatchResponse)
    render(
      <TestWrapper>
        <ValidateCapabilitiesDialog open={true} target={sampleTarget} onClose={jest.fn()} />
      </TestWrapper>,
    )
    await waitFor(() => expect(screen.getByText(/Test prompts written to memory/i)).toBeInTheDocument())
    expect(screen.getByText(/Output modalities are reported as declared/i)).toBeInTheDocument()
    expect(screen.getByText(/Do not run Validate while an attack/i)).toBeInTheDocument()
  })

  it('calls onClose when the Close button is clicked', async () => {
    const user = userEvent.setup()
    const onClose = jest.fn()
    mockedApi.validateCapabilities.mockResolvedValue(allMatchResponse)
    render(
      <TestWrapper>
        <ValidateCapabilitiesDialog open={true} target={sampleTarget} onClose={onClose} />
      </TestWrapper>,
    )
    await waitFor(() => expect(screen.getByText('Multi-turn')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: /close/i }))
    expect(onClose).toHaveBeenCalled()
  })

  it('resets state when reopened for a different target', async () => {
    mockedApi.validateCapabilities.mockResolvedValue(allMatchResponse)
    const { rerender } = render(
      <TestWrapper>
        <ValidateCapabilitiesDialog open={true} target={sampleTarget} onClose={jest.fn()} />
      </TestWrapper>,
    )
    await waitFor(() => expect(mockedApi.validateCapabilities).toHaveBeenCalledWith('azure_chat_test'))
    await waitFor(() => expect(screen.getByText('Multi-turn')).toBeInTheDocument())

    const otherTarget: TargetInstance = { ...sampleTarget, target_registry_name: 'other' }
    mockedApi.validateCapabilities.mockReturnValue(new Promise(() => {}))
    rerender(
      <TestWrapper>
        <ValidateCapabilitiesDialog open={true} target={otherTarget} onClose={jest.fn()} />
      </TestWrapper>,
    )
    await waitFor(() => expect(mockedApi.validateCapabilities).toHaveBeenCalledWith('other'))
    // Spinner is visible (state reset for the new target).
    expect(screen.getByText(/Probing target/i)).toBeInTheDocument()
  })

  it('resets state when reopened for the SAME target', async () => {
    // First open: resolves successfully.
    mockedApi.validateCapabilities.mockResolvedValueOnce(allMatchResponse)
    const onClose = jest.fn()
    const { rerender } = render(
      <TestWrapper>
        <ValidateCapabilitiesDialog open={true} target={sampleTarget} onClose={onClose} />
      </TestWrapper>,
    )
    await waitFor(() => expect(screen.getByText('Multi-turn')).toBeInTheDocument())

    // Close.
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: /close/i }))
    expect(onClose).toHaveBeenCalled()
    rerender(
      <TestWrapper>
        <ValidateCapabilitiesDialog open={false} target={sampleTarget} onClose={onClose} />
      </TestWrapper>,
    )

    // Reopen same target — must re-fire the request and show spinner again,
    // not the stale prior result.
    mockedApi.validateCapabilities.mockReturnValueOnce(new Promise(() => {}))
    rerender(
      <TestWrapper>
        <ValidateCapabilitiesDialog open={true} target={sampleTarget} onClose={onClose} />
      </TestWrapper>,
    )
    await waitFor(() => {
      expect(mockedApi.validateCapabilities).toHaveBeenCalledTimes(2)
    })
    expect(screen.getByText(/Probing target/i)).toBeInTheDocument()
    // Stale "Multi-turn" row should be gone while loading.
    expect(screen.queryByText('Multi-turn')).not.toBeInTheDocument()
  })

  it('renders the "Not probed (no asset)" row when non_probeable_input_modalities is non-empty', async () => {
    mockedApi.validateCapabilities.mockResolvedValue(notProbedResponse)
    render(
      <TestWrapper>
        <ValidateCapabilitiesDialog
          open={true}
          target={{ ...sampleTarget, target_registry_name: 'openai_response_test' }}
          onClose={jest.fn()}
        />
      </TestWrapper>,
    )
    await waitFor(() => expect(screen.getByTestId('not-probed-row')).toBeInTheDocument())
    expect(screen.getByText(/Not probed \(no asset\)/i)).toBeInTheDocument()
    expect(screen.getByText('function_call, reasoning, tool_call')).toBeInTheDocument()
    // Non-probeable types must NOT appear in the Input modalities cells —
    // otherwise the user sees the same types in both Observed and Not-probed,
    // which is contradictory. function_call should appear exactly twice on
    // screen: once in the Not-probed row, once in the warning bar text.
    // (Before the fix this would have been 3 — the third occurrence was the
    // Input modalities Observed cell, which is the regression we're guarding.)
    const functionCallOccurrences = screen.getAllByText(/function_call/)
    expect(functionCallOccurrences).toHaveLength(2)
  })

  it('does NOT render the "Not probed" row when non_probeable_input_modalities is empty', async () => {
    mockedApi.validateCapabilities.mockResolvedValue(allMatchResponse)
    render(
      <TestWrapper>
        <ValidateCapabilitiesDialog open={true} target={sampleTarget} onClose={jest.fn()} />
      </TestWrapper>,
    )
    await waitFor(() => expect(screen.getByText('Multi-turn')).toBeInTheDocument())
    expect(screen.queryByTestId('not-probed-row')).not.toBeInTheDocument()
    expect(screen.queryByText(/Not probed \(no asset\)/i)).not.toBeInTheDocument()
  })

  it('keeps probeable-confirmed types in the Input modalities cells even when a sibling combo bundles them with a non-probeable type', async () => {
    // Regression: a target declaring both {text} and {text, function_call}
    // sends back non_probeable_input_modalities=['function_call+text'] (the
    // mixed combo) AND non_probeable_only_types=['function_call'] (only
    // function_call is exclusively non-probeable; text is confirmed via the
    // {text} singleton). The cells must hide function_call but keep text —
    // otherwise the user sees '— / —' for Input modalities and a green
    // match indicator while text was actually probed and confirmed.
    const mixedComboResponse: ValidateCapabilitiesResponse = {
      target_registry_name: 'mixed_combo_target',
      declared: {
        ...sampleTarget.capabilities!,
        supported_input_modalities: ['function_call', 'text'],
      },
      observed: {
        ...sampleTarget.capabilities!,
        supported_input_modalities: ['function_call', 'text'],
      },
      non_probeable_input_modalities: ['function_call+text'],
      non_probeable_only_types: ['function_call'],
      warnings: [],
    }
    mockedApi.validateCapabilities.mockResolvedValue(mixedComboResponse)
    render(
      <TestWrapper>
        <ValidateCapabilitiesDialog
          open={true}
          target={{ ...sampleTarget, target_registry_name: 'mixed_combo_target' }}
          onClose={jest.fn()}
        />
      </TestWrapper>,
    )
    await waitFor(() => expect(screen.getByTestId('not-probed-row')).toBeInTheDocument())
    // The Input modalities row must show 'text' in both cells (probed and
    // confirmed). The Not-probed row must show the mixed combo.
    const inputRow = screen.getByTestId('input-modalities-row')
    expect(inputRow).toHaveTextContent('text')
    expect(inputRow).not.toHaveTextContent('function_call')
    expect(screen.getByText('function_call+text')).toBeInTheDocument()
  })

  it('shows a warning banner for composite targets', async () => {
    mockedApi.validateCapabilities.mockResolvedValue(allMatchResponse)
    const compositeTarget: TargetInstance = {
      ...sampleTarget,
      inner_targets: [
        { ...sampleTarget, target_registry_name: 'inner_1' },
        { ...sampleTarget, target_registry_name: 'inner_2' },
      ],
    }
    render(
      <TestWrapper>
        <ValidateCapabilitiesDialog open={true} target={compositeTarget} onClose={jest.fn()} />
      </TestWrapper>,
    )
    await waitFor(() => expect(screen.getByText(/This is a composite target/i)).toBeInTheDocument())
  })
})
