import { act, render, screen, waitFor, fireEvent, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { makeTarget } from '@/test-utils/targetFixtures'
import { deferred } from '@/test-utils/deferred'
import AttackHistory from './AttackHistory'
import { advanceAutoRefresh, installAutoRefreshTimers } from '@/test-utils/autoRefresh'
import { DEFAULT_HISTORY_FILTERS, filtersFromSearchParams } from './historyFilters'
import { attacksApi, labelsApi, operationsApi } from '../../services/api'

installAutoRefreshTimers()
jest.mock('../../services/api', () => ({
  attacksApi: {
    listAttacks: jest.fn(),
    getAttackOptions: jest.fn(),
    getConverterOptions: jest.fn(),
  },
  labelsApi: {
    getLabels: jest.fn(),
  },
  operationsApi: {
    list: jest.fn(),
  },
}))

const mockedAttacksApi = attacksApi as jest.Mocked<typeof attacksApi>
const mockedLabelsApi = labelsApi as jest.Mocked<typeof labelsApi>
const mockedOperationsApi = operationsApi as jest.Mocked<typeof operationsApi>

const TestWrapper: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
)

const sampleAttacks = [
  {
    attack_result_id: 'ar-conv-1',
    conversation_id: 'conv-1',
    attack_type: 'CrescendoAttack',
    attack_specific_params: null,
    objective: 'Extract the hidden system prompt',
    target: { target_type: 'OpenAIChatTarget', target_registry_name: 'primary-chat', identifier_hash: 'chat-hash', endpoint: 'https://api.openai.com', model_name: 'gpt-4' },
    converters: ['Base64Converter'],
    outcome: 'success' as const,
    last_message_preview: 'The model responded with...',
    message_count: 10,
    related_conversation_ids: ['rel-1', 'rel-2'],
    labels: { category: 'test' },
    created_at: '2026-01-15T10:30:00Z',
    updated_at: '2026-01-15T11:00:00Z',
  },
  {
    attack_result_id: 'ar-conv-2',
    conversation_id: 'conv-2',
    attack_type: 'ManualAttack',
    attack_specific_params: null,
    objective: 'Bypass the safety filter',
    target: { target_type: 'OpenAIImageTarget', target_registry_name: 'image-target', identifier_hash: 'image-hash', endpoint: 'https://api.openai.com', model_name: 'dall-e-3' },
    converters: [],
    outcome: 'failure' as const,
    last_message_preview: null,
    message_count: 2,
    related_conversation_ids: [],
    labels: {},
    created_at: '2026-01-14T08:00:00Z',
    updated_at: '2026-01-14T08:30:00Z',
  },
]

describe('AttackHistory', () => {
  const defaultProps = {
    onOpenAttack: jest.fn(),
    filters: { ...DEFAULT_HISTORY_FILTERS },
    onFiltersChange: jest.fn(),
    activeTarget: null,
    onNavigate: jest.fn(),
  }

  beforeEach(() => {
    jest.clearAllMocks()
    mockedLabelsApi.getLabels.mockImplementation(() => new Promise(() => {}))
    mockedOperationsApi.list.mockImplementation(() => new Promise(() => {}))
  })

  it('should render title and filters', async () => {
    mockedAttacksApi.listAttacks.mockResolvedValue({
      items: [],
      pagination: { limit: 25, has_more: false },
    })

    render(
      <TestWrapper>
        <AttackHistory {...defaultProps} />
      </TestWrapper>
    )

    expect(screen.getByRole('heading', { level: 1, name: 'Attack History' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Refresh' })).not.toBeInTheDocument()
    expect(screen.getByTestId('outcome-filter')).toBeInTheDocument()
    expect(screen.getByTestId('operator-filter')).toBeInTheDocument()
    expect(screen.getByTestId('operation-filter')).toBeInTheDocument()
    expect(screen.getByTestId('label-filter')).toBeInTheDocument()
    expect(screen.queryByTestId('attack-type-filter')).not.toBeInTheDocument()
    expect(screen.queryByTestId('converter-filter')).not.toBeInTheDocument()

    await waitFor(() => {
      expect(mockedAttacksApi.listAttacks).toHaveBeenCalledTimes(1)
    })
  })

  it('should guide users without an active target to Chat', async () => {
    const user = userEvent.setup()
    const onNavigate = jest.fn()
    mockedAttacksApi.listAttacks.mockResolvedValue({
      items: [],
      pagination: { limit: 25, has_more: false },
    })

    render(
      <TestWrapper>
        <AttackHistory {...defaultProps} onNavigate={onNavigate} />
      </TestWrapper>
    )

    await waitFor(() => {
      expect(screen.getByTestId('empty-state')).toBeInTheDocument()
    })
    expect(screen.getByText('No attacks found')).toBeInTheDocument()
    expect(screen.getByText('Select a target in Chat to start an attack.')).toBeInTheDocument()

    const openChatButton = screen.getByRole('button', { name: 'Open Chat' })
    expect(openChatButton).toBeEnabled()
    expect(screen.queryByRole('button', { name: 'Start attack' })).not.toBeInTheDocument()

    await user.click(openChatButton)
    expect(onNavigate).toHaveBeenCalledWith('chat')
  })

  it('should guide users with an active target to start an attack', async () => {
    const user = userEvent.setup()
    const onNavigate = jest.fn()
    mockedAttacksApi.listAttacks.mockResolvedValue({
      items: [],
      pagination: { limit: 25, has_more: false },
    })

    render(
      <TestWrapper>
        <AttackHistory
          {...defaultProps}
          activeTarget={makeTarget({ target_registry_name: 'active_target' })}
          onNavigate={onNavigate}
        />
      </TestWrapper>
    )

    const startAttackButton = await screen.findByRole('button', { name: 'Start attack' })
    expect(startAttackButton).toBeEnabled()
    expect(screen.getByText('Start an attack to see it here.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Open Chat' })).not.toBeInTheDocument()

    await user.click(startAttackButton)
    expect(onNavigate).toHaveBeenCalledWith('chat')
  })

  it('should not show an empty-state action while attacks are loading', () => {
    mockedAttacksApi.listAttacks.mockImplementation(() => new Promise(() => {}))

    render(
      <TestWrapper>
        <AttackHistory {...defaultProps} />
      </TestWrapper>
    )

    expect(screen.getByText('Loading attacks...')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Start attack' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Open Chat' })).not.toBeInTheDocument()
  })

  it('should render attack table rows', async () => {
    mockedAttacksApi.listAttacks.mockResolvedValue({
      items: sampleAttacks,
      pagination: { limit: 25, has_more: false },
    })

    render(
      <TestWrapper>
        <AttackHistory {...defaultProps} />
      </TestWrapper>
    )

    await waitFor(() => {
      expect(screen.getByTestId('attacks-table')).toBeInTheDocument()
    })
    expect(screen.getByTestId('attack-row-ar-conv-1')).toBeInTheDocument()
    expect(screen.getByTestId('attack-row-ar-conv-2')).toBeInTheDocument()
    expect(screen.getByText(sampleAttacks[0].objective)).toBeInTheDocument()
    expect(screen.getByText(sampleAttacks[1].objective)).toBeInTheDocument()
  })

  it('should show outcome badges', async () => {
    mockedAttacksApi.listAttacks.mockResolvedValue({
      items: sampleAttacks,
      pagination: { limit: 25, has_more: false },
    })

    render(
      <TestWrapper>
        <AttackHistory {...defaultProps} />
      </TestWrapper>
    )

    await waitFor(() => {
      expect(screen.getByTestId('outcome-badge-ar-conv-1')).toBeInTheDocument()
    })
    expect(screen.getByText('success')).toBeInTheDocument()
    expect(screen.getByText('failure')).toBeInTheDocument()
  })

  it('should show target info as badge', async () => {
    mockedAttacksApi.listAttacks.mockResolvedValue({
      items: sampleAttacks,
      pagination: { limit: 25, has_more: false },
    })

    render(
      <TestWrapper>
        <AttackHistory {...defaultProps} />
      </TestWrapper>
    )

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Identity for primary-chat' })).toBeInTheDocument()
    })
    expect(screen.getByRole('button', { name: 'Identity for image-target' })).toBeInTheDocument()
  })

  it('should resolve legacy target names from the supplied registry', async () => {
    const attack = { ...sampleAttacks[0], target: { ...sampleAttacks[0].target, target_registry_name: null } }
    mockedAttacksApi.listAttacks.mockResolvedValue({
      items: [attack],
      pagination: { limit: 25, has_more: false },
    })

    render(
      <TestWrapper>
        <AttackHistory {...defaultProps} targets={[makeTarget({ target_registry_name: 'resolved-chat', identifier_hash: 'chat-hash' })]} />
      </TestWrapper>
    )

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Identity for resolved-chat' })).toBeInTheDocument()
    })
  })

  it('should report registry failures without hiding saved attacks', async () => {
    mockedAttacksApi.listAttacks.mockResolvedValue({
      items: sampleAttacks,
      pagination: { limit: 25, has_more: false },
    })

    render(
      <TestWrapper>
        <AttackHistory {...defaultProps} targetsError="Registry unavailable" />
      </TestWrapper>
    )

    await screen.findByText(sampleAttacks[0].objective)
    expect(screen.getByText('Target registry: Registry unavailable')).toBeInTheDocument()
  })

  it('should call onOpenAttack when row is clicked', async () => {
    const onOpenAttack = jest.fn()
    mockedAttacksApi.listAttacks.mockResolvedValue({
      items: sampleAttacks,
      pagination: { limit: 25, has_more: false },
    })

    render(
      <TestWrapper>
        <AttackHistory {...defaultProps} onOpenAttack={onOpenAttack} />
      </TestWrapper>
    )

    await waitFor(() => {
      expect(screen.getByTestId('attack-row-ar-conv-1')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByTestId('attack-row-ar-conv-1'))
    expect(onOpenAttack).toHaveBeenCalledWith('ar-conv-1')
  })

  it('should not render separate open attack buttons', async () => {
    const onOpenAttack = jest.fn()
    mockedAttacksApi.listAttacks.mockResolvedValue({
      items: sampleAttacks,
      pagination: { limit: 25, has_more: false },
    })

    render(
      <TestWrapper>
        <AttackHistory {...defaultProps} onOpenAttack={onOpenAttack} />
      </TestWrapper>
    )

    await screen.findByTestId('attack-row-ar-conv-2')
    expect(screen.queryByTestId('open-attack-ar-conv-2')).not.toBeInTheDocument()
    expect(onOpenAttack).not.toHaveBeenCalled()
  })

  it('should show objectives instead of last message previews', async () => {
    mockedAttacksApi.listAttacks.mockResolvedValue({
      items: sampleAttacks,
      pagination: { limit: 25, has_more: false },
    })

    render(
      <TestWrapper>
        <AttackHistory {...defaultProps} />
      </TestWrapper>
    )

    await waitFor(() => {
      expect(screen.getByText(sampleAttacks[0].objective)).toBeInTheDocument()
    })
    expect(screen.queryByText('The model responded with...')).not.toBeInTheDocument()
  })

  it('should expand target details without opening the attack', async () => {
    const user = userEvent.setup()
    mockedAttacksApi.listAttacks.mockResolvedValue({
      items: sampleAttacks,
      pagination: { limit: 25, has_more: false },
    })

    render(
      <TestWrapper>
        <AttackHistory {...defaultProps} />
      </TestWrapper>
    )

    const toggle = await screen.findByRole('button', { name: 'Identity for primary-chat' })
    await user.click(toggle)
    expect(screen.getByRole('region', { name: 'Identity for primary-chat' })).toHaveTextContent('OpenAIChatTarget')
    expect(defaultProps.onOpenAttack).not.toHaveBeenCalled()
    expect(within(screen.getByTestId('attack-row-ar-conv-1')).queryByText('Base64Converter')).not.toBeInTheDocument()
  })

  it('should show label badges', async () => {
    mockedAttacksApi.listAttacks.mockResolvedValue({
      items: sampleAttacks,
      pagination: { limit: 25, has_more: false },
    })

    render(
      <TestWrapper>
        <AttackHistory {...defaultProps} />
      </TestWrapper>
    )

    await waitFor(() => {
      expect(screen.getByText('category: test')).toBeInTheDocument()
    })
  })

  it('should update the visible history after one minute', async () => {
    mockedAttacksApi.listAttacks.mockResolvedValue({
      items: [],
      pagination: { limit: 25, has_more: false },
    })

    render(
      <TestWrapper>
        <AttackHistory {...defaultProps} />
      </TestWrapper>
    )

    await waitFor(() => {
      expect(screen.getByTestId('empty-state')).toBeInTheDocument()
    })

    expect(mockedAttacksApi.listAttacks).toHaveBeenCalledTimes(1)

    await advanceAutoRefresh()
    await waitFor(() => {
      expect(mockedAttacksApi.listAttacks).toHaveBeenCalledTimes(2)
    })
  })

  it('should show pagination when there are results', async () => {
    mockedAttacksApi.listAttacks.mockResolvedValue({
      items: sampleAttacks,
      pagination: { limit: 25, has_more: true, next_cursor: 'cursor-2' },
    })

    render(
      <TestWrapper>
        <AttackHistory {...defaultProps} />
      </TestWrapper>
    )

    await waitFor(() => {
      expect(screen.getByTestId('next-page-btn')).toBeInTheDocument()
    })
    expect(screen.getByTestId('prev-page-btn')).toBeInTheDocument()
    expect(screen.getByTestId('next-page-btn')).toBeEnabled()
    expect(screen.getByTestId('prev-page-btn')).toBeDisabled()
  })

  it('should disable next page button when no more results', async () => {
    mockedAttacksApi.listAttacks.mockResolvedValue({
      items: sampleAttacks,
      pagination: { limit: 25, has_more: false },
    })

    render(
      <TestWrapper>
        <AttackHistory {...defaultProps} />
      </TestWrapper>
    )

    await waitFor(() => {
      expect(screen.getByTestId('next-page-btn')).toBeDisabled()
    })
  })

  it('should load filter options on mount', async () => {
    mockedAttacksApi.listAttacks.mockResolvedValue({
      items: [],
      pagination: { limit: 25, has_more: false },
    })

    render(
      <TestWrapper>
        <AttackHistory {...defaultProps} />
      </TestWrapper>
    )

    await waitFor(() => {
      expect(mockedLabelsApi.getLabels).toHaveBeenCalledTimes(1)
    })
    expect(mockedOperationsApi.list).toHaveBeenCalledTimes(1)
    expect(mockedAttacksApi.getAttackOptions).not.toHaveBeenCalled()
    expect(mockedAttacksApi.getConverterOptions).not.toHaveBeenCalled()
  })

  it('should render table header columns', async () => {
    mockedAttacksApi.listAttacks.mockResolvedValue({
      items: sampleAttacks,
      pagination: { limit: 25, has_more: false },
    })

    render(
      <TestWrapper>
        <AttackHistory {...defaultProps} />
      </TestWrapper>
    )

    await waitFor(() => {
      expect(screen.getByTestId('attacks-table')).toBeInTheDocument()
    })
    const table = screen.getByTestId('attacks-table')
    expect(within(table).getAllByRole('columnheader').map((header: HTMLElement) => header.textContent)).toEqual([
      'Status', 'Objective', 'Target', 'Operator', 'Operation', 'Labels', 'Last Update',
    ])
  })

  it('should show error state on API failure', async () => {
    const axiosError = {
      isAxiosError: true,
      response: { status: 500, data: { detail: 'Internal server error' } },
    }
    mockedAttacksApi.listAttacks.mockRejectedValue(axiosError)

    render(
      <TestWrapper>
        <AttackHistory {...defaultProps} />
      </TestWrapper>
    )

    await waitFor(() => {
      expect(screen.getByText('Internal server error')).toBeInTheDocument()
    })
    expect(screen.getByText('Internal server error')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Retry' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Start attack' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Open Chat' })).not.toBeInTheDocument()
  })

  it('should not keep old rows under changed filters after an automatic check', async () => {
    mockedAttacksApi.listAttacks.mockResolvedValue({
      items: sampleAttacks, pagination: { limit: 25, has_more: false },
    })
    const rendered = render(<TestWrapper><AttackHistory {...defaultProps} /></TestWrapper>)
    await screen.findByTestId('attacks-table')
    await advanceAutoRefresh()
    mockedAttacksApi.listAttacks.mockRejectedValueOnce(new Error('Different cohort unavailable'))
    rendered.rerender(<TestWrapper><AttackHistory {...defaultProps}
      filters={{ ...defaultProps.filters, outcome: 'error' }} /></TestWrapper>)
    expect(await screen.findByText('Different cohort unavailable')).toBeInTheDocument()
    expect(screen.queryByTestId('attacks-table')).not.toBeInTheDocument()
    expect(screen.queryByTestId('attack-row-ar-conv-1')).not.toBeInTheDocument()
  })

  it('should recover on the next automatic check', async () => {
    const axiosError = {
      isAxiosError: true,
      response: { status: 500, data: { detail: 'Internal server error' } },
    }
    mockedAttacksApi.listAttacks.mockRejectedValueOnce(axiosError)

    render(
      <TestWrapper>
        <AttackHistory {...defaultProps} />
      </TestWrapper>
    )

    await waitFor(() => {
      expect(screen.getByText('Internal server error')).toBeInTheDocument()
    })

    mockedAttacksApi.listAttacks.mockResolvedValue({
      items: sampleAttacks,
      pagination: { limit: 25, has_more: false },
    })

    await advanceAutoRefresh()

    await waitFor(() => {
      expect(screen.getByTestId('attacks-table')).toBeInTheDocument()
    })
  })

  it('should keep an options error visible until all option reads succeed', async () => {
    mockedAttacksApi.listAttacks.mockResolvedValue({
      items: sampleAttacks, pagination: { limit: 25, has_more: false },
    })
    mockedLabelsApi.getLabels
      .mockRejectedValueOnce(new Error('Options unavailable'))
      .mockResolvedValue({ labels: {} })
    mockedOperationsApi.list.mockResolvedValue({ items: [] })
    render(<TestWrapper><AttackHistory {...defaultProps} /></TestWrapper>)
    expect(await screen.findByText('Options unavailable')).toBeInTheDocument()
    const recovery = deferred<Awaited<ReturnType<typeof labelsApi.getLabels>>>()
    mockedLabelsApi.getLabels.mockReturnValueOnce(recovery.promise)
    await advanceAutoRefresh()
    expect(screen.getByText('Options unavailable')).toBeInTheDocument()
    await act(async () => { recovery.resolve({ labels: {} }) })
    expect(screen.queryByText('Options unavailable')).not.toBeInTheDocument()
  })

  it('should show network error message for network failures', async () => {
    const networkError = {
      isAxiosError: true,
      message: 'Network Error',
    }
    mockedAttacksApi.listAttacks.mockRejectedValue(networkError)

    render(
      <TestWrapper>
        <AttackHistory {...defaultProps} />
      </TestWrapper>
    )

    await waitFor(() => {
      expect(screen.getByText(/network error/i)).toBeInTheDocument()
    })
    expect(screen.getByText(/network error/i)).toBeInTheDocument()
  })

  it('should distinguish empty results from error state', async () => {
    mockedAttacksApi.listAttacks.mockResolvedValue({
      items: [],
      pagination: { limit: 25, has_more: false },
    })

    render(
      <TestWrapper>
        <AttackHistory {...defaultProps} />
      </TestWrapper>
    )

    await waitFor(() => {
      expect(screen.getByTestId('empty-state')).toBeInTheDocument()
    })
    expect(screen.queryByTestId('error-state')).toBeNull()
  })

  it('should navigate to next page when Next is clicked', async () => {
    mockedAttacksApi.listAttacks
      .mockResolvedValueOnce({
        items: sampleAttacks,
        pagination: { limit: 25, has_more: true, next_cursor: 'cursor-page2' },
      })
      .mockResolvedValueOnce({
        items: [sampleAttacks[1]],
        pagination: { limit: 25, has_more: false },
      })

    render(
      <TestWrapper>
        <AttackHistory {...defaultProps} />
      </TestWrapper>
    )

    await waitFor(() => {
      expect(screen.getByTestId('next-page-btn')).toBeEnabled()
    })

    fireEvent.click(screen.getByTestId('next-page-btn'))

    await waitFor(() => {
      expect(mockedAttacksApi.listAttacks).toHaveBeenCalledTimes(2)
    })
    // Second call should include cursor
    expect(mockedAttacksApi.listAttacks).toHaveBeenLastCalledWith(
      expect.objectContaining({ cursor: 'cursor-page2' })
    )
  })

  it('should return to first page when First is clicked', async () => {
    mockedAttacksApi.listAttacks
      .mockResolvedValueOnce({
        items: sampleAttacks,
        pagination: { limit: 25, has_more: true, next_cursor: 'cursor-page2' },
      })
      .mockResolvedValueOnce({
        items: [sampleAttacks[1]],
        pagination: { limit: 25, has_more: false },
      })
      .mockResolvedValueOnce({
        items: sampleAttacks,
        pagination: { limit: 25, has_more: true, next_cursor: 'cursor-page2' },
      })

    render(
      <TestWrapper>
        <AttackHistory {...defaultProps} />
      </TestWrapper>
    )

    // Go to page 2
    await waitFor(() => expect(screen.getByTestId('next-page-btn')).toBeEnabled())
    fireEvent.click(screen.getByTestId('next-page-btn'))

    // Wait for page 2 to load, then click "First"
    await waitFor(() => expect(screen.getByTestId('prev-page-btn')).toBeEnabled())
    fireEvent.click(screen.getByTestId('prev-page-btn'))

    await waitFor(() => {
      expect(mockedAttacksApi.listAttacks).toHaveBeenCalledTimes(3)
    })
  })

  it('should discard the current cursor when filters change on a later page', async () => {
    mockedAttacksApi.listAttacks
      .mockResolvedValueOnce({
        items: sampleAttacks,
        pagination: { limit: 25, has_more: true, next_cursor: 'cursor-page2' },
      })
      .mockResolvedValueOnce({
        items: [sampleAttacks[1]],
        pagination: { limit: 25, has_more: false },
      })
      .mockResolvedValueOnce({
        items: [sampleAttacks[0]],
        pagination: { limit: 25, has_more: false },
      })
      .mockResolvedValueOnce({
        items: sampleAttacks,
        pagination: { limit: 25, has_more: false },
      })

    const history = render(
      <TestWrapper>
        <AttackHistory {...defaultProps} />
      </TestWrapper>
    )
    await waitFor(() => expect(screen.getByTestId('next-page-btn')).toBeEnabled())
    fireEvent.click(screen.getByTestId('next-page-btn'))
    await waitFor(() => expect(mockedAttacksApi.listAttacks).toHaveBeenCalledTimes(2))

    history.rerender(
      <TestWrapper>
        <AttackHistory
          {...defaultProps}
          filters={{ ...DEFAULT_HISTORY_FILTERS, outcome: 'success' }}
        />
      </TestWrapper>
    )

    await waitFor(() => expect(mockedAttacksApi.listAttacks).toHaveBeenCalledTimes(3))
    const filteredRequest = mockedAttacksApi.listAttacks.mock.calls[2][0]
    expect(filteredRequest).toEqual(expect.objectContaining({ outcome: 'success' }))
    expect(filteredRequest).not.toHaveProperty('cursor')
    expect(await screen.findByText('Page 1')).toBeInTheDocument()

    history.rerender(
      <TestWrapper>
        <AttackHistory {...defaultProps} />
      </TestWrapper>
    )

    await waitFor(() => expect(mockedAttacksApi.listAttacks).toHaveBeenCalledTimes(4))
    expect(mockedAttacksApi.listAttacks.mock.calls[3][0]).not.toHaveProperty('cursor')
  })

  it('should load and display filter options from API', async () => {
    mockedAttacksApi.listAttacks.mockResolvedValue({
      items: [],
      pagination: { limit: 25, has_more: false },
    })
    mockedLabelsApi.getLabels.mockResolvedValue({
      source: 'attacks',
      operators: ['alice', 'bob'],
      operations: ['op_one'],
      labels: {
        custom_tag: ['val1', 'val2'],
      },
    })

    render(
      <TestWrapper>
        <AttackHistory {...defaultProps} />
      </TestWrapper>
    )

    await waitFor(() => {
      expect(mockedLabelsApi.getLabels).toHaveBeenCalled()
    })
    expect(mockedOperationsApi.list).toHaveBeenCalled()
    expect(mockedAttacksApi.getAttackOptions).not.toHaveBeenCalled()
    expect(mockedAttacksApi.getConverterOptions).not.toHaveBeenCalled()
  })

  it('should narrow arbitrary label options by operator and labels and filter attacks by operation ID', async () => {
    mockedAttacksApi.listAttacks.mockResolvedValue({
      items: [],
      pagination: { limit: 25, has_more: false },
    })
    mockedLabelsApi.getLabels.mockResolvedValue({
      source: 'attacks',
      operators: ['alice', 'bob'],
      operations: ['nightly'],
      labels: { env: ['prod'] },
    })
    const activeFilters = {
      ...DEFAULT_HISTORY_FILTERS,
      operator: ['alice'],
      operationId: ['id-nightly'],
      otherLabels: ['team:red'],
    }

    render(
      <TestWrapper>
        <AttackHistory {...defaultProps} filters={activeFilters} />
      </TestWrapper>
    )

    await waitFor(() => {
      expect(mockedLabelsApi.getLabels).toHaveBeenCalledWith('attacks', {
        operator: ['alice'],
        label: ['team:red'],
      })
    })
    expect(mockedAttacksApi.listAttacks).toHaveBeenCalledWith(
      expect.objectContaining({ operation_id: ['id-nightly'], operator: ['alice'] }),
    )
    expect(mockedAttacksApi.listAttacks.mock.calls[0][0]).not.toHaveProperty('operation')
  })

  it('should show empty text with filter hint when filters active and no results', async () => {
    mockedAttacksApi.listAttacks.mockResolvedValue({
      items: [],
      pagination: { limit: 25, has_more: false },
    })
    const activeFilters = { ...DEFAULT_HISTORY_FILTERS, outcome: 'success' }

    render(
      <TestWrapper>
        <AttackHistory {...defaultProps} filters={activeFilters} />
      </TestWrapper>
    )

    await waitFor(() => {
      expect(screen.getByTestId('empty-state')).toBeInTheDocument()
    })
    expect(screen.getByText('Try adjusting your filters.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Start attack' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Open Chat' })).not.toBeInTheDocument()
  })

  it('should show reset filters button when a filter is active', async () => {
    mockedAttacksApi.listAttacks.mockResolvedValue({
      items: sampleAttacks,
      pagination: { limit: 25, has_more: false },
    })

    const onFiltersChange = jest.fn()
    const activeFilters = { ...DEFAULT_HISTORY_FILTERS, outcome: 'true' }

    render(
      <TestWrapper>
        <AttackHistory {...defaultProps} filters={activeFilters} onFiltersChange={onFiltersChange} />
      </TestWrapper>
    )

    await waitFor(() => {
      expect(screen.getByTestId('reset-filters-btn')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByTestId('reset-filters-btn'))
    expect(onFiltersChange).toHaveBeenCalledWith(DEFAULT_HISTORY_FILTERS)
  })

  it('should disable reset filters when no filters are active', async () => {
    mockedAttacksApi.listAttacks.mockResolvedValue({
      items: sampleAttacks,
      pagination: { limit: 25, has_more: false },
    })

    render(
      <TestWrapper>
        <AttackHistory {...defaultProps} />
      </TestWrapper>
    )

    await waitFor(() => {
      expect(screen.getByText('Attack History')).toBeInTheDocument()
    })

    expect(screen.getByRole('button', { name: 'Reset all filters' })).toHaveAttribute('aria-disabled', 'true')
  })

  it('should call onFiltersChange with outcome when outcome filter is selected', async () => {
    mockedAttacksApi.listAttacks.mockResolvedValue({
      items: [],
      pagination: { limit: 25, has_more: false },
    })
    mockedLabelsApi.getLabels.mockResolvedValue({ source: 'attacks', labels: {} })

    const onFiltersChange = jest.fn()

    render(
      <TestWrapper>
        <AttackHistory {...defaultProps} onFiltersChange={onFiltersChange} />
      </TestWrapper>
    )

    await waitFor(() => {
      expect(screen.getByTestId('outcome-filter')).toBeInTheDocument()
    })

    const outcomeDropdown = screen.getByTestId('outcome-filter')
    fireEvent.click(outcomeDropdown)

    await waitFor(() => {
      expect(screen.getByText('Success')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('Success'))

    expect(onFiltersChange).toHaveBeenCalledWith(
      expect.objectContaining({ outcome: 'success' })
    )
  })

  it('should call onFiltersChange with operator when operator filter is selected', async () => {
    mockedAttacksApi.listAttacks.mockResolvedValue({
      items: [],
      pagination: { limit: 25, has_more: false },
    })
    mockedLabelsApi.getLabels.mockResolvedValue({
      source: 'attacks',
      labels: {
        operator: ['alice', 'bob'],
        operation: ['op_one'],
      },
    })

    const onFiltersChange = jest.fn()

    render(
      <TestWrapper>
        <AttackHistory {...defaultProps} onFiltersChange={onFiltersChange} />
      </TestWrapper>
    )

    await waitFor(() => {
      expect(mockedLabelsApi.getLabels).toHaveBeenCalled()
    })

    const operatorDropdown = screen.getByTestId('operator-filter')
    fireEvent.click(operatorDropdown)

    await waitFor(() => {
      expect(screen.getByText('alice')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('alice'))

    expect(onFiltersChange).toHaveBeenCalledWith(
      expect.objectContaining({ operator: ['alice'] })
    )
  })

  it('should list saved operations by name and filter by the selected ID', async () => {
    mockedAttacksApi.listAttacks.mockResolvedValue({
      items: [],
      pagination: { limit: 25, has_more: false },
    })
    mockedLabelsApi.getLabels.mockResolvedValue({
      source: 'attacks',
      labels: { operator: [], operation: ['legacy_name_only'] },
    })
    mockedOperationsApi.list.mockResolvedValue({
      items: [
        { id: 'id-alpha', name: 'op_alpha', created_at: '2026-01-01T00:00:00Z', finding_counts: {} },
        { id: 'id-beta', name: 'op_beta', created_at: '2026-01-01T00:00:00Z', finding_counts: {} },
      ],
    })

    const onFiltersChange = jest.fn()

    render(
      <TestWrapper>
        <AttackHistory {...defaultProps} onFiltersChange={onFiltersChange} />
      </TestWrapper>
    )

    await waitFor(() => {
      expect(mockedOperationsApi.list).toHaveBeenCalled()
    })

    const operationDropdown = screen.getByTestId('operation-filter')
    fireEvent.click(operationDropdown)

    await waitFor(() => {
      expect(screen.getByText('op_alpha')).toBeInTheDocument()
    })
    expect(screen.queryByText('legacy_name_only')).not.toBeInTheDocument()

    fireEvent.click(screen.getByText('op_alpha'))

    expect(onFiltersChange).toHaveBeenCalledWith(
      expect.objectContaining({ operationId: ['id-alpha'] })
    )
  })

  it('should update label search text when typing in label combobox', async () => {
    mockedAttacksApi.listAttacks.mockResolvedValue({
      items: [],
      pagination: { limit: 25, has_more: false },
    })
    mockedLabelsApi.getLabels.mockResolvedValue({
      source: 'attacks',
      labels: {
        team: ['red', 'blue'],
        env: ['prod'],
      },
    })

    const onFiltersChange = jest.fn()

    render(
      <TestWrapper>
        <AttackHistory {...defaultProps} onFiltersChange={onFiltersChange} />
      </TestWrapper>
    )

    await waitFor(() => {
      expect(mockedLabelsApi.getLabels).toHaveBeenCalled()
    })

    fireEvent.change(screen.getByTestId('label-filter'), { target: { value: 'red' } })

    expect(onFiltersChange).toHaveBeenCalledWith(
      expect.objectContaining({ labelSearchText: 'red' })
    )
  })

  it('should reset all filters when reset button is clicked with multiple active filters', async () => {
    mockedAttacksApi.listAttacks.mockResolvedValue({
      items: sampleAttacks,
      pagination: { limit: 25, has_more: false },
    })

    const onFiltersChange = jest.fn()
    const activeFilters = {
      ...DEFAULT_HISTORY_FILTERS,
      outcome: 'success',
      operator: ['alice'],
    }

    render(
      <TestWrapper>
        <AttackHistory {...defaultProps} filters={activeFilters} onFiltersChange={onFiltersChange} />
      </TestWrapper>
    )

    await waitFor(() => {
      expect(screen.getByTestId('reset-filters-btn')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByTestId('reset-filters-btn'))
    expect(onFiltersChange).toHaveBeenCalledWith(DEFAULT_HISTORY_FILTERS)
  })

  it('should call onFiltersChange when selecting a label option from combobox', async () => {
    mockedAttacksApi.listAttacks.mockResolvedValue({
      items: [],
      pagination: { limit: 25, has_more: false },
    })
    mockedLabelsApi.getLabels.mockResolvedValue({
      source: 'attacks',
      labels: {
        team: ['red', 'blue'],
        env: ['prod'],
      },
    })

    const onFiltersChange = jest.fn()

    render(
      <TestWrapper>
        <AttackHistory {...defaultProps} onFiltersChange={onFiltersChange} />
      </TestWrapper>
    )

    await waitFor(() => {
      expect(mockedLabelsApi.getLabels).toHaveBeenCalled()
    })

    // Click on the label filter combobox to open the dropdown
    const labelCombobox = screen.getByTestId('label-filter')
    fireEvent.click(labelCombobox)

    // Wait for options to appear, then select one
    await waitFor(() => {
      expect(screen.getByText('team:red')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('team:red'))

    expect(onFiltersChange).toHaveBeenCalledWith(
      expect.objectContaining({ otherLabels: expect.any(Array), labelSearchText: '' })
    )
  })

  it('should ignore removed filter parameters from older history URLs', async () => {
    mockedAttacksApi.listAttacks.mockResolvedValue({
      items: [],
      pagination: { limit: 25, has_more: false },
    })
    const filters = filtersFromSearchParams(new URLSearchParams(
      'attackType=CrescendoAttack&converter=Base64Converter&converter=ROT13Converter&converterMatch=all&hasConverters=false',
    ))

    render(
      <TestWrapper>
        <AttackHistory {...defaultProps} filters={filters} />
      </TestWrapper>
    )

    await waitFor(() => {
      expect(mockedAttacksApi.listAttacks).toHaveBeenCalled()
    })

    expect(mockedAttacksApi.listAttacks).toHaveBeenCalledWith({
      limit: 25,
      include_scenario_attacks: true,
    })
  })

  it('should include scanner attacks by default and exclude them when disabled', async () => {
    mockedAttacksApi.listAttacks.mockResolvedValue({
      items: [],
      pagination: { limit: 25, has_more: false },
    })

    const { unmount } = render(
      <TestWrapper>
        <AttackHistory {...defaultProps} />
      </TestWrapper>
    )

    await waitFor(() => expect(mockedAttacksApi.listAttacks).toHaveBeenCalled())
    expect(mockedAttacksApi.listAttacks.mock.calls[0][0]).toEqual(
      expect.objectContaining({ include_scenario_attacks: true })
    )

    unmount()
    jest.clearAllMocks()
    mockedAttacksApi.listAttacks.mockResolvedValue({
      items: [],
      pagination: { limit: 25, has_more: false },
    })

    render(
      <TestWrapper>
        <AttackHistory
          {...defaultProps}
          filters={{ ...DEFAULT_HISTORY_FILTERS, includeScenarioAttacks: false }}
        />
      </TestWrapper>
    )

    await waitFor(() => expect(mockedAttacksApi.listAttacks).toHaveBeenCalled())
    expect(mockedAttacksApi.listAttacks.mock.calls[0][0]).toEqual(
      expect.objectContaining({ include_scenario_attacks: false })
    )
  })

  describe('pagination across filter visits', () => {
    const FILTERS_A = { ...DEFAULT_HISTORY_FILTERS }
    const FILTERS_B = { ...DEFAULT_HISTORY_FILTERS, outcome: 'success' as const }
    const PAGE_ONE = [sampleAttacks[0]]
    const PAGE_TWO = [sampleAttacks[1]]
    const FILTERED = [{ ...sampleAttacks[0], attack_result_id: 'ar-filtered' }]

    type ListAttacksParams = Parameters<typeof attacksApi.listAttacks>[0]

    function mockListAttacks({ pendingFiltered = false } = {}) {
      mockedAttacksApi.listAttacks.mockImplementation((params: ListAttacksParams = {}) => {
        if (params.outcome === 'success') {
          if (pendingFiltered) return new Promise(() => {})
          return Promise.resolve({ items: FILTERED, pagination: { limit: 25, has_more: false } })
        }
        if (params.cursor === 'a-page-2') {
          return Promise.resolve({ items: PAGE_TWO, pagination: { limit: 25, has_more: false } })
        }
        return Promise.resolve({
          items: PAGE_ONE,
          pagination: { limit: 25, has_more: true, next_cursor: 'a-page-2' },
        })
      })
    }

    function historyWithFilters(filters: typeof FILTERS_A) {
      return (
        <TestWrapper>
          <AttackHistory {...defaultProps} filters={filters} />
        </TestWrapper>
      )
    }

    async function openPageTwo() {
      await screen.findByTestId('attack-row-ar-conv-1')
      expect(screen.getByText('Page 1')).toBeInTheDocument()
      expect(screen.getByTestId('prev-page-btn')).toBeDisabled()
      fireEvent.click(screen.getByTestId('next-page-btn'))
      expect(await screen.findByTestId('attack-row-ar-conv-2')).toBeInTheDocument()
      expect(screen.getByText('Page 2')).toBeInTheDocument()
      expect(screen.getByTestId('prev-page-btn')).toBeEnabled()
    }

    async function expectFirstPageOfA() {
      expect(await screen.findByTestId('attack-row-ar-conv-1')).toBeInTheDocument()
      expect(screen.queryByTestId('attack-row-ar-conv-2')).not.toBeInTheDocument()
      expect(screen.getByText('Page 1')).toBeInTheDocument()
      expect(screen.getByTestId('prev-page-btn')).toBeDisabled()
      const lastCall = mockedAttacksApi.listAttacks.mock.calls.at(-1)?.[0]
      expect(lastCall).not.toHaveProperty('cursor')
      expect(lastCall).not.toHaveProperty('outcome')
    }

    it('retains the current page during a quiet refresh failure and resets on the next filter visit', async () => {
      mockListAttacks()
      const history = render(historyWithFilters(FILTERS_A))
      await openPageTwo()

      const refresh = deferred<Awaited<ReturnType<typeof attacksApi.listAttacks>>>()
      mockedAttacksApi.listAttacks.mockReturnValueOnce(refresh.promise)
      await advanceAutoRefresh()
      expect(mockedAttacksApi.listAttacks).toHaveBeenLastCalledWith(
        expect.objectContaining({ cursor: 'a-page-2' }),
      )
      expect(screen.getByTestId('attack-row-ar-conv-2')).toBeInTheDocument()
      expect(screen.getByText('Page 2')).toBeInTheDocument()
      expect(screen.queryByText('Loading attacks...')).not.toBeInTheDocument()

      await act(async () => { refresh.reject(new Error('Refresh unavailable')) })
      expect(screen.getByText('Refresh unavailable')).toBeInTheDocument()
      expect(screen.getByTestId('attack-row-ar-conv-2')).toBeInTheDocument()
      expect(screen.getByText('Page 2')).toBeInTheDocument()

      history.rerender(historyWithFilters(FILTERS_B))
      expect(screen.getByText('Loading attacks...')).toBeInTheDocument()
      expect(await screen.findByTestId('attack-row-ar-filtered')).toBeInTheDocument()
      expect(screen.getByText('Page 1')).toBeInTheDocument()
      history.rerender(historyWithFilters(FILTERS_A))
      await expectFirstPageOfA()
    })

    it('keeps the page label and First button in sync with rows during first/next navigation', async () => {
      mockListAttacks()
      render(historyWithFilters(FILTERS_A))

      await openPageTwo()
      expect(mockedAttacksApi.listAttacks).toHaveBeenLastCalledWith(
        expect.objectContaining({ cursor: 'a-page-2' })
      )

      fireEvent.click(screen.getByTestId('prev-page-btn'))
      await expectFirstPageOfA()
      expect(mockedAttacksApi.listAttacks).toHaveBeenCalledTimes(3)
    })

    it('starts A on the first page again after an A-to-B-to-A change once B has loaded', async () => {
      mockListAttacks()
      const history = render(historyWithFilters(FILTERS_A))
      await openPageTwo()

      history.rerender(historyWithFilters(FILTERS_B))
      expect(await screen.findByTestId('attack-row-ar-filtered')).toBeInTheDocument()
      expect(screen.getByText('Page 1')).toBeInTheDocument()
      expect(mockedAttacksApi.listAttacks.mock.calls.at(-1)?.[0]).not.toHaveProperty('cursor')

      history.rerender(historyWithFilters(FILTERS_A))
      await expectFirstPageOfA()
      expect(mockedAttacksApi.listAttacks).toHaveBeenCalledTimes(4)
    })

    it('starts A on the first page again after an A-to-B-to-A change while B is still loading', async () => {
      mockListAttacks({ pendingFiltered: true })
      const history = render(historyWithFilters(FILTERS_A))
      await openPageTwo()

      history.rerender(historyWithFilters(FILTERS_B))
      expect(screen.getByText('Loading attacks...')).toBeInTheDocument()
      await waitFor(() => expect(mockedAttacksApi.listAttacks).toHaveBeenCalledTimes(3))

      history.rerender(historyWithFilters(FILTERS_A))
      await expectFirstPageOfA()
      expect(mockedAttacksApi.listAttacks).toHaveBeenCalledTimes(4)
    })
  })
})
