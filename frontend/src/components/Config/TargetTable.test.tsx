import { render, screen, fireEvent } from '@testing-library/react'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import TargetTable from './TargetTable'
import type { TargetInstance } from '../../types'

jest.mock('./TargetTable.styles', () => ({
  useTargetTableStyles: () => new Proxy({}, { get: () => '' }),
}))

const TestWrapper: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
)

const sampleTargets: TargetInstance[] = [
  {
    target_registry_name: 'openai_chat_gpt4',
    target_type: 'OpenAIChatTarget',
    endpoint: 'https://api.openai.com',
    model_name: 'gpt-4',
  },
  {
    target_registry_name: 'azure_image_dalle',
    target_type: 'AzureImageTarget',
    endpoint: 'https://azure.openai.com',
    model_name: 'dall-e-3',
  },
  {
    target_registry_name: 'text_target_basic',
    target_type: 'TextTarget',
    endpoint: null,
    model_name: null,
  },
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

  it('should display Type, Model, Endpoint and Parameters columns', () => {
    render(
      <TestWrapper>
        <TargetTable {...defaultProps} />
      </TestWrapper>
    )

    expect(screen.getByText('Type')).toBeInTheDocument()
    expect(screen.getByText('Model')).toBeInTheDocument()
    expect(screen.getByText('Endpoint')).toBeInTheDocument()
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

    const dashes = screen.getAllByText('—')
    expect(dashes.length).toBeGreaterThanOrEqual(2)
  })

  it('should display target_specific_params when present', () => {
    const targetWithParams: TargetInstance[] = [
      {
        target_registry_name: 'param_target',
        target_type: 'OpenAIResponseTarget',
        endpoint: 'https://api.openai.com',
        model_name: 'o3',
        target_specific_params: {
          reasoning_effort: 'high',
          max_output_tokens: 4096,
        },
      },
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
      {
        target_registry_name: 'azure_deployment',
        target_type: 'OpenAIChatTarget',
        endpoint: 'https://azure.openai.com',
        model_name: 'my-gpt4o-deployment',
        underlying_model_name: 'gpt-4o',
      },
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
    const select = screen.getByRole('combobox')
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
})
