import { render, screen, fireEvent } from '@testing-library/react'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { makeTarget } from '@/test-utils/targetFixtures'
import TargetTable from './TargetTable'
import type { TargetInstance } from '../../types'

jest.mock('./TargetTable.styles', () => ({
  useTargetTableStyles: () => new Proxy({}, { get: () => '' }),
}))

const TestWrapper: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
)

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
    activeTarget: null as TargetInstance | null,
    onSetActiveTarget: jest.fn(),
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

  it('should display Type, Model, Endpoint, Inputs, Outputs, capability columns and Parameters columns', () => {
    render(
      <TestWrapper>
        <TargetTable {...defaultProps} />
      </TestWrapper>
    )

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

  it('should show "Set Active" button for non-active targets', () => {
    render(
      <TestWrapper>
        <TargetTable {...defaultProps} />
      </TestWrapper>
    )

    const setActiveButtons = screen.getAllByText('Set Active')
    expect(setActiveButtons).toHaveLength(3)
  })

  it('should show "Active" badge for the active target', () => {
    render(
      <TestWrapper>
        <TargetTable {...defaultProps} activeTarget={sampleTargets[0]} />
      </TestWrapper>
    )

    // Active badge appears in both the indicator and the table row
    expect(screen.getAllByText('Active').length).toBeGreaterThanOrEqual(2)
    const setActiveButtons = screen.getAllByText('Set Active')
    expect(setActiveButtons).toHaveLength(2)
  })

  it('should show active target indicator above the table', () => {
    render(
      <TestWrapper>
        <TargetTable {...defaultProps} activeTarget={sampleTargets[0]} />
      </TestWrapper>
    )

    // Active indicator shows type and model above the table
    const badges = screen.getAllByText('Active')
    expect(badges.length).toBeGreaterThanOrEqual(2) // one above table + one in row
  })

  it('should not show active target indicator when no target is active', () => {
    render(
      <TestWrapper>
        <TargetTable {...defaultProps} />
      </TestWrapper>
    )

    expect(screen.queryByText('Active')).not.toBeInTheDocument()
  })

  it('should call onSetActiveTarget when "Set Active" is clicked', () => {
    const onSetActiveTarget = jest.fn()

    render(
      <TestWrapper>
        <TargetTable {...defaultProps} onSetActiveTarget={onSetActiveTarget} />
      </TestWrapper>
    )

    const setActiveButtons = screen.getAllByText('Set Active')
    fireEvent.click(setActiveButtons[1])

    expect(onSetActiveTarget).toHaveBeenCalledTimes(1)
    expect(onSetActiveTarget).toHaveBeenCalledWith(sampleTargets[1])
  })

  it('should handle empty targets list gracefully', () => {
    render(
      <TestWrapper>
        <TargetTable {...defaultProps} targets={[]} />
      </TestWrapper>
    )

    expect(screen.getByRole('table')).toBeInTheDocument()
    expect(screen.queryByText('Set Active')).not.toBeInTheDocument()
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
        <TargetTable {...defaultProps} targets={targetWithParams} activeTarget={null} />
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
        <TargetTable {...defaultProps} targets={targetWithUnderlying} activeTarget={null} />
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

    const select = screen.getByRole('combobox')

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
