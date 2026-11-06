import { act, renderHook } from '@testing-library/react'

import { attacksApi } from '@/services/api'
import { deferred } from '@/test-utils/deferred'
import type {
  AddMessageResponse, AttackSummary, ChatConverterController, ConversationMessagesResponse, Message,
} from '@/types'

import { useConversationCopy } from './useConversationCopy'
import type { UseConversationCopyOptions } from './useConversationCopy'

jest.mock('@/services/api', () => ({
  attacksApi: { getMessages: jest.fn(), saveConversation: jest.fn(), createAttack: jest.fn() },
}))

const source: ConversationMessagesResponse = {
  conversation_id: 'main',
  messages: [0, 1, 2].map((turn: number) => ({
    turn_number: turn, role: turn % 2 ? 'assistant' : 'user', message_pieces: [], created_at: '',
  })),
  target_response_status: null,
}
const attack: AttackSummary = {
  attack_result_id: 'copied-attack', conversation_id: 'copy', attack_type: 'ManualAttack', objective: 'Objective',
  converters: [], message_count: 2, related_conversation_ids: [], labels: {}, created_at: '', updated_at: '',
}
const saved: AddMessageResponse = {
  attack, messages: { conversation_id: 'copy', messages: [], target_response_status: null },
}
const converters = {
  isConverting: false,
  snapshot: () => ({ generation: 'g1', pipelines: {}, workingInputs: {}, stageResults: {}, errors: {}, applied: {} }),
} as unknown as ChatConverterController
const messages = [0, 1, 2].map((turn: number): Message => ({ role: turn % 2 ? 'assistant' : 'user', content: `${turn}`, timestamp: '' }))

function renderCopy(overrides: Partial<UseConversationCopyOptions> = {}) {
  const options: UseConversationCopyOptions = {
    attackResultId: 'attack',
    viewedConversationId: 'main',
    messages,
    activeTarget: null,
    targetResolutionStatus: 'unbound',
    objective: 'Objective',
    labels: { operator: 'me' },
    runtimeGeneration: 'g1',
    runtimeReady: true,
    defaultsReady: true,
    scorerSelectionReady: true,
    executionReadOnly: false,
    isMutationLocked: false,
    isBusy: false,
    converters,
    promptRestoreDisabledReason: () => undefined,
    getViewedConversationId: () => 'main',
    getViewedAttackId: () => 'attack',
    getNavigationRevision: () => 0,
    getDraftRevision: () => 1,
    getLaunchState: () => ({ generation: 'g1', ready: true, defaultsReady: true, scorerSelectionReady: true }),
    getConverters: () => converters,
    setBusy: jest.fn(),
    setEditorError: jest.fn(),
    setEditorNotice: jest.fn(),
    recoverProcessingError: jest.fn().mockResolvedValue(undefined),
    restoreRecoverableDraft: jest.fn(),
    restoreEditorPrompt: jest.fn(),
    clearUndoableSend: jest.fn(),
    showConversation: jest.fn(),
    refreshPanel: jest.fn(),
    openPanel: jest.fn(),
    navigate: jest.fn(),
    onSelectConversation: jest.fn(),
    onConversationCreated: jest.fn(),
    onAttackChange: jest.fn(),
    ...overrides,
  }
  return { options, ...renderHook((props: UseConversationCopyOptions) => useConversationCopy(props), { initialProps: options }) }
}

describe('useConversationCopy', () => {
  const getMessages = jest.mocked(attacksApi.getMessages)
  const saveConversation = jest.mocked(attacksApi.saveConversation)

  beforeEach(() => {
    jest.resetAllMocks()
    getMessages.mockResolvedValue(source)
    saveConversation.mockResolvedValue(saved)
  })

  it('should copy to a new conversation in the same attack and select it', async () => {
    const { result, options } = renderCopy()

    await act(async () => { await result.current.copyConversation(1, 'same_attack') })

    expect(getMessages).toHaveBeenCalledWith('attack', 'main')
    expect(saveConversation).toHaveBeenCalledWith(expect.objectContaining({
      destination: 'same_attack', attack_result_id: 'attack', source_conversation_id: 'main',
    }))
    expect(saveConversation.mock.calls[0][0].messages).toHaveLength(2)
    expect(options.onSelectConversation).toHaveBeenCalledWith('copy')
    expect(options.onConversationCreated).not.toHaveBeenCalled()
    expect(options.onAttackChange).toHaveBeenCalledWith(attack)
    expect(options.refreshPanel).toHaveBeenCalled()
    expect(options.openPanel).toHaveBeenCalled()
    expect(options.setBusy).toHaveBeenLastCalledWith(false)
  })

  it('should branch into a new attack and report the created conversation', async () => {
    const { result, options } = renderCopy()

    await act(async () => { await result.current.copyConversation(2, 'new_attack') })

    expect(saveConversation).toHaveBeenCalledWith(expect.objectContaining({ destination: 'new_attack', objective: 'Objective' }))
    expect(options.onConversationCreated).toHaveBeenCalledWith('copied-attack', 'copy', 'Objective', null)
    expect(options.onSelectConversation).not.toHaveBeenCalled()
  })

  it('should send technique copies to the technique runner without a target', async () => {
    const { result, options } = renderCopy()

    await act(async () => { await result.current.copyConversation(1, 'technique') })

    expect(saveConversation.mock.calls[0][0].target_registry_name).toBeUndefined()
    expect(options.navigate).toHaveBeenCalledWith('/scanner/launch/technique.execute?prepended_conversation_id=copy')
    expect(options.onConversationCreated).not.toHaveBeenCalled()
  })

  it('should ignore a second copy while one is in flight', async () => {
    const load = deferred<ConversationMessagesResponse>()
    getMessages.mockReturnValueOnce(load.promise)
    const { result } = renderCopy()

    let first!: Promise<void>
    act(() => { first = result.current.copyConversation(1, 'same_attack') })
    await act(async () => { await result.current.copyConversation(1, 'new_attack') })
    await act(async () => { load.resolve(source); await first })

    expect(getMessages).toHaveBeenCalledTimes(1)
    expect(saveConversation).toHaveBeenCalledTimes(1)
  })

  it('should not navigate when the user left the source conversation during the copy', async () => {
    const load = deferred<ConversationMessagesResponse>()
    getMessages.mockReturnValueOnce(load.promise)
    let viewed = 'main'
    const { result, options } = renderCopy({ getViewedConversationId: () => viewed })

    let pending!: Promise<void>
    act(() => { pending = result.current.copyConversation(1, 'same_attack') })
    viewed = 'other'
    await act(async () => { load.resolve(source); await pending })

    expect(options.onSelectConversation).not.toHaveBeenCalled()
    expect(options.openPanel).not.toHaveBeenCalled()
  })

  it('should not copy into the same attack while mutations are locked', async () => {
    const { result } = renderCopy({ isMutationLocked: true })

    await act(async () => { await result.current.copyConversation(1, 'same_attack') })

    expect(getMessages).not.toHaveBeenCalled()
  })

  it('should not start a new attack before default labels are ready', async () => {
    const { result } = renderCopy({ defaultsReady: false })

    await act(async () => { await result.current.copyConversation(1, 'new_attack') })

    expect(getMessages).not.toHaveBeenCalled()
  })

  it('should not start a new attack before the remembered chat scorer is ready', async () => {
    const { result } = renderCopy({ scorerSelectionReady: false })

    await act(async () => { await result.current.copyConversation(1, 'new_attack') })

    expect(getMessages).not.toHaveBeenCalled()
  })

  it('should recover a failed prompt in place instead of copying it into the same attack', async () => {
    const { result, options } = renderCopy({
      recoverableSend: { history: [], historyCutoffIndex: 0, failedRequestMessageIndex: 2 },
    })

    await act(async () => { await result.current.copyConversation(2, 'same_attack') })

    expect(options.recoverProcessingError).toHaveBeenCalled()
    expect(getMessages).not.toHaveBeenCalled()
  })

  it('should report a failed copy only while the source conversation is viewed', async () => {
    getMessages.mockRejectedValue(new Error('Load failed'))
    const { result, options } = renderCopy()

    await act(async () => { await result.current.copyConversation(1, 'same_attack') })

    expect(options.setEditorError).toHaveBeenLastCalledWith('Load failed')
    expect(options.setBusy).toHaveBeenLastCalledWith(false)
  })

  it('should explain why branching needs a configured target', () => {
    const { result, rerender, options } = renderCopy({ targetResolutionStatus: 'idle' })

    expect(result.current.branchDisabledReason(1)).toBe('Configure a target before branching this conversation.')
    rerender({ ...options, targetResolutionStatus: 'unbound' })
    expect(result.current.branchDisabledReason(1)).toBeUndefined()
  })
})
