import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'

import { makeTarget } from '@/test-utils/targetFixtures'
import type { AttackSummary, TargetInstance } from '@/types'

import AttackTable from './AttackTable'

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
    objective: 'Extract the hidden system prompt',
    target: {
      target_type: 'OpenAIChatTarget',
      target_registry_name: 'primary-chat',
      identifier_hash: 'chat-hash',
      endpoint: 'https://api.openai.com',
      model_name: 'gpt-4',
      identifier: {
        class_name: 'OpenAIChatTarget',
        hash: 'chat-hash',
        endpoint: 'https://api.openai.com',
        model_name: 'gpt-4',
        temperature: 0.25,
      },
    },
    converters: ['Base64Converter', 'ROT13Converter', 'UnicodeConverter'],
    outcome: 'success',
    last_message_preview: 'Hello world',
    message_count: 5,
    related_conversation_ids: ['rel-1'],
    operator: 'alice',
    operation: 'op_one',
    operation_name: 'op_one',
    labels: { custom: 'val' },
    created_at: '2026-01-15T10:30:00Z',
    updated_at: '2026-01-15T11:00:00Z',
  },
  {
    attack_result_id: 'ar-2',
    conversation_id: 'conv-2',
    attack_type: 'ManualAttack',
    objective: 'Bypass the safety filter',
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
    objective: 'Elicit disallowed content',
    target: {
      target_type: 'TextTarget',
      target_registry_name: 'text-output',
      identifier_hash: 'text-hash',
      endpoint: null,
      model_name: null,
    },
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

    expect(screen.getAllByRole('columnheader').map((header: HTMLElement) => header.textContent)).toEqual([
      'Status', 'Objective', 'Target', 'Operator', 'Operation', 'Labels', 'Last Update',
    ])
    expect(within(screen.getByTestId('attack-row-ar-1')).getAllByRole('cell')).toHaveLength(7)
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

  it('should always display objectives instead of attack types', () => {
    render(
      <TestWrapper>
        <AttackTable {...defaultProps} />
      </TestWrapper>
    )

    for (const attack of sampleAttacks) {
      expect(screen.getByText(attack.objective)).toBeInTheDocument()
    }
    expect(screen.queryByText('CrescendoAttack')).not.toBeInTheDocument()
    expect(screen.queryByText('ManualAttack')).not.toBeInTheDocument()
  })

  it('should call onOpenAttack once when an objective is clicked', async () => {
    const user = userEvent.setup()
    const onOpenAttack = jest.fn()

    render(
      <TestWrapper>
        <AttackTable {...defaultProps} onOpenAttack={onOpenAttack} />
      </TestWrapper>
    )

    await user.click(screen.getByText(sampleAttacks[0].objective))
    expect(onOpenAttack).toHaveBeenCalledTimes(1)
    expect(onOpenAttack).toHaveBeenCalledWith('ar-1')
  })

  it('should call onOpenAttack when Enter or Space is pressed on a row', async () => {
    const user = userEvent.setup()
    const onOpenAttack = jest.fn()

    render(
      <TestWrapper>
        <AttackTable {...defaultProps} onOpenAttack={onOpenAttack} />
      </TestWrapper>
    )

    const row = screen.getByTestId('attack-row-ar-1')
    expect(row).toHaveAttribute('tabindex', '0')

    row.focus()
    await user.keyboard('{Enter}')
    expect(onOpenAttack).toHaveBeenCalledWith('ar-1')

    onOpenAttack.mockClear()
    row.focus()
    await user.keyboard(' ')
    expect(onOpenAttack).toHaveBeenCalledWith('ar-1')

    onOpenAttack.mockClear()
    row.focus()
    await user.keyboard('a')
    expect(onOpenAttack).not.toHaveBeenCalled()
  })

  it('should not render a separate open attack button', () => {
    render(
      <TestWrapper>
        <AttackTable {...defaultProps} />
      </TestWrapper>
    )

    expect(screen.queryByRole('button', { name: /open attack/i })).not.toBeInTheDocument()
    expect(screen.queryByTestId('open-attack-ar-2')).not.toBeInTheDocument()
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

  it('should show the target registry name instead of its model or type', () => {
    render(
      <TestWrapper>
        <AttackTable {...defaultProps} />
      </TestWrapper>
    )

    expect(screen.getByRole('button', { name: 'Identity for primary-chat' })).toHaveTextContent('primary-chat')
    expect(screen.queryByText('gpt-4')).not.toBeInTheDocument()
    expect(screen.queryByText('OpenAIChatTarget')).not.toBeInTheDocument()
  })

  it('should show the registry name even when the model name is unavailable', () => {
    render(
      <TestWrapper>
        <AttackTable {...defaultProps} />
      </TestWrapper>
    )

    expect(screen.getByRole('button', { name: 'Identity for text-output' })).toBeInTheDocument()
    expect(screen.queryByText('TextTarget')).not.toBeInTheDocument()
  })

  it('should show dash when target is null', () => {
    render(
      <TestWrapper>
        <AttackTable {...defaultProps} />
      </TestWrapper>
    )

    const row = screen.getByTestId('attack-row-ar-2')
    expect(within(row).getAllByRole('cell')[2]).toHaveTextContent('—')
    expect(within(row).queryByRole('button')).not.toBeInTheDocument()
  })

  it('should expand and collapse saved target identity without opening the attack', async () => {
    const user = userEvent.setup()
    render(
      <TestWrapper>
        <AttackTable {...defaultProps} />
      </TestWrapper>
    )

    const toggle = screen.getByRole('button', { name: 'Identity for primary-chat' })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    await user.click(toggle)
    const details = screen.getByRole('region', { name: 'Identity for primary-chat' })
    expect(details.textContent).toBe(JSON.stringify(sampleAttacks[0].target?.identifier, null, 2))
    expect(toggle).toHaveAttribute('aria-controls', details.id)
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    await user.click(details)
    expect(defaultProps.onOpenAttack).not.toHaveBeenCalled()
    await user.click(toggle)
    expect(screen.queryByRole('region')).not.toBeInTheDocument()
    expect(toggle).toHaveFocus()
    expect(defaultProps.onOpenAttack).not.toHaveBeenCalled()
  })

  it('should expand target identity with Enter and collapse with Space without navigating', async () => {
    const user = userEvent.setup()
    render(
      <TestWrapper>
        <AttackTable {...defaultProps} />
      </TestWrapper>
    )

    const toggle = screen.getByRole('button', { name: 'Identity for primary-chat' })
    toggle.focus()
    await user.keyboard('{Enter}')
    expect(screen.getByRole('region', { name: 'Identity for primary-chat' })).toBeVisible()
    await user.keyboard(' ')
    expect(screen.queryByRole('region')).not.toBeInTheDocument()
    expect(toggle).toHaveFocus()
    expect(defaultProps.onOpenAttack).not.toHaveBeenCalled()
  })

  it('should preserve the saved target name and identity when the live registry changes', async () => {
    const user = userEvent.setup()
    const replacement = makeTarget({
      target_registry_name: 'primary-chat',
      identifier_hash: 'replacement-hash',
      model_name: 'replacement-model',
    })

    render(
      <TestWrapper>
        <AttackTable {...defaultProps} targets={[replacement]} />
      </TestWrapper>
    )

    await user.click(screen.getByRole('button', { name: 'Identity for primary-chat' }))
    const details = screen.getByRole('region', { name: 'Identity for primary-chat' })
    expect(details.textContent).toBe(JSON.stringify(sampleAttacks[0].target?.identifier, null, 2))
    expect(details).not.toHaveTextContent('replacement-model')
  })

  it('should resolve an unnamed legacy target only by its canonical hash', () => {
    const attack: AttackSummary = {
      ...sampleAttacks[0],
      target: { ...sampleAttacks[0].target, target_type: 'OpenAIChatTarget', identifier_hash: 'chat-hash', target_registry_name: null },
    }
    const targets = [
      makeTarget({ target_registry_name: 'near-match', identifier_hash: 'other-hash', model_name: 'gpt-4' }),
      makeTarget({ target_registry_name: 'exact-match', identifier_hash: 'chat-hash' }),
    ]

    render(
      <TestWrapper>
        <AttackTable {...defaultProps} attacks={[attack]} targets={targets} />
      </TestWrapper>
    )

    expect(screen.getByRole('button', { name: 'Identity for exact-match' })).toBeInTheDocument()
    expect(screen.queryByText('near-match')).not.toBeInTheDocument()
  })

  it.each<{ targets: TargetInstance[] }>([
    { targets: [] },
    { targets: [
      makeTarget({ target_registry_name: 'first', identifier_hash: 'chat-hash' }),
      makeTarget({ target_registry_name: 'second', identifier_hash: 'chat-hash' }),
    ] },
  ])('should not guess a target name from unavailable or ambiguous registry matches (%#)', ({ targets }: { targets: TargetInstance[] }) => {
    const attack: AttackSummary = {
      ...sampleAttacks[0],
      target: { target_type: 'OpenAIChatTarget', identifier_hash: 'chat-hash', model_name: 'gpt-4' },
    }

    render(<TestWrapper><AttackTable {...defaultProps} attacks={[attack]} targets={targets} /></TestWrapper>)

    expect(screen.getByRole('button', { name: 'Identity for Unnamed target' })).toBeInTheDocument()
    expect(screen.queryByText('gpt-4')).not.toBeInTheDocument()
    expect(screen.queryByText('OpenAIChatTarget')).not.toBeInTheDocument()
  })

  it('should show label badges excluding source', () => {
    const attack = { ...sampleAttacks[0], labels: { custom: 'val', source: 'gui', category: 'test', extra: 'value' } }
    render(
      <TestWrapper>
        <AttackTable {...defaultProps} attacks={[attack]} />
      </TestWrapper>
    )

    expect(screen.getByText('custom: val')).toBeInTheDocument()
    expect(screen.getByText('category: test')).toBeInTheDocument()
    expect(screen.getByText('+1')).toBeInTheDocument()
    expect(screen.queryByText('source: gui')).not.toBeInTheDocument()
  })

  it('should display the last update rather than creation time or message previews', () => {
    const formatDateMock = jest.fn((dateStr: string): string => `Updated: ${dateStr}`)
    render(
      <TestWrapper>
        <AttackTable {...defaultProps} formatDate={formatDateMock} />
      </TestWrapper>
    )

    for (const attack of sampleAttacks) {
      expect(formatDateMock).toHaveBeenCalledWith(attack.updated_at)
      expect(formatDateMock).not.toHaveBeenCalledWith(attack.created_at)
    }
    expect(screen.queryByText('Hello world')).not.toBeInTheDocument()
    expect(screen.queryByText('Base64Converter')).not.toBeInTheDocument()
  })

  it('should show the operator and current operation name', () => {
    render(
      <TestWrapper>
        <AttackTable {...defaultProps} />
      </TestWrapper>
    )

    expect(screen.getByText('alice')).toBeInTheDocument()
    expect(screen.getByText('op_one')).toBeInTheDocument()
  })

  it('should use the persisted source name for a temperature-bound target', () => {
    const attack: AttackSummary = {
      ...sampleAttacks[0],
      target: {
        target_type: 'OpenAIChatTarget',
        identifier_hash: 'effective-hash',
        binding: {
          version: 1,
          source_name: 'temperature-source',
          source_hash: 'chat-hash',
          temperature: 0.5,
          effective_hash: 'effective-hash',
        },
      },
    }

    render(<TestWrapper><AttackTable {...defaultProps} attacks={[attack]} /></TestWrapper>)

    expect(screen.getByRole('button', { name: 'Identity for temperature-source' })).toBeInTheDocument()
  })

  it('should keep target expansions independent and preserve them across refreshed rows', async () => {
    const user = userEvent.setup()
    const rendered = render(<TestWrapper><AttackTable {...defaultProps} /></TestWrapper>)
    await user.click(screen.getByRole('button', { name: 'Identity for primary-chat' }))
    await user.click(screen.getByRole('button', { name: 'Identity for text-output' }))
    const updatedAttacks = sampleAttacks.map((attack: AttackSummary) => ({ ...attack, updated_at: '2026-02-01T10:00:00Z' }))

    rendered.rerender(<TestWrapper><AttackTable {...defaultProps} attacks={updatedAttacks} /></TestWrapper>)

    expect(screen.getByRole('region', { name: 'Identity for primary-chat' })).toBeVisible()
    expect(screen.getByRole('region', { name: 'Identity for text-output' })).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Identity for primary-chat' }))
    expect(screen.queryByRole('region', { name: 'Identity for primary-chat' })).not.toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Identity for text-output' })).toBeVisible()
  })

  it('should keep an objective cell for attacks without an objective', () => {
    const attack = { ...sampleAttacks[1], objective: '' }

    render(<TestWrapper><AttackTable {...defaultProps} attacks={[attack]} /></TestWrapper>)

    const row = screen.getByRole('row', { name: 'Open attack: No objective' })
    expect(within(row).getAllByRole('cell')[1]).toHaveTextContent('—')
    expect(row).toHaveAttribute('tabindex', '0')
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
