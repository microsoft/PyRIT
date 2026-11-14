import type { ConversationMessagesResponse, MessageSendStatus } from '@/types'

import {
  chatSendReducer,
  getPersistedProcessingRecovery,
  INITIAL_CHAT_SEND_STATE,
  isSendFinished,
  mergeLoadedRecovery,
  PENDING_SEND_CONVERSATION_ID,
} from './chatSendState'
import type { ChatSendAction, ChatSendState, RecoverableSendDraft, UndoableSend } from './chatSendState'

function reduce(actions: ChatSendAction[], state: ChatSendState = INITIAL_CHAT_SEND_STATE): ChatSendState {
  return actions.reduce(chatSendReducer, state)
}

function recovery(overrides: Partial<RecoverableSendDraft> = {}): RecoverableSendDraft {
  return {
    history: [],
    conversationId: 'conv',
    failedRequestTurnNumber: 1,
    failedResponseTurnNumber: 2,
    historyCutoffIndex: 0,
    failedRequestMessageIndex: 0,
    originalValue: 'persisted prompt',
    attachments: [],
    conversions: {},
    source: 'persisted',
    ...overrides,
  }
}

function progress(sendId: string, state: MessageSendStatus['state']): MessageSendStatus {
  return {
    send_id: sendId, attack_result_id: 'attack', conversation_id: 'conv', request_turn_number: 1,
    state, error: null, failure_stage: null,
  }
}

const undoable: UndoableSend = {
  attackResultId: 'attack', conversationId: 'conv', historySignature: 'sig', cutoffIndex: 0, originalValue: 'hi',
  attachments: [], history: [],
  converters: { generation: 'g1', pipelines: {}, workingInputs: {}, stageResults: {}, errors: {}, applied: {} },
}

describe('chatSendReducer', () => {
  it('locks each conversation independently', () => {
    const state = reduce([
      { type: 'send-started', conversationId: 'a' },
      { type: 'send-started', conversationId: 'b' },
      { type: 'tracking-finished', conversationId: 'a' },
    ])

    expect([...state.sending]).toEqual(['b'])
  })

  it('clears the conversation issue, recovery and undo when a send starts', () => {
    const started = reduce([
      { type: 'issue-changed', conversationId: 'a', issue: { description: 'old', blocking: true } },
      { type: 'issue-changed', conversationId: 'b', issue: { description: 'other', blocking: true } },
      { type: 'recovery-replaced', conversationId: 'a', recovery: recovery({ conversationId: 'a' }) },
      { type: 'undo-recorded', undoable },
      { type: 'send-started', conversationId: 'a' },
    ])

    expect(started.issues).toEqual({ b: { description: 'other', blocking: true } })
    expect(started.recoveries).toEqual({})
    expect(started.undoable).toBeNull()
  })

  it('moves a first send lock from the pending key to the created conversation', () => {
    const state = reduce([
      { type: 'send-started', conversationId: PENDING_SEND_CONVERSATION_ID },
      { type: 'send-conversation-created', from: PENDING_SEND_CONVERSATION_ID, to: 'created' },
    ])

    expect([...state.sending]).toEqual(['created'])
  })

  it('tracks scoring per conversation and releases it with the send', () => {
    const scoring = reduce([
      { type: 'tracking-started', conversationId: 'a' },
      { type: 'scoring-observed', conversationId: 'a', scoring: true },
      { type: 'scoring-observed', conversationId: 'b', scoring: true },
    ])
    expect([...scoring.scoring]).toEqual(['a', 'b'])

    const finished = chatSendReducer(scoring, { type: 'tracking-finished', conversationId: 'a' })
    expect([...finished.scoring]).toEqual(['b'])
    expect(finished.sending.size).toBe(0)
  })

  it('returns the same state for transitions that change nothing', () => {
    const state = reduce([{ type: 'tracking-started', conversationId: 'a' }])

    expect(chatSendReducer(state, { type: 'tracking-started', conversationId: 'a' })).toBe(state)
    expect(chatSendReducer(state, { type: 'tracking-finished', conversationId: 'b' })).toBe(state)
    expect(chatSendReducer(state, { type: 'scoring-observed', conversationId: 'a', scoring: false })).toBe(state)
    expect(chatSendReducer(state, { type: 'issue-changed', conversationId: 'a' })).toBe(state)
    expect(chatSendReducer(state, { type: 'recovery-loaded', conversationId: 'a' })).toBe(state)
    expect(chatSendReducer(state, { type: 'recovery-replaced', conversationId: 'a' })).toBe(state)
    expect(chatSendReducer(state, { type: 'recoveries-cleared' })).toBe(state)
    expect(chatSendReducer(state, { type: 'undo-cleared' })).toBe(state)
  })

  it('keeps a live recovery when a reload reports the same failed turn', () => {
    const live = recovery({ source: 'live', originalValue: 'typed prompt', historyCutoffIndex: 0, failedRequestMessageIndex: 0 })
    const state = reduce([
      { type: 'recovery-replaced', conversationId: 'conv', recovery: live },
      {
        type: 'recovery-loaded', conversationId: 'conv',
        recovery: recovery({ historyCutoffIndex: 3, failedRequestMessageIndex: 4 }),
      },
    ])

    expect(state.recoveries.conv).toEqual({ ...live, historyCutoffIndex: 3, failedRequestMessageIndex: 4 })
  })

  it('replaces a recovery for a different failed turn and drops it when the load has none', () => {
    const live = recovery({ source: 'live', originalValue: 'typed prompt' })
    const persisted = recovery({ failedRequestTurnNumber: 3, failedResponseTurnNumber: 4 })
    const replaced = reduce([
      { type: 'recovery-replaced', conversationId: 'conv', recovery: live },
      { type: 'recovery-loaded', conversationId: 'conv', recovery: persisted },
    ])
    expect(replaced.recoveries.conv).toBe(persisted)

    const cleared = chatSendReducer(replaced, { type: 'recovery-loaded', conversationId: 'conv' })
    expect(cleared.recoveries).toEqual({})
  })

  it('lets a send response replace a live recovery even for the same turn', () => {
    const state = reduce([
      { type: 'recovery-replaced', conversationId: 'conv', recovery: recovery({ source: 'live' }) },
      { type: 'recovery-replaced', conversationId: 'conv', recovery: recovery() },
    ])

    expect(state.recoveries.conv.source).toBe('persisted')
  })

  it('clears every recovery when the attack resets', () => {
    const state = reduce([
      { type: 'recovery-replaced', conversationId: 'a', recovery: recovery() },
      { type: 'recovery-replaced', conversationId: 'b', recovery: recovery() },
      { type: 'recoveries-cleared' },
    ])

    expect(state.recoveries).toEqual({})
  })

  it('replaces finished repeated sends when a new one starts and updates only the matching view', () => {
    const state = reduce([
      { type: 'repeat-started', progress: progress('done', 'completed') },
      { type: 'repeat-started', progress: progress('running', 'sending') },
      { type: 'repeat-started', progress: progress('next', 'queued') },
      { type: 'repeat-progressed', progress: progress('running', 'failed'), needsRefresh: true },
    ])

    expect(state.repeatSends).toEqual([
      { progress: progress('running', 'failed'), needsRefresh: true },
      { progress: progress('next', 'queued'), needsRefresh: false },
    ])
    expect(chatSendReducer(state, { type: 'repeat-dismissed', sendId: 'running' }).repeatSends).toEqual([
      { progress: progress('next', 'queued'), needsRefresh: false },
    ])
  })

  it('records and clears the undoable send', () => {
    const recorded = chatSendReducer(INITIAL_CHAT_SEND_STATE, { type: 'undo-recorded', undoable })
    expect(recorded.undoable).toBe(undoable)
    expect(chatSendReducer(recorded, { type: 'undo-cleared' }).undoable).toBeNull()
  })
})

describe('mergeLoadedRecovery', () => {
  it('returns nothing when the load has no recovery', () => {
    expect(mergeLoadedRecovery(recovery({ source: 'live' }), undefined)).toBeUndefined()
  })

  it('prefers the persisted recovery over an older persisted one', () => {
    const persisted = recovery({ historyCutoffIndex: 2 })
    expect(mergeLoadedRecovery(recovery(), persisted)).toBe(persisted)
  })
})

describe('isSendFinished', () => {
  it.each([
    ['completed', true], ['failed', true], ['interrupted', true], ['queued', false], ['sending', false], ['finalizing', false],
  ] as const)('%s is finished: %s', (state, finished) => {
    expect(isSendFinished(progress('send', state))).toBe(finished)
  })
})

describe('getPersistedProcessingRecovery', () => {
  const piece = (id: string, value: string, responseError: 'none' | 'processing' = 'none') => ({
    id, original_value_data_type: 'text', converted_value_data_type: 'text', original_value: value,
    converted_value: value, scores: [], response_error: responseError, prompt_metadata: { tone: id },
  })
  const response = (status: ConversationMessagesResponse['target_response_status']): ConversationMessagesResponse => ({
    conversation_id: 'conv',
    messages: [
      { turn_number: 1, role: 'user', message_pieces: [piece('u1', 'first')], created_at: '' },
      { turn_number: 2, role: 'assistant', message_pieces: [piece('a1', 'reply')], created_at: '' },
      { turn_number: 3, role: 'user', message_pieces: [piece('u2', 'retry me')], created_at: '' },
      { turn_number: 4, role: 'assistant', message_pieces: [piece('a2', '', 'processing')], created_at: '' },
    ],
    target_response_status: status,
  })

  it('restores the failed request and keeps only the history before it', () => {
    const result = getPersistedProcessingRecovery('conv', response({
      request_turn_number: 3, response_turn_number: 4, response_error: 'processing',
    }))

    expect(result).toEqual(expect.objectContaining({
      conversationId: 'conv',
      failedRequestTurnNumber: 3,
      failedResponseTurnNumber: 4,
      historyCutoffIndex: 2,
      history: [{ role: 'user' }, { role: 'assistant' }],
      failedRequestMessageIndex: 2,
      originalValue: 'retry me',
      textMetadata: { tone: 'u2' },
      source: 'persisted',
    }))
  })

  it('ignores responses that are not retryable processing failures', () => {
    expect(getPersistedProcessingRecovery('conv', response(null))).toBeUndefined()
    expect(getPersistedProcessingRecovery('conv', response({
      request_turn_number: 9, response_turn_number: 10, response_error: 'processing',
    }))).toBeUndefined()
  })
})
