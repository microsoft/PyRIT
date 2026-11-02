import { StrictMode, type ReactNode } from 'react'

import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent, { type UserEvent } from '@testing-library/user-event'
import { createMemoryRouter, MemoryRouter } from 'react-router'
import { RouterProvider } from 'react-router/dom'

import { attacksApi, convertersApi, runtimeApi, targetsApi } from '@/services/api'
import { deferred } from '@/test-utils/deferred'
import { makeTarget } from '@/test-utils/targetFixtures'
import { installRouterTestRequest } from '@/test-utils/routerRequest'
import type {
  AttackSummary, BackendMessage, ConverterInstance, ConverterPreviewResponse, MessageSendStatus, ScorerInstance,
} from '@/types'
import { targetReference } from '@/utils/targetIdentity'
import { DEFAULT_USER_PREFERENCES, readUserPreferences, writeUserPreferences } from '@/utils/userPreferences'

import App from './App'

let mockScorerRegistry: { scorers: ScorerInstance[]; loading: boolean; error: string | null } = {
  scorers: [], loading: false, error: null,
}

jest.mock('@azure/msal-react', () => ({
  useMsal: () => ({ instance: { getActiveAccount: () => null, getAllAccounts: () => [] } }),
}))
jest.mock('react-joyride', () => ({ Joyride: () => null }))
jest.mock('@/hooks/useTour', () => ({
  useTour: () => ({ startTour: jest.fn(), tourProps: {} }),
}))
jest.mock('@/hooks/useScorerRegistry', () => ({
  useScorerRegistry: () => mockScorerRegistry,
}))
jest.mock('@/hooks/useConnectionHealth', () => ({
  ConnectionHealthProvider: ({ children }: { children: ReactNode }) => children,
  useConnectionHealth: () => ({ status: 'connected', reconnectCount: 0 }),
}))
jest.mock('@/components/Home/Home', () => ({
  __esModule: true,
  default: () => <h1>Home</h1>,
}))
jest.mock('@/components/Datasets/DatasetCatalog', () => ({
  __esModule: true,
  default: () => <h1>Dataset catalog</h1>,
}))
jest.mock('@/components/Scenarios/ScenarioCatalog', () => ({
  __esModule: true,
  default: () => <h1>Scanner launch</h1>,
}))
jest.mock('@/components/Layout/MainLayout', () => {
  const { useNavigate, useLocation } = jest.requireActual<typeof import('react-router')>('react-router')
  const { useChatDraftHandoff } = jest.requireActual<typeof import('@/hooks/useChatDraftHandoff')>('@/hooks/useChatDraftHandoff')
  return {
    __esModule: true,
    default: function DraftTestLayout({ children, onNavigate, featureNavigation }: {
      children: ReactNode; onNavigate: (view: string) => void; featureNavigation?: ReactNode
    }) {
      const navigate = useNavigate()
      const location = useLocation()
      const handoff = useChatDraftHandoff()
      return <>
        <button onClick={() => onNavigate('datasets')}>Datasets</button>
        <button onClick={() => onNavigate('chat')}>Chat</button>
        <button onClick={() => onNavigate('scenarios')}>Scanner</button>
        <button onClick={() => navigate('/attack/chat/saved/conversations/main')}>Open saved main</button>
        <button onClick={() => navigate('/attack/chat/saved/conversations/branch')}>Open another conversation</button>
        <button onClick={() => navigate(-1)}>Back</button>
        <button onClick={() => navigate(1)}>Forward</button>
        <button onClick={() => handoff.stagePendingImport({
          destinationType: 'new_attack',
          content: { text: 'Imported prompt' },
          replaceDirty: true,
          focus: true,
        })}>Stage import</button>
        <button onClick={() => handoff.stagePendingImport({
          destinationType: 'new_attack',
          content: { text: 'Protected import' },
          replaceDirty: false,
        })}>Stage protected import</button>
        <button onClick={() => handoff.stagePendingImport({
          destinationType: 'current_conversation',
          targetAttackId: 'saved',
          targetConversationId: 'main',
          content: { text: 'Import for saved main' },
          replaceDirty: true,
        })}>Stage saved import</button>
        <output aria-label="Pending import">{handoff.pendingImport ? 'waiting' : 'none'}</output>
        <output aria-label="Current route">{location.pathname}</output>
        {featureNavigation}
        {children}
      </>
    },
  }
})
jest.mock('@/services/api', () => ({
  runtimeApi: { getReadiness: jest.fn().mockResolvedValue({ ready: true, state: 'ready', generation: '' }) },
  versionApi: { getVersion: jest.fn().mockResolvedValue({ version: 'test' }) },
  authApi: { getAccess: jest.fn().mockResolvedValue({ isAdmin: false }) },
  targetsApi: { listTargets: jest.fn(), getTarget: jest.fn() },
  attacksApi: {
    createAttack: jest.fn(),
    listAttacks: jest.fn(),
    getAttackOptions: jest.fn(),
    getConverterOptions: jest.fn(),
    getAttack: jest.fn(),
    getMessages: jest.fn(),
    getConversations: jest.fn(),
    saveConversation: jest.fn(),
    submitMessageSend: jest.fn(),
    getMessageSend: jest.fn(),
  },
  convertersApi: {
    listConverters: jest.fn(),
    listConverterTypes: jest.fn().mockResolvedValue({ items: [] }),
    previewConversion: jest.fn(),
  },
  labelsApi: { getLabels: jest.fn().mockResolvedValue({ labels: {} }) },
  operationsApi: { list: jest.fn().mockResolvedValue({ items: [] }) },
  datasetsApi: {
    listDatasets: jest.fn().mockResolvedValue({ items: [{
      name: 'Example dataset', selection_key: 'example-dataset',
      loaded: true, provider_available: true, logical_examples: 2, seed_pieces: 2, objectives: 0,
      modalities: ['text'], harm_categories: [], has_unlabeled_harm_categories: true,
    }] }),
    listSeedExamples: jest.fn().mockResolvedValue({
      items: [], total: 2, pagination: { limit: 20, has_more: false, next_cursor: null, prev_cursor: null },
    }),
  },
}))

const TARGET = makeTarget({
  target_registry_name: 'draft-target',
  capabilities: {
    supports_multi_turn: true,
    supports_multi_message_pieces: true,
    supports_json_schema: false,
    supports_json_output: false,
    supports_system_prompt: true,
    supports_editable_history: true,
    supported_input_modalities: ['text', 'image_path', 'audio_path', 'video_path'],
    supported_output_modalities: ['text'],
  },
})
const CONVERTER: ConverterInstance = {
  converter_id: 'draft-converter',
  identifier: {
    class_name: 'Base64Converter', class_module: 'pyrit.converter', hash: 'converter-hash',
    pyrit_version: 'test', supported_input_types: ['text'], supported_output_types: ['text'],
  },
}

function convertedText(value = 'Converted text', original = 'Original text'): ConverterPreviewResponse {
  return {
    original_value: original, original_value_data_type: 'text',
    converted_value: value, converted_value_data_type: 'text',
    steps: [{
      converter_id: CONVERTER.converter_id, converter_type: 'Base64Converter',
      input_value: original, input_data_type: 'text',
      output_value: value, output_data_type: 'text',
    }],
  }
}

function savedAttack(id = 'saved'): AttackSummary {
  return {
    attack_result_id: id, conversation_id: 'main', attack_type: 'ManualAttack',
    objective: '', converters: [], message_count: 0, labels: {}, operator: null,
    related_conversation_ids: ['branch'], target: null, target_unbound: true,
    created_at: '', updated_at: '',
  }
}

function renderChat(path = '/attack/chat', strict = false): ReturnType<typeof render> {
  const router = createMemoryRouter([{ path: '*', element: <App /> }], { initialEntries: [path] })
  const app = <RouterProvider router={router} />
  return render(strict ? <StrictMode>{app}</StrictMode> : app)
}

async function visitDatasets(user: UserEvent): Promise<void> {
  await user.click(screen.getByRole('button', { name: 'Datasets', exact: true }))
  await screen.findByRole('heading', { name: 'Dataset catalog' })
}

async function returnToChat(user: UserEvent): Promise<void> {
  await user.click(screen.getByRole('button', { name: 'Chat', exact: true }))
  await waitFor(() => expect(screen.getByPlaceholderText('Type prompt here')).toBeEnabled())
}

async function visitHistory(user: UserEvent): Promise<void> {
  await user.click(screen.getByRole('tab', { name: 'History', exact: true }))
  await screen.findByRole('row', { name: 'Open ManualAttack attack' })
}

async function addConverter(user: UserEvent): Promise<void> {
  await user.click(screen.getByRole('button', { name: 'Toggle converter panel' }))
  await user.click(await screen.findByRole('combobox', { name: 'Add converter' }))
  await user.click(await screen.findByRole('option', { name: /draft-converter/ }))
}

describe('App draft handoff with the real chat and composer', () => {
  let restoreRequest: () => void
  beforeAll(() => { restoreRequest = installRouterTestRequest() })
  afterAll(() => { restoreRequest() })

  beforeEach(() => {
    jest.clearAllMocks()
    mockScorerRegistry = { scorers: [], loading: false, error: null }
    window.localStorage.clear()
    jest.mocked(runtimeApi.getReadiness).mockResolvedValue({ ready: true, state: 'ready', generation: '' })
    jest.mocked(targetsApi.listTargets).mockResolvedValue({
      items: [TARGET], pagination: { limit: 200, has_more: false },
    })
    jest.mocked(targetsApi.getTarget).mockResolvedValue(TARGET)
    jest.mocked(attacksApi.getAttack).mockImplementation(async (id: string) => savedAttack(id))
    jest.mocked(attacksApi.listAttacks).mockResolvedValue({
      items: [{
        ...savedAttack('other'),
        created_at: '2026-10-28T10:00:00Z',
        updated_at: '2026-10-28T10:00:00Z',
      }],
      pagination: { limit: 25, has_more: false },
    })
    jest.mocked(attacksApi.getAttackOptions).mockResolvedValue({ attack_types: ['ManualAttack'] })
    jest.mocked(attacksApi.getConverterOptions).mockResolvedValue({ converter_types: [] })
    jest.mocked(attacksApi.getMessages).mockImplementation(async (_id: string, conversationId: string) => ({
      conversation_id: conversationId, messages: [], target_response_status: null,
    }))
    jest.mocked(attacksApi.getConversations).mockResolvedValue({ conversations: [], main_conversation_id: 'main' })
    jest.mocked(convertersApi.listConverters).mockResolvedValue({ items: [CONVERTER] })
    jest.mocked(convertersApi.previewConversion).mockResolvedValue(convertedText())
  })

  afterEach(() => { jest.useRealTimers() })

  const rememberedScorer = { registry_name: 'personal-judge', identifier_hash: 'judge-hash' }
  const scorerCatalog: ScorerInstance[] = [{
    scorer_registry_name: rememberedScorer.registry_name,
    identifier: {
      class_name: 'SubStringScorer', class_module: 'pyrit.score', hash: rememberedScorer.identifier_hash,
      scorer_type: 'true_false',
    },
  }]
  const confirmed: MessageSendStatus = {
    attack_result_id: 'sent-attack', conversation_id: 'sent-conversation', send_id: 'send',
    request_turn_number: 0, state: 'completed', failure_stage: null, error: null,
  }
  async function prepareNewSend(user: UserEvent): Promise<void> {
    const picker = await screen.findByRole('combobox', { name: 'Chat target' })
    await waitFor(() => expect(picker).toBeEnabled())
    await user.selectOptions(picker, TARGET.target_registry_name)
    await user.click(screen.getByRole('button', { name: 'Add objective', exact: true }))
    await user.type(screen.getByRole('textbox', { name: 'Attack objective' }), 'Goal')
    await user.click(screen.getByRole('button', { name: 'Save', exact: true }))
    await user.type(screen.getByPlaceholderText('Type prompt here'), 'Prompt')
  }

  it('waits for the remembered scorer catalog before sending a new objective', async () => {
    const user = userEvent.setup()
    mockScorerRegistry = { scorers: [], loading: true, error: null }
    writeUserPreferences('local', { ...DEFAULT_USER_PREFERENCES, lastUsedChatScorer: rememberedScorer })
    const rendered = render(<MemoryRouter initialEntries={['/attack/chat']}><App /></MemoryRouter>)
    await prepareNewSend(user)
    expect(screen.getByRole('button', { name: 'Send message' })).toHaveAttribute('aria-disabled', 'true')
    expect(attacksApi.createAttack).not.toHaveBeenCalled()
    expect(readUserPreferences('local').lastUsedChatScorer).toEqual(rememberedScorer)
    mockScorerRegistry = { scorers: scorerCatalog, loading: false, error: null }
    rendered.rerender(<MemoryRouter initialEntries={['/attack/chat']}><App /></MemoryRouter>)
    jest.mocked(attacksApi.createAttack).mockImplementation(() => new Promise(() => {}))
    await user.click(screen.getByRole('button', { name: 'Send message' }))
    expect(attacksApi.createAttack).toHaveBeenCalledWith(expect.objectContaining({ chat_scorer: rememberedScorer }))
  })

  it.each([false, true])('keeps configured scorer precedence while the catalog loads (Off=%s)', async (off: boolean) => {
    const user = userEvent.setup()
    mockScorerRegistry = { scorers: [], loading: true, error: null }
    const configured = off ? null : { registry_name: 'configured', identifier_hash: 'configured-hash' }
    writeUserPreferences('local', {
      ...DEFAULT_USER_PREFERENCES, lastUsedChatScorer: rememberedScorer, chatScorer: configured,
    })
    jest.mocked(attacksApi.createAttack).mockImplementation(() => new Promise(() => {}))
    renderChat()
    await prepareNewSend(user)
    await user.click(screen.getByRole('button', { name: 'Send message' }))
    expect(attacksApi.createAttack).toHaveBeenCalledWith(expect.objectContaining({ chat_scorer: configured }))
  })

  it('does not overwrite an unavailable implicit scorer as Off on a confirmed non-inherited objective send', async () => {
    const user = userEvent.setup()
    writeUserPreferences('local', { ...DEFAULT_USER_PREFERENCES, lastUsedChatScorer: rememberedScorer })
    jest.mocked(attacksApi.createAttack).mockResolvedValue({
      attack_result_id: confirmed.attack_result_id, conversation_id: confirmed.conversation_id,
      related_conversation_ids: [],
    })
    jest.mocked(attacksApi.submitMessageSend).mockResolvedValue(confirmed)
    jest.mocked(attacksApi.getAttack).mockResolvedValue({
      ...savedAttack(confirmed.attack_result_id), conversation_id: confirmed.conversation_id,
      objective: 'Goal', chat_scorer: null, chat_scorer_inherited: false,
    })
    renderChat()
    await prepareNewSend(user)
    await user.click(screen.getByRole('button', { name: 'Send message' }))
    await waitFor(() => expect(readUserPreferences('local').lastUsedTargets?.objective).toEqual(targetReference(TARGET)))
    expect(attacksApi.createAttack).toHaveBeenCalledWith(expect.not.objectContaining({ chat_scorer: null }))
    expect(readUserPreferences('local').lastUsedChatScorer).toEqual(rememberedScorer)
  })

  it('retains explicit Off through a delayed catalog update and records that actual choice', async () => {
    const user = userEvent.setup()
    mockScorerRegistry = { scorers: scorerCatalog, loading: false, error: null }
    writeUserPreferences('local', { ...DEFAULT_USER_PREFERENCES, lastUsedChatScorer: rememberedScorer })
    jest.mocked(attacksApi.createAttack).mockResolvedValue({
      attack_result_id: confirmed.attack_result_id, conversation_id: confirmed.conversation_id,
      related_conversation_ids: [],
    })
    jest.mocked(attacksApi.submitMessageSend).mockResolvedValue(confirmed)
    jest.mocked(attacksApi.getAttack).mockResolvedValue({
      ...savedAttack(confirmed.attack_result_id), conversation_id: confirmed.conversation_id,
      objective: 'Goal', chat_scorer: null, chat_scorer_inherited: false,
    })
    const rendered = render(<MemoryRouter initialEntries={['/attack/chat']}><App /></MemoryRouter>)
    await prepareNewSend(user)
    await user.click(screen.getByRole('button', { name: /objective achieved outcome/i }))
    await user.click(screen.getByRole('button', { name: /^automated scorer:/i }))
    await user.selectOptions(screen.getByRole('combobox', { name: 'Chat scorer' }), '')
    await user.keyboard('{Escape}')
    mockScorerRegistry = { scorers: [], loading: true, error: null }
    rendered.rerender(<MemoryRouter initialEntries={['/attack/chat']}><App /></MemoryRouter>)
    await user.click(screen.getByRole('button', { name: 'Send message' }))
    await waitFor(() => expect(readUserPreferences('local').lastUsedTargets?.objective).toEqual(targetReference(TARGET)))
    expect(attacksApi.createAttack).toHaveBeenCalledWith(expect.objectContaining({ chat_scorer: null }))
    expect(readUserPreferences('local').lastUsedChatScorer).toBeNull()
  })

  it('records confirmed use after navigation aborts chat evidence reads', async () => {
    const user = userEvent.setup()
    mockScorerRegistry = { scorers: scorerCatalog, loading: false, error: null }
    writeUserPreferences('local', { ...DEFAULT_USER_PREFERENCES, lastUsedChatScorer: rememberedScorer })
    const progress = deferred<MessageSendStatus>()
    const receipt = deferred<AttackSummary>()
    jest.mocked(attacksApi.createAttack).mockResolvedValue({
      attack_result_id: confirmed.attack_result_id, conversation_id: confirmed.conversation_id,
      related_conversation_ids: [],
    })
    jest.mocked(attacksApi.submitMessageSend).mockResolvedValue({
      ...confirmed, state: 'queued', request_turn_number: null,
    })
    jest.mocked(attacksApi.getMessageSend).mockReturnValue(progress.promise)
    jest.mocked(attacksApi.getAttack).mockReturnValue(receipt.promise)
    renderChat()
    await prepareNewSend(user)
    await user.click(screen.getByRole('button', { name: 'Send message' }))
    await waitFor(() => expect(attacksApi.getMessageSend).toHaveBeenCalledTimes(1))
    await visitDatasets(user)
    expect(readUserPreferences('local').lastUsedTargets).toBeUndefined()
    await act(async () => { progress.resolve(confirmed) })
    await act(async () => { receipt.resolve({
      ...savedAttack(confirmed.attack_result_id), conversation_id: confirmed.conversation_id,
      objective: 'Goal', chat_scorer: rememberedScorer, chat_scorer_inherited: false,
    }) })
    expect(readUserPreferences('local').lastUsedTargets?.objective).toEqual(targetReference(TARGET))
    expect(readUserPreferences('local').lastUsedChatScorer).toEqual(rememberedScorer)
    expect(screen.getByRole('heading', { name: 'Dataset catalog' })).toBeInTheDocument()
  })

  it.each([false, true])('should preserve an unsaved draft across repeated visits with StrictMode=%s', async (strict: boolean) => {
    const user = userEvent.setup()
    renderChat('/attack/chat', strict)
    await user.type(await screen.findByPlaceholderText('Type prompt here'), 'Unsent prompt')
    for (let visit = 0; visit < 2; visit += 1) {
      await visitDatasets(user)
      await returnToChat(user)
      expect(screen.getByPlaceholderText('Type prompt here')).toHaveValue('Unsent prompt')
    }
    expect(readUserPreferences('local').lastUsedTargets).toBeUndefined()
    expect(readUserPreferences('local').lastUsedChatScorer).toBeUndefined()
    expect(attacksApi.submitMessageSend).not.toHaveBeenCalled()
  })

  it('does not record last use when opening saved history without sending', async () => {
    renderChat('/attack/chat/saved/conversations/main')
    await waitFor(() => expect(screen.getByPlaceholderText('Type prompt here')).toBeEnabled())
    expect(readUserPreferences('local').lastUsedTargets).toBeUndefined()
    expect(readUserPreferences('local').lastUsedChatScorer).toBeUndefined()
    expect(attacksApi.submitMessageSend).not.toHaveBeenCalled()
  })

  it.each(['/does-not-exist', '/attack/chat/saved/extra'])(
    'should preserve the latest chat and draft through the fallback at %s',
    async (path: string) => {
      const user = userEvent.setup()
      const router = createMemoryRouter([{ path: '*', element: <App /> }], {
        initialEntries: ['/attack/chat'],
      })
      render(<RouterProvider router={router} />)
      const target = await screen.findByRole('combobox', { name: 'Chat target' })
      await waitFor(() => expect(target).toBeEnabled())
      await user.selectOptions(target, 'draft-target')
      await user.type(screen.getByPlaceholderText('Type prompt here'), 'Unsent prompt')
      await act(async () => { await router.navigate(path) })
      await screen.findByRole('heading', { name: 'Page not found' })
      expect(router.state.location.pathname).toBe(path)
      await user.click(screen.getByRole('link', { name: 'Home', exact: true }))
      await screen.findByRole('heading', { name: 'Home', exact: true })
      await returnToChat(user)
      expect(screen.getByPlaceholderText('Type prompt here')).toHaveValue('Unsent prompt')
      expect(screen.getByRole('combobox', { name: 'Chat target' })).toHaveValue('draft-target')
      expect(screen.getByLabelText('Current route')).toHaveTextContent(/^\/attack\/chat$/)
      expect(attacksApi.createAttack).not.toHaveBeenCalled()
      expect(attacksApi.submitMessageSend).not.toHaveBeenCalled()
    },
  )

  it('should retain the unsent chat through History and Scanner and reset only for New attack', async () => {
    const user = userEvent.setup()
    renderChat()
    const composer = await screen.findByPlaceholderText('Type prompt here')
    await user.type(composer, 'Unsent attack prompt')
    await user.click(screen.getByRole('button', { name: /add objective/i }))
    await user.type(screen.getByRole('textbox', { name: 'Attack objective' }), 'Unsent objective')
    await user.click(screen.getByRole('button', { name: 'Save', exact: true }))
    await visitHistory(user)
    await user.click(screen.getByRole('tab', { name: 'Chat', exact: true }))
    expect(await screen.findByPlaceholderText('Type prompt here')).toHaveValue('Unsent attack prompt')
    expect(screen.getByRole('button', { name: 'Edit objective' })).toHaveTextContent('Unsent objective')
    await user.click(screen.getByRole('button', { name: 'Scanner', exact: true }))
    await screen.findByRole('heading', { name: 'Scanner launch' })
    await returnToChat(user)
    expect(screen.getByPlaceholderText('Type prompt here')).toHaveValue('Unsent attack prompt')
    await user.click(screen.getByRole('button', { name: 'New attack', exact: true }))
    expect(screen.getByPlaceholderText('Type prompt here')).toHaveValue('')
    expect(screen.getByRole('button', { name: /add objective/i })).toBeInTheDocument()
    await visitDatasets(user)
    await returnToChat(user)
    expect(screen.getByPlaceholderText('Type prompt here')).toHaveValue('')
  })

  it.each(['/attack/chat', '/attack/chat/saved/conversations/main'])(
    'should let the editor block New attack before any draft reset from %s',
    async (path: string) => {
      const user = userEvent.setup()
      renderChat(path)
      await waitFor(() => expect(screen.getByPlaceholderText('Type prompt here')).toBeEnabled())
      if (path === '/attack/chat') {
        const target = screen.getByRole('combobox', { name: 'Chat target' })
        await waitFor(() => expect(target).toBeEnabled())
        await user.selectOptions(target, 'draft-target')
      }
      await user.type(screen.getByPlaceholderText('Type prompt here'), 'Original unsent prompt')
      await user.click(screen.getByRole('button', { name: 'Edit Conversation', exact: true }))
      await user.click(await screen.findByRole('button', { name: 'Insert message at start' }))
      await user.type(
        within(screen.getByRole('region', { name: 'Message 1' })).getByPlaceholderText('Type prompt here'),
        'Unsaved conversation edit',
      )

      await user.click(screen.getByRole('button', { name: 'New attack', exact: true }))
      const firstDialog = await screen.findByRole('dialog', { name: 'Discard conversation draft?' })
      await user.click(within(firstDialog).getByRole('button', { name: 'Keep editing' }))
      await screen.findByRole('region', { name: 'Message 1' })
      expect(within(screen.getByRole('region', { name: 'Message 1' }))
        .getByPlaceholderText('Type prompt here')).toHaveValue('Unsaved conversation edit')

      await user.click(await screen.findByRole('button', { name: 'Cancel', exact: true }))
      const cancelDialog = await screen.findByRole('dialog', { name: 'Discard conversation draft?' })
      await user.click(within(cancelDialog).getByRole('button', { name: 'Discard draft' }))
      await waitFor(() => expect(screen.queryByTestId('conversation-editor')).not.toBeInTheDocument())
      expect(screen.getByPlaceholderText('Type prompt here')).toHaveValue('Original unsent prompt')
      await visitHistory(user)
      await user.click(screen.getByRole('tab', { name: 'Chat', exact: true }))
      await waitFor(() => expect(screen.getByPlaceholderText('Type prompt here')).toHaveValue('Original unsent prompt'))

      await user.click(screen.getByRole('button', { name: 'Edit Conversation', exact: true }))
      await user.click(await screen.findByRole('button', { name: 'Insert message at start' }))
      await user.type(
        within(screen.getByRole('region', { name: 'Message 1' })).getByPlaceholderText('Type prompt here'),
        'Confirmed conversation edit',
      )
      await user.click(screen.getByRole('button', { name: 'New attack', exact: true }))
      const secondDialog = await screen.findByRole('dialog', { name: 'Discard conversation draft?' })
      await user.click(within(secondDialog).getByRole('button', { name: 'Discard draft' }))
      await waitFor(() => expect(screen.queryByTestId('conversation-editor')).not.toBeInTheDocument())
      expect(screen.getByPlaceholderText('Type prompt here')).toHaveValue('')
      expect(attacksApi.saveConversation).not.toHaveBeenCalled()
      expect(attacksApi.submitMessageSend).not.toHaveBeenCalled()
    },
  )

  it('should restore a saved conversation only after its attack and transcript load', async () => {
    const user = userEvent.setup()
    renderChat('/attack/chat/saved/conversations/branch')
    await waitFor(() => expect(screen.getByPlaceholderText('Type prompt here')).toBeEnabled())
    await user.type(screen.getByPlaceholderText('Type prompt here'), 'Saved attack draft')
    await user.upload(screen.getByTestId('file-input'), new File(['image'], 'draft.png', { type: 'image/png' }))
    const url = jest.mocked(URL.createObjectURL).mock.results[0].value
    await visitDatasets(user)
    const attackLoad = deferred<AttackSummary>()
    jest.mocked(attacksApi.getAttack).mockReturnValueOnce(attackLoad.promise)
    await user.click(screen.getByRole('button', { name: 'Chat', exact: true }))
    expect(screen.queryByDisplayValue('Saved attack draft')).not.toBeInTheDocument()
    await act(async () => { attackLoad.resolve(savedAttack()) })
    await waitFor(() => expect(screen.getByPlaceholderText('Type prompt here')).toBeEnabled())
    expect(screen.getByLabelText('Current route')).toHaveTextContent('/attack/chat/saved/conversations/branch')
    expect(screen.getByPlaceholderText('Type prompt here')).toHaveValue('Saved attack draft')
    expect(screen.getByRole('button', { name: 'Remove draft.png' })).toBeInTheDocument()
    expect(URL.revokeObjectURL).not.toHaveBeenCalledWith(url)
  })

  it('should keep the historical conversation shell mounted while metadata loads', async () => {
    const attackLoad = deferred<AttackSummary>()
    jest.mocked(attacksApi.getAttack).mockReturnValueOnce(attackLoad.promise)
    renderChat('/attack/chat/saved')
    const toggle = await screen.findByTestId('toggle-panel-btn')
    expect(toggle).toBeDisabled()
    expect(screen.getByText('Loading conversation...', { exact: true })).toBeInTheDocument()
    expect(screen.getByPlaceholderText('Type prompt here')).toBeDisabled()
    await act(async () => { attackLoad.resolve(savedAttack()) })
    await waitFor(() => expect(screen.getByPlaceholderText('Type prompt here')).toBeEnabled())
    expect(screen.getByTestId('toggle-panel-btn')).toBe(toggle)
  })

  it('should retain a recovered failed prompt while branching to a new attack', async () => {
    const originalValue = 'Restore this failed prompt'
    const failedMessages: BackendMessage[] = [{
      role: 'user', turn_number: 0, created_at: '',
      message_pieces: [{
        id: 'failed-prompt', original_value_data_type: 'text', original_value: originalValue,
        converted_value_data_type: 'text', converted_value: originalValue, scores: [], response_error: 'none',
      }],
    }, {
      role: 'assistant', turn_number: 1, created_at: '',
      message_pieces: [{
        id: 'failed-response', original_value_data_type: 'text', original_value: '',
        converted_value_data_type: 'text', converted_value: '', scores: [], response_error: 'processing',
        response_error_description: 'The target could not process this message.',
      }],
    }]
    jest.mocked(attacksApi.createAttack).mockResolvedValue({
      attack_result_id: 'recovered', conversation_id: 'recovered-main', created_at: '',
    })
    jest.mocked(attacksApi.getAttack).mockImplementation(async (id: string) => ({
      ...savedAttack(id),
      conversation_id: id === 'recovered' ? 'recovered-main' : 'main',
      target_unbound: false,
      target: {
        target_type: TARGET.identifier.class_name,
        target_registry_name: TARGET.target_registry_name,
        identifier_hash: TARGET.identifier.hash,
      },
    }))
    jest.mocked(attacksApi.getMessages).mockImplementation(async (id: string, conversationId: string) => ({
      conversation_id: conversationId,
      messages: id === 'saved' ? failedMessages : [],
      target_response_status: id === 'saved'
        ? { response_error: 'processing', request_turn_number: 0, response_turn_number: 1 } : null,
    }))
    const user = userEvent.setup()
    renderChat('/attack/chat/saved/conversations/main')
    await user.click(await screen.findByTestId('copy-to-input-btn-0'))
    await user.click(await screen.findByRole('menuitem', { name: 'Branch attack', exact: true }))
    await waitFor(() => expect(screen.getByLabelText('Current route')).toHaveTextContent(
      '/attack/chat/recovered/conversations/recovered-main',
    ))
    await waitFor(() => expect(screen.getByPlaceholderText('Type prompt here')).toHaveValue(originalValue))
    await visitDatasets(user)
    await returnToChat(user)
    await waitFor(() => expect(screen.getByPlaceholderText('Type prompt here')).toHaveValue(originalValue))
    expect(attacksApi.submitMessageSend).not.toHaveBeenCalled()
  })

  it('should return to retained work after clean saved-chat inspection through History', async () => {
    const user = userEvent.setup()
    renderChat()
    await user.type(await screen.findByPlaceholderText('Type prompt here'), 'Only for my unsaved chat')
    await visitHistory(user)
    await user.click(screen.getByRole('row', { name: 'Open ManualAttack attack' }))
    await waitFor(() => expect(screen.getByPlaceholderText('Type prompt here')).toBeEnabled())
    expect(screen.getByPlaceholderText('Type prompt here')).toHaveValue('')
    await visitHistory(user)
    await user.click(screen.getByRole('tab', { name: 'Chat', exact: true }))
    await waitFor(() => expect(screen.getByPlaceholderText('Type prompt here')).toHaveValue('Only for my unsaved chat'))
    expect(screen.getByLabelText('Current route')).toHaveTextContent(/^\/attack\/chat$/)
    expect(attacksApi.createAttack).not.toHaveBeenCalled()
    expect(attacksApi.submitMessageSend).not.toHaveBeenCalled()
  })

  it('should keep newer live work and return to it through History until New attack resets it', async () => {
    const user = userEvent.setup()
    renderChat()
    await user.type(await screen.findByPlaceholderText('Type prompt here'), 'Older working draft')
    await visitHistory(user)
    await user.click(screen.getByRole('row', { name: 'Open ManualAttack attack' }))
    await waitFor(() => expect(screen.getByPlaceholderText('Type prompt here')).toBeEnabled())
    await user.type(screen.getByPlaceholderText('Type prompt here'), 'Newer work in the saved chat')
    await user.click(screen.getByRole('tab', { name: 'Chat', exact: true }))
    expect(screen.getByPlaceholderText('Type prompt here')).toHaveValue('Newer work in the saved chat')
    await user.click(screen.getByRole('button', { name: 'Chat', exact: true }))
    expect(screen.getByLabelText('Current route')).toHaveTextContent(/^\/attack\/chat\/other$/)
    expect(screen.getByPlaceholderText('Type prompt here')).toHaveValue('Newer work in the saved chat')

    await visitHistory(user)
    await user.click(screen.getByRole('tab', { name: 'Chat', exact: true }))
    await waitFor(() => expect(screen.getByPlaceholderText('Type prompt here')).toHaveValue('Newer work in the saved chat'))
    expect(screen.getByLabelText('Current route')).toHaveTextContent(/^\/attack\/chat\/other$/)
    await user.click(screen.getByRole('button', { name: 'New attack', exact: true }))
    expect(screen.getByPlaceholderText('Type prompt here')).toHaveValue('')
    await visitHistory(user)
    await user.click(screen.getByRole('tab', { name: 'Chat', exact: true }))
    expect(await screen.findByPlaceholderText('Type prompt here')).toHaveValue('')
    expect(screen.getByLabelText('Current route')).toHaveTextContent(/^\/attack\/chat$/)
    expect(attacksApi.createAttack).not.toHaveBeenCalled()
    expect(attacksApi.submitMessageSend).not.toHaveBeenCalled()
  })

  it.each(['operator', 'execution'])('should not consume a draft when its destination becomes %s locked', async (lock: string) => {
    const user = userEvent.setup()
    renderChat('/attack/chat/saved/conversations/main')
    await waitFor(() => expect(screen.getByPlaceholderText('Type prompt here')).toBeEnabled())
    await user.type(screen.getByPlaceholderText('Type prompt here'), 'Keep until editable')
    await visitDatasets(user)
    const lockedAttack = lock === 'operator'
      ? { ...savedAttack(), operator: 'another-operator' }
      : { ...savedAttack(), execution_read_only: true }
    jest.mocked(attacksApi.getAttack).mockResolvedValue(lockedAttack)
    await user.click(screen.getByRole('button', { name: 'Chat', exact: true }))
    if (lock === 'operator') {
      await screen.findByTestId('operator-locked-banner')
    } else {
      await waitFor(() => expect(screen.getByPlaceholderText('Type prompt here')).toBeDisabled())
    }
    expect(screen.queryByDisplayValue('Keep until editable')).not.toBeInTheDocument()
    jest.mocked(attacksApi.getAttack).mockImplementation(async (id: string) => savedAttack(id))
    await visitDatasets(user)
    await returnToChat(user)
    await waitFor(() => expect(screen.getByPlaceholderText('Type prompt here')).toHaveValue('Keep until editable'))
  })

  it('should retain a draft when its attack is temporarily missing', async () => {
    const user = userEvent.setup()
    renderChat('/attack/chat/saved/conversations/main')
    await waitFor(() => expect(screen.getByPlaceholderText('Type prompt here')).toBeEnabled())
    await user.type(screen.getByPlaceholderText('Type prompt here'), 'Retry this destination')
    await visitDatasets(user)
    jest.mocked(attacksApi.getAttack).mockRejectedValueOnce({
      isAxiosError: true, response: { status: 404, data: { detail: 'Missing' } },
    })
    await user.click(screen.getByRole('button', { name: 'Chat', exact: true }))
    await screen.findByText('Attack not found')
    await visitDatasets(user)
    await returnToChat(user)
    await waitFor(() => expect(screen.getByPlaceholderText('Type prompt here')).toHaveValue('Retry this destination'))
  })

  it('should discard a missing destination when explicitly starting a new attack', async () => {
    const user = userEvent.setup()
    renderChat('/attack/chat/saved/conversations/main')
    await waitFor(() => expect(screen.getByPlaceholderText('Type prompt here')).toBeEnabled())
    await user.type(screen.getByPlaceholderText('Type prompt here'), 'Discard this missing destination')
    await visitDatasets(user)
    jest.mocked(attacksApi.getAttack).mockRejectedValueOnce({
      isAxiosError: true, response: { status: 404, data: { detail: 'Missing' } },
    })
    await user.click(screen.getByRole('button', { name: 'Chat', exact: true }))
    await user.click(await screen.findByRole('button', { name: 'Start a new attack' }))
    await returnToChat(user)
    expect(screen.getByLabelText('Current route')).toHaveTextContent('/attack/chat')
    expect(screen.getByPlaceholderText('Type prompt here')).toHaveValue('')
  })

  it.each([404, 500])('should retain a draft until its transcript can be loaded after HTTP %s', async (status: number) => {
    const user = userEvent.setup()
    renderChat('/attack/chat/saved/conversations/main')
    await waitFor(() => expect(screen.getByPlaceholderText('Type prompt here')).toBeEnabled())
    await user.type(screen.getByPlaceholderText('Type prompt here'), 'Retry this transcript')
    await visitDatasets(user)
    jest.mocked(attacksApi.getMessages).mockRejectedValueOnce({
      isAxiosError: true, response: { status, data: { detail: 'Transcript not ready' } },
    })
    await returnToChat(user)
    const retry = await screen.findByRole('button', { name: 'Retry conversation load' })
    expect(screen.queryByDisplayValue('Retry this transcript')).not.toBeInTheDocument()
    await user.click(retry)
    await waitFor(() => expect(screen.getByPlaceholderText('Type prompt here')).toHaveValue('Retry this transcript'))
  })

  it('should leave a draft available when another conversation in the same attack is opened', async () => {
    const user = userEvent.setup()
    renderChat('/attack/chat/saved/conversations/main')
    await waitFor(() => expect(screen.getByPlaceholderText('Type prompt here')).toBeEnabled())
    await user.type(screen.getByPlaceholderText('Type prompt here'), 'Only for the main conversation')
    await visitDatasets(user)
    await user.click(screen.getByRole('button', { name: 'Open another conversation' }))
    await waitFor(() => expect(screen.getByPlaceholderText('Type prompt here')).toBeEnabled())
    expect(screen.getByPlaceholderText('Type prompt here')).toHaveValue('')
    await visitDatasets(user)
    await returnToChat(user)
    await waitFor(() => expect(screen.getByPlaceholderText('Type prompt here')).toHaveValue('Only for the main conversation'))
    expect(screen.getByLabelText('Current route')).toHaveTextContent('/attack/chat/saved/conversations/main')
  })

  it.each([false, true])('should retain newer edits made before transcript loading with early departure=%s', async (leaveEarly: boolean) => {
    const user = userEvent.setup()
    renderChat('/attack/chat/saved/conversations/main')
    await waitFor(() => expect(screen.getByPlaceholderText('Type prompt here')).toBeEnabled())
    await user.type(screen.getByPlaceholderText('Type prompt here'), 'Older draft')
    await visitDatasets(user)
    const transcript = deferred<Awaited<ReturnType<typeof attacksApi.getMessages>>>()
    jest.mocked(attacksApi.getMessages).mockReturnValueOnce(transcript.promise)
    await returnToChat(user)
    await user.type(screen.getByPlaceholderText('Type prompt here'), 'Newer draft')
    if (leaveEarly) await visitDatasets(user)
    await act(async () => {
      transcript.resolve({ conversation_id: 'main', messages: [], target_response_status: null })
    })
    if (!leaveEarly) {
      expect(screen.getByPlaceholderText('Type prompt here')).toHaveValue('Newer draft')
      await visitDatasets(user)
    }
    await returnToChat(user)
    await waitFor(() => expect(screen.getByPlaceholderText('Type prompt here')).toHaveValue('Newer draft'))
  })

  it('should preserve target selection and system prompt without changing defaults', async () => {
    const user = userEvent.setup()
    renderChat()
    const selector = await screen.findByRole('combobox', { name: 'Chat target' })
    await waitFor(() => expect(selector).toBeEnabled())
    await user.selectOptions(selector, 'draft-target')
    await user.click(screen.getByRole('button', { name: /system prompt/i }))
    await user.type(screen.getByRole('textbox', { name: /system prompt/i }), 'System instruction')
    await visitDatasets(user)
    await returnToChat(user)
    expect(screen.getByRole('combobox', { name: 'Chat target' })).toHaveValue('draft-target')
    await user.click(screen.getByRole('button', { name: /system prompt/i }))
    expect(screen.getByRole('textbox', { name: /system prompt/i })).toHaveValue('System instruction')
  })

  it('should preserve a target selected before entering text', async () => {
    const user = userEvent.setup()
    renderChat()
    const selector = await screen.findByRole('combobox', { name: 'Chat target' })
    await waitFor(() => expect(selector).toBeEnabled())
    await user.selectOptions(selector, 'draft-target')
    await visitDatasets(user)
    await returnToChat(user)
    expect(screen.getByRole('combobox', { name: 'Chat target' })).toHaveValue('draft-target')
    expect(screen.getByPlaceholderText('Type prompt here')).toHaveValue('')
  })

  it('should preserve an explicit empty target instead of reapplying the default', async () => {
    writeUserPreferences('local', {
      ...DEFAULT_USER_PREFERENCES,
      lastUsedTargets: { objective: targetReference(TARGET), adversarial: null },
    })
    const user = userEvent.setup()
    renderChat()
    const selector = await screen.findByRole('combobox', { name: 'Chat target' })
    await waitFor(() => expect(selector).toHaveValue('draft-target'))
    await user.selectOptions(selector, '')
    await visitDatasets(user)
    await returnToChat(user)
    expect(screen.getByRole('combobox', { name: 'Chat target' })).toHaveValue('')
  })

  it.each(['', 'Unconverted text'])('should preserve an unapplied converter pipeline with text "%s"', async (text: string) => {
    const user = userEvent.setup()
    renderChat()
    await screen.findByPlaceholderText('Type prompt here')
    if (text) await user.type(screen.getByPlaceholderText('Type prompt here'), text)
    await addConverter(user)
    await visitDatasets(user)
    await returnToChat(user)
    await user.click(screen.getByRole('button', { name: 'Toggle converter panel' }))
    expect(await screen.findByRole('button', { name: 'Remove converter draft-converter' })).toBeInTheDocument()
  })

  it('should preserve manually edited converter inputs, outputs, and applied values', async () => {
    jest.mocked(convertersApi.previewConversion).mockResolvedValue(convertedText('Converted text', 'Edited working input'))
    const user = userEvent.setup()
    renderChat()
    await user.type(await screen.findByPlaceholderText('Type prompt here'), 'Original text')
    await addConverter(user)
    const working = screen.getByRole('textbox', { name: 'Working input - Text' })
    await user.clear(working)
    await user.type(working, 'Edited working input')
    await user.click(screen.getByRole('button', { name: 'Convert', exact: true }))
    const output = await screen.findByRole('textbox', { name: 'Stage 1 output - Text' })
    await user.clear(output)
    await user.type(output, 'Edited stage output')
    await visitDatasets(user)
    await returnToChat(user)
    await user.click(screen.getByRole('button', { name: 'Toggle converter panel' }))
    expect(await screen.findByRole('textbox', { name: 'Working input - Text' })).toHaveValue('Edited working input')
    expect(screen.getByRole('textbox', { name: 'Stage 1 output - Text' })).toHaveValue('Edited stage output')
    expect(screen.getByRole('textbox', { name: /converted prompt/i })).toHaveValue('Edited stage output')
    expect(screen.getByPlaceholderText('Type prompt here')).toHaveValue('Original text')
  })

  it('should ignore a conversion that completes after leaving its composer', async () => {
    const conversion = deferred<ConverterPreviewResponse>()
    jest.mocked(convertersApi.previewConversion).mockReturnValueOnce(conversion.promise)
    const user = userEvent.setup()
    renderChat()
    await user.type(await screen.findByPlaceholderText('Type prompt here'), 'Original text')
    await addConverter(user)
    await user.click(screen.getByRole('button', { name: 'Convert', exact: true }))
    await visitDatasets(user)
    await returnToChat(user)
    await act(async () => { conversion.resolve(convertedText('Late conversion')) })
    await user.click(screen.getByRole('button', { name: 'Toggle converter panel' }))
    await screen.findByRole('button', { name: 'Remove converter draft-converter' })
    expect(screen.queryByRole('textbox', { name: 'Stage 1 output - Text' })).not.toBeInTheDocument()
    expect(screen.getByPlaceholderText('Type prompt here')).toHaveValue('Original text')
  })

  it('should retain named pipelines but drop generated results after the runtime changes', async () => {
    jest.useFakeTimers()
    const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime })
    renderChat()
    await waitFor(() => expect(screen.getByPlaceholderText('Type prompt here')).toBeEnabled())
    await user.type(screen.getByPlaceholderText('Type prompt here'), 'Original text')
    await addConverter(user)
    await user.click(screen.getByRole('button', { name: 'Convert', exact: true }))
    await screen.findByRole('textbox', { name: 'Stage 1 output - Text' })
    await visitDatasets(user)
    jest.mocked(runtimeApi.getReadiness).mockResolvedValue({ ready: true, state: 'ready', generation: 'new-runtime' })
    await act(async () => { jest.advanceTimersByTime(2000) })
    await returnToChat(user)
    await user.click(screen.getByRole('button', { name: 'Toggle converter panel' }))
    await screen.findByRole('button', { name: 'Remove converter draft-converter' })
    expect(screen.queryByRole('textbox', { name: 'Stage 1 output - Text' })).not.toBeInTheDocument()
    expect(screen.getByPlaceholderText('Type prompt here')).toHaveValue('Original text')
  })

  it('should preserve text-only video metadata through editor save, datasets, and send', async () => {
    const metadata = { video_id: 'existing-video', custom: 'preserved' }
    const attack: AttackSummary = {
      ...savedAttack(),
      target_unbound: false,
      target: {
        target_type: TARGET.identifier.class_name,
        target_registry_name: TARGET.target_registry_name,
        identifier_hash: TARGET.identifier.hash,
      },
    }
    const source: BackendMessage[] = [{
      role: 'user', turn_number: 0, created_at: '',
      message_pieces: [{
        id: 'text-remix', original_value_data_type: 'text', original_value: 'Change the background',
        converted_value_data_type: 'text', converted_value: 'Change the background',
        prompt_metadata: metadata, scores: [], response_error: 'none',
      }],
    }]
    const empty = { conversation_id: 'main', messages: [], target_response_status: null }
    jest.mocked(attacksApi.getAttack).mockResolvedValue(attack)
    jest.mocked(attacksApi.getMessages).mockResolvedValue({ ...empty, messages: source })
    jest.mocked(attacksApi.saveConversation).mockResolvedValue({ attack, messages: empty })
    jest.mocked(attacksApi.submitMessageSend).mockImplementation(() => new Promise(() => {}))
    const user = userEvent.setup()
    renderChat('/attack/chat/saved/conversations/main')
    await waitFor(() => expect(screen.getByPlaceholderText('Type prompt here')).toBeEnabled())
    await user.click(screen.getByRole('button', { name: 'Edit Conversation', exact: true }))
    await screen.findByRole('region', { name: 'Message 1' })
    await user.click(screen.getByRole('button', { name: 'Save', exact: true }))
    await waitFor(() => expect(screen.getByPlaceholderText('Type prompt here')).toHaveValue('Change the background'))
    expect(attacksApi.saveConversation).toHaveBeenCalledWith(expect.objectContaining({ destination: 'same_attack' }))
    jest.mocked(attacksApi.getMessages).mockResolvedValue(empty)
    await visitDatasets(user)
    await returnToChat(user)
    await waitFor(() => expect(screen.getByPlaceholderText('Type prompt here')).toHaveValue('Change the background'))
    await user.type(screen.getByPlaceholderText('Type prompt here'), ' to blue')
    await user.click(screen.getByRole('button', { name: 'Send message', exact: true }))
    await waitFor(() => expect(attacksApi.submitMessageSend).toHaveBeenCalledWith(
      'saved', expect.objectContaining({
        pieces: [{ data_type: 'text', original_value: 'Change the background to blue', prompt_metadata: metadata }],
      }),
    ))
  })

  it('should keep cleared text empty while retaining the selected target', async () => {
    writeUserPreferences('local', {
      ...DEFAULT_USER_PREFERENCES,
      lastUsedTargets: { objective: targetReference(TARGET), adversarial: null },
    })
    const user = userEvent.setup()
    renderChat()
    await user.type(await screen.findByPlaceholderText('Type prompt here'), 'Delete this draft')
    await visitDatasets(user)
    await returnToChat(user)
    await user.clear(screen.getByPlaceholderText('Type prompt here'))
    await visitDatasets(user)
    await returnToChat(user)
    expect(screen.getByPlaceholderText('Type prompt here')).toHaveValue('')
    expect(screen.getByRole('combobox', { name: 'Chat target' })).toHaveValue('draft-target')
  })

  it('should retain uploaded media across visits and release it when explicitly removed', async () => {
    const user = userEvent.setup()
    renderChat('/attack/chat', true)
    await screen.findByPlaceholderText('Type prompt here')
    await user.upload(screen.getByTestId('file-input'), new File(['media'], 'draft.wav', { type: 'audio/wav' }))
    const url = jest.mocked(URL.createObjectURL).mock.results[0].value
    for (let visit = 0; visit < 2; visit += 1) {
      await visitDatasets(user)
      await returnToChat(user)
      expect(screen.getByRole('button', { name: 'Remove draft.wav' })).toBeInTheDocument()
      expect(URL.revokeObjectURL).not.toHaveBeenCalledWith(url)
    }
    await user.click(screen.getByRole('button', { name: 'Remove draft.wav' }))
    expect(URL.revokeObjectURL).toHaveBeenCalledWith(url)
  })

  it('should not return to a discarded saved draft after starting a new attack', async () => {
    const user = userEvent.setup()
    renderChat('/attack/chat/saved/conversations/main')
    await waitFor(() => expect(screen.getByPlaceholderText('Type prompt here')).toBeEnabled())
    await user.type(screen.getByPlaceholderText('Type prompt here'), 'Discard this saved draft')
    await user.click(screen.getByRole('button', { name: 'New attack', exact: true }))
    await user.click(screen.getByRole('button', { name: 'Chat', exact: true }))
    expect(screen.getByLabelText('Current route')).toHaveTextContent('/attack/chat')
    expect(screen.getByPlaceholderText('Type prompt here')).toHaveValue('')
  })

  it('should apply an import only once across edits and Back/Forward navigation', async () => {
    const user = userEvent.setup()
    renderChat()
    await screen.findByPlaceholderText('Type prompt here')
    await user.click(screen.getByRole('button', { name: 'Stage import' }))
    expect(screen.getByPlaceholderText('Type prompt here')).toHaveValue('Imported prompt')
    expect(screen.getByPlaceholderText('Type prompt here')).toHaveFocus()
    expect(screen.getByLabelText('Pending import')).toHaveTextContent('none')
    await user.type(screen.getByPlaceholderText('Type prompt here'), ' with my edit')
    await visitDatasets(user)
    await user.click(screen.getByRole('button', { name: 'Back', exact: true }))
    await waitFor(() => expect(screen.getByPlaceholderText('Type prompt here')).toHaveValue('Imported prompt with my edit'))
    await user.click(screen.getByRole('button', { name: 'Forward', exact: true }))
    await screen.findByRole('heading', { name: 'Dataset catalog' })
    await returnToChat(user)
    expect(screen.getByPlaceholderText('Type prompt here')).toHaveValue('Imported prompt with my edit')
  })

  it('should protect a preserved dirty draft from an import that cannot replace it', async () => {
    const user = userEvent.setup()
    renderChat()
    await user.type(await screen.findByPlaceholderText('Type prompt here'), 'Keep my original prompt')
    await visitDatasets(user)
    await user.click(screen.getByRole('button', { name: 'Stage protected import' }))
    await returnToChat(user)
    await waitFor(() => expect(screen.getByPlaceholderText('Type prompt here')).toHaveValue('Keep my original prompt'))
    expect(screen.getByLabelText('Pending import')).toHaveTextContent('waiting')
  })

  it('should apply an import only to its exact saved conversation', async () => {
    const user = userEvent.setup()
    renderChat('/attack/chat/saved/conversations/branch')
    await waitFor(() => expect(screen.getByPlaceholderText('Type prompt here')).toBeEnabled())
    await user.click(screen.getByRole('button', { name: 'Stage saved import' }))
    expect(screen.getByPlaceholderText('Type prompt here')).toHaveValue('')
    expect(screen.getByLabelText('Pending import')).toHaveTextContent('waiting')
    await user.click(screen.getByRole('button', { name: 'Open saved main' }))
    await waitFor(() => expect(screen.getByPlaceholderText('Type prompt here')).toHaveValue('Import for saved main'))
    expect(screen.getByLabelText('Pending import')).toHaveTextContent('none')
  })

  it.each([false, true])('should release owned media when the App closes with a preserved snapshot=%s', async (preserve: boolean) => {
    const user = userEvent.setup()
    const rendered = renderChat('/attack/chat', true)
    await screen.findByPlaceholderText('Type prompt here')
    await user.upload(screen.getByTestId('file-input'), new File(['image'], 'owned.png', { type: 'image/png' }))
    const url = jest.mocked(URL.createObjectURL).mock.results[0].value
    if (preserve) await visitDatasets(user)
    expect(URL.revokeObjectURL).not.toHaveBeenCalledWith(url)
    rendered.unmount()
    expect(URL.revokeObjectURL).toHaveBeenCalledWith(url)
  })

  it('should retain dataset return state and cursor history across the round trip', async () => {
    const user = userEvent.setup()
    render(<MemoryRouter initialEntries={[{
      pathname: '/datasets/detail', search: '?selection_key=example-dataset&cursor=page-2',
      state: { catalogPath: '/datasets?q=example&sort=name', seedCursors: [null], cursorGeneration: '' },
    }]}><App /></MemoryRouter>)
    await screen.findByRole('heading', { name: 'Example dataset' })
    await waitFor(() => expect(screen.getByRole('button', { name: 'Previous page' })).toBeEnabled())
    await returnToChat(user)
    await user.click(screen.getByRole('button', { name: 'Datasets', exact: true }))
    await screen.findByRole('heading', { name: 'Example dataset' })
    expect(screen.getByRole('link', { name: 'Back to datasets' })).toHaveAttribute('href', '/datasets?q=example&sort=name')
    expect(screen.getByRole('button', { name: 'Previous page' })).toBeEnabled()
  })
})
