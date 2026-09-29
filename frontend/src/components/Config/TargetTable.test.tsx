import { render, screen, fireEvent, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { makeTarget } from '@/test-utils/targetFixtures'
import TargetTable from './TargetTable'
import type { TargetCapabilities, TargetInstance } from '../../types'

jest.mock('./TargetTable.styles', () => ({
  useTargetTableStyles: () => new Proxy({}, { get: () => '' }),
}))

const TestWrapper: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
)

function makeCapabilities(inputs: string[], outputs: string[]): TargetCapabilities {
  return {
    supports_multi_turn: true,
    supports_json_schema: false,
    supports_json_output: false,
    supports_system_prompt: false,
    supported_input_modalities: inputs,
    supported_output_modalities: outputs,
  }
}

function makeModalityTarget(name: string, inputs: string[], outputs: string[]): TargetInstance {
  return makeTarget({
    target_registry_name: name,
    target_type: 'OpenAIChatTarget',
    capabilities: makeCapabilities(inputs, outputs),
  })
}

const sampleTargets: TargetInstance[] = [
  makeTarget({
    target_registry_name: 'openai_chat_gpt4',
    target_type: 'OpenAIChatTarget',
    endpoint: 'https://api.openai.com',
    model_name: 'gpt-4',
    capabilities: {
      supports_multi_turn: true,
      supports_multi_message_pieces: true,
      supports_json_schema: true,
      supports_json_output: true,
      supports_editable_history: true,
      supports_system_prompt: true,
      supported_input_modalities: ['text', 'image_path'],
      supported_output_modalities: ['text'],
    },
  }),
  makeTarget({
    target_registry_name: 'azure_image_dalle',
    target_type: 'AzureImageTarget',
    endpoint: 'https://azure.openai.com',
    model_name: 'dall-e-3',
    capabilities: {
      supports_multi_turn: false,
      supports_multi_message_pieces: false,
      supports_json_schema: false,
      supports_json_output: false,
      supports_editable_history: false,
      supports_system_prompt: false,
      supported_input_modalities: ['text'],
      supported_output_modalities: ['image_path'],
    },
  }),
  makeTarget({
    target_registry_name: 'text_target_basic',
    target_type: 'TextTarget',
    endpoint: null,
    model_name: null,
  }),
]

describe('TargetTable', () => {
  const defaultProps = {
    targets: sampleTargets,
    defaultObjectiveTarget: null as TargetInstance | null,
    defaultAdversarialTarget: null as TargetInstance | null,
    onSetDefaultObjectiveTarget: jest.fn(),
    onSetDefaultAdversarialTarget: jest.fn(),
  }

  beforeEach(() => {
    jest.clearAllMocks()
  })

  it('should render a flat table with all targets visible', () => {
    render(
      <TestWrapper>
        <TargetTable {...defaultProps} />
      </TestWrapper>
    )

    expect(screen.getByRole('table')).toBeInTheDocument()
    expect(screen.getByText('gpt-4')).toBeInTheDocument()
    expect(screen.getByText('dall-e-3')).toBeInTheDocument()
    expect(screen.getAllByText('OpenAIChatTarget').length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText('AzureImageTarget').length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText('TextTarget').length).toBeGreaterThanOrEqual(1)
  })

  it('should display Registry Name, Type, Model, Endpoint, Inputs, Outputs, capability columns and Parameters columns', () => {
    render(
      <TestWrapper>
        <TargetTable {...defaultProps} />
      </TestWrapper>
    )

    expect(screen.getByText('Registry Name')).toBeInTheDocument()
    expect(screen.getByText('openai_chat_gpt4')).toBeInTheDocument()
    expect(screen.getByText('Type')).toBeInTheDocument()
    expect(screen.getByText('Model')).toBeInTheDocument()
    expect(screen.getByText('Endpoint')).toBeInTheDocument()
    expect(screen.getByText('Inputs')).toBeInTheDocument()
    expect(screen.getByText('Outputs')).toBeInTheDocument()
    expect(screen.getByText('Multi-turn')).toBeInTheDocument()
    expect(screen.getByText('Multi-piece')).toBeInTheDocument()
    expect(screen.getByText('JSON Schema')).toBeInTheDocument()
    expect(screen.getByText('JSON Output')).toBeInTheDocument()
    expect(screen.getByText('Edit History')).toBeInTheDocument()
    expect(screen.getByText('System Prompt')).toBeInTheDocument()
    expect(screen.getByText('Parameters')).toBeInTheDocument()
  })

  it('should offer stacked selectors with registry names and models, without a defaults column', () => {
    render(
      <TestWrapper>
        <TargetTable {...defaultProps} />
      </TestWrapper>
    )

    const summary = screen.getByRole('region', { name: 'Target defaults' })
    const objective = within(summary).getByRole('combobox', { name: 'Default objective target' })
    const adversarial = within(summary).getByRole('combobox', { name: 'Default adversarial target' })
    expect(within(summary).getAllByRole('combobox')).toEqual([objective, adversarial])
    expect(screen.getByRole('separator')).toBeInTheDocument()
    expect(within(objective).getAllByRole('option')).toHaveLength(4)
    expect(within(objective).getByRole('option', { name: 'openai_chat_gpt4 (gpt-4)' })).toHaveValue('openai_chat_gpt4')
    expect(within(adversarial).getAllByRole('option')).toHaveLength(2)
    expect(within(adversarial).queryByRole('option', { name: /azure_image_dalle/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('columnheader', { name: 'Defaults' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Set default' })).not.toBeInTheDocument()
  })

  it('should show the objective default badge and a clear action', () => {
    render(
      <TestWrapper>
        <TargetTable {...defaultProps} defaultObjectiveTarget={sampleTargets[0]} />
      </TestWrapper>
    )

    const objective = screen.getByRole('combobox', { name: 'Default objective target' })
    expect(objective).toHaveValue('openai_chat_gpt4')
    expect(within(objective).getByRole('option', { name: 'Not set' })).toHaveValue('')
    expect(within(screen.getByRole('table')).getByLabelText('Default objective target')).toHaveTextContent('Objective')
  })

  it('should allow the same eligible target to serve both roles and clear them separately', async () => {
    const user = userEvent.setup()
    render(
      <TestWrapper>
        <TargetTable
          {...defaultProps}
          defaultObjectiveTarget={sampleTargets[0]}
          defaultAdversarialTarget={sampleTargets[0]}
        />
      </TestWrapper>
    )

    const row = screen.getByRole('row', { name: /openai_chat_gpt4/ })
    expect(within(row).getByText('Objective')).toBeInTheDocument()
    expect(within(row).getByText('Adversarial')).toBeInTheDocument()
    const summary = screen.getByRole('region', { name: 'Target defaults' })
    await user.selectOptions(within(summary).getByRole('combobox', { name: 'Default objective target' }), '')
    expect(defaultProps.onSetDefaultObjectiveTarget).toHaveBeenCalledWith(null)
    expect(defaultProps.onSetDefaultAdversarialTarget).not.toHaveBeenCalled()
    await user.selectOptions(within(summary).getByRole('combobox', { name: 'Default adversarial target' }), '')
    expect(defaultProps.onSetDefaultAdversarialTarget).toHaveBeenCalledWith(null)
  })

  it('should not show default badges when no defaults are selected', () => {
    render(
      <TestWrapper>
        <TargetTable {...defaultProps} />
      </TestWrapper>
    )

    expect(within(screen.getByRole('table')).queryByLabelText('Default objective target')).not.toBeInTheDocument()
    expect(within(screen.getByRole('table')).queryByLabelText('Default adversarial target')).not.toBeInTheDocument()
    const defaults = within(screen.getByRole('region', { name: 'Target defaults' }))
    expect(defaults.getByRole('option', { name: 'Not set' })).toHaveValue('')
    expect(defaults.getByRole('option', { name: 'Use server default' })).toHaveValue('')
  })

  it('should set each role independently', async () => {
    const user = userEvent.setup()

    render(
      <TestWrapper>
        <TargetTable {...defaultProps} />
      </TestWrapper>
    )

    await user.selectOptions(screen.getByRole('combobox', { name: 'Default objective target' }), 'azure_image_dalle')
    expect(defaultProps.onSetDefaultObjectiveTarget).toHaveBeenCalledWith(sampleTargets[1])
    expect(defaultProps.onSetDefaultAdversarialTarget).not.toHaveBeenCalled()
    await user.selectOptions(screen.getByRole('combobox', { name: 'Default adversarial target' }), 'openai_chat_gpt4')
    expect(defaultProps.onSetDefaultAdversarialTarget).toHaveBeenCalledWith(sampleTargets[0])
  })

  it('should handle empty targets list gracefully', () => {
    render(
      <TestWrapper>
        <TargetTable {...defaultProps} targets={[]} />
      </TestWrapper>
    )

    expect(screen.getByRole('table')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Set default' })).not.toBeInTheDocument()
  })

  it('should keep the default summary visible when its target is filtered out', async () => {
    const user = userEvent.setup()
    render(
      <TestWrapper>
        <TargetTable {...defaultProps} defaultObjectiveTarget={sampleTargets[0]} />
      </TestWrapper>,
    )
    await user.selectOptions(screen.getByRole('combobox', { name: 'Filter by type:' }), 'AzureImageTarget')
    expect(screen.queryByRole('row', { name: /openai_chat_gpt4/ })).not.toBeInTheDocument()
    const summary = screen.getByRole('region', { name: 'Target defaults' })
    const objective = within(summary).getByRole('combobox', { name: 'Default objective target' })
    expect(objective).toHaveValue('openai_chat_gpt4')
    expect(within(objective).getByRole('option', { name: 'openai_chat_gpt4 (gpt-4)' })).toBeInTheDocument()
    await user.selectOptions(objective, '')
    expect(defaultProps.onSetDefaultObjectiveTarget).toHaveBeenCalledWith(null)
  })

  it('should keep an existing default selected when its option is selected again', async () => {
    const user = userEvent.setup()
    render(
      <TestWrapper><TargetTable {...defaultProps} defaultObjectiveTarget={sampleTargets[0]} /></TestWrapper>,
    )
    await user.selectOptions(screen.getByRole('combobox', { name: 'Default objective target' }), 'openai_chat_gpt4')
    expect(defaultProps.onSetDefaultObjectiveTarget).toHaveBeenCalledWith(sampleTargets[0])
  })

  it('should show dash when model_name or endpoint is null', () => {
    render(
      <TestWrapper>
        <TargetTable {...defaultProps} targets={[sampleTargets[2]]} />
      </TestWrapper>
    )

    // Dashes for model, endpoint, inputs, outputs, 6 capability columns (all unknown), and params
    const dashes = screen.getAllByText('—')
    expect(dashes).toHaveLength(11)
  })

  it('should show dash for capability columns when capabilities is absent', () => {
    render(
      <TestWrapper>
        <TargetTable {...defaultProps} targets={[sampleTargets[2]]} />
      </TestWrapper>
    )

    // TextTarget has no capabilities — all 6 should be dashes
    const dashes = screen.getAllByText('—')
    // model (—) + endpoint (—) + inputs (—) + outputs (—) + 6 capabilities (—) + params (—) = 11
    expect(dashes).toHaveLength(11)
  })

  it('should render modality icons with tooltips for inputs and outputs', () => {
    render(
      <TestWrapper>
        <TargetTable {...defaultProps} targets={[sampleTargets[0]]} />
      </TestWrapper>
    )

    // Modality tooltips are accessible labels; multiple identical labels can appear
    // (e.g. one "Text" for input and one for output).
    expect(screen.getAllByLabelText('Text').length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByLabelText('Image').length).toBeGreaterThanOrEqual(1)
  })

  it('should render modality icons in canonical order: text, image, audio, video, reasoning, function_call, tool_call', () => {
    const target: TargetInstance = makeTarget({
      target_registry_name: 'multi_modal',
      target_type: 'CustomTarget',
      endpoint: null,
      model_name: null,
      capabilities: {
        supports_multi_turn: true,
        supports_multi_message_pieces: true,
        supports_json_schema: false,
        supports_json_output: false,
        supports_editable_history: false,
        supports_system_prompt: false,
        // Backend returns alphabetically sorted; UI must reorder.
        supported_input_modalities: [
          'audio_path',
          'function_call',
          'image_path',
          'reasoning',
          'text',
          'tool_call',
          'video_path',
        ],
        supported_output_modalities: ['text'],
      },
    })
    render(
      <TestWrapper>
        <TargetTable {...defaultProps} targets={[target]} />
      </TestWrapper>
    )

    const expectedOrder = ['Text', 'Image', 'Audio', 'Video', 'Reasoning', 'Function call', 'Tool call']
    // The first set of modality icons belongs to the Inputs column.
    const labels = expectedOrder.map((label) => screen.getAllByLabelText(label)[0])
    const positions = labels.map((el) => el.compareDocumentPosition(labels[0]))
    // Each subsequent label should follow (or be) the first; verify monotonic ordering pairwise.
    for (let i = 0; i < labels.length - 1; i += 1) {
      const relation = labels[i].compareDocumentPosition(labels[i + 1])
      expect(relation & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    }
    expect(positions).toBeDefined()
  })

  it('should display target_specific_params when present', () => {
    const targetWithParams: TargetInstance[] = [
      makeTarget({
        target_registry_name: 'param_target',
        target_type: 'OpenAIResponseTarget',
        endpoint: 'https://api.openai.com',
        model_name: 'o3',
        target_specific_params: {
          reasoning_effort: 'high',
          max_output_tokens: 4096,
        },
      }),
    ]

    render(
      <TestWrapper>
        <TargetTable {...defaultProps} targets={targetWithParams} />
      </TestWrapper>
    )

    expect(screen.getByText(/reasoning_effort: high/)).toBeInTheDocument()
    expect(screen.getByText(/max_output_tokens: 4096/)).toBeInTheDocument()
  })

  it('should show tooltip for model with different underlying model', () => {
    const targetWithUnderlying: TargetInstance[] = [
      makeTarget({
        target_registry_name: 'azure_deployment',
        target_type: 'OpenAIChatTarget',
        endpoint: 'https://azure.openai.com',
        model_name: 'my-gpt4o-deployment',
        underlying_model_name: 'gpt-4o',
      }),
    ]

    render(
      <TestWrapper>
        <TargetTable {...defaultProps} targets={targetWithUnderlying} />
      </TestWrapper>
    )

    const modelText = screen.getByText('my-gpt4o-deployment')
    expect(modelText).toHaveStyle({ textDecoration: 'underline dotted' })
  })

  it('should filter targets by type when filter is selected', () => {
    render(
      <TestWrapper>
        <TargetTable {...defaultProps} />
      </TestWrapper>
    )

    // All targets visible initially
    expect(screen.getByText('gpt-4')).toBeInTheDocument()
    expect(screen.getByText('dall-e-3')).toBeInTheDocument()

    // Filter to OpenAIChatTarget
    const select = screen.getByRole('combobox', { name: 'Filter by type:' })
    fireEvent.change(select, { target: { value: 'OpenAIChatTarget' } })

    expect(screen.getByText('gpt-4')).toBeInTheDocument()
    expect(screen.queryByText('dall-e-3')).not.toBeInTheDocument()
  })

  it('should show all targets when filter is cleared', () => {
    render(
      <TestWrapper>
        <TargetTable {...defaultProps} />
      </TestWrapper>
    )

    const select = screen.getByRole('combobox', { name: 'Filter by type:' })

    // Filter then clear
    fireEvent.change(select, { target: { value: 'OpenAIChatTarget' } })
    expect(screen.queryByText('dall-e-3')).not.toBeInTheDocument()

    fireEvent.change(select, { target: { value: '' } })
    expect(screen.getByText('dall-e-3')).toBeInTheDocument()
  })

  it('should not show filter when only one target type exists', () => {
    render(
      <TestWrapper>
        <TargetTable {...defaultProps} targets={[sampleTargets[0]]} />
      </TestWrapper>
    )

    expect(screen.queryByText('Filter by type:')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Reset all filters' })).not.toBeInTheDocument()
  })

  it('should filter targets that include the selected input modality', async () => {
    const user = userEvent.setup()
    render(
      <TestWrapper>
        <TargetTable {...defaultProps} />
      </TestWrapper>
    )

    await user.selectOptions(screen.getByRole('combobox', { name: 'Filter by input:' }), 'image_path')

    expect(screen.getByRole('row', { name: /openai_chat_gpt4/ })).toBeInTheDocument()
    expect(screen.queryByRole('row', { name: /azure_image_dalle/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('row', { name: /text_target_basic/ })).not.toBeInTheDocument()
  })

  it('should filter targets that include the selected output modality', async () => {
    const user = userEvent.setup()
    render(
      <TestWrapper>
        <TargetTable {...defaultProps} />
      </TestWrapper>
    )

    await user.selectOptions(screen.getByRole('combobox', { name: 'Filter by output:' }), 'image_path')

    expect(screen.getByRole('row', { name: /azure_image_dalle/ })).toBeInTheDocument()
    expect(screen.queryByRole('row', { name: /openai_chat_gpt4/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('row', { name: /text_target_basic/ })).not.toBeInTheDocument()
  })

  it('should list modality options by display name in canonical order', () => {
    const targets = [
      makeModalityTarget('first', ['tool_call', 'text', 'zeta_path'], ['text']),
      makeModalityTarget('second', ['audio_path', 'image_path', 'alpha_path', 'text'], ['text']),
    ]
    render(
      <TestWrapper>
        <TargetTable {...defaultProps} targets={targets} />
      </TestWrapper>
    )

    const inputFilter = screen.getByRole('combobox', { name: 'Filter by input:' })
    const options = within(inputFilter).getAllByRole('option')
    expect(options.map((option) => option.textContent)).toEqual([
      'All inputs', 'Text', 'Image', 'Audio', 'Tool call', 'alpha_path', 'zeta_path',
    ])
    expect(options.map((option) => option.getAttribute('value'))).toEqual([
      '', 'text', 'image_path', 'audio_path', 'tool_call', 'alpha_path', 'zeta_path',
    ])
  })

  it('should combine the type, input, and output filters', async () => {
    const user = userEvent.setup()
    const targets = [
      ...sampleTargets,
      makeModalityTarget('openai_audio_chat', ['text', 'image_path'], ['audio_path']),
    ]
    render(
      <TestWrapper>
        <TargetTable {...defaultProps} targets={targets} />
      </TestWrapper>
    )

    await user.selectOptions(screen.getByRole('combobox', { name: 'Filter by type:' }), 'OpenAIChatTarget')
    await user.selectOptions(screen.getByRole('combobox', { name: 'Filter by input:' }), 'image_path')
    expect(screen.getByRole('row', { name: /openai_chat_gpt4/ })).toBeInTheDocument()
    expect(screen.getByRole('row', { name: /openai_audio_chat/ })).toBeInTheDocument()
    // Options come from every target, not only the rows left by the other filters.
    const outputFilter = screen.getByRole('combobox', { name: 'Filter by output:' })
    expect(within(outputFilter).getByRole('option', { name: 'Image' })).toBeInTheDocument()

    await user.selectOptions(outputFilter, 'audio_path')
    expect(screen.getByRole('row', { name: /openai_audio_chat/ })).toBeInTheDocument()
    expect(screen.queryByRole('row', { name: /openai_chat_gpt4/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('row', { name: /azure_image_dalle/ })).not.toBeInTheDocument()
  })

  it('should say so when no target matches the filters', async () => {
    const user = userEvent.setup()
    render(
      <TestWrapper>
        <TargetTable {...defaultProps} />
      </TestWrapper>
    )

    expect(screen.getByRole('status')).toBeEmptyDOMElement()

    await user.selectOptions(screen.getByRole('combobox', { name: 'Filter by input:' }), 'image_path')
    await user.selectOptions(screen.getByRole('combobox', { name: 'Filter by output:' }), 'image_path')

    expect(screen.getByRole('status')).toHaveTextContent('No targets match the selected filters.')
    expect(screen.getByRole('table', { name: 'Target instances' })).toBeInTheDocument()
    expect(screen.queryByRole('row', { name: /openai_chat_gpt4|azure_image_dalle|text_target_basic/ })).not.toBeInTheDocument()
  })

  it('should reset every filter at once', async () => {
    const user = userEvent.setup()
    render(
      <TestWrapper>
        <TargetTable {...defaultProps} />
      </TestWrapper>
    )
    const resetButton = screen.getByRole('button', { name: 'Reset all filters' })
    expect(resetButton).toBeDisabled()

    await user.selectOptions(screen.getByRole('combobox', { name: 'Filter by type:' }), 'OpenAIChatTarget')
    await user.selectOptions(screen.getByRole('combobox', { name: 'Filter by input:' }), 'image_path')
    await user.selectOptions(screen.getByRole('combobox', { name: 'Filter by output:' }), 'image_path')
    expect(screen.getByRole('status')).toHaveTextContent('No targets match the selected filters.')

    await user.click(resetButton)

    expect(screen.getByRole('combobox', { name: 'Filter by type:' })).toHaveValue('')
    expect(screen.getByRole('combobox', { name: 'Filter by input:' })).toHaveValue('')
    expect(screen.getByRole('combobox', { name: 'Filter by output:' })).toHaveValue('')
    expect(screen.getAllByRole('row', { name: /openai_chat_gpt4|azure_image_dalle|text_target_basic/ })).toHaveLength(3)
    expect(resetButton).toBeDisabled()
  })

  it('should only show a modality filter that can narrow the targets', () => {
    const targets = [
      makeModalityTarget('first', ['text', 'image_path'], ['text']),
      makeModalityTarget('second', ['text', 'image_path'], ['audio_path']),
    ]
    render(
      <TestWrapper>
        <TargetTable {...defaultProps} targets={targets} />
      </TestWrapper>
    )

    expect(screen.queryByRole('combobox', { name: 'Filter by input:' })).not.toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: 'Filter by output:' })).toBeInTheDocument()
  })

  it('should keep the inner targets of a matching round robin target', async () => {
    const user = userEvent.setup()
    const roundRobin = makeTarget({
      target_registry_name: 'rr_image',
      target_type: 'RoundRobinTarget',
      capabilities: makeCapabilities(['text', 'image_path'], ['text']),
      target_specific_params: { weights: [1, 1] },
      inner_targets: [
        { target_registry_name: 'inner_a', target_type: 'OpenAIChatTarget', endpoint: 'https://a.example.test' },
        { target_registry_name: 'inner_b', target_type: 'OpenAIChatTarget', endpoint: 'https://b.example.test' },
      ],
    })
    render(
      <TestWrapper>
        <TargetTable {...defaultProps} targets={[roundRobin, sampleTargets[1]]} />
      </TestWrapper>
    )

    await user.selectOptions(screen.getByRole('combobox', { name: 'Filter by input:' }), 'image_path')
    await user.click(screen.getByRole('button', { name: 'Expand inner targets' }))

    expect(screen.queryByRole('row', { name: /azure_image_dalle/ })).not.toBeInTheDocument()
    expect(screen.getByText('#1 inner_a')).toBeInTheDocument()
    expect(screen.getByText('#2 inner_b')).toBeInTheDocument()
  })

  it('should show expand button for RoundRobinTarget with inner targets', () => {
    const rrTarget: TargetInstance = makeTarget({
      target_registry_name: 'rr_gpt4o',
      target_type: 'RoundRobinTarget',
      model_name: 'gpt-4o',
      target_specific_params: { weights: [1, 1] },
      inner_targets: [
        {
          target_registry_name: 'inner_a',
          target_type: 'OpenAIChatTarget',
          endpoint: 'https://a.openai.azure.com',
          model_name: 'gpt-4o',
        },
        {
          target_registry_name: 'inner_b',
          target_type: 'OpenAIChatTarget',
          endpoint: 'https://b.openai.azure.com',
          model_name: 'gpt-4o',
        },
      ],
    })

    render(
      <TestWrapper>
        <TargetTable {...defaultProps} targets={[rrTarget]} />
      </TestWrapper>
    )

    // Expand button should be present
    const expandButton = screen.getByLabelText('Expand inner targets')
    expect(expandButton).toBeInTheDocument()

    // Inner targets are not visible before expanding
    expect(screen.queryByText('https://a.openai.azure.com')).not.toBeInTheDocument()

    // Click to expand
    fireEvent.click(expandButton)

    // Inner targets should now be visible
    expect(screen.getByText('#1 inner_a')).toBeInTheDocument()
    expect(screen.getByText('#2 inner_b')).toBeInTheDocument()
    expect(screen.getByText('https://a.openai.azure.com')).toBeInTheDocument()
    expect(screen.getByText('https://b.openai.azure.com')).toBeInTheDocument()
  })

  it('should not show expand button for regular targets', () => {
    render(
      <TestWrapper>
        <TargetTable {...defaultProps} targets={[sampleTargets[0]]} />
      </TestWrapper>
    )

    expect(screen.queryByLabelText('Expand inner targets')).not.toBeInTheDocument()
  })
})
