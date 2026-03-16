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

  it('should render table with target rows', () => {
    render(
      <TestWrapper>
        <TargetTable {...defaultProps} />
      </TestWrapper>
    )

    expect(screen.getByRole('table')).toBeInTheDocument()
    expect(screen.getByText('OpenAIChatTarget')).toBeInTheDocument()
    expect(screen.getByText('AzureImageTarget')).toBeInTheDocument()
    expect(screen.getByText('TextTarget')).toBeInTheDocument()
  })

  it('should display target type, endpoint, and model name columns', () => {
    render(
      <TestWrapper>
        <TargetTable {...defaultProps} />
      </TestWrapper>
    )

    // Header cells
    expect(screen.getByText('Type')).toBeInTheDocument()
    expect(screen.getByText('Model')).toBeInTheDocument()
    expect(screen.getByText('Endpoint')).toBeInTheDocument()

    // Data cells
    expect(screen.getByText('gpt-4')).toBeInTheDocument()
    expect(screen.getByText('dall-e-3')).toBeInTheDocument()
    expect(screen.getByText('https://api.openai.com')).toBeInTheDocument()
    expect(screen.getByText('https://azure.openai.com')).toBeInTheDocument()
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

    expect(screen.getByText('Active')).toBeInTheDocument()
    // The other two should still have "Set Active"
    const setActiveButtons = screen.getAllByText('Set Active')
    expect(setActiveButtons).toHaveLength(2)
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

    // TextTarget has null model_name and endpoint; should render "—"
    const dashes = screen.getAllByText('—')
    expect(dashes.length).toBeGreaterThanOrEqual(2)
  })

  it('should display Parameters column header', () => {
    render(
      <TestWrapper>
        <TargetTable {...defaultProps} />
      </TestWrapper>
    )

    expect(screen.getByText('Parameters')).toBeInTheDocument()
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
})
