import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { MemoryRouter, useLocation } from 'react-router'
import type { ReactNode } from 'react'

import { useRuntime } from '@/hooks/useRuntime'
import { attacksApi, operationsApi, scenariosApi } from '@/services/api'
import type { AttackSummary, OperationListItem, ScenarioRunListItem, ScenarioRunState } from '@/types'

import Home from './Home'

jest.mock('@/services/api', () => ({
  attacksApi: { listAttacks: jest.fn() },
  scenariosApi: { listRuns: jest.fn() },
  operationsApi: { list: jest.fn() },
}))
jest.mock('@/hooks/useRuntime', () => ({ useRuntime: jest.fn() }))

const mockAttacks = jest.mocked(attacksApi.listAttacks)
const mockScans = jest.mocked(scenariosApi.listRuns)
const mockOperations = jest.mocked(operationsApi.list)
const mockRuntime = jest.mocked(useRuntime)
const PAGINATION = { limit: 5, has_more: false, next_cursor: null, prev_cursor: null }

function Location() {
  return <output aria-label="Current path">{useLocation().pathname}</output>
}

function TestWrapper({ children }: { readonly children: ReactNode }) {
  return <FluentProvider theme={webLightTheme}><MemoryRouter>{children}<Location /></MemoryRouter></FluentProvider>
}

function attack(overrides: Partial<AttackSummary> = {}): AttackSummary {
  return {
    attack_result_id: 'attack-1', conversation_id: 'conversation-1', attack_type: 'ManualAttack',
    objective: 'Check the support assistant', converters: [], outcome: 'undetermined', message_count: 2,
    related_conversation_ids: [], labels: {}, created_at: '2026-10-28T10:00:00Z',
    updated_at: '2026-10-28T11:00:00Z', operation_name: 'Current operation name', ...overrides,
  }
}

function scan(overrides: Partial<ScenarioRunListItem> = {}): ScenarioRunListItem {
  return {
    scenario_result_id: 'run-1', scenario_name: 'Safety scan', scenario_version: 1, status: 'IN_PROGRESS',
    created_at: '2026-10-28T09:00:00Z', updated_at: '2026-10-28T12:00:00Z', techniques_used: [],
    total_attacks: null, completed_attacks: 0, objective_achieved_rate: 0, total_retries: 0,
    labels: {}, operation_name: 'Saved scan operation', datasets_used: [], scenario_parameters: {},
    planned_total_available: false, successful_attacks: 0, error_attacks: 0, attack_details_available: false,
    ...overrides,
  }
}

function operation(overrides: Partial<OperationListItem> = {}): OperationListItem {
  return { id: 'saved-operation', name: 'Saved operation', description: 'A real saved engagement',
    created_at: '2026-10-28T08:00:00Z', finding_counts: {}, ...overrides }
}

describe('Home', () => {
  const defaultProps = {
    onNavigate: jest.fn(), onNewAttack: jest.fn(), onOpenAttack: jest.fn(),
    onOpenAttackHistory: jest.fn(), onOpenScannerHistory: jest.fn(),
  }

  beforeEach(() => {
    jest.clearAllMocks()
    mockRuntime.mockReturnValue({ ready: true, generation: 'first', state: 'ready' })
    mockAttacks.mockResolvedValue({ items: [], pagination: PAGINATION })
    mockScans.mockResolvedValue({ items: [], pagination: PAGINATION })
    mockOperations.mockResolvedValue({ items: [] })
  })

  it('should prioritize workflows and only read summaries on entry', async () => {
    render(<TestWrapper><Home {...defaultProps} /></TestWrapper>)
    expect(screen.getAllByRole('heading', { level: 2 }).map((heading: HTMLElement) => heading.textContent))
      .toEqual(['Attacks', 'Scans', 'Recent work', 'Operations'])
    expect(screen.queryByRole('heading', { name: 'Default objective target' })).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Labels' })).not.toBeInTheDocument()
    await screen.findByText('Your recent work will appear here')
    expect(mockAttacks).toHaveBeenCalledWith({ limit: 5, include_scenario_attacks: false })
    expect(mockScans).toHaveBeenCalledWith({ limit: 5 })
    expect(mockOperations).toHaveBeenCalledTimes(1)
    for (const callback of Object.values(defaultProps)) expect(callback).not.toHaveBeenCalled()
  })

  it('should use the intentional new-attack action and each workflow history', async () => {
    const user = userEvent.setup()
    render(<TestWrapper><Home {...defaultProps} /></TestWrapper>)
    await user.click(screen.getByRole('button', { name: 'New attack', exact: true }))
    expect(defaultProps.onNewAttack).toHaveBeenCalledTimes(1)
    await user.click(screen.getByRole('button', { name: 'Attack history', exact: true }))
    expect(defaultProps.onOpenAttackHistory).toHaveBeenCalledTimes(1)
    await user.click(screen.getByRole('button', { name: 'Configure scan', exact: true }))
    expect(defaultProps.onNavigate).toHaveBeenCalledWith('scenarios')
    await user.click(screen.getByRole('button', { name: 'Scan history', exact: true }))
    expect(defaultProps.onOpenScannerHistory).toHaveBeenCalledTimes(1)
    await user.click(screen.getByRole('button', { name: 'View operations', exact: true }))
    expect(defaultProps.onNavigate).toHaveBeenCalledWith('operations')
    await user.click(screen.getByRole('button', { name: 'Open Registry', exact: true }))
    expect(defaultProps.onNavigate).toHaveBeenCalledWith('registry')
    expect(defaultProps.onNavigate).not.toHaveBeenCalledWith('chat')
  })

  it('should merge recent work by activity, filter without fetching, and open saved destinations', async () => {
    const user = userEvent.setup()
    mockAttacks.mockResolvedValue({ items: [attack()], pagination: PAGINATION })
    mockScans.mockResolvedValue({ items: [scan({ scenario_result_id: 'run/one' })], pagination: PAGINATION })
    render(<TestWrapper><Home {...defaultProps} /></TestWrapper>)
    const list = await screen.findByRole('list')
    await waitFor(() => { expect(within(list).getAllByRole('button')).toHaveLength(2) })
    expect(within(list).getAllByRole('button')[0]).toHaveTextContent('Safety scan')
    expect(within(list).getByText('Executing')).toBeInTheDocument()
    expect(within(list).getByText('Undetermined')).toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: 'Attacks', exact: true }))
    expect(within(list).queryByText('Safety scan')).not.toBeInTheDocument()
    await user.click(within(list).getByRole('button'))
    expect(defaultProps.onOpenAttack).toHaveBeenCalledWith('attack-1')
    await user.click(screen.getByRole('tab', { name: 'Scans', exact: true }))
    await user.click(within(list).getByRole('button'))
    expect(screen.getByLabelText('Current path')).toHaveTextContent('/scanner/history/run%2Fone')
    expect(mockAttacks).toHaveBeenCalledTimes(1)
    expect(mockScans).toHaveBeenCalledTimes(1)
  })

  it('should limit the combined list and use meaningful title fallbacks', async () => {
    mockAttacks.mockResolvedValue({ items: Array.from({ length: 5 }, (_, index: number) =>
      attack({ attack_result_id: `attack-${index}`, objective: '', last_message_preview: `Preview ${index}` })),
    pagination: PAGINATION })
    mockScans.mockResolvedValue({ items: [scan()], pagination: PAGINATION })
    render(<TestWrapper><Home {...defaultProps} /></TestWrapper>)
    await screen.findByText('Preview 0')
    const list = screen.getByRole('list')
    expect(within(list).getAllByRole('button')).toHaveLength(5)
    expect(within(list).getAllByRole('button')[0]).toHaveTextContent('Safety scan')
  })

  it.each<[ScenarioRunState, string]>([
    ['CREATED', 'Created'], ['QUEUED', 'Queued'], ['IN_PROGRESS', 'Executing'],
    ['COMPLETED', 'Completed'], ['FAILED', 'Failed'], ['CANCELLED', 'Cancelled'],
  ])('should show scan status %s without assuming completion', async (status: ScenarioRunState, label: string) => {
    mockScans.mockResolvedValue({ items: [scan({ status })], pagination: PAGINATION })
    render(<TestWrapper><Home {...defaultProps} /></TestWrapper>)
    expect(await screen.findByText(label)).toBeInTheDocument()
  })

  it('should show saved Operations independently of attack labels, with encoded IDs and no invented totals', async () => {
    const user = userEvent.setup()
    mockAttacks.mockResolvedValue({ items: [attack({ operation_name: 'Legacy name only' })], pagination: PAGINATION })
    mockOperations.mockResolvedValue({ items: [operation({ id: 'operation/one' }), operation({ id: 'two', name: 'Second' }),
      operation({ id: 'three', name: 'Third' }), operation({ id: 'four', name: 'Fourth' })] })
    render(<TestWrapper><Home {...defaultProps} /></TestWrapper>)
    const section = screen.getByRole('region', { name: 'Operations' })
    await within(section).findByText('Saved operation')
    expect(within(section).queryByText('Legacy name only')).not.toBeInTheDocument()
    expect(within(section).queryByText('Fourth')).not.toBeInTheDocument()
    expect(within(section).queryByText(/attacks|total/i)).not.toBeInTheDocument()
    await user.click(within(section).getByRole('button', { name: /Saved operation A real saved engagement/ }))
    expect(screen.getByLabelText('Current path')).toHaveTextContent('/operations/operation%2Fone')
  })

  it('should keep successful sources visible when another fails and retry without an empty-success fallback', async () => {
    const user = userEvent.setup()
    mockAttacks.mockRejectedValueOnce(new Error('Attack service unavailable'))
    mockScans.mockResolvedValue({ items: [scan()], pagination: PAGINATION })
    mockOperations.mockRejectedValueOnce(new Error('Operations unavailable'))
    render(<TestWrapper><Home {...defaultProps} /></TestWrapper>)
    expect(await screen.findByText(/Could not load recent attacks/)).toBeInTheDocument()
    expect(screen.getByText('Safety scan')).toBeInTheDocument()
    expect(await screen.findByText(/Could not load operations:/)).toBeInTheDocument()
    expect(screen.queryByText('Your recent work will appear here')).not.toBeInTheDocument()
    expect(screen.queryByText('Keep related work together')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Retry operations' }))
    await screen.findByText('Keep related work together')
    expect(mockAttacks).toHaveBeenCalledTimes(2)
    expect(screen.queryByText(/Could not load recent attacks/)).not.toBeInTheDocument()
  })

  it('should show loading rather than empty while summaries are pending and ignore stale runtime results', async () => {
    let resolveOld: ((value: { items: AttackSummary[]; pagination: typeof PAGINATION }) => void) | undefined
    mockAttacks.mockReturnValueOnce(new Promise((resolve) => { resolveOld = resolve }))
    const { rerender } = render(<TestWrapper><Home {...defaultProps} /></TestWrapper>)
    expect(screen.getByText('Loading recent work...')).toBeInTheDocument()
    expect(screen.queryByText('Your recent work will appear here')).not.toBeInTheDocument()
    mockRuntime.mockReturnValue({ ready: true, generation: 'second', state: 'ready' })
    rerender(<TestWrapper><Home {...defaultProps} /></TestWrapper>)
    await screen.findByText('Your recent work will appear here')
    await act(async () => { resolveOld?.({ items: [attack()], pagination: PAGINATION }) })
    expect(screen.queryByText('Check the support assistant')).not.toBeInTheDocument()
  })

  it('should not fetch while the runtime is unavailable', () => {
    mockRuntime.mockReturnValue({ ready: false, generation: '', state: 'connecting' })
    render(<TestWrapper><Home {...defaultProps} /></TestWrapper>)
    expect(mockAttacks).not.toHaveBeenCalled()
    expect(mockScans).not.toHaveBeenCalled()
    expect(mockOperations).not.toHaveBeenCalled()
  })
})
