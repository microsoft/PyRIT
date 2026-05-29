import { render, screen, fireEvent } from '@testing-library/react'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import AttackTable from './AttackTable'
import type { AttackSummary } from '../../types'

jest.mock('./AttackHistory.styles', () => ({
  useAttackHistoryStyles: () => new Proxy({}, { get: () => '' }),
}))

const TestWrapper: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
)

const sampleAttacks: AttackSummary[] = [
  {
    attack_result_id: 'ar-1',
    conversation_id: 'conv-1',
    attack_type: 'CrescendoAttack',
    target: { target_type: 'OpenAIChatTarget', endpoint: 'https://api.openai.com', model_name: 'gpt-4' },
    converters: ['Base64Converter', 'ROT13Converter', 'UnicodeConverter'],
    outcome: 'success',
    last_message_preview: 'Hello world',
    message_count: 5,
    related_conversation_ids: ['rel-1'],
    labels: { operator: 'alice', operation: 'op_one', custom: 'val' },
    created_at: '2026-01-15T10:30:00Z',
    updated_at: '2026-01-15T11:00:00Z',
  },
  {
    attack_result_id: 'ar-2',
    conversation_id: 'conv-2',
    attack_type: 'ManualAttack',
    target: null,
    converters: [],
    outcome: 'failure',
    last_message_preview: null,
    message_count: 1,
    related_conversation_ids: [],
    labels: {},
    created_at: '2026-01-14T08:00:00Z',
    updated_at: '2026-01-14T08:30:00Z',
  },
  {
    attack_result_id: 'ar-3',
    conversation_id: 'conv-3',
    attack_type: 'ManualAttack',
    target: { target_type: 'TextTarget', endpoint: null, model_name: null },
    converters: [],
    outcome: undefined,
    last_message_preview: null,
    message_count: 0,
    related_conversation_ids: [],
    labels: {},
    created_at: '2026-01-13T08:00:00Z',
    updated_at: '2026-01-13T08:30:00Z',
  },
]

const formatDate = (dateStr: string) => {
  const date = new Date(dateStr)
  return date.toLocaleDateString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

describe('AttackTable', () => {
  const defaultProps = {
    attacks: sampleAttacks,
    onOpenAttack: jest.fn(),
    formatDate,
  }

  beforeEach(() => {
    jest.clearAllMocks()
  })

  it('should render the table with correct test id', () => {
    render(
      <TestWrapper>
        <AttackTable {...defaultProps} />
      </TestWrapper>
    )

    expect(screen.getByTestId('attacks-table')).toBeInTheDocument()
  })

  it('should render table header columns', () => {
    render(
      <TestWrapper>
        <AttackTable {...defaultProps} />
      </TestWrapper>
    )

    expect(screen.getByText('Status')).toBeInTheDocument()
    expect(screen.getByText('Attack Type')).toBeInTheDocument()
    expect(screen.getByText('Target')).toBeInTheDocument()
    expect(screen.getByText('Operator')).toBeInTheDocument()
    expect(screen.getByText('Operation')).toBeInTheDocument()
    expect(screen.getByText('Msgs')).toBeInTheDocument()
    expect(screen.getByText('Convs')).toBeInTheDocument()
    expect(screen.getByText('Converters')).toBeInTheDocument()
    expect(screen.getByText('Labels')).toBeInTheDocument()
    expect(screen.getByText('Created')).toBeInTheDocument()
    expect(screen.getByText('Updated')).toBeInTheDocument()
    expect(screen.getByText('Last Message')).toBeInTheDocument()
  })

  it('should render a row for each attack', () => {
    render(
      <TestWrapper>
        <AttackTable {...defaultProps} />
      </TestWrapper>
    )

    expect(screen.getByTestId('attack-row-ar-1')).toBeInTheDocument()
    expect(screen.getByTestId('attack-row-ar-2')).toBeInTheDocument()
    expect(screen.getByTestId('attack-row-ar-3')).toBeInTheDocument()
  })

  it('should display attack type text', () => {
    render(
      <TestWrapper>
        <AttackTable {...defaultProps} />
      </TestWrapper>
    )

    expect(screen.getByText('CrescendoAttack')).toBeInTheDocument()
    // ManualAttack appears twice
    expect(screen.getAllByText('ManualAttack')).toHaveLength(2)
  })

  it('should call onOpenAttack when row is clicked', () => {
    const onOpenAttack = jest.fn()

    render(
      <TestWrapper>
        <AttackTable {...defaultProps} onOpenAttack={onOpenAttack} />
      </TestWrapper>
    )

    fireEvent.click(screen.getByTestId('attack-row-ar-1'))
    expect(onOpenAttack).toHaveBeenCalledWith('ar-1')
  })

  it('should call onOpenAttack when open button is clicked', () => {
    const onOpenAttack = jest.fn()

    render(
      <TestWrapper>
        <AttackTable {...defaultProps} onOpenAttack={onOpenAttack} />
      </TestWrapper>
    )

    fireEvent.click(screen.getByTestId('open-attack-ar-2'))
    expect(onOpenAttack).toHaveBeenCalledWith('ar-2')
  })

  it('should render outcome badges with correct text', () => {
    render(
      <TestWrapper>
        <AttackTable {...defaultProps} />
      </TestWrapper>
    )

    expect(screen.getByTestId('outcome-badge-ar-1')).toBeInTheDocument()
    expect(screen.getByText('success')).toBeInTheDocument()
    expect(screen.getByText('failure')).toBeInTheDocument()
    // undetermined for null outcome
    expect(screen.getAllByText('undetermined')).toHaveLength(1)
  })

  it('should show target model name as badge when available', () => {
    render(
      <TestWrapper>
        <AttackTable {...defaultProps} />
      </TestWrapper>
    )

    expect(screen.getByText('gpt-4')).toBeInTheDocument()
  })

  it('should show target type when model name is not available', () => {
    render(
      <TestWrapper>
        <AttackTable {...defaultProps} />
      </TestWrapper>
    )

    expect(screen.getByText('TextTarget')).toBeInTheDocument()
  })

  it('should show dash when target is null', () => {
    render(
      <TestWrapper>
        <AttackTable {...defaultProps} />
      </TestWrapper>
    )

    // ar-2 has no target
    const row = screen.getByTestId('attack-row-ar-2')
    expect(row).toBeInTheDocument()
  })

  it('should display message counts', () => {
    render(
      <TestWrapper>
        <AttackTable {...defaultProps} />
      </TestWrapper>
    )

    expect(screen.getByText('5')).toBeInTheDocument()
  })

  it('should show conversation count as related + 1', () => {
    render(
      <TestWrapper>
        <AttackTable {...defaultProps} />
      </TestWrapper>
    )

    // ar-1: 1 related + 1 main = 2
    expect(screen.getByText('2')).toBeInTheDocument()
  })

  it('should show converter badges', () => {
    render(
      <TestWrapper>
        <AttackTable {...defaultProps} />
      </TestWrapper>
    )

    expect(screen.getByText('Base64Converter')).toBeInTheDocument()
    expect(screen.getByText('ROT13Converter')).toBeInTheDocument()
    // Third converter overflows to +1
    expect(screen.getByText('+1')).toBeInTheDocument()
  })

  it('should show label badges excluding operator, operation, and source', () => {
    render(
      <TestWrapper>
        <AttackTable {...defaultProps} />
      </TestWrapper>
    )

    expect(screen.getByText('custom: val')).toBeInTheDocument()
  })

  it('should show last message preview when available', () => {
    render(
      <TestWrapper>
        <AttackTable {...defaultProps} />
      </TestWrapper>
    )

    expect(screen.getByText('Hello world')).toBeInTheDocument()
  })

  it('should show operator and operation from labels', () => {
    render(
      <TestWrapper>
        <AttackTable {...defaultProps} />
      </TestWrapper>
    )

    expect(screen.getByText('alice')).toBeInTheDocument()
    expect(screen.getByText('op_one')).toBeInTheDocument()
  })

  it('should render with empty attacks array', () => {
    render(
      <TestWrapper>
        <AttackTable {...defaultProps} attacks={[]} />
      </TestWrapper>
    )

    expect(screen.getByTestId('attacks-table')).toBeInTheDocument()
    expect(screen.queryByTestId('attack-row-ar-1')).not.toBeInTheDocument()
  })
})
