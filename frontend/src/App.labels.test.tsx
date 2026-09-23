import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'

import { useScenarioRunProgress } from '@/hooks/useScenarioRunProgress'
import { ThemeProvider } from '@/hooks/useTheme'
import { attacksApi, labelsApi, scenariosApi, targetsApi, versionApi } from '@/services/api'
import { makeTarget } from '@/test-utils/targetFixtures'
import type { RegisteredScenario } from '@/types'
import { exportConversation } from '@/utils/conversationExport'
import { INITIAL_SCENARIO_RUN_PROGRESS_STATE } from '@/utils/scenarioRunProgress'

import App from './App'

const mockGetActiveAccount = jest.fn()
const mockMsalInstance = { getActiveAccount: mockGetActiveAccount }

jest.mock('@azure/msal-react', () => ({
  useMsal: () => ({ instance: mockMsalInstance }),
}))

jest.mock('react-joyride', () => ({ Joyride: () => null }))
jest.mock('@/hooks/useTour', () => ({
  useTour: () => ({ startTour: jest.fn(), tourProps: {} }),
}))
jest.mock('@/hooks/useConnectionHealth', () => ({
  ConnectionHealthProvider: ({ children }: { children: React.ReactNode }) => children,
  useConnectionHealth: () => ({ status: 'connected', reconnectCount: 0 }),
}))
jest.mock('@/hooks/useScenarioRunProgress', () => ({
  useScenarioRunProgress: jest.fn(),
}))
jest.mock('@/utils/conversationExport', () => ({
  exportConversation: jest.fn().mockResolvedValue(undefined),
}))

jest.mock('@/services/api', () => ({
  authApi: { getAccess: jest.fn().mockResolvedValue({ isAdmin: false }) },
  versionApi: { getVersion: jest.fn() },
  labelsApi: { getLabels: jest.fn() },
  attacksApi: {
    listAttacks: jest.fn(),
    getAttack: jest.fn(),
    getMessages: jest.fn(),
    getConversations: jest.fn(),
    updateAttack: jest.fn(),
    createAttack: jest.fn(),
    addMessage: jest.fn(),
  },
  targetsApi: { listTargets: jest.fn(), getTarget: jest.fn() },
  scenariosApi: {
    listCatalog: jest.fn(),
    getScenario: jest.fn(),
    estimateRun: jest.fn(),
    startRun: jest.fn(),
    cancelRun: jest.fn(),
  },
}))

const SCENARIO: RegisteredScenario = {
  scenario_name: 'test.scenario',
  scenario_type: 'TestScenario',
  scenario_version: 1,
  description: 'A test scenario.',
  description_markdown: 'A test scenario.',
  default_technique: 'default',
  default_techniques: ['test_technique'],
  aggregate_techniques: ['default'],
  aggregate_technique_expansions: { default: ['test_technique'] },
  all_techniques: ['test_technique'],
  technique_summaries: [{ name: 'test_technique', description: 'Test technique.', tags: [] }],
  default_datasets: [],
  baseline_policy: 'forbidden',
  include_baseline_by_default: false,
  supported_parameters: [],
  default_run_size: { estimated_attack_count: 1, components: [], datasets: [], note: null },
}
const TARGET = makeTarget({ target_registry_name: 'test_target', identifier_hash: 'test_hash' })
const DEFAULT_LABELS = { operator: 'config_user', operation: 'config_op', team: 'config_team' }
const SAVED_LABELS = { operator: 'original_user', operation: 'original_op', team: 'original_team' }
const SCENARIO_PATH = '/scanner/test.scenario'

function TestWrapper({ children }: { children: React.ReactNode }) {
  return <ThemeProvider>{children}</ThemeProvider>
}

function renderApp(path = SCENARIO_PATH) {
  return render(
    <TestWrapper>
      <MemoryRouter initialEntries={[path]}>
        <App />
      </MemoryRouter>
    </TestWrapper>,
  )
}

function currentLabels(): HTMLElement {
  return screen.getByRole('region', { name: 'Default Labels' })
}

async function chooseOperation(user: ReturnType<typeof userEvent.setup>, operation: string): Promise<void> {
  await user.click(within(currentLabels()).getByRole('button', { name: /^Edit operation, currently / }))
  await user.click(screen.getByRole('combobox', { name: 'Operation' }))
  await user.paste(operation)
  await user.keyboard('{ArrowDown}')
  await user.click(await screen.findByRole('option', { name: `Create "${operation}"` }))
  expect(within(currentLabels()).getByRole('button', { name: `Edit operation, currently ${operation}` }))
    .toBeInTheDocument()
}

async function launchScenario(user: ReturnType<typeof userEvent.setup>): Promise<void> {
  await user.selectOptions(await screen.findByRole('combobox', { name: 'Target' }), 'test_target')
  await user.click(screen.getByRole('button', { name: 'Launch scan' }))
  const dialog = await screen.findByRole('dialog')
  await user.click(within(dialog).getByRole('button', { name: 'Launch scan' }))
  await waitFor(() => expect(scenariosApi.startRun).toHaveBeenCalledTimes(1))
}

describe('Shared new run labels', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    window.localStorage.clear()
    mockGetActiveAccount.mockReturnValue(null)
    jest.mocked(versionApi.getVersion).mockResolvedValue({ version: '1.0.0', default_labels: DEFAULT_LABELS })
    jest.mocked(labelsApi.getLabels).mockResolvedValue({ source: 'attacks', labels: {} })
    jest.mocked(targetsApi.listTargets).mockResolvedValue({
      items: [TARGET], pagination: { limit: 200, has_more: false },
    })
    jest.mocked(targetsApi.getTarget).mockResolvedValue(TARGET)
    jest.mocked(attacksApi.listAttacks).mockResolvedValue({
      items: [], pagination: { limit: 50, has_more: false },
    })
    jest.mocked(attacksApi.getMessages).mockResolvedValue({
      conversation_id: 'old_conversation', messages: [], target_response_status: null,
    })
    jest.mocked(attacksApi.getConversations).mockResolvedValue({
      attack_result_id: 'old_attack', main_conversation_id: 'old_conversation', conversations: [],
    })
    jest.mocked(scenariosApi.getScenario).mockResolvedValue(SCENARIO)
    jest.mocked(scenariosApi.listCatalog).mockResolvedValue({
      items: [SCENARIO], pagination: { limit: 200, has_more: false },
    })
    jest.mocked(scenariosApi.estimateRun).mockResolvedValue(SCENARIO.default_run_size)
    jest.mocked(scenariosApi.startRun).mockResolvedValue({
      scenario_result_id: 'saved_run', scenario_name: SCENARIO.scenario_name,
      scenario_version: 1, status: 'COMPLETED', created_at: '2026-01-01T00:00:00Z',
    })
    jest.mocked(useScenarioRunProgress).mockReturnValue({
      state: {
        ...INITIAL_SCENARIO_RUN_PROGRESS_STATE,
        loadStatus: 'ready',
        run: {
          scenario_result_id: 'saved_run', scenario_name: 'SavedScenario',
          scenario_version: 1, status: 'COMPLETED', created_at: '2026-01-01T00:00:00Z',
          labels: SAVED_LABELS,
        },
        summary: {
          overall: { completed: 0, planned: 0, succeeded: 0, success_percentage: 0, errors: 0, retries: 0 },
          techniques: [], seed_groups: [], atomic_groups: [],
        },
      },
      retry: jest.fn(),
      applyRunSummary: jest.fn(),
    })
  })

  it('edits operator, operation, and custom labels during scenario setup and sends them on launch', async () => {
    const user = userEvent.setup()
    renderApp()
    await user.click(await screen.findByRole('button', { name: 'Edit operator, currently config_user' }))
    const operator = screen.getByRole('textbox', { name: 'Value for operator label' })
    await user.clear(operator)
    await user.paste('test_user')
    await user.keyboard('{Enter}')
    await chooseOperation(user, 'test_op')

    await user.click(screen.getByRole('button', { name: /^1 label .* click to view or add$/ }))
    await user.click(screen.getByRole('textbox', { name: 'Label key' }))
    await user.paste('campaign')
    await user.click(screen.getByRole('textbox', { name: 'Label value' }))
    await user.paste('regression')
    await user.click(screen.getByRole('button', { name: 'Add', exact: true }))
    expect(screen.getByRole('button', { name: /^2 labels .* click to view or add$/ })).toBeInTheDocument()

    await launchScenario(user)
    expect(scenariosApi.startRun).toHaveBeenCalledWith(expect.objectContaining({
      scenario_name: 'test.scenario',
      labels: { operator: 'test_user', operation: 'test_op', team: 'config_team', campaign: 'regression' },
    }))
  })

  it('keeps one editor through navigation and restores choices without pinning backend defaults', async () => {
    const user = userEvent.setup()
    const app = renderApp()
    await screen.findByRole('button', { name: 'Edit operation, currently config_op' })
    await chooseOperation(user, 'remembered_op')
    const bar = currentLabels()

    for (const destination of ['Home', 'Chat', 'Scanner']) {
      await user.click(screen.getByRole('button', { name: destination, exact: true }))
      expect(screen.getAllByTestId('labels-bar')).toHaveLength(1)
      expect(currentLabels()).toBe(bar)
      expect(within(bar).getByRole('button', { name: /currently remembered_op$/ })).toBeInTheDocument()
    }
    await user.click(await screen.findByRole('link', { name: 'test.scenario' }))
    await screen.findByRole('combobox', { name: 'Target' })
    expect(currentLabels()).toBe(bar)
    expect(JSON.parse(window.localStorage.getItem('pyrit.globalLabels') ?? '{}'))
      .toEqual({ operation: 'remembered_op' })

    app.unmount()
    jest.mocked(versionApi.getVersion).mockResolvedValue({
      version: '1.0.0', default_labels: { ...DEFAULT_LABELS, operation: 'new_default', team: 'new_team' },
    })
    renderApp()
    await screen.findByRole('button', { name: 'Edit team label, currently new_team' })
    expect(screen.getByRole('button', { name: /currently remembered_op$/ })).toBeInTheDocument()
  })

  it('uses the signed-in alias ahead of stored and backend operators when launching', async () => {
    const user = userEvent.setup()
    window.localStorage.setItem('pyrit.globalLabels', JSON.stringify({ operator: 'remembered_user', operation: 'remembered_op' }))
    mockGetActiveAccount.mockReturnValue({ username: 'Signed.In@contoso.com' })
    renderApp()
    await screen.findByRole('button', { name: 'Edit operator, currently signed.in' })
    await chooseOperation(user, 'signed_in_op')
    await launchScenario(user)
    expect(scenariosApi.startRun).toHaveBeenCalledWith(expect.objectContaining({
      scenario_name: 'test.scenario',
      labels: { operator: 'signed.in', operation: 'signed_in_op', team: 'config_team' },
    }))
  })

  it('hosts Chat controls beside the labels and removes them when navigating away', async () => {
    const user = userEvent.setup()
    renderApp('/chat')
    const toolbar = within(currentLabels()).getByRole('group', { name: 'Chat controls' })
    expect(within(screen.getByTestId('chat-area')).queryByRole('group', { name: 'Chat controls' }))
      .not.toBeInTheDocument()
    expect(within(toolbar).getByRole('button', { name: 'Export conversation' })).toBeDisabled()
    expect(within(toolbar).getByRole('button', { name: 'Toggle conversations panel' })).toBeDisabled()
    expect(within(toolbar).getByRole('button', { name: 'New Attack' })).toBeDisabled()
    const markdown = within(toolbar).getByRole('switch')
    await user.click(markdown)
    expect(window.localStorage.getItem('pyrit.chatMarkdownMode')).toBe('markdown')

    await user.click(screen.getByRole('button', { name: 'Home', exact: true }))
    expect(screen.queryByRole('group', { name: 'Chat controls' })).not.toBeInTheDocument()
    expect(screen.getAllByTestId('labels-bar')).toHaveLength(1)
    await user.click(screen.getByRole('button', { name: 'Scanner', exact: true }))
    expect(screen.queryByRole('group', { name: 'Chat controls' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Chat', exact: true }))
    expect(screen.getAllByRole('group', { name: 'Chat controls' })).toHaveLength(1)
    expect(within(currentLabels()).getByRole('switch')).toBeChecked()
  })

  it('preserves an edit made before the backend defaults arrive', async () => {
    const user = userEvent.setup()
    let resolveVersion: (value: { version: string; default_labels: Record<string, string> }) => void = () => {}
    jest.mocked(versionApi.getVersion).mockReturnValue(new Promise((resolve) => { resolveVersion = resolve }))
    renderApp()
    await chooseOperation(user, 'early_choice')
    await act(async () => { resolveVersion({ version: '1.0.0', default_labels: DEFAULT_LABELS }) })
    await screen.findByRole('button', { name: 'Edit operator, currently config_user' })
    expect(screen.getByRole('button', { name: /currently early_choice$/ })).toBeInTheDocument()
  })

  it('leaves saved scenario attribution unchanged when future launch labels change', async () => {
    const user = userEvent.setup()
    renderApp('/scanner-history/saved_run')
    const saved = screen.getByRole('region', { name: 'Run configuration' })
    await screen.findByRole('button', { name: 'Edit operation, currently config_op' })
    await chooseOperation(user, 'future_op')
    expect(saved).toHaveTextContent('original_user')
    expect(saved).toHaveTextContent('original_op')
    expect(saved).toHaveTextContent('original_team')
    expect(saved).not.toHaveTextContent('future_op')
    expect(within(saved).queryByRole('button', { name: /^Edit / })).not.toBeInTheDocument()
    expect(scenariosApi.startRun).not.toHaveBeenCalled()
    expect(scenariosApi.cancelRun).not.toHaveBeenCalled()
  })

  it('keeps existing chat operator restrictions and does not mutate the attack when changing future labels', async () => {
    const user = userEvent.setup()
    jest.mocked(attacksApi.getMessages).mockResolvedValue({
      conversation_id: 'old_conversation',
      target_response_status: null,
      messages: [{
        turn_number: 1, role: 'assistant', created_at: '2026-01-01T00:00:00Z',
        message_pieces: [{
          id: 'response_piece', original_value_data_type: 'text', converted_value_data_type: 'text',
          original_value: 'Saved response', converted_value: 'Saved response', scores: [], response_error: 'none',
        }],
      }],
    })
    jest.mocked(attacksApi.getAttack).mockResolvedValue({
      attack_result_id: 'old_attack', conversation_id: 'old_conversation',
      attack_type: 'PromptSendingAttack', outcome: 'undetermined', labels: { team: 'original_team' },
      objective: '', converters: [], message_count: 0, related_conversation_ids: [],
      operator: 'original_user', operation: 'original_op',
      target: { target_type: 'OpenAIChatTarget', target_registry_name: 'test_target', identifier_hash: 'test_hash' },
      created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
    })
    renderApp('/attacks/old_attack')
    expect(await screen.findByTestId('operator-locked-banner')).toBeInTheDocument()
    await chooseOperation(user, 'future_op')
    expect(screen.getByTestId('operator-locked-banner')).toBeInTheDocument()
    expect(screen.getAllByTestId('labels-bar')).toHaveLength(1)
    expect(attacksApi.updateAttack).not.toHaveBeenCalled()
    expect(attacksApi.createAttack).not.toHaveBeenCalled()
    expect(attacksApi.addMessage).not.toHaveBeenCalled()

    const toolbar = within(currentLabels()).getByRole('group', { name: 'Chat controls' })
    expect(within(toolbar).getByLabelText('Active target: test_target')).toBeInTheDocument()
    await waitFor(() => expect(within(toolbar).getByRole('button', { name: 'Export conversation' })).toBeEnabled())
    await user.click(within(toolbar).getByRole('button', { name: 'Export conversation' }))
    await user.click(await screen.findByRole('menuitem', { name: 'Export as JSON (.json)' }))
    expect(exportConversation).toHaveBeenCalledWith(expect.objectContaining({
      conversationId: 'old_conversation', format: 'json',
      messages: expect.arrayContaining([expect.objectContaining({ content: 'Saved response' })]),
    }))
    const panelToggle = within(toolbar).getByRole('button', { name: 'Toggle conversations panel' })
    await user.click(panelToggle)
    expect(panelToggle).toHaveAttribute('aria-expanded', 'true')
    await user.click(panelToggle)
    expect(panelToggle).toHaveAttribute('aria-expanded', 'false')
    await user.click(within(toolbar).getByRole('button', { name: 'New Attack' }))
    expect(await screen.findByRole('button', { name: 'New Attack' })).toBeDisabled()
    expect(screen.queryByTestId('operator-locked-banner')).not.toBeInTheDocument()
    expect(within(currentLabels()).getByRole('button', { name: /currently future_op$/ })).toBeInTheDocument()
  })
})
