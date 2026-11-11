import { act, renderHook } from '@testing-library/react'
import { useState } from 'react'

import { attacksApi } from '@/services/api'
import { deferred } from '@/test-utils/deferred'
import { makeTarget } from '@/test-utils/targetFixtures'
import type {
  AttackSummary, ChatConverterController, ConversationMessagesResponse, Message, MessageSendStatus, PieceConversion,
  RequestGenerationDraft, TargetCapabilities, TargetInstance,
} from '@/types'
import { PENDING_SEND_CONVERSATION_ID } from '@/utils/chatSendState'

import { useChatSend, useChatSendState } from './useChatSend'
import type { UseChatSendOptions } from './useChatSend'

jest.mock('@/services/api', () => ({
  attacksApi: {
    createAttack: jest.fn(),
    submitMessageSend: jest.fn(),
    getMessageSend: jest.fn(),
    getAttack: jest.fn(),
    getMessages: jest.fn(),
  },
}))

const api = attacksApi as jest.Mocked<typeof attacksApi>
const target = makeTarget({ target_registry_name: 'chat_target', target_type: 'OpenAIChatTarget' })
const temperatureTarget = makeTarget({
  target_registry_name: 'chat_target', target_type: 'OpenAIChatTarget',
  capabilities: { supports_temperature: true } as TargetCapabilities,
  generation_ranges: { temperature: [0, 2] },
})
const attack = {
  attack_result_id: 'attack', conversation_id: 'a', attack_type: 'ManualAttack', objective: '', converters: [],
  message_count: 2, related_conversation_ids: [], labels: {}, created_at: '', updated_at: '',
} as AttackSummary

function accepted(conversationId: string, state: MessageSendStatus['state'] = 'completed'): MessageSendStatus {
  return {
    send_id: `send-${conversationId}`, attack_result_id: 'attack', conversation_id: conversationId,
    request_turn_number: 1, state, error: null, failure_stage: null,
  }
}

function transcript(conversationId: string, messages: ConversationMessagesResponse['messages'] = []): ConversationMessagesResponse {
  return { conversation_id: conversationId, messages, target_response_status: null }
}

function convertersWith(applied: Record<string, PieceConversion> = {}): ChatConverterController {
  return {
    applied,
    pipelines: {},
    snapshot: () => ({ generation: 'g1', pipelines: {}, workingInputs: {}, stageResults: {}, errors: {}, applied }),
  } as unknown as ChatConverterController
}

interface HarnessOptions {
  attackResultId?: string | null
  conversationId?: string | null
  activeConversationId?: string | null
  converters?: ChatConverterController
  activeTarget?: TargetInstance
  generationDraft?: RequestGenerationDraft
}

function renderSend(initial: HarnessOptions = {}) {
  const viewed = { attackId: initial.attackResultId === undefined ? 'attack' : initial.attackResultId, conversationId: null as string | null }
  const options = {
    attackResultId: 'attack' as string | null,
    conversationId: 'a' as string | null,
    activeConversationId: 'a' as string | null,
    converters: convertersWith(),
    activeTarget: target,
    generationDraft: {} as RequestGenerationDraft,
    ...initial,
  }
  viewed.conversationId = options.activeConversationId ?? options.conversationId
  const callbacks = {
    onConversationCreated: jest.fn(),
    onCreatedDestination: jest.fn(),
    onAttackCreated: jest.fn(),
    markConversationLoaded: jest.fn(),
    clearSentDraft: jest.fn(),
    restoreSendDraft: jest.fn(),
    refreshPanel: jest.fn(),
    revealRepeatConversations: jest.fn(),
    observeChatSend: jest.fn((_attackId: string, acceptance: Promise<MessageSendStatus>) => ({
      readProgress: () => acceptance,
      readAttack: () => api.getAttack('attack'),
    })),
  }
  const hook = renderHook((props: HarnessOptions) => {
    const [messages, setMessages] = useState<Message[]>([])
    const merged = { ...options, ...props }
    const viewedConversationId = merged.activeConversationId ?? merged.conversationId ?? null
    const tracking = useChatSendState({
      activeConversationId: merged.activeConversationId ?? null,
      viewedConversationId,
      runtimeGeneration: 'g1',
    })
    const sendOptions: UseChatSendOptions = {
      tracking,
      route: {
        attackResultId: merged.attackResultId,
        conversationId: merged.conversationId,
        activeConversationId: merged.activeConversationId,
        viewedConversationId,
        getViewedAttackId: () => viewed.attackId,
        setViewedAttackId: (id: string) => { viewed.attackId = id },
        getNavigationRevision: () => 0,
      },
      readiness: {
        runtimeReady: true,
        runtimeGeneration: 'g1',
        defaultsReady: true,
        scorerSelectionReady: true,
        isSendBlocked: false,
        getLaunchState: () => ({ generation: 'g1', ready: true, defaultsReady: true, scorerSelectionReady: true }),
      },
      request: {
        activeTarget: merged.activeTarget,
        newAttack: {
          objective: 'Objective', systemPrompt: '', supportsSystemPrompt: false,
          labels: { operator: 'me' }, operationId: null, draftScorer: undefined, defaultChatScorer: undefined,
        },
        generationDraft: merged.generationDraft,
        chatScorer: null,
        availableScorers: [],
      },
      composer: {
        converters: merged.converters,
        getDraftRevision: () => 1,
        getTextMetadata: () => undefined,
        clearSentDraft: callbacks.clearSentDraft,
        restoreSendDraft: callbacks.restoreSendDraft,
        clearPreservedDraft: jest.fn(),
      },
      loader: {
        messages,
        setMessages,
        loadedConversationId: merged.activeConversationId ?? null,
        invalidateConversationLoads: jest.fn(),
        claimConversationRead: () => 1,
        isLatestConversationRead: () => true,
        markConversationLoaded: callbacks.markConversationLoaded,
        getViewedConversationId: () => viewed.conversationId,
        setViewedConversationId: (id: string | null) => { viewed.conversationId = id },
        getLoadedConversationId: () => viewed.conversationId,
      },
      callbacks: {
        observeChatSend: callbacks.observeChatSend,
        showConversationScoring: jest.fn(),
        showScoringNotice: jest.fn(),
        setEditorNotice: jest.fn(),
        revealRepeatConversations: callbacks.revealRepeatConversations,
        refreshPanel: callbacks.refreshPanel,
        onConversationCreated: callbacks.onConversationCreated,
        onCreatedDestination: callbacks.onCreatedDestination,
        onAttackCreated: callbacks.onAttackCreated,
      },
    }
    const send = useChatSend(sendOptions)
    return { tracking, send, messages }
  }, { initialProps: {} })
  const view = (props: HarnessOptions): void => {
    viewed.conversationId = props.activeConversationId ?? viewed.conversationId
    hook.rerender(props)
  }
  return { ...hook, callbacks, view }
}

beforeEach(() => {
  jest.clearAllMocks()
  api.getAttack.mockResolvedValue(attack)
  api.getMessages.mockImplementation(async (_attackId: string, conversationId: string) => transcript(conversationId))
})

describe('useChatSend', () => {
  it('shows the prompt optimistically and locks the conversation until the send settles', async () => {
    const acceptance = deferred<MessageSendStatus>()
    api.submitMessageSend.mockReturnValue(acceptance.promise)
    const { result } = renderSend()

    let sent!: Promise<unknown>
    await act(async () => { sent = result.current.send.send('hello', undefined, []) })

    expect(result.current.messages).toEqual([
      expect.objectContaining({ role: 'user', content: 'hello' }),
      expect.objectContaining({ role: 'assistant', isLoading: true }),
    ])
    expect(result.current.tracking.isSending).toBe(true)
    expect(result.current.tracking.isSendInFlight('a')).toBe(true)

    await act(async () => {
      acceptance.resolve(accepted('a'))
      await sent
    })

    expect(await sent).toEqual({ status: 'sent', clearDraft: true })
    expect(result.current.tracking.isSending).toBe(false)
    expect(result.current.tracking.isSendInFlight('a')).toBe(false)
    expect(result.current.messages.some((message: Message) => message.isLoading)).toBe(false)
  })

  it('rejects a duplicate send while the conversation is locked', async () => {
    api.submitMessageSend.mockReturnValue(deferred<MessageSendStatus>().promise)
    const { result } = renderSend()

    await act(async () => { void result.current.send.send('first', undefined, []) })
    let duplicate: unknown
    await act(async () => { duplicate = await result.current.send.send('second', undefined, []) })

    expect(duplicate).toEqual({ status: 'retryable_failure', clearDraft: false })
    expect(api.submitMessageSend).toHaveBeenCalledTimes(1)
  })

  it('sends independently in two conversations', async () => {
    const first = deferred<MessageSendStatus>()
    const second = deferred<MessageSendStatus>()
    api.submitMessageSend.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)
    const { result, view } = renderSend()

    let firstSend!: Promise<unknown>
    await act(async () => { firstSend = result.current.send.send('to a', undefined, []) })
    view({ activeConversationId: 'b', conversationId: 'b' })
    await act(async () => { void result.current.send.send('to b', undefined, []) })

    expect(api.submitMessageSend).toHaveBeenCalledTimes(2)
    expect([...result.current.tracking.state.sending].sort()).toEqual(['a', 'b'])

    await act(async () => {
      first.resolve(accepted('a'))
      await firstSend
    })

    expect([...result.current.tracking.state.sending]).toEqual(['b'])
    expect(result.current.tracking.isSending).toBe(true)
  })

  it('restores the optimistic prompt when the user returns to a conversation that is still sending', async () => {
    api.submitMessageSend.mockReturnValue(deferred<MessageSendStatus>().promise)
    const { result, view } = renderSend()

    await act(async () => { void result.current.send.send('pending prompt', undefined, []) })
    view({ activeConversationId: 'b', conversationId: 'b' })

    let reloaded: Message[] = []
    let other: Message[] = []
    act(() => {
      reloaded = result.current.tracking.reconcileLoadedConversation('a', transcript('a'), [])
      other = result.current.tracking.reconcileLoadedConversation('b', transcript('b'), [])
    })
    expect(reloaded).toEqual([
      expect.objectContaining({ role: 'user', content: 'pending prompt' }),
      expect.objectContaining({ role: 'assistant', isLoading: true }),
    ])
    expect(other).toEqual([])
  })

  it('moves the first send to the created conversation before routing to it', async () => {
    api.createAttack.mockResolvedValue({ attack_result_id: 'created-attack', conversation_id: 'created' } as never)
    const acceptance = deferred<MessageSendStatus>()
    api.submitMessageSend.mockReturnValue(acceptance.promise)
    const { result, callbacks } = renderSend({ attackResultId: null, conversationId: null, activeConversationId: null })

    await act(async () => { void result.current.send.send('first prompt', undefined, []) })

    expect(api.createAttack).toHaveBeenCalledWith(expect.objectContaining({
      target_registry_name: 'chat_target', objective: 'Objective', labels: { operator: 'me' },
    }))
    expect(callbacks.onCreatedDestination).toHaveBeenCalledWith('created-attack', 'created')
    expect(callbacks.onConversationCreated).toHaveBeenCalledWith('created-attack', 'created', 'Objective')
    expect(api.submitMessageSend).toHaveBeenCalledWith('created-attack', expect.objectContaining({
      target_conversation_id: 'created', send: true,
    }))
    expect(result.current.tracking.isSendInFlight(PENDING_SEND_CONVERSATION_ID)).toBe(false)
    expect(result.current.tracking.isSendInFlight('created')).toBe(true)
    expect([...result.current.tracking.state.sending]).toEqual(['created'])
  })

  it('holds the created conversation as loaded so committing its route mid-send keeps the first prompt', async () => {
    api.createAttack.mockResolvedValue({ attack_result_id: 'created-attack', conversation_id: 'created' } as never)
    const acceptance = deferred<MessageSendStatus>()
    api.submitMessageSend.mockReturnValue(acceptance.promise)
    const { result, callbacks } = renderSend({ attackResultId: null, conversationId: null, activeConversationId: null })

    await act(async () => { void result.current.send.send('first prompt', undefined, []) })

    // The loader must already hold the created conversation when the route
    // commits; otherwise it fetches the empty server transcript over the
    // optimistic prompt while the first send is still in flight (#3663).
    expect(callbacks.markConversationLoaded).toHaveBeenCalledWith('created')
    expect(callbacks.markConversationLoaded.mock.invocationCallOrder[0])
      .toBeLessThan(api.submitMessageSend.mock.invocationCallOrder[0])
    expect(result.current.tracking.isSendInFlight('created')).toBe(true)
    expect(result.current.messages).toEqual([
      expect.objectContaining({ role: 'user', content: 'first prompt' }),
      expect.objectContaining({ role: 'assistant', isLoading: true }),
    ])
  })

  it('sends the next-send generation options with a first send and keeps them across the created route', async () => {
    api.createAttack.mockResolvedValue({ attack_result_id: 'created-attack', conversation_id: 'created' } as never)
    api.submitMessageSend.mockReturnValue(new Promise(() => {}))
    const { result, callbacks } = renderSend({
      attackResultId: null, conversationId: null, activeConversationId: null,
      activeTarget: temperatureTarget, generationDraft: { temperature: '0.7' },
    })

    await act(async () => { void result.current.send.send('first prompt', undefined, []) })

    // Runtime generation is request metadata (#3506), not a rebuilt target binding.
    const createRequest = api.createAttack.mock.calls[0][0]
    expect(createRequest).not.toHaveProperty('target_binding')
    const [, request] = api.submitMessageSend.mock.calls[0]
    expect(request).not.toHaveProperty('target_binding')
    expect(request.pieces[0].prompt_metadata).toEqual({ request_generation_config: { temperature: 0.7 } })
    // Routing to the attack this send created is not navigation, so ChatWindow keeps the draft.
    expect(callbacks.onAttackCreated).toHaveBeenCalledWith('created-attack')
  })

  it('applies the generation draft captured at send time to a send in an existing attack', async () => {
    api.submitMessageSend.mockReturnValue(new Promise(() => {}))
    const { result, rerender } = renderSend({ activeTarget: temperatureTarget, generationDraft: { temperature: '0' } })

    await act(async () => { void result.current.send.send('hello', undefined, []) })
    rerender({ generationDraft: { temperature: '1.5' } })

    const [attackId, request] = api.submitMessageSend.mock.calls[0]
    expect(attackId).toBe('attack')
    expect(request.pieces[0].prompt_metadata).toEqual({ request_generation_config: { temperature: 0 } })
  })

  it('fails the send before submission when a generation option is out of range for the target', async () => {
    const { result } = renderSend({ activeTarget: temperatureTarget, generationDraft: { temperature: '3' } })

    let outcome: unknown
    await act(async () => { outcome = await result.current.send.send('hello', undefined, []) })

    expect(outcome).toEqual({ status: 'retryable_failure', clearDraft: false })
    expect(api.submitMessageSend).not.toHaveBeenCalled()
    expect(result.current.messages).toEqual([
      expect.objectContaining({ role: 'user', content: 'hello' }),
      expect.objectContaining({
        role: 'assistant', error: expect.objectContaining({ description: 'Temperature must be a finite number between 0 and 2.' }),
      }),
    ])
  })

  it('blocks resending after unknown delivery and refreshes without resubmitting', async () => {
    api.submitMessageSend.mockRejectedValue(new Error('socket closed'))
    const { result } = renderSend()

    let outcome: unknown
    await act(async () => { outcome = await result.current.send.send('hello', undefined, []) })

    expect(outcome).toEqual({ status: 'non_retryable_failure', clearDraft: false })
    expect(result.current.send.sendIssue).toEqual(expect.objectContaining({
      blocking: true,
      description: 'socket closed Send acceptance is unknown. Refresh saved messages only; do not resend.',
    }))
    let resend: unknown
    await act(async () => { resend = await result.current.send.send('hello', undefined, []) })
    expect(resend).toEqual({ status: 'retryable_failure', clearDraft: false })

    await act(async () => { await result.current.send.refreshSend() })

    expect(api.submitMessageSend).toHaveBeenCalledTimes(1)
    expect(api.getMessages).toHaveBeenCalledWith('attack', 'a')
    expect(result.current.send.sendIssue?.blocking).toBe(true)
    expect(result.current.tracking.isSending).toBe(false)
  })

  it('shows a retryable error when the send fails before submission', async () => {
    api.createAttack.mockRejectedValue(new Error('labels changed'))
    const { result } = renderSend({ attackResultId: null, conversationId: null, activeConversationId: null })

    let outcome: unknown
    await act(async () => { outcome = await result.current.send.send('hello', undefined, []) })

    expect(outcome).toEqual({ status: 'retryable_failure', clearDraft: false })
    expect(api.submitMessageSend).not.toHaveBeenCalled()
    expect(result.current.messages).toEqual([
      expect.objectContaining({ role: 'user', content: 'hello' }),
      expect.objectContaining({ role: 'assistant', error: expect.objectContaining({ description: 'labels changed' }) }),
    ])
    expect(result.current.tracking.isSendInFlight(PENDING_SEND_CONVERSATION_ID)).toBe(false)
  })

  it('sends converted pieces and shows a text-to-file conversion as an attachment', async () => {
    api.submitMessageSend.mockReturnValue(deferred<MessageSendStatus>().promise)
    const conversion: PieceConversion = {
      pieceId: 'text', pieceType: 'text', converterInstanceIds: ['qr'], convertedValue: '/media/out.png',
      originalValue: 'hello', convertedDataType: 'image_path',
    }
    const { result } = renderSend({ converters: convertersWith({ text: conversion }) })

    await act(async () => { void result.current.send.send('hello', undefined, []) })

    expect(result.current.messages[0]).toEqual(expect.objectContaining({
      content: 'hello',
      attachments: [expect.objectContaining({ name: 'out.png', mimeType: 'application/octet-stream' })],
    }))
    expect(api.submitMessageSend).toHaveBeenCalledWith('attack', expect.objectContaining({
      pieces: [expect.objectContaining({
        original_value: 'hello', converted_value: '/media/out.png', converted_value_data_type: 'image_path',
        applied_converter_ids: ['qr'],
      })],
    }))
  })

  it('offers the prompt back when preparation fails before target dispatch', async () => {
    api.submitMessageSend.mockResolvedValue({
      ...accepted('a', 'failed'), failure_stage: 'preparation', error: 'Converter failed.',
    })
    const { result, callbacks } = renderSend()

    let outcome: unknown
    await act(async () => { outcome = await result.current.send.send('hello', undefined, [], undefined) })

    expect(outcome).toEqual({ status: 'retryable_failure', clearDraft: false })
    expect(result.current.send.sendIssue).toEqual(expect.objectContaining({
      description: 'Converter failed.', blocking: false, draft: expect.anything(),
    }))

    act(() => { result.current.send.restorePreparationDraft() })

    expect(callbacks.restoreSendDraft).toHaveBeenCalledWith('hello', [], undefined, {}, undefined)
  })

  it('records the last single send as undoable once its response is saved', async () => {
    api.submitMessageSend.mockResolvedValue(accepted('a'))
    api.getMessages.mockResolvedValue({
      conversation_id: 'a',
      messages: [
        { turn_number: 1, role: 'user', message_pieces: [], created_at: '' },
        {
          turn_number: 2, role: 'assistant', created_at: '',
          message_pieces: [{
            id: 'reply', original_value_data_type: 'text', converted_value_data_type: 'text',
            converted_value: 'hi', scores: [], response_error: 'none',
          }],
        },
      ],
      target_response_status: { request_turn_number: 1, response_turn_number: 2, response_error: 'none' },
    } as ConversationMessagesResponse)
    const { result } = renderSend()

    await act(async () => { await result.current.send.send('hello', undefined, []) })

    expect(result.current.tracking.state.undoable).toEqual(expect.objectContaining({
      attackResultId: 'attack', conversationId: 'a', cutoffIndex: 0, originalValue: 'hello',
    }))

    act(() => { result.current.tracking.clearUndoableSend() })
    expect(result.current.tracking.state.undoable).toBeNull()
  })
})
