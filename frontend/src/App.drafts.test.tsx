import { StrictMode, type ReactNode } from 'react'

import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent, { type UserEvent } from '@testing-library/user-event'
import { createMemoryRouter, MemoryRouter } from 'react-router'
import { RouterProvider } from 'react-router/dom'

import { attacksApi, convertersApi, datasetsApi, runtimeApi, targetsApi } from '@/services/api'
import { deferred } from '@/test-utils/deferred'
import { makeTarget } from '@/test-utils/targetFixtures'
import { installRouterTestRequest } from '@/test-utils/routerRequest'
import { computeAttachmentSignature } from '@/utils/chatDraft'
import type {
  AttackSummary, BackendMessage, ConverterInstance, ConverterPreviewResponse, MessageSendStatus, SaveConversationRequest, ScorerInstance,
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
        <button onClick={() => handoff.stagePendingImport({
          destinationType: 'new_attack',
          content: { text: '' },
          conversation: {
            messages: [
              { id: 'm1', role: 'user', pieces: [{ draftId: 'p1', data_type: 'text', original_value: 'Imported user turn' }] },
              { id: 'm2', role: 'simulated_assistant', pieces: [{ draftId: 'p2', data_type: 'text', original_value: 'Imported assistant turn' }] },
            ],
            objective: 'Imported test objective',
          },
          replaceDirty: true,
        })}>Stage conversation import</button>
        <button onClick={() => handoff.stagePendingImport({
          destinationType: 'new_attack', content: { text: '' }, replaceDirty: true,
          conversation: { messages: [
            { id: 'import-system', role: 'system', pieces: [{ draftId: 'system-piece', data_type: 'text', original_value: 'Imported instructions' }] },
            { id: 'import-user', role: 'user', pieces: [{ draftId: 'user-piece', data_type: 'text', original_value: 'Imported history' }] },
          ] },
        })}>Stage system import</button>
        <button onClick={() => handoff.stagePendingImport({
          destinationType: 'current_conversation',
          targetAttackId: 'saved',
          targetConversationId: 'main',
          content: { text: 'Unsent user prompt' },
          conversation: {
            messages: [
              { id: 'm-prepended', role: 'simulated_assistant', pieces: [{ draftId: 'p-prep', data_type: 'text', original_value: 'Prepended assistant turn' }] },
            ],
            objective: 'Saved objective',
          },
          replaceDirty: true,
        })}>Stage saved conversation import</button>
        <button onClick={() => handoff.stagePendingImport({
          destinationType: 'current_conversation',
          targetAttackId: 'saved',
          targetConversationId: 'main',
          content: {
            text: 'Turn C prompt',
            attachments: [{ draftId: 'att-c', type: 'image', name: 'img-c.png', url: 'blob:img-c', mimeType: 'image/png' }],
          },
          conversation: {
            messages: [
              { id: 'm-a', role: 'user', pieces: [{ draftId: 'p-a', data_type: 'text', original_value: 'Turn A message' }] },
              { id: 'm-b', role: 'simulated_assistant', pieces: [{ draftId: 'p-b', data_type: 'text', original_value: 'Turn B message' }] },
            ],
            composerDraft: {
              text: 'Turn C prompt',
              attachments: [{ draftId: 'att-c', type: 'image', name: 'img-c.png', url: 'blob:img-c', mimeType: 'image/png', metadata: { version: '1' } }],
              textMetadata: { video_id: 'vid-c' },
            },
            objective: 'Imported objective B',
          },
          replaceDirty: true,
        })}>Stage A-B-C with attachment import</button>
        <button onClick={() => handoff.stagePendingImport({
          destinationType: 'current_conversation',
          targetAttackId: 'saved',
          targetConversationId: 'main',
          content: { text: '' },
          conversation: {
            messages: [
              { id: 'm-a2', role: 'user', pieces: [{ draftId: 'p-a2', data_type: 'text', original_value: 'Turn A only' }] },
              { id: 'm-b2', role: 'simulated_assistant', pieces: [{ draftId: 'p-b2', data_type: 'text', original_value: 'Turn B assistant' }] },
            ],
          },
          replaceDirty: true,
        })}>Stage ending in assistant import</button>
        <button onClick={() => handoff.stagePendingImport({
          destinationType: 'current_conversation',
          targetAttackId: 'saved',
          targetConversationId: 'main',
          content: { text: '' },
          conversation: {
            messages: [
              { id: 'm-h1', role: 'user', pieces: [{ draftId: 'p-h1', data_type: 'text', original_value: 'History user 1' }] },
              { id: 'm-h2', role: 'simulated_assistant', pieces: [{ draftId: 'p-h2', data_type: 'text', original_value: 'History assistant 2' }] },
              { id: 'm-h3', role: 'user', pieces: [{ draftId: 'p-h3', data_type: 'text', original_value: 'History user 3' }] },
            ],
          },
          replaceDirty: true,
        })}>Stage history ending in user message import</button>
        <button onClick={() => handoff.stagePendingImport({
          destinationType: 'current_conversation',
          targetAttackId: 'saved',
          targetConversationId: 'main',
          content: { text: '' },
          conversation: {
            messages: [
              { id: 'm-no-obj', role: 'user', pieces: [{ draftId: 'p-no-obj', data_type: 'text', original_value: 'No obj turn' }] },
            ],
          },
          replaceDirty: true,
        })}>Stage no-objective import</button>
        <button onClick={() => {
          handoff.preserveDraft({
            originatingAttackId: 'saved',
            originatingConversationId: 'main',
            draft: {
              text: 'Preserved with missing target',
              attachments: [],
              conversation: {
                messages: [{ id: 'm1', role: 'user', pieces: [{ draftId: 'p1', data_type: 'text', original_value: 'Hello' }] }],
                target: { target_type: 'NonExistent', target_registry_name: 'missing', identifier_hash: '999' },
                objective: 'Test objective',
              },
            },
          })
        }}>Preserve draft with missing target</button>
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
    createConversation: jest.fn(),
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
    getSeedExample: jest.fn(),
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
    related_conversation_ids: ['branch', 'new-conv-id'], target: null, target_unbound: true,
    created_at: '', updated_at: '',
  }
}

function savedMessages(request: SaveConversationRequest): BackendMessage[] {
  return request.messages.map((message, index: number) => ({
    role: message.role,
    turn_number: index,
    created_at: '',
    message_pieces: message.pieces.map((piece, pieceIndex: number) => ({
      id: `saved-${index}-${pieceIndex}`,
      original_value: piece.original_value,
      original_value_data_type: piece.data_type,
      converted_value: piece.converted_value ?? piece.original_value,
      converted_value_data_type: piece.converted_value_data_type ?? piece.data_type,
      prompt_metadata: piece.prompt_metadata,
      scores: [],
      response_error: 'none',
    })),
  }))
}

function renderChat(path = '/attack/chat', strict = false): ReturnType<typeof render> {
  const router = createMemoryRouter([{ path: '*', element: <App /> }], { initialEntries: [path] })
  const app = <RouterProvider router={router} />
  return render(strict ? <StrictMode>{app}</StrictMode> : app)
}

async function visitDatasets(user: UserEvent): Promise<void> {
  await user.click(await screen.findByRole('button', { name: 'Datasets', exact: true }))
  await screen.findByRole('heading', { name: 'Datasets' })
}

async function returnToChat(user: UserEvent): Promise<void> {
  await user.click(await screen.findByRole('button', { name: 'Chat', exact: true }))
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

async function selectDraftTarget(user: UserEvent): Promise<void> {
  const picker = await screen.findByRole('combobox', { name: 'Objective target' })
  await waitFor(() => expect(picker).toBeEnabled())
  await user.selectOptions(picker, TARGET.target_registry_name)
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
    jest.mocked(attacksApi.createConversation).mockResolvedValue({
      conversation_id: 'new-conv-id',
      attack_result_id: 'saved',
      created_at: '2026-01-01T00:00:00Z',
    })
    jest.mocked(convertersApi.listConverters).mockResolvedValue({ items: [CONVERTER] })
    jest.mocked(convertersApi.previewConversion).mockResolvedValue(convertedText())
    jest.mocked(datasetsApi.listDatasets).mockResolvedValue({
      items: [
        {
          name: 'Example dataset', selection_key: 'example-dataset',
          loaded: true, provider_available: true, logical_examples: 2, seed_pieces: 2, objectives: 0,
          modalities: ['text'], harm_categories: [], has_unlabeled_harm_categories: true,
        },
        {
          name: 'jailbreak_templates', selection_key: 'dataset:named:jailbreak_templates',
          loaded: true, provider_available: true, logical_examples: 1, seed_pieces: 1, objectives: 0,
          modalities: ['text'], harm_categories: [], has_unlabeled_harm_categories: false,
        },
      ],
    })
    jest.mocked(datasetsApi.listSeedExamples).mockResolvedValue({
      items: [{
        example_id: 'wikipedia_with_title',
        name: 'wikipedia with title',
        preview: 'Write a wikipedia article on {{ prompt }}. Start the article with "Title:".',
        preview_truncated: false,
        modalities: ['text'],
        seed_types: ['prompt'],
        piece_count: 1,
        objective_count: 0,
        harm_categories: [],
        has_unlabeled_harm: false,
      }],
      total: 1,
      pagination: { limit: 20, has_more: false, next_cursor: null, prev_cursor: null },
    })
    jest.mocked(datasetsApi.getSeedExample).mockResolvedValue({
      example_id: 'wikipedia_with_title',
      name: 'wikipedia with title',
      preview: 'Write a wikipedia article on {{ prompt }}. Start the article with "Title:".',
      preview_truncated: false,
      modalities: ['text'],
      seed_types: ['prompt'],
      piece_count: 1,
      objective_count: 0,
      harm_categories: [],
      has_unlabeled_harm: false,
      members: [{
        id: 'member-wiki-1',
        seed_type: 'prompt',
        value: 'Write a wikipedia article on {{ prompt }}. Start the article with "Title:".',
        value_sha256: 'abc123',
        data_type: 'text',
        name: 'wikipedia with title',
        dataset_name: 'jailbreak_templates',
        origin: 'local',
        harm_categories: [],
        description: 'Template prompt',
        authors: ['PyRIT team'],
        groups: [],
        source: 'jailbreak_templates',
        date_added: '2026-01-01T00:00:00Z',
        added_by: 'tester',
        metadata: null,
        prompt_group_id: null,
        sequence: 0,
        role: 'user',
        parameters: ['prompt'],
        conditions: [],
        response_json_schema: null,
      }],
    })
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
    const picker = await screen.findByRole('combobox', { name: 'Objective target' })
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
    await user.click(screen.getByRole('button', { name: /^objective achieved outcome:/i }))
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
    expect(screen.getByRole('heading', { name: 'Datasets' })).toBeInTheDocument()
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
      const target = await screen.findByRole('combobox', { name: 'Objective target' })
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
      expect(screen.getByRole('combobox', { name: 'Objective target' })).toHaveValue('draft-target')
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
        const target = screen.getByRole('combobox', { name: 'Objective target' })
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
      expect(within(await screen.findByRole('region', { name: 'Message 1' }))
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
    const selector = await screen.findByRole('combobox', { name: 'Objective target' })
    await waitFor(() => expect(selector).toBeEnabled())
    await user.selectOptions(selector, 'draft-target')
    await user.click(screen.getByRole('button', { name: /system prompt/i }))
    await user.type(screen.getByRole('textbox', { name: /system prompt/i }), 'System instruction')
    await visitDatasets(user)
    await returnToChat(user)
    expect(screen.getByRole('combobox', { name: 'Objective target' })).toHaveValue('draft-target')
    await user.click(screen.getByRole('button', { name: /system prompt/i }))
    expect(screen.getByRole('textbox', { name: /system prompt/i })).toHaveValue('System instruction')
  })

  it('should preserve a target selected before entering text', async () => {
    const user = userEvent.setup()
    renderChat()
    const selector = await screen.findByRole('combobox', { name: 'Objective target' })
    await waitFor(() => expect(selector).toBeEnabled())
    await user.selectOptions(selector, 'draft-target')
    await visitDatasets(user)
    await returnToChat(user)
    expect(screen.getByRole('combobox', { name: 'Objective target' })).toHaveValue('draft-target')
    expect(screen.getByPlaceholderText('Type prompt here')).toHaveValue('')
  })

  it('should preserve an explicit empty target instead of reapplying the default', async () => {
    writeUserPreferences('local', {
      ...DEFAULT_USER_PREFERENCES,
      lastUsedTargets: { objective: targetReference(TARGET), adversarial: null },
    })
    const user = userEvent.setup()
    renderChat()
    const selector = await screen.findByRole('combobox', { name: 'Objective target' })
    await waitFor(() => expect(selector).toHaveValue('draft-target'))
    await user.selectOptions(selector, '')
    await visitDatasets(user)
    await returnToChat(user)
    expect(screen.getByRole('combobox', { name: 'Objective target' })).toHaveValue('')
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
    expect(screen.getByRole('combobox', { name: 'Objective target' })).toHaveValue('draft-target')
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
    await screen.findByRole('heading', { name: 'Datasets' })
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

  it('should consume a conversation import and open ConversationEditor', async () => {
    const user = userEvent.setup()
    renderChat('/attack/chat')
    await waitFor(() => expect(screen.getByPlaceholderText('Type prompt here')).toBeEnabled())
    await user.click(screen.getByRole('button', { name: 'Stage conversation import' }))
    expect(screen.getByLabelText('Pending import')).toHaveTextContent('none')
    await waitFor(() => expect(screen.getByText('Imported user turn')).toBeInTheDocument())
    expect(screen.getByText('Imported assistant turn')).toBeInTheDocument()
  })

  it('should populate composer with template prompt after visiting datasets from clean chat through real UI (Roman journey)', async () => {
    const user = userEvent.setup()
    renderChat('/attack/chat')
    await waitFor(() => expect(screen.getByPlaceholderText('Type prompt here')).toBeEnabled())
    expect(screen.getByPlaceholderText('Type prompt here')).toHaveValue('')

    // 1. User visits datasets from clean chat
    await visitDatasets(user)
    await screen.findByRole('heading', { name: 'Datasets' })

    // 2. User selects jailbreak_templates dataset
    await user.click(await screen.findByRole('link', { name: 'jailbreak_templates' }))
    await screen.findByRole('heading', { name: 'jailbreak_templates' })

    // 3. User selects the wikipedia with title example
    await user.click(await screen.findByRole('button', { name: /Show wikipedia with title/i }))
    await screen.findByRole('heading', { level: 2, name: 'wikipedia with title' })

    // 4. User clicks real Import into Chat -> Import to new attack
    await user.click(screen.getByTestId('import-into-chat-button'))
    const importItem = await screen.findByTestId('import-new-attack-item')
    await user.click(importItem)

    // 5. App navigates back to Chat
    await waitFor(() => expect(screen.getByPlaceholderText('Type prompt here')).toBeEnabled())

    // 6. Assertions:
    // - The prompt box contains the template text
    expect(screen.getByPlaceholderText('Type prompt here')).toHaveValue(
      'Write a wikipedia article on {{ prompt }}. Start the article with "Title:".',
    )
    // - No prompt was sent
    expect(attacksApi.submitMessageSend).not.toHaveBeenCalled()
    // - Pending import was consumed exactly once
    expect(screen.getByLabelText('Pending import')).toHaveTextContent('none')
  })

  it('should retain system setup and an unapplied pipeline while importing a single prompt objective', async () => {
    jest.useFakeTimers()
    const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime })
    const detail = await datasetsApi.getSeedExample('wikipedia_with_title', 'dataset:named:jailbreak_templates')
    jest.mocked(datasetsApi.getSeedExample).mockResolvedValue({
      ...detail, members: [...detail.members, { ...detail.members[0], id: 'objective', seed_type: 'objective', value: 'Imported single objective' }],
    })
    renderChat()
    await selectDraftTarget(user)
    await user.click(screen.getByRole('button', { name: 'System Prompt', exact: true }))
    await user.type(screen.getByRole('textbox', { name: 'System prompt', exact: true }), 'Destination instructions')
    await addConverter(user)
    await visitDatasets(user)
    await user.click(await screen.findByRole('link', { name: 'jailbreak_templates' }))
    await user.click(await screen.findByRole('button', { name: /Show wikipedia with title/i }))
    await user.click(await screen.findByRole('button', { name: 'Import into Chat', exact: true }))
    await user.click(screen.getByTestId('import-new-attack-item'))
    await user.click(await screen.findByRole('button', { name: 'Replace', exact: true }))
    await act(async () => { jest.advanceTimersByTime(1000) })
    await waitFor(() => expect(screen.getByPlaceholderText('Type prompt here')).toHaveValue(detail.members[0].value))
    expect(screen.getByTestId('objective-header-content')).toHaveTextContent('Imported single objective')
    await user.click(await screen.findByRole('button', { name: 'System Prompt', exact: true }))
    expect(screen.getByRole('textbox', { name: 'System prompt', exact: true })).toHaveValue('Destination instructions')
    await user.click(screen.getByRole('button', { name: 'Toggle converter panel' }))
    expect(await screen.findByRole('button', { name: 'Remove converter draft-converter' })).toBeVisible()
    expect(screen.queryByRole('textbox', { name: 'Stage 1 output - Text' })).not.toBeInTheDocument()
    expect(attacksApi.submitMessageSend).not.toHaveBeenCalled()
  })

  it.each(['destination', 'imported'] as const)('should explicitly reconcile %s setup before consuming an import', async (choice: 'destination' | 'imported') => {
    const user = userEvent.setup()
    renderChat()
    await selectDraftTarget(user)
    await user.click(screen.getByRole('button', { name: 'System Prompt', exact: true }))
    await user.type(screen.getByRole('textbox', { name: 'System prompt', exact: true }), 'Destination instructions')
    await user.click(screen.getByRole('button', { name: 'Stage system import' }))
    expect(await screen.findByText(/different system or developer instructions/)).toBeVisible()
    expect(screen.getByLabelText('Pending import')).toHaveTextContent('waiting')
    expect(screen.queryByTestId('conversation-editor')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', {
      name: choice === 'destination' ? 'Keep destination setup' : 'Use imported setup',
    }))
    const imported = await screen.findByTestId('conversation-editor')
    expect(within(imported).getByDisplayValue(choice === 'destination' ? 'Destination instructions' : 'Imported instructions')).toBeVisible()
    expect(within(imported).getAllByRole('region')).toHaveLength(2)
    expect(screen.getByLabelText('Pending import')).toHaveTextContent('none')
  })

  it('should retain a template imported directly from datasets when StrictMode replays the first chat mount', async () => {
    const user = userEvent.setup()
    renderChat('/datasets/detail?selection_key=dataset%3Anamed%3Ajailbreak_templates&example=wikipedia_with_title', true)
    await screen.findByRole('heading', { level: 2, name: 'wikipedia with title' })
    await user.click(screen.getByTestId('import-into-chat-button'))
    await user.click(screen.getByTestId('import-new-attack-item'))
    await waitFor(() => expect(screen.getByPlaceholderText('Type prompt here')).toHaveValue(
      'Write a wikipedia article on {{ prompt }}. Start the article with "Title:".',
    ))
    await user.click(screen.getByRole('button', { name: 'Datasets', exact: true }))
    await screen.findByRole('heading', { level: 1, name: 'jailbreak_templates' })
    await returnToChat(user)
    expect(screen.getByPlaceholderText('Type prompt here')).toHaveValue(
      'Write a wikipedia article on {{ prompt }}. Start the article with "Title:".',
    )
  })

  it.each(['assistant', 'tool', 'trace'] as const)('should require a new destination rather than silently branching over %s evidence', async (evidence: 'assistant' | 'tool' | 'trace') => {
    const user = userEvent.setup()
    jest.mocked(attacksApi.getMessages).mockResolvedValue({
      conversation_id: 'main', target_response_status: null, messages: [{
        role: evidence === 'trace' ? 'user' : evidence, turn_number: 0, created_at: '',
        message_pieces: [{
          id: 'sent-piece', original_value_data_type: 'text', original_value: 'Sent evidence',
          converted_value_data_type: 'text', converted_value: 'Sent evidence',
          prompt_metadata: evidence === 'trace' ? { pyrit_request_trace: { version: 1 } } : {},
          scores: [], response_error: 'none',
        }],
      }],
    })
    renderChat('/attack/chat/saved/conversations/main')
    await waitFor(() => expect(screen.getByPlaceholderText('Type prompt here')).toBeEnabled())
    await user.click(screen.getByRole('button', { name: 'Stage saved conversation import' }))
    expect(await screen.findByText(/This conversation contains sent evidence/)).toBeVisible()
    expect(screen.getByLabelText('Pending import')).toHaveTextContent('waiting')
    expect(screen.queryByTestId('conversation-editor')).not.toBeInTheDocument()
    expect(attacksApi.saveConversation).not.toHaveBeenCalled()
  })

  it('should handle dirty draft confirmation for new_conversation import (cancel vs confirm)', async () => {
    const user = userEvent.setup()
    // Start at a saved attack conversation
    renderChat('/attacks/saved/conversations/main')
    await waitFor(() => expect(screen.getByPlaceholderText('Type prompt here')).toBeEnabled())

    // User types a dirty draft
    await user.type(screen.getByPlaceholderText('Type prompt here'), 'Unsaved attack message')

    // User visits datasets
    await visitDatasets(user)
    await user.click(await screen.findByRole('link', { name: 'jailbreak_templates' }))
    await user.click(await screen.findByRole('button', { name: /Show wikipedia with title/i }))

    // User attempts to import to new conversation
    await user.click(screen.getByTestId('import-into-chat-button'))
    const newConvItem = await screen.findByTestId('import-new-conversation-item')
    await user.click(newConvItem)

    // Confirmation dialog appears because destination attack has a dirty draft
    expect(await screen.findByRole('dialog', { name: 'Replace Existing Draft?' })).toBeInTheDocument()

    // 1. Clicking Cancel leaves the dirty draft and stays on datasets without creating conversation
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Replace Existing Draft?' })).not.toBeInTheDocument())
    expect(attacksApi.createConversation).not.toHaveBeenCalled()

    // Return to chat: original dirty draft is intact!
    await returnToChat(user)
    await waitFor(() => expect(screen.getByPlaceholderText('Type prompt here')).toHaveValue('Unsaved attack message'))

    // 2. Now user visits datasets again and confirms replacement
    await user.click(await screen.findByRole('button', { name: 'Datasets', exact: true }))
    await screen.findByRole('heading', { name: 'jailbreak_templates' })
    await user.click(await screen.findByTestId('import-into-chat-button'))
    await user.click(await screen.findByTestId('import-new-conversation-item'))

    expect(await screen.findByRole('dialog', { name: 'Replace Existing Draft?' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Replace' }))
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Replace Existing Draft?' })).not.toBeInTheDocument())

    // New conversation is created, navigated, and populated with template prompt
    await waitFor(() => expect(attacksApi.createConversation).toHaveBeenCalledWith('saved', {}))
    await waitFor(() => expect(screen.getByPlaceholderText('Type prompt here')).toHaveValue(
      'Write a wikipedia article on {{ prompt }}. Start the article with "Title:".',
    ))
    expect(screen.getByLabelText('Current route')).toHaveTextContent('/attack/chat/saved/conversations/new-conv-id')
  })

  it('should import to new_conversation immediately without confirmation when draft is clean', async () => {
    const user = userEvent.setup()
    renderChat('/attacks/saved/conversations/main')
    await waitFor(() => expect(screen.getByPlaceholderText('Type prompt here')).toBeEnabled())
    expect(screen.getByPlaceholderText('Type prompt here')).toHaveValue('')

    await visitDatasets(user)
    await user.click(await screen.findByRole('link', { name: 'jailbreak_templates' }))
    await user.click(await screen.findByRole('button', { name: /Show wikipedia with title/i }))

    await user.click(screen.getByTestId('import-into-chat-button'))
    await user.click(await screen.findByTestId('import-new-conversation-item'))

    // No dialog shown; proceeds directly
    expect(screen.queryByRole('dialog', { name: 'Replace Existing Draft?' })).not.toBeInTheDocument()
    await waitFor(() => expect(attacksApi.createConversation).toHaveBeenCalledWith('saved', {}))
    await waitFor(() => expect(screen.getByPlaceholderText('Type prompt here')).toHaveValue(
      'Write a wikipedia article on {{ prompt }}. Start the article with "Title:".',
    ))
    expect(screen.getByLabelText('Current route')).toHaveTextContent('/attack/chat/saved/conversations/new-conv-id')
  })

  it('should preserve destination existing messages and append imported history after them in current_conversation', async () => {
    const user = userEvent.setup()
    const backendMsgs = [
      {
        role: 'user',
        turn_number: 0,
        created_at: '',
        message_pieces: [
          {
            id: 'p-orig-1',
            original_value_data_type: 'text',
            original_value: 'Existing user message',
            converted_value_data_type: 'text',
            converted_value: 'Existing user message',
            scores: [],
            response_error: 'none',
          },
        ],
      },
      {
        role: 'simulated_assistant',
        turn_number: 1,
        created_at: '',
        message_pieces: [
          {
            id: 'p-orig-2',
            original_value_data_type: 'text',
            original_value: 'Existing assistant message',
            converted_value_data_type: 'text',
            converted_value: 'Existing assistant message',
            scores: [],
            response_error: 'none',
          },
        ],
      },
    ]
    jest.mocked(attacksApi.getMessages).mockResolvedValueOnce({
      conversation_id: 'main',
      messages: backendMsgs,
      target_response_status: null,
    })
    jest.mocked(attacksApi.saveConversation).mockImplementation(async (request: SaveConversationRequest) => ({
      attack: savedAttack(), messages: { conversation_id: 'main', messages: savedMessages(request), target_response_status: null },
    }))

    renderChat('/attacks/saved/conversations/main')
    await waitFor(() => expect(screen.getByPlaceholderText('Type prompt here')).toBeEnabled())

    await user.click(screen.getByRole('button', { name: 'Stage saved conversation import' }))

    // Existing messages are loaded and preserved first, imported assistant turn appended
    await waitFor(() => expect(screen.getByText('Existing user message')).toBeInTheDocument())
    expect(screen.getByText('Existing assistant message')).toBeInTheDocument()
    expect(screen.getByText('Prepended assistant turn')).toBeInTheDocument()

    // Assert exact order: existing user message first, then existing assistant, then imported turn
    const messageBodies = screen.getAllByRole('region').filter((r) => r.getAttribute('aria-label')?.startsWith('Message '))
    expect(messageBodies).toHaveLength(4)
    expect(within(messageBodies[0]).getByText('Existing user message')).toBeInTheDocument()
    expect(within(messageBodies[1]).getByText('Existing assistant message')).toBeInTheDocument()
    expect(within(messageBodies[2]).getByText('Prepended assistant turn')).toBeInTheDocument()

    // And composer contains the unsent user prompt
    expect(within(messageBodies[3]).getByDisplayValue('Unsent user prompt')).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Save', exact: true }))
    await waitFor(() => expect(attacksApi.saveConversation).toHaveBeenCalledWith(expect.objectContaining({
      source_conversation_id: 'main', reuse_unsent_conversation: true,
      expected_source_piece_ids: ['p-orig-1', 'p-orig-2'],
    })))
  })

  it('should display error and preserve pending import if history loading fails for current_conversation', async () => {
    const user = userEvent.setup()
    renderChat('/attacks/saved/conversations/main')
    await waitFor(() => expect(screen.getByPlaceholderText('Type prompt here')).toBeEnabled())

    jest.mocked(attacksApi.getMessages).mockRejectedValueOnce({
      isAxiosError: true,
      response: { status: 500, data: { detail: 'Backend database unavailable' } },
    })

    await user.click(screen.getByRole('button', { name: 'Stage saved conversation import' }))

    // Error message appears in the editor
    await waitFor(() => expect(screen.getByText('Backend database unavailable')).toBeInTheDocument())
    // Pending import is preserved (not consumed)
    expect(screen.getByLabelText('Pending import')).toHaveTextContent('waiting')
  })

  it('should display explicit error when conversation target is no longer available in the registry', async () => {
    const user = userEvent.setup()
    renderChat('/attacks/saved/conversations/branch')
    await waitFor(() => expect(screen.getByPlaceholderText('Type prompt here')).toBeEnabled())

    await user.click(screen.getByRole('button', { name: 'Preserve draft with missing target' }))

    // Navigate to saved main where the draft was preserved
    await user.click(screen.getByRole('button', { name: 'Open saved main' }))

    await waitFor(() => {
      expect(
        screen.getByText("The conversation's configured target is no longer available in the registry. Select an available target."),
      ).toBeInTheDocument()
    })
  })

  describe('Final user turn preservation during Import -> Save', () => {
    it('does not rebind an explicitly cleared imported editor target after a datasets round-trip', async () => {
      const user = userEvent.setup()
      renderChat()
      await selectDraftTarget(user)
      await user.click(screen.getByRole('button', { name: 'Stage conversation import' }))
      await screen.findByTestId('conversation-editor')
      await user.selectOptions(screen.getByRole('combobox', { name: 'Objective target' }), '')
      await visitDatasets(user)
      await user.click(await screen.findByRole('button', { name: 'Chat', exact: true }))
      await screen.findByTestId('conversation-editor')
      expect(screen.getByRole('combobox', { name: 'Objective target' })).toHaveValue('')
    })

    it('does not move an earlier history user turn to the composer after the pending turn is deleted', async () => {
      const user = userEvent.setup()
      jest.mocked(attacksApi.saveConversation).mockImplementation(async (request: SaveConversationRequest) => ({
        attack: savedAttack(), messages: { conversation_id: 'main', messages: savedMessages(request), target_response_status: null },
      }))
      renderChat('/attack/chat/saved/conversations/main')
      await waitFor(() => expect(screen.getByPlaceholderText('Type prompt here')).toBeEnabled())
      await user.click(screen.getByRole('button', { name: 'Stage A-B-C with attachment import' }))
      await user.click(await screen.findByRole('button', { name: 'Delete message 3' }))
      await user.click(screen.getByRole('button', { name: 'Delete message 2' }))
      await user.click(screen.getByRole('button', { name: 'Save', exact: true }))
      await waitFor(() => expect(attacksApi.saveConversation).toHaveBeenCalledWith(expect.objectContaining({
        messages: [{ role: 'user', pieces: [expect.objectContaining({ original_value: 'Turn A message' })] }],
      })))
      await waitFor(() => expect(screen.queryByTestId('conversation-editor')).not.toBeInTheDocument())
      expect(screen.getByPlaceholderText('Type prompt here')).toHaveValue('')
    })

    it('preserves user C with attachment after Save, without duplicating C in history', async () => {
      const user = userEvent.setup()
      let savedPayload: SaveConversationRequest | null = null
      jest.mocked(attacksApi.saveConversation).mockImplementation(async (req) => {
        savedPayload = req
        return {
          attack: { ...savedAttack('saved'), objective: req.objective ?? '' },
          messages: { conversation_id: 'main', messages: savedMessages(req), target_response_status: null },
        }
      })

      renderChat('/attacks/saved/conversations/main')
      await waitFor(() => expect(screen.getByPlaceholderText('Type prompt here')).toBeEnabled())

      // Stage import: User A -> Assistant B -> User C (with attachment on C)
      await user.click(screen.getByRole('button', { name: 'Stage A-B-C with attachment import' }))

      // ConversationEditor is opened
      await waitFor(() => expect(screen.getByTestId('conversation-editor')).toBeInTheDocument())

      // The final unsent turn is visible and editable before saving history.
      const editorElement = screen.getByTestId('conversation-editor')
      expect(within(editorElement).getByText('Turn A message')).toBeInTheDocument()
      expect(within(editorElement).getByText('Turn B message')).toBeInTheDocument()
      const finalPrompt = within(editorElement).getByDisplayValue('Turn C prompt')
      expect(finalPrompt).toBeVisible()
      await user.clear(finalPrompt)
      await user.type(finalPrompt, 'Edited C prompt')

      // Click Save in editor
      await user.click(screen.getByRole('button', { name: 'Save' }))

      // Verify save API payload
      await waitFor(() => expect(attacksApi.saveConversation).toHaveBeenCalled())
      expect(savedPayload).not.toBeNull()
      // History sent to backend has only turns A and B: Turn C is NOT duplicated in saved history
      expect(savedPayload!.messages).toHaveLength(2)
      expect(savedPayload!.messages[0].pieces[0].original_value).toBe('Turn A message')
      expect(savedPayload!.messages[1].pieces[0].original_value).toBe('Turn B message')
      expect(savedPayload).toMatchObject({
        source_conversation_id: 'main', reuse_unsent_conversation: true, expected_source_piece_ids: [],
      })

      // Editor closes, visible composer has Turn C with its attachment
      await waitFor(() => expect(screen.queryByTestId('conversation-editor')).not.toBeInTheDocument())
      expect(screen.getByPlaceholderText('Type prompt here')).toHaveValue('Edited C prompt')
      expect(screen.getByRole('button', { name: 'Remove img-c.png' })).toBeInTheDocument()
    })

    it('does not create a false pending composer prompt when conversation ends in assistant content', async () => {
      const user = userEvent.setup()
      let savedPayload: SaveConversationRequest | null = null
      jest.mocked(attacksApi.saveConversation).mockImplementation(async (req) => {
        savedPayload = req
        return {
          attack: { ...savedAttack('saved'), objective: req.objective ?? '' },
          messages: { conversation_id: 'main', messages: savedMessages(req), target_response_status: null },
        }
      })

      renderChat('/attacks/saved/conversations/main')
      await waitFor(() => expect(screen.getByPlaceholderText('Type prompt here')).toBeEnabled())

      await user.click(screen.getByRole('button', { name: 'Stage ending in assistant import' }))
      await waitFor(() => expect(screen.getByTestId('conversation-editor')).toBeInTheDocument())

      await user.click(screen.getByRole('button', { name: 'Save' }))
      await waitFor(() => expect(attacksApi.saveConversation).toHaveBeenCalled())
      expect(savedPayload!.messages).toHaveLength(2)

      // Composer is empty
      await waitFor(() => expect(screen.queryByTestId('conversation-editor')).not.toBeInTheDocument())
      expect(screen.getByPlaceholderText('Type prompt here')).toHaveValue('')
      expect(screen.queryByRole('button', { name: /Remove/i })).not.toBeInTheDocument()
    })

    it('does not mistake an earlier history user message for a pending prompt when editor history ends in user', async () => {
      const user = userEvent.setup()
      let savedPayload: SaveConversationRequest | null = null
      jest.mocked(attacksApi.saveConversation).mockImplementation(async (req) => {
        savedPayload = req
        return {
          attack: { ...savedAttack('saved'), objective: req.objective ?? '' },
          messages: { conversation_id: 'main', messages: savedMessages(req), target_response_status: null },
        }
      })

      renderChat('/attacks/saved/conversations/main')
      await waitFor(() => expect(screen.getByPlaceholderText('Type prompt here')).toBeEnabled())

      // There is no separate pending prompt in this imported history.
      await user.click(screen.getByRole('button', { name: 'Stage history ending in user message import' }))
      await waitFor(() => expect(screen.getByTestId('conversation-editor')).toBeInTheDocument())

      await user.click(screen.getByRole('button', { name: 'Save' }))
      await waitFor(() => expect(attacksApi.saveConversation).toHaveBeenCalled())

      // History sent to backend includes all 3 messages (including 'History user 3')
      expect(savedPayload!.messages).toHaveLength(3)
      expect(savedPayload!.messages[2].pieces[0].original_value).toBe('History user 3')

      // Composer is empty (History user 3 was NOT sliced out into the composer)
      await waitFor(() => expect(screen.queryByTestId('conversation-editor')).not.toBeInTheDocument())
      expect(screen.getByPlaceholderText('Type prompt here')).toHaveValue('')
    })

    it('preserves the visible final user turn across navigation and restores it on save', async () => {
      const user = userEvent.setup()
      let savedPayload: SaveConversationRequest | null = null
      jest.mocked(attacksApi.saveConversation).mockImplementation(async (req) => {
        savedPayload = req
        return {
          attack: { ...savedAttack('saved'), objective: req.objective ?? '' },
          messages: { conversation_id: 'main', messages: savedMessages(req), target_response_status: null },
        }
      })

      renderChat('/attacks/saved/conversations/main')
      await waitFor(() => expect(screen.getByPlaceholderText('Type prompt here')).toBeEnabled())

      // 1. Stage an A/B/C import where C includes an attachment
      await user.click(screen.getByRole('button', { name: 'Stage A-B-C with attachment import' }))

      // 2. Open the ConversationEditor
      await waitFor(() => expect(screen.getByTestId('conversation-editor')).toBeInTheDocument())
      expect(within(screen.getByTestId('conversation-editor')).getByText('Turn A message')).toBeInTheDocument()
      expect(within(screen.getByTestId('conversation-editor')).getByText('Turn B message')).toBeInTheDocument()

      // 3. Navigate away before saving
      await visitDatasets(user)

      // 4. Navigate back to original destination
      await returnToChat(user)

      // 5. Verify the editor is restored
      await waitFor(() => expect(screen.getByTestId('conversation-editor')).toBeInTheDocument())
      expect(within(screen.getByTestId('conversation-editor')).getByText('Turn A message')).toBeInTheDocument()
      expect(within(screen.getByTestId('conversation-editor')).getByText('Turn B message')).toBeInTheDocument()

      // 6. Save the editor
      await user.click(screen.getByRole('button', { name: 'Save' }))
      await waitFor(() => expect(attacksApi.saveConversation).toHaveBeenCalled())

      // 7. Verify:
      // - A and B are saved as history
      // - C is not duplicated in saved history
      expect(savedPayload).not.toBeNull()
      expect(savedPayload!.messages).toHaveLength(2)
      expect(savedPayload!.messages[0].pieces[0].original_value).toBe('Turn A message')
      expect(savedPayload!.messages[1].pieces[0].original_value).toBe('Turn B message')

      // - C and its attachment appear in the visible composer after Save
      await waitFor(() => expect(screen.queryByTestId('conversation-editor')).not.toBeInTheDocument())
      expect(screen.getByPlaceholderText('Type prompt here')).toHaveValue('Turn C prompt')
      expect(screen.getByRole('button', { name: 'Remove img-c.png' })).toBeInTheDocument()
    })

    it('does not restore C when imported editor is intentionally discarded', async () => {
      const user = userEvent.setup()

      renderChat('/attacks/saved/conversations/main')
      await waitFor(() => expect(screen.getByPlaceholderText('Type prompt here')).toBeEnabled())

      // Stage A-B-C import
      await user.click(screen.getByRole('button', { name: 'Stage A-B-C with attachment import' }))
      await waitFor(() => expect(screen.getByTestId('conversation-editor')).toBeInTheDocument())

      // Discard/Cancel the editor
      await user.click(screen.getByRole('button', { name: 'Cancel' }))
      await user.click(await screen.findByRole('button', { name: 'Discard draft' }))

      // Editor closes, C is not restored to composer
      await waitFor(() => expect(screen.queryByTestId('conversation-editor')).not.toBeInTheDocument())
      expect(screen.getByPlaceholderText('Type prompt here')).toHaveValue('')
      expect(screen.queryByRole('button', { name: 'Remove img-c.png' })).not.toBeInTheDocument()

      // Navigating away and back does not restore C
      await visitDatasets(user)
      await returnToChat(user)
      expect(screen.queryByTestId('conversation-editor')).not.toBeInTheDocument()
      expect(screen.getByPlaceholderText('Type prompt here')).toHaveValue('')
      expect(screen.queryByRole('button', { name: 'Remove img-c.png' })).not.toBeInTheDocument()
    })
  })

  describe('ConversationEditor navigation and dirty-draft protection', () => {
    it('opens discard confirmation when navigating away with a dirty normal editor and keep-editing preserves the editor', async () => {
      const user = userEvent.setup()
      const source: BackendMessage[] = [{
        turn_number: 0,
        role: 'user',
        created_at: '2026-01-01T00:00:00Z',
        message_pieces: [{
          id: 'p1', original_value_data_type: 'text', original_value: 'Original turn 1',
          converted_value_data_type: 'text', converted_value: 'Original turn 1', scores: [], response_error: 'none',
        }],
      }]
      jest.mocked(attacksApi.getMessages).mockResolvedValue({ conversation_id: 'main', messages: source, target_response_status: null })

      renderChat('/attacks/saved/conversations/main')
      await waitFor(() => expect(screen.getByPlaceholderText('Type prompt here')).toBeEnabled())

      // Open normal editor
      await user.click(screen.getByRole('button', { name: 'Edit Conversation', exact: true }))
      await screen.findByRole('region', { name: 'Message 1' })

      // Edit message to make editor dirty
      const promptInput = screen.getAllByPlaceholderText('Type prompt here')[0]
      await user.type(promptInput, ' modified')

      // Attempt navigation to Datasets
      await user.click(await screen.findByRole('button', { name: 'Datasets', exact: true }))

      // Confirmation dialog opens
      expect(await screen.findByRole('dialog', { name: 'Discard conversation draft?' })).toBeInTheDocument()
      expect(screen.getByText('Your unsaved changes will be lost.')).toBeInTheDocument()

      // Choosing "Keep editing" preserves the editor and unsaved edits
      await user.click(screen.getByRole('button', { name: 'Keep editing' }))
      expect(screen.queryByRole('dialog', { name: 'Discard conversation draft?' })).not.toBeInTheDocument()
      expect(screen.getByTestId('conversation-editor')).toBeInTheDocument()
      expect(screen.queryByRole('heading', { name: 'Datasets' })).not.toBeInTheDocument()
      expect(screen.getByDisplayValue('Original turn 1 modified')).toBeInTheDocument()
    })

    it('completes navigation and clears the draft when choosing Discard draft from normal dirty editor', async () => {
      const user = userEvent.setup()
      const source: BackendMessage[] = [{
        turn_number: 0,
        role: 'user',
        created_at: '2026-01-01T00:00:00Z',
        message_pieces: [{
          id: 'p1', original_value_data_type: 'text', original_value: 'Original turn 1',
          converted_value_data_type: 'text', converted_value: 'Original turn 1', scores: [], response_error: 'none',
        }],
      }]
      jest.mocked(attacksApi.getMessages).mockResolvedValue({ conversation_id: 'main', messages: source, target_response_status: null })

      renderChat('/attacks/saved/conversations/main')
      await waitFor(() => expect(screen.getByPlaceholderText('Type prompt here')).toBeEnabled())

      // Open normal editor and edit
      await user.click(screen.getByRole('button', { name: 'Edit Conversation', exact: true }))
      await screen.findByRole('region', { name: 'Message 1' })
      const promptInput = screen.getAllByPlaceholderText('Type prompt here')[0]
      await user.type(promptInput, ' modified')

      // Attempt navigation to Datasets
      await user.click(await screen.findByRole('button', { name: 'Datasets', exact: true }))
      expect(await screen.findByRole('dialog', { name: 'Discard conversation draft?' })).toBeInTheDocument()

      // Choosing "Discard draft" completes navigation to Datasets
      await user.click(screen.getByRole('button', { name: 'Discard draft' }))
      await screen.findByRole('heading', { name: 'Datasets' })

      // Navigating back to Chat confirms editor is closed and dirty draft was cleared
      await returnToChat(user)
      expect(screen.queryByTestId('conversation-editor')).not.toBeInTheDocument()
      expect(screen.getByText('Original turn 1')).toBeInTheDocument()
      expect(screen.queryByText('Original turn 1 modified')).not.toBeInTheDocument()
    })

    it('preserves imported conversation edits across navigation without losing unsaved content', async () => {
      const user = userEvent.setup()
      renderChat('/attacks/saved/conversations/main')
      await waitFor(() => expect(screen.getByPlaceholderText('Type prompt here')).toBeEnabled())

      // Stage A-B-C import
      await user.click(screen.getByRole('button', { name: 'Stage A-B-C with attachment import' }))
      await waitFor(() => expect(screen.getByTestId('conversation-editor')).toBeInTheDocument())
      expect(within(screen.getByTestId('conversation-editor')).getByText('Turn A message')).toBeInTheDocument()

      // Edit Turn A inside the imported conversation
      const promptInput = within(screen.getByRole('region', { name: 'Message 1' })).getByPlaceholderText('Type prompt here')
      await user.type(promptInput, ' edited by user')

      // Navigate away to Datasets (does not lose draft, preserves in draftHandoff)
      await visitDatasets(user)

      // Navigate back to Chat
      await returnToChat(user)

      // Verify the editor is restored with user's edits preserved
      await waitFor(() => expect(screen.getByTestId('conversation-editor')).toBeInTheDocument())
      expect(screen.getByDisplayValue('Turn A message edited by user')).toBeInTheDocument()
      expect(within(screen.getByTestId('conversation-editor')).getByText('Turn B message')).toBeInTheDocument()
    })

    it('preserves and restores a new composer draft created after cancelling a clean editor', async () => {
      const user = userEvent.setup()
      const source: BackendMessage[] = [{
        turn_number: 0,
        role: 'user',
        created_at: '2026-01-01T00:00:00Z',
        message_pieces: [{
          id: 'p1', original_value_data_type: 'text', original_value: 'Initial message',
          converted_value_data_type: 'text', converted_value: 'Initial message', scores: [], response_error: 'none',
        }],
      }]
      jest.mocked(attacksApi.getMessages).mockResolvedValue({ conversation_id: 'main', messages: source, target_response_status: null })

      renderChat('/attacks/saved/conversations/main')
      await waitFor(() => expect(screen.getByPlaceholderText('Type prompt here')).toBeEnabled())

      // 1. Open editor
      await user.click(screen.getByRole('button', { name: 'Edit Conversation', exact: true }))
      await screen.findByRole('region', { name: 'Message 1' })

      // 2. Cancel/discard clean editor without editing
      await user.click(screen.getByRole('button', { name: 'Cancel' }))
      await waitFor(() => expect(screen.queryByTestId('conversation-editor')).not.toBeInTheDocument())

      // 3. Create a new composer draft
      const composer = screen.getByPlaceholderText('Type prompt here')
      await user.type(composer, 'Prompt after cancelling editor')

      // 4. Navigate away to Datasets
      await visitDatasets(user)

      // 5. Navigate back to Chat
      await returnToChat(user)

      // 6. Verify the new draft was preserved and is restored
      await waitFor(() => expect(screen.getByPlaceholderText('Type prompt here')).toBeEnabled())
      expect(screen.getByPlaceholderText('Type prompt here')).toHaveValue('Prompt after cancelling editor')
    })

    it('preserves and restores a new composer draft created after discarding a dirty editor', async () => {
      const user = userEvent.setup()
      const source: BackendMessage[] = [{
        turn_number: 0,
        role: 'user',
        created_at: '2026-01-01T00:00:00Z',
        message_pieces: [{
          id: 'p1', original_value_data_type: 'text', original_value: 'Initial message',
          converted_value_data_type: 'text', converted_value: 'Initial message', scores: [], response_error: 'none',
        }],
      }]
      jest.mocked(attacksApi.getMessages).mockResolvedValue({ conversation_id: 'main', messages: source, target_response_status: null })

      renderChat('/attacks/saved/conversations/main')
      await waitFor(() => expect(screen.getByPlaceholderText('Type prompt here')).toBeEnabled())

      // 1. Open editor and modify message
      await user.click(screen.getByRole('button', { name: 'Edit Conversation', exact: true }))
      await screen.findByRole('region', { name: 'Message 1' })
      const promptInput = screen.getAllByPlaceholderText('Type prompt here')[0]
      await user.type(promptInput, ' dirty edit')

      // 2. Discard dirty editor on-screen
      await user.click(screen.getByRole('button', { name: 'Cancel' }))
      await user.click(await screen.findByRole('button', { name: 'Discard draft' }))
      await waitFor(() => expect(screen.queryByTestId('conversation-editor')).not.toBeInTheDocument())

      // 3. Create a new composer draft
      const composer = screen.getByPlaceholderText('Type prompt here')
      await user.type(composer, 'Prompt after discarding dirty editor')

      // 4. Navigate away to Datasets
      await visitDatasets(user)

      // 5. Navigate back to Chat
      await returnToChat(user)

      // 6. Verify the new draft was preserved and is restored
      await waitFor(() => expect(screen.getByPlaceholderText('Type prompt here')).toBeEnabled())
      expect(screen.getByPlaceholderText('Type prompt here')).toHaveValue('Prompt after discarding dirty editor')
    })
  })

  describe('Imported objective baseline and Save payload', () => {
    it('sets initialObjective to persisted objective A and objective to imported B, sending both in Save payload', async () => {
      const user = userEvent.setup()
      let savedPayload: SaveConversationRequest | null = null
      jest.mocked(attacksApi.getAttack).mockImplementation(async (id: string) => ({
        ...savedAttack(id),
        objective: 'Objective A',
      }))
      jest.mocked(attacksApi.saveConversation).mockImplementation(async (req) => {
        savedPayload = req
        return {
          attack: { ...savedAttack('saved'), objective: req.objective ?? 'Objective A' },
          messages: { conversation_id: 'main', messages: [], target_response_status: null },
        }
      })

      renderChat('/attacks/saved/conversations/main')
      await waitFor(() => expect(screen.getByPlaceholderText('Type prompt here')).toBeEnabled())

      // Import with Objective B into attack with Objective A
      await user.click(screen.getByRole('button', { name: 'Stage A-B-C with attachment import' }))
      await waitFor(() => expect(screen.getByTestId('conversation-editor')).toBeInTheDocument())

      // Editor displays proposed objective B in ObjectiveHeader
      expect(screen.getByTestId('objective-header-content')).toHaveTextContent('Imported objective B')

      // Save to same attack
      await user.click(screen.getByRole('button', { name: 'Save' }))
      await waitFor(() => expect(attacksApi.saveConversation).toHaveBeenCalled())

      // Verify payload
      expect(savedPayload).not.toBeNull()
      expect(savedPayload!.expected_objective).toBe('Objective A')
      expect(savedPayload!.objective).toBe('Imported objective B')

      // UI updates to B and does not silently revert to A
      await waitFor(() => expect(screen.queryByTestId('conversation-editor')).not.toBeInTheDocument())
      expect(screen.getByTestId('objective-header-content')).toHaveTextContent('Imported objective B')
      expect(screen.queryByText('Objective A')).not.toBeInTheDocument()
    })

    it('retains destination objective A when imported example has no objective', async () => {
      const user = userEvent.setup()
      let savedPayload: SaveConversationRequest | null = null
      jest.mocked(attacksApi.getAttack).mockImplementation(async (id: string) => ({
        ...savedAttack(id),
        objective: 'Objective A',
      }))
      jest.mocked(attacksApi.saveConversation).mockImplementation(async (req) => {
        savedPayload = req
        return {
          attack: { ...savedAttack('saved'), objective: req.objective ?? 'Objective A' },
          messages: { conversation_id: 'main', messages: [], target_response_status: null },
        }
      })

      renderChat('/attacks/saved/conversations/main')
      await waitFor(() => expect(screen.getByPlaceholderText('Type prompt here')).toBeEnabled())

      await user.click(screen.getByRole('button', { name: 'Stage no-objective import' }))
      await waitFor(() => expect(screen.getByTestId('conversation-editor')).toBeInTheDocument())

      // ObjectiveHeader displays Objective A
      expect(screen.getByTestId('objective-header-content')).toHaveTextContent('Objective A')

      await user.click(screen.getByRole('button', { name: 'Save' }))
      await waitFor(() => expect(attacksApi.saveConversation).toHaveBeenCalled())

      // Neither objective nor expected_objective should be sent since objective did not change
      expect(savedPayload!.expected_objective).toBeUndefined()
      expect(savedPayload!.objective).toBeUndefined()
      await waitFor(() => expect(screen.queryByTestId('conversation-editor')).not.toBeInTheDocument())
      expect(screen.getByTestId('objective-header-content')).toHaveTextContent('Objective A')
    })
  })

  describe('Guard async history-loading continuation', () => {
    it('does not overwrite typed text or cache stale history while an import is loading', async () => {
      const user = userEvent.setup()
      renderChat('/attacks/saved/conversations/main')
      await waitFor(() => expect(screen.getByPlaceholderText('Type prompt here')).toBeEnabled())

      const getMessagesDeferred = deferred<Awaited<ReturnType<typeof attacksApi.getMessages>>>()
      jest.mocked(attacksApi.getMessages).mockReturnValue(getMessagesDeferred.promise)

      // Stage current conversation import (triggers async getMessages)
      await user.click(screen.getByRole('button', { name: 'Stage saved conversation import' }))
      await waitFor(() => expect(attacksApi.getMessages).toHaveBeenCalled())

      // While getMessages is in flight, user types into composer
      const composer = screen.getByPlaceholderText('Type prompt here')
      await user.type(composer, 'My newly typed text')
      expect(composer).toHaveValue('My newly typed text')

      // Resolve getMessages
      await act(async () => {
        getMessagesDeferred.resolve({
          conversation_id: 'main', target_response_status: null,
          messages: [{
            turn_number: 0, role: 'user', created_at: '2026-01-01T00:00:00Z',
            message_pieces: [{
              id: 'stale-piece', original_value_data_type: 'text', original_value: 'Stale history',
              converted_value_data_type: 'text', converted_value: 'Stale history', scores: [], response_error: 'none',
            }],
          }],
        })
      })

      // Newly typed prompt remains intact, not overwritten
      expect(screen.getByPlaceholderText('Type prompt here')).toHaveValue('My newly typed text')
      // Pending import was not consumed (remains waiting)
      expect(screen.getByLabelText('Pending import')).toHaveTextContent('waiting')
      expect(screen.queryByTestId('conversation-editor')).not.toBeInTheDocument()

      jest.mocked(attacksApi.getMessages).mockResolvedValue({
        conversation_id: 'main', messages: [], target_response_status: null,
      })
      const requestCount = jest.mocked(attacksApi.getMessages).mock.calls.length
      await user.clear(composer)
      await screen.findByTestId('conversation-editor')
      expect(attacksApi.getMessages).toHaveBeenCalledTimes(requestCount + 1)
      expect(screen.queryByDisplayValue('Stale history')).not.toBeInTheDocument()
    })

    it('Test B: does not apply import to new destination when destination switches while history is loading', async () => {
      const user = userEvent.setup()
      renderChat('/attacks/saved/conversations/main')
      await waitFor(() => expect(screen.getByPlaceholderText('Type prompt here')).toBeEnabled())

      const getMessagesDeferred = deferred<Awaited<ReturnType<typeof attacksApi.getMessages>>>()
      jest.mocked(attacksApi.getMessages).mockReturnValue(getMessagesDeferred.promise)

      // Stage current conversation import for main (triggers async getMessages)
      await user.click(screen.getByRole('button', { name: 'Stage saved conversation import' }))

      // User switches to branch conversation before getMessages resolves
      await user.click(screen.getByRole('button', { name: 'Open another conversation' }))
      await waitFor(() => expect(screen.getByLabelText('Current route')).toHaveTextContent('/attack/chat/saved/conversations/branch'))

      // Resolve getMessages for main
      await act(async () => {
        getMessagesDeferred.resolve({ conversation_id: 'main', messages: [], target_response_status: null })
      })

      // Branch conversation is unaffected, editor not opened, pending import not applied
      expect(screen.queryByTestId('conversation-editor')).not.toBeInTheDocument()
      expect(screen.getByPlaceholderText('Type prompt here')).toHaveValue('')
      expect(screen.getByLabelText('Pending import')).toHaveTextContent('waiting')
    })

    it('Test C: ignores superseded import when a newer import is staged during request', async () => {
      const user = userEvent.setup()
      renderChat('/attacks/saved/conversations/main')
      await waitFor(() => expect(screen.getByPlaceholderText('Type prompt here')).toBeEnabled())

      const getMessagesDeferred1 = deferred<Awaited<ReturnType<typeof attacksApi.getMessages>>>()
      jest.mocked(attacksApi.getMessages).mockReturnValueOnce(getMessagesDeferred1.promise)

      // Trigger import 1 (which calls getMessages with deferred promise)
      await user.click(screen.getByRole('button', { name: 'Stage saved conversation import' }))

      // Stage import 2 (supersedes import 1 for saved main)
      await user.click(screen.getByRole('button', { name: 'Stage saved import' }))

      // Resolve import 1
      await act(async () => {
        getMessagesDeferred1.resolve({ conversation_id: 'main', messages: [], target_response_status: null })
      })

      // Import 1 did not apply. Only import 2 applies (single prompt 'Import for saved main')
      await waitFor(() => expect(screen.getByPlaceholderText('Type prompt here')).toHaveValue('Import for saved main'))
      expect(screen.queryByTestId('conversation-editor')).not.toBeInTheDocument()
      expect(screen.getByLabelText('Pending import')).toHaveTextContent('none')
    })

    it('does not overwrite composer or consume import when user replaces attachment with same count during request', async () => {
      const user = userEvent.setup()
      renderChat('/attacks/saved/conversations/main')
      await waitFor(() => expect(screen.getByPlaceholderText('Type prompt here')).toBeEnabled())

      // Upload initial attachment
      await user.upload(screen.getByTestId('file-input'), new File(['initial'], 'initial.png', { type: 'image/png' }))
      await waitFor(() => expect(screen.getByRole('button', { name: 'Remove initial.png' })).toBeInTheDocument())

      const getMessagesDeferred = deferred<Awaited<ReturnType<typeof attacksApi.getMessages>>>()
      jest.mocked(attacksApi.getMessages).mockReturnValue(getMessagesDeferred.promise)

      // Stage current conversation import (triggers async getMessages)
      await user.click(screen.getByRole('button', { name: 'Stage saved conversation import' }))
      await waitFor(() => expect(attacksApi.getMessages).toHaveBeenCalled())

      // Replace attachment while getMessages is in flight: remove initial, upload replacement (array length stays 1)
      await user.click(screen.getByRole('button', { name: 'Remove initial.png' }))
      await user.upload(screen.getByTestId('file-input'), new File(['replacement'], 'replacement.png', { type: 'image/png' }))
      await waitFor(() => expect(screen.getByRole('button', { name: 'Remove replacement.png' })).toBeInTheDocument())

      // Resolve getMessages
      await act(async () => {
        getMessagesDeferred.resolve({ conversation_id: 'main', messages: [], target_response_status: null })
      })

      // Replacement attachment remains intact, editor is not opened, import not consumed
      expect(screen.queryByTestId('conversation-editor')).not.toBeInTheDocument()
      expect(screen.getByRole('button', { name: 'Remove replacement.png' })).toBeInTheDocument()
      expect(screen.getByLabelText('Pending import')).toHaveTextContent('waiting')
    })

    it('computeAttachmentSignature accurately distinguishes metadata changes and identical attachments', () => {
      const att1 = {
        draftId: 'd1', url: 'blob:url1', name: 'img.png', mimeType: 'image/png', type: 'image' as const, size: 100, metadata: { custom: 'v1' },
      }
      const att1Identical = { ...att1 }
      const att1SameMetaDifferentKeyOrder = {
        ...att1,
        metadata: { a: '1', b: '2' },
      }
      const att1ReverseKeyOrder = {
        ...att1,
        metadata: { b: '2', a: '1' },
      }
      const att1ModifiedMeta = {
        ...att1,
        metadata: { custom: 'v2' },
      }
      const att2 = {
        draftId: 'd2', url: 'blob:url2', name: 'img2.png', mimeType: 'image/png', type: 'image' as const, size: 200,
      }

      // Identical attachment produces identical signature (no unnecessary revision advance)
      expect(computeAttachmentSignature([att1])).toBe(computeAttachmentSignature([att1Identical]))
      expect(computeAttachmentSignature([att1SameMetaDifferentKeyOrder])).toBe(computeAttachmentSignature([att1ReverseKeyOrder]))

      // Changing metadata changes signature even when array count and all other fields are identical
      expect(computeAttachmentSignature([att1])).not.toBe(computeAttachmentSignature([att1ModifiedMeta]))

      // Replacing attachment changes signature even when array count is identical
      expect(computeAttachmentSignature([att1])).not.toBe(computeAttachmentSignature([att2]))

      // Empty or undefined
      expect(computeAttachmentSignature([])).toBe('')
      expect(computeAttachmentSignature(undefined)).toBe('')
    })
  })
})
