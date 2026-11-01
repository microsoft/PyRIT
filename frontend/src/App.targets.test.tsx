import type { ReactNode } from 'react'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'

import { attacksApi, targetsApi } from '@/services/api'
import { makeTarget } from '@/test-utils/targetFixtures'
import type { AttackSummary, ConversationMessagesResponse, RegisteredComponentReference, ScorerInstance } from '@/types'
import { DEFAULT_USER_PREFERENCES, readUserPreferences, writeUserPreferences } from '@/utils/userPreferences'

import App from './App'

const mockGetActiveAccount = jest.fn()
const mockMsalInstance = { getActiveAccount: mockGetActiveAccount }
let mockScorers: ScorerInstance[] = []

jest.mock('@azure/msal-react', () => ({
  useMsal: () => ({ instance: mockMsalInstance, accounts: [] }),
}))
jest.mock('@/auth/AuthConfigContext', () => ({
  useAuthConfig: () => ({ clientId: 'test-client' }),
}))
jest.mock('@/hooks/useTour', () => ({
  useTour: () => ({ startTour: jest.fn(), tourProps: {} }),
}))
jest.mock('react-joyride', () => ({ Joyride: () => null }))
jest.mock('@/hooks/useScorerRegistry', () => ({
  useScorerRegistry: () => ({ scorers: mockScorers, loading: false, error: null, refresh: jest.fn() }),
}))
jest.mock('@/components/Layout/MainLayout', () => {
  const { Link } = jest.requireActual<typeof import('react-router')>('react-router')
  return {
    __esModule: true,
    default: ({ children, featureNavigation }: { children: ReactNode; featureNavigation?: ReactNode }) => (
      <>
        <Link to="/attack/chat">Open new chat</Link>
        <Link to="/attack/chat/saved-attack">Open saved chat</Link>
        <Link to="/registry/targets">Open registry</Link>
        {featureNavigation}
        {children}
      </>
    ),
  }
})
jest.mock('@/services/api', () => ({
  runtimeApi: {
    getReadiness: jest.fn().mockResolvedValue({ ready: true, state: 'ready', generation: '' }),
  },
  attacksApi: {
    getAttack: jest.fn(),
    getMessages: jest.fn(),
    getConversations: jest.fn(),
    submitMessageSend: jest.fn(),
    getMessageSend: jest.fn(),
    createAttack: jest.fn(),
  },
  targetsApi: { listTargets: jest.fn(), getTarget: jest.fn() },
  convertersApi: { listConverters: jest.fn().mockResolvedValue({ items: [] }) },
  labelsApi: { getLabels: jest.fn().mockResolvedValue({ labels: {} }) },
  versionApi: { getVersion: jest.fn().mockResolvedValue({ version: 'test' }) },
  authApi: { getAccess: jest.fn().mockResolvedValue({ isAdmin: false }) },
}))

const targetA = makeTarget({ target_registry_name: 'target-a', capabilities: { supports_multi_turn: true } })
const targetB = makeTarget({ target_registry_name: 'target-b', capabilities: { supports_multi_turn: true } })
const savedAttack: AttackSummary = {
  attack_result_id: 'saved-attack',
  conversation_id: 'saved-conversation',
  attack_type: 'ManualAttack',
  objective: '',
  converters: [],
  message_count: 1,
  related_conversation_ids: [],
  labels: { operator: 'alice' },
  operator: 'alice',
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  target: {
    target_type: 'TextTarget',
    target_registry_name: 'target-b',
    identifier_hash: targetB.identifier.hash,
  },
}
const savedMessages: ConversationMessagesResponse = {
  conversation_id: 'saved-conversation',
  target_response_status: null,
  messages: [{
    role: 'assistant',
    turn_number: 1,
    created_at: '2026-01-01T00:00:00Z',
    message_pieces: [{
      id: 'saved-response',
      original_value_data_type: 'text',
      converted_value_data_type: 'text',
      converted_value: 'Saved reply from target B',
      scores: [],
      response_error: 'none',
    }],
  }],
}

function TestWrapper({ children }: { children: ReactNode }) {
  return (
    <FluentProvider theme={webLightTheme}>
      <MemoryRouter initialEntries={['/attack/chat']}>{children}</MemoryRouter>
    </FluentProvider>
  )
}

function saveDefault(account: string, target: typeof targetA): void {
  writeUserPreferences(`tenant:${account}`, {
    ...DEFAULT_USER_PREFERENCES,
    lastUsedTargets: {
      objective: { registryName: target.target_registry_name, identifierHash: target.identifier.hash },
      adversarial: null,
    },
  })
}

describe('App target selection with the chat composer', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    window.localStorage.clear()
    mockScorers = []
    mockGetActiveAccount.mockReturnValue({
      homeAccountId: 'alice', tenantId: 'tenant', username: 'alice@example.test',
    })
    jest.mocked(targetsApi.listTargets).mockResolvedValue({
      items: [targetA, targetB], pagination: { limit: 200, has_more: false },
    })
    jest.mocked(targetsApi.getTarget).mockResolvedValue(targetB)
    jest.mocked(attacksApi.getAttack).mockResolvedValue(savedAttack)
    jest.mocked(attacksApi.getMessages).mockResolvedValue(savedMessages)
    jest.mocked(attacksApi.getConversations).mockResolvedValue({
      main_conversation_id: 'saved-conversation', conversations: [],
    })
    jest.mocked(attacksApi.submitMessageSend).mockResolvedValue({
      send_id: 'saved-send', attack_result_id: 'saved-attack', conversation_id: 'saved-conversation',
      state: 'completed', error: null, failure_stage: null, request_turn_number: 0,
    })
  })

  it('keeps saved target B and remembers it only after sending, not when viewing history', async () => {
    const user = userEvent.setup()
    saveDefault('alice', targetA)
    render(<App />, { wrapper: TestWrapper })
    await waitFor(() => expect(screen.getByRole('combobox', { name: 'Chat target' })).toHaveValue('target-a'))
    await user.click(screen.getByRole('link', { name: 'Open saved chat' }))
    expect(await screen.findByText('Saved reply from target B')).toBeInTheDocument()
    expect(readUserPreferences('tenant:alice').lastUsedTargets?.objective?.registryName).toBe('target-a')
    const prompt = screen.getByPlaceholderText('Type prompt here')
    await waitFor(() => expect(prompt).toBeEnabled())
    await user.type(prompt, 'Continue this chat')
    await user.click(screen.getByRole('button', { name: 'Send message' }))
    await waitFor(() => expect(attacksApi.submitMessageSend).toHaveBeenCalledWith(
      'saved-attack',
      expect.objectContaining({
        target_registry_name: 'target-b',
        target_conversation_id: 'saved-conversation',
        pieces: [{ data_type: 'text', original_value: 'Continue this chat' }],
      }),
    ))
    expect(attacksApi.createAttack).not.toHaveBeenCalled()
    expect(screen.queryByRole('combobox', { name: 'Chat target' })).not.toBeInTheDocument()
    await waitFor(() => expect(readUserPreferences('tenant:alice').lastUsedTargets?.objective?.registryName).toBe('target-b'))
    await user.click(screen.getByRole('link', { name: 'Open new chat' }))
    await waitFor(() => expect(screen.getByRole('combobox', { name: 'Chat target' })).toHaveValue('target-b'))
  })

  it('selects a new-chat target from the badge without changing the default', async () => {
    const user = userEvent.setup()
    render(<App />, { wrapper: TestWrapper })
    const selector = screen.getByRole('combobox', { name: 'Chat target' })
    await waitFor(() => expect(selector).toBeEnabled())
    await user.selectOptions(selector, 'target-b')
    expect(selector).toHaveValue('target-b')
    expect(screen.getByTestId('target-badge')).toHaveAccessibleName('Active target: target-b')
    expect(screen.queryByRole('button', { name: 'Refresh targets' })).not.toBeInTheDocument()
    expect(readUserPreferences('tenant:alice').lastUsedTargets).toBeUndefined()
  })

  it('clears the draft and loads the next account defaults when the signed-in account changes', async () => {
    const user = userEvent.setup()
    saveDefault('alice', targetA)
    saveDefault('bob', targetB)
    const { rerender } = render(<App />, { wrapper: TestWrapper })
    await waitFor(() => expect(screen.getByRole('combobox', { name: 'Chat target' })).toHaveValue('target-a'))
    await user.type(screen.getByPlaceholderText('Type prompt here'), 'Private draft')
    mockGetActiveAccount.mockReturnValue({
      homeAccountId: 'bob', tenantId: 'tenant', username: 'bob@example.test',
    })
    rerender(<App />)
    await waitFor(() => expect(screen.getByRole('combobox', { name: 'Chat target' })).toHaveValue('target-b'))
    expect(screen.getByPlaceholderText('Type prompt here')).toHaveValue('')
    expect(readUserPreferences('tenant:alice').lastUsedTargets?.objective?.registryName).toBe('target-a')
  })

  it('keeps the draft and sends to its selected target after navigating to Chat again', async () => {
    const user = userEvent.setup()
    saveDefault('alice', targetA)
    jest.mocked(attacksApi.createAttack).mockResolvedValue(savedAttack)
    render(<App />, { wrapper: TestWrapper })
    const selector = screen.getByRole('combobox', { name: 'Chat target' })
    await waitFor(() => expect(selector).toHaveValue('target-a'))
    await user.selectOptions(selector, 'target-b')
    await user.type(screen.getByPlaceholderText('Type prompt here'), 'Prompt for B')
    await user.click(screen.getByRole('link', { name: 'Open new chat' }))
    expect(selector).toHaveValue('target-b')
    expect(screen.getByPlaceholderText('Type prompt here')).toHaveValue('Prompt for B')
    await user.click(screen.getByRole('button', { name: 'Send message' }))
    await waitFor(() => expect(attacksApi.submitMessageSend).toHaveBeenCalledWith(
      'saved-attack',
      expect.objectContaining({
        target_registry_name: 'target-b',
        pieces: [{ data_type: 'text', original_value: 'Prompt for B' }],
      }),
    ))
  })

  it('keeps the unsent draft and applies cross-tab last use only to a new attack', async () => {
    const user = userEvent.setup()
    saveDefault('alice', targetA)
    render(<App />, { wrapper: TestWrapper })
    await waitFor(() => expect(screen.getByRole('combobox', { name: 'Chat target' })).toHaveValue('target-a'))
    await user.type(screen.getByPlaceholderText('Type prompt here'), 'Prompt for A')
    act(() => {
      saveDefault('alice', targetB)
      window.dispatchEvent(new StorageEvent('storage', {
        key: 'pyrit.userPreferences.v1.tenant:alice', storageArea: window.localStorage,
      }))
    })
    expect(screen.getByRole('combobox', { name: 'Chat target' })).toHaveValue('target-a')
    expect(screen.getByPlaceholderText('Type prompt here')).toHaveValue('Prompt for A')
    await user.click(screen.getByRole('link', { name: 'Open registry' }))
    await user.click(screen.getByRole('link', { name: 'Open new chat' }))
    await waitFor(() => expect(screen.getByRole('combobox', { name: 'Chat target' })).toHaveValue('target-a'))
    expect(screen.getByPlaceholderText('Type prompt here')).toHaveValue('Prompt for A')
    await user.click(screen.getByRole('button', { name: 'New attack', exact: true }))
    await waitFor(() => expect(screen.getByRole('combobox', { name: 'Chat target' })).toHaveValue('target-b'))
    expect(screen.getByPlaceholderText('Type prompt here')).toHaveValue('')
  })

  it('resets both the composer and target when starting a new attack', async () => {
    const user = userEvent.setup()
    saveDefault('alice', targetA)
    render(<App />, { wrapper: TestWrapper })
    await user.click(screen.getByRole('link', { name: 'Open saved chat' }))
    await screen.findByText('Saved reply from target B')
    await user.type(screen.getByPlaceholderText('Type prompt here'), 'Do not carry this into A')
    await user.click(screen.getByRole('button', { name: 'New attack', exact: true }))
    await waitFor(() => expect(screen.getByRole('combobox', { name: 'Chat target' })).toHaveValue('target-a'))
    expect(screen.getByPlaceholderText('Type prompt here')).toHaveValue('')
  })

  it('recovers the app registry after the registry page completes a successful full load', async () => {
    const user = userEvent.setup()
    saveDefault('alice', targetA)
    jest.mocked(targetsApi.listTargets).mockRejectedValueOnce(new Error('Initial registry failed'))
    render(<App />, { wrapper: TestWrapper })
    await screen.findByText('Targets unavailable', { selector: 'span' })
    await user.click(screen.getByRole('link', { name: 'Open registry' }))
    await screen.findByRole('combobox', { name: 'Default adversarial target' })
    expect(screen.queryByRole('combobox', { name: 'Default objective target' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('link', { name: 'Open new chat' }))
    await user.click(screen.getByRole('button', { name: 'New attack', exact: true }))
    await waitFor(() => expect(screen.getByRole('combobox', { name: 'Chat target' })).toHaveValue('target-a'))
    expect(screen.getByPlaceholderText('Type prompt here')).toBeEnabled()
    expect(screen.queryByText('Targets unavailable')).not.toBeInTheDocument()
    expect(targetsApi.listTargets).toHaveBeenCalledTimes(2)
  })

  it('does not initialize a new chat with an unregistered automatic last-use alias', async () => {
    const user = userEvent.setup()
    mockScorers = [{
      scorer_registry_name: 'registered',
      identifier: {
        class_name: 'SubStringScorer', class_module: 'pyrit.score', hash: 'same-hash', scorer_type: 'true_false',
      },
    }]
    writeUserPreferences('tenant:alice', {
      ...DEFAULT_USER_PREFERENCES,
      lastUsedChatScorer: { registry_name: 'SelfAskTrueFalseScorer::alias', identifier_hash: 'same-hash' },
    })
    render(<App />, { wrapper: TestWrapper })
    await user.click(await screen.findByRole('button', { name: 'Add objective', exact: true }))
    await user.type(screen.getByRole('textbox', { name: 'Attack objective' }), 'Check the policy')
    await user.click(screen.getByRole('button', { name: 'Save', exact: true }))
    await user.click(screen.getByRole('button', { name: /objective achieved outcome/i }))
    await user.click(screen.getByRole('button', { name: /^automated scorer:/i }))
    expect(screen.getByRole('combobox', { name: 'Chat scorer', exact: true })).toHaveValue('')
    expect(screen.queryByText(/saved scorer is unavailable/i)).not.toBeInTheDocument()
  })

  it.each([
    { name: 'missing', reference: { registry_name: 'missing', identifier_hash: 'old-hash' } },
    { name: 'changed', reference: { registry_name: 'registered', identifier_hash: 'old-hash' } },
  ])('preserves a $name configured override and its feedback instead of substituting last use', async ({
    reference,
  }: { reference: RegisteredComponentReference }) => {
    const user = userEvent.setup()
    const lastUsed = { registry_name: 'registered', identifier_hash: 'same-hash' }
    mockScorers = [{
      scorer_registry_name: 'registered',
      identifier: {
        class_name: 'SubStringScorer', class_module: 'pyrit.score', hash: 'same-hash', scorer_type: 'true_false',
      },
    }]
    writeUserPreferences('tenant:alice', {
      ...DEFAULT_USER_PREFERENCES, chatScorer: reference, lastUsedChatScorer: lastUsed,
    })
    jest.mocked(attacksApi.createAttack).mockRejectedValueOnce({
      isAxiosError: true, response: { status: 409, data: { detail: 'Configured scorer is unavailable.' } },
    })
    render(<App />, { wrapper: TestWrapper })
    const target = screen.getByRole('combobox', { name: 'Chat target', exact: true })
    await waitFor(() => expect(target).toBeEnabled())
    await user.selectOptions(target, 'target-a')
    await user.click(screen.getByRole('button', { name: 'Add objective', exact: true }))
    await user.type(screen.getByRole('textbox', { name: 'Attack objective' }), 'Check the policy')
    await user.click(screen.getByRole('button', { name: 'Save', exact: true }))
    await user.click(screen.getByRole('button', { name: /objective achieved outcome/i }))
    await user.click(screen.getByRole('button', { name: /^automated scorer:/i }))
    expect(screen.getByRole('combobox', { name: 'Chat scorer', exact: true })).toHaveValue('unavailable')
    expect(screen.getByText(/The saved scorer is unavailable or its configuration changed/)).toBeVisible()
    await user.keyboard('{Escape}')
    await user.type(screen.getByPlaceholderText('Type prompt here'), 'Use the configured scorer')
    await user.click(screen.getByRole('button', { name: 'Send message', exact: true }))
    expect(await screen.findByText(/Configured scorer is unavailable\./)).toBeVisible()
    expect(attacksApi.createAttack).toHaveBeenCalledWith(expect.objectContaining({ chat_scorer: reference }))
    expect(attacksApi.submitMessageSend).not.toHaveBeenCalled()
    expect(readUserPreferences('tenant:alice').chatScorer).toEqual(reference)
    expect(readUserPreferences('tenant:alice').lastUsedChatScorer).toEqual(lastUsed)
  })
})
