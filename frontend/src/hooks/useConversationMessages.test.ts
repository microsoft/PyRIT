import { act, renderHook, waitFor } from '@testing-library/react'

import { attacksApi } from '@/services/api'
import { deferred } from '@/test-utils/deferred'
import type { BackendMessage, BackendScore, ConversationMessagesResponse, Message, MessageDisplayPiece } from '@/types'
import { backendMessagesToFrontend } from '@/utils/messageMapper'

import { useConversationMessages } from './useConversationMessages'
import type { UseConversationMessagesOptions } from './useConversationMessages'

jest.mock('@/services/api', () => ({ attacksApi: { getMessages: jest.fn() } }))
jest.mock('@/utils/messageMapper', () => ({
  ...jest.requireActual<typeof import('@/utils/messageMapper')>('@/utils/messageMapper'),
  backendMessagesToFrontend: jest.fn(),
}))

function transcript(conversationId: string, turns: number[] = [1]): ConversationMessagesResponse {
  return {
    conversation_id: conversationId,
    messages: turns.map((turn) => ({
      turn_number: turn, role: 'user', message_pieces: [], created_at: conversationId,
    })),
    target_response_status: null,
  }
}

const notSending = new Set<string>()
const defaults: UseConversationMessagesOptions = {
  attackResultId: 'attack',
  conversationId: 'main',
  activeConversationId: 'main',
  sendingConversations: notSending,
  isSendInFlight: () => false,
}

function renderMessages(initialProps: Partial<UseConversationMessagesOptions> = {}) {
  return renderHook(
    (props: Partial<UseConversationMessagesOptions>) => useConversationMessages({ ...defaults, ...props }),
    { initialProps },
  )
}

function contents(messages: Message[]): string[] {
  return messages.map((message) => message.content)
}

describe('useConversationMessages', () => {
  const getMessages = jest.mocked(attacksApi.getMessages)
  afterEach(() => { jest.useRealTimers() })

  beforeEach(() => {
    jest.resetAllMocks()
    jest.mocked(backendMessagesToFrontend).mockImplementation((messages: BackendMessage[]) => messages.map(
      (message) => ({ role: 'user', content: `${message.created_at}:${message.turn_number}`, timestamp: '' }),
    ))
  })

  it('should retain a long live window and split turn through saved paging while rejecting a late live response', async () => {
    jest.mocked(backendMessagesToFrontend).mockImplementation(
      jest.requireActual<typeof import('@/utils/messageMapper')>('@/utils/messageMapper').backendMessagesToFrontend,
    )
    const full: ConversationMessagesResponse = {
      conversation_id: 'main', target_response_status: null,
      messages: [{
        role: 'assistant', turn_number: 1, created_at: '2026-01-01T00:00:00Z',
        message_pieces: Array.from({ length: 122 }, (_value: unknown, index: number) => ({
          id: `piece-${index}`, original_value: `Piece ${index}`, converted_value: `Piece ${index}`,
          original_value_data_type: 'text', converted_value_data_type: 'text', scores: [], response_error: 'none',
        })),
      }],
    }
    const lateLive = deferred<ConversationMessagesResponse>()
    const continuation = deferred<ConversationMessagesResponse>()
    getMessages.mockResolvedValueOnce(full).mockReturnValueOnce(lateLive.promise).mockResolvedValueOnce({
      ...full, messages: full.messages.map((message: BackendMessage) => ({ ...message, message_pieces: message.message_pieces.slice(0, 100) })),
      pagination: { limit: 100, has_more: true, next_cursor: 'saved-next' }, partial_message_sequences: [1],
    }).mockReturnValueOnce(continuation.promise)
    const { result, rerender } = renderMessages()
    await waitFor(() => { expect(result.current.messages[0]?.content).toContain('Piece 121') })
    let refresh = Promise.resolve()
    act(() => { refresh = result.current.loadConversation('attack', 'main') })
    rerender({ messagePageSize: 100 })
    await waitFor(() => { expect(getMessages).toHaveBeenLastCalledWith('attack', 'main', { limit: 100, cursor: 'saved-next' }) })
    expect(result.current.messages[0].content).toContain('Piece 121')
    expect(result.current.isTranscriptIncomplete).toBe(true)
    await act(async () => {
      continuation.resolve({
        ...full, messages: full.messages.map((message: BackendMessage) => ({ ...message, message_pieces: message.message_pieces.slice(100) })),
        pagination: { limit: 100, has_more: false, next_cursor: null }, partial_message_sequences: [],
      })
    })
    expect(result.current.loadedPieceCount).toBe(122)
    expect(result.current.isTranscriptIncomplete).toBe(false)
    expect(result.current.messages[0].content).toContain('Piece 121')
    expect(result.current.messages[0].isPartial).toBe(false)
    await act(async () => {
      lateLive.resolve({ ...full, messages: [{ ...full.messages[0], message_pieces: [{
        ...full.messages[0].message_pieces[0], original_value: 'Stale live data', converted_value: 'Stale live data',
      }] }] })
      await refresh
    })
    expect(result.current.loadedPieceCount).toBe(122)
    expect(result.current.messages[0].content).not.toContain('Stale live data')
  })

  it('polls an injected saved-conversation reader without changing manual send ownership', async () => {
    jest.useFakeTimers()
    const readConversation = jest.fn().mockResolvedValue(transcript('main', [1]))
    const { result, unmount } = renderMessages({ readConversation, pollIntervalMs: 2500 })
    await act(async () => { await Promise.resolve() })
    expect(readConversation).toHaveBeenCalledTimes(1)
    readConversation.mockResolvedValue(transcript('main', [1, 2]))
    await act(async () => { jest.advanceTimersByTime(2500); await Promise.resolve() })
    expect(contents(result.current.messages)).toEqual(['main:1', 'main:2'])
    expect(getMessages).not.toHaveBeenCalled()
    unmount()
    await act(async () => { jest.advanceTimersByTime(10000); await Promise.resolve() })
    expect(readConversation).toHaveBeenCalledTimes(2)
    jest.useRealTimers()
  })

  it('should load a standalone conversation by ID without an attack and reject late previous-owner evidence', async () => {
    const first = deferred<ConversationMessagesResponse>()
    const next = deferred<ConversationMessagesResponse>()
    const readConversationById = jest.fn().mockReturnValueOnce(first.promise).mockReturnValueOnce(next.promise)
    const { result, rerender } = renderMessages({ attackResultId: null, readConversationById })
    expect(readConversationById).toHaveBeenCalledWith('main')
    rerender({ attackResultId: null, activeConversationId: 'other', readConversationById })
    expect(readConversationById).toHaveBeenLastCalledWith('other')
    await act(async () => { first.resolve(transcript('main')) })
    expect(result.current.messages).toEqual([])
    expect(result.current.isLoadingMessages).toBe(true)
    await act(async () => { next.resolve(transcript('other')) })
    expect(contents(result.current.messages)).toEqual(['other:1'])
    expect(result.current.loadedConversationId).toBe('other')
    expect(result.current.isLoadingMessages).toBe(false)
    expect(getMessages).not.toHaveBeenCalled()
  })

  it('should load the active conversation and expose its loading state', async () => {
    const load = deferred<ConversationMessagesResponse>()
    getMessages.mockReturnValueOnce(load.promise)
    const { result } = renderMessages()

    expect(getMessages).toHaveBeenCalledWith('attack', 'main')
    expect(result.current.isLoadingMessages).toBe(true)
    expect(result.current.awaitingConversationLoad).toBe(true)

    await act(async () => { load.resolve(transcript('main', [1, 2])) })

    expect(contents(result.current.messages)).toEqual(['main:1', 'main:2'])
    expect(result.current.loadedConversationId).toBe('main')
    expect(result.current.getLoadedConversationId()).toBe('main')
    expect(result.current.isLoadingMessages).toBe(false)
    expect(result.current.awaitingConversationLoad).toBe(false)
  })

  it('should reload a sending conversation after another conversation has loaded', async () => {
    const returnLoad = deferred<ConversationMessagesResponse>()
    getMessages
      .mockResolvedValueOnce(transcript('main'))
      .mockReturnValueOnce(returnLoad.promise)
    const sendingConversations = new Set(['main'])
    const isSendInFlight = (conversationId: string): boolean => conversationId === 'main'
    const { result, rerender } = renderMessages()

    await act(async () => { /* the initial load of main resolves through the first mock */ })
    await waitFor(() => expect(result.current.loadedConversationId).toBe('main'))

    // The send flow holds another conversation (marked loaded), replacing
    // whatever view the hook held before; switching to it stays skipped.
    act(() => { result.current.markConversationLoaded('branch') })
    rerender({ activeConversationId: 'branch', sendingConversations, isSendInFlight })
    expect(getMessages).toHaveBeenCalledTimes(1)

    // Returning to the sending conversation must fetch server state: the
    // hook holds branch, so keeping the skip would render branch's view
    // under main's URL with no loading state until the send completes.
    rerender({ activeConversationId: 'main', sendingConversations, isSendInFlight })
    await waitFor(() => expect(getMessages).toHaveBeenLastCalledWith('attack', 'main'))
    await act(async () => { returnLoad.resolve(transcript('main', [2])) })
    expect(contents(result.current.messages)).toEqual(['main:2'])
    expect(result.current.loadedConversationId).toBe('main')
  })

  it('should ignore a stale response after navigating to another conversation', async () => {
    const mainLoad = deferred<ConversationMessagesResponse>()
    const branchLoad = deferred<ConversationMessagesResponse>()
    getMessages.mockReturnValueOnce(mainLoad.promise).mockReturnValueOnce(branchLoad.promise)
    const { result, rerender } = renderMessages()

    rerender({ activeConversationId: 'branch' })
    expect(getMessages).toHaveBeenLastCalledWith('attack', 'branch')
    expect(result.current.getViewedConversationId()).toBe('branch')

    await act(async () => { mainLoad.resolve(transcript('main')) })
    expect(result.current.messages).toEqual([])
    expect(result.current.loadedConversationId).toBeNull()
    expect(result.current.isLoadingMessages).toBe(true)

    await act(async () => { branchLoad.resolve(transcript('branch')) })
    expect(contents(result.current.messages)).toEqual(['branch:1'])
    expect(result.current.loadedConversationId).toBe('branch')
    expect(result.current.isLoadingMessages).toBe(false)
  })

  it('should ignore a stale failure after navigating to another conversation', async () => {
    const mainLoad = deferred<ConversationMessagesResponse>()
    getMessages.mockReturnValueOnce(mainLoad.promise).mockResolvedValueOnce(transcript('branch'))
    const { result, rerender } = renderMessages({ findingEvidenceId: 'evidence' })

    rerender({ activeConversationId: 'branch', findingEvidenceId: 'evidence' })
    await waitFor(() => expect(result.current.loadedConversationId).toBe('branch'))
    await act(async () => { mainLoad.reject(new Error('Late failure')) })

    expect(contents(result.current.messages)).toEqual(['branch:1'])
    expect(result.current.conversationLoadIssue).toBeNull()
  })

  it('should let only the latest load of a conversation control the transcript and loading state', async () => {
    const olderLoad = deferred<ConversationMessagesResponse>()
    const newerLoad = deferred<ConversationMessagesResponse>()
    getMessages.mockReturnValueOnce(olderLoad.promise).mockReturnValueOnce(newerLoad.promise)
    const { result } = renderMessages()

    let refresh: Promise<void> = Promise.resolve()
    act(() => { refresh = result.current.loadConversation('attack', 'main') })
    await act(async () => {
      newerLoad.resolve(transcript('main', [1, 2]))
      await refresh
    })
    expect(result.current.isLoadingMessages).toBe(false)

    await act(async () => { olderLoad.resolve(transcript('main', [1])) })
    expect(contents(result.current.messages)).toEqual(['main:1', 'main:2'])
    expect(result.current.isLoadingMessages).toBe(false)
  })

  it('should keep a loaded transcript when a refresh fails but clear an initial load failure', async () => {
    getMessages.mockResolvedValueOnce(transcript('main')).mockRejectedValueOnce(new Error('Refresh failed'))
    const { result, rerender } = renderMessages()
    await waitFor(() => expect(result.current.loadedConversationId).toBe('main'))

    await act(async () => { await result.current.loadConversation('attack', 'main') })
    expect(contents(result.current.messages)).toEqual(['main:1'])
    expect(result.current.conversationLoadIssue).toEqual({
      conversationId: 'main', unavailable: false, detail: 'Could not load conversation: Refresh failed',
    })

    getMessages.mockRejectedValueOnce(new Error('Initial load failed'))
    rerender({ activeConversationId: 'branch' })
    await waitFor(() => expect(result.current.loadedConversationId).toBe('branch'))
    expect(result.current.messages).toEqual([])
    expect(result.current.isLoadingMessages).toBe(false)
    expect(result.current.conversationLoadIssue?.detail).toBe('Could not load conversation: Initial load failed')

    getMessages.mockResolvedValueOnce(transcript('branch'))
    await act(async () => { await result.current.loadConversation('attack', 'branch') })
    expect(result.current.conversationLoadIssue).toBeNull()
    expect(contents(result.current.messages)).toEqual(['branch:1'])
  })

  it('should retain the manual transcript and reject a failed score refresh', async () => {
    getMessages.mockResolvedValueOnce(transcript('main')).mockRejectedValueOnce(new Error('Scores unavailable'))
    const { result } = renderMessages()
    await waitFor(() => expect(result.current.loadedConversationId).toBe('main'))

    await act(async () => {
      await expect(result.current.refreshConversationScores('attack', 'main')).rejects.toThrow('Scores unavailable')
    })

    expect(contents(result.current.messages)).toEqual(['main:1'])
    expect(result.current.conversationLoadIssue?.detail).toContain('Scores unavailable')
    expect(getMessages.mock.calls).toEqual([['attack', 'main'], ['attack', 'main']])
  })

  it('should load a sending conversation when another one is loaded', async () => {
    getMessages.mockResolvedValueOnce(transcript('main')).mockResolvedValueOnce(transcript('branch'))
    const isSendInFlight = (conversationId: string): boolean => conversationId === 'branch'
    const sendingConversations = new Set(['branch'])
    const { result, rerender } = renderMessages({ isSendInFlight, sendingConversations })
    await waitFor(() => expect(result.current.loadedConversationId).toBe('main'))
    expect(getMessages).toHaveBeenCalledTimes(1)

    // A sending conversation must load when another conversation's transcript is
    // on screen; without the fetch, the other conversation keeps rendering under
    // this conversation's URL until the send completes. The send pipeline layers
    // its pending messages on top of the fetched transcript.
    rerender({ activeConversationId: 'branch', isSendInFlight, sendingConversations })
    await waitFor(() => expect(result.current.loadedConversationId).toBe('branch'))
    expect(getMessages).toHaveBeenCalledTimes(2)

    // Returning to main re-fetches it: loading branch replaced the held
    // transcript, and nothing caches per-conversation transcripts here.
    rerender({ activeConversationId: 'main', isSendInFlight, sendingConversations })
    expect(getMessages).toHaveBeenCalledTimes(3)
    expect(getMessages).toHaveBeenLastCalledWith('attack', 'main')
    await waitFor(() => expect(result.current.loadedConversationId).toBe('main'))
  })

  it('should report a load gap when returning to a sending conversation while another is held', () => {
    getMessages.mockReturnValue(new Promise(() => {}))
    const sendingConversations = new Set(['main'])
    const isSendInFlight = (conversationId: string): boolean => conversationId === 'main'
    const { result, rerender } = renderMessages({ activeConversationId: 'branch', sendingConversations, isSendInFlight })
    act(() => { result.current.markConversationLoaded('branch') })

    rerender({ activeConversationId: 'main', sendingConversations, isSendInFlight })

    expect(result.current.awaitingConversationLoad).toBe(true)
  })

  it('should not refetch a conversation the hook already holds', async () => {
    getMessages.mockResolvedValueOnce(transcript('main'))
    const { result, rerender } = renderMessages()
    await waitFor(() => expect(result.current.loadedConversationId).toBe('main'))

    act(() => { result.current.markConversationLoaded('branch') })
    rerender({ activeConversationId: 'branch' })

    expect(getMessages).toHaveBeenCalledTimes(1)
  })

  it('should load a held conversation when a forced load is requested', async () => {
    getMessages.mockResolvedValueOnce(transcript('main')).mockResolvedValueOnce(transcript('branch'))
    const { result, rerender } = renderMessages()
    await waitFor(() => expect(result.current.loadedConversationId).toBe('main'))

    act(() => { result.current.markConversationLoaded('branch') })
    act(() => { result.current.requestForcedLoad() })
    rerender({ activeConversationId: 'branch' })

    expect(getMessages).toHaveBeenCalledTimes(2)
    expect(getMessages).toHaveBeenLastCalledWith('attack', 'branch')
  })

  it('should let a claimed send read supersede an older load and yield to a newer claim', async () => {
    const load = deferred<ConversationMessagesResponse>()
    getMessages.mockReturnValueOnce(load.promise)
    const { result } = renderMessages()

    const sendRead = result.current.claimConversationRead('main')
    expect(result.current.isLatestConversationRead('main', sendRead)).toBe(true)

    await act(async () => { load.resolve(transcript('main')) })
    expect(result.current.messages).toEqual([])
    expect(result.current.loadedConversationId).toBeNull()
    expect(result.current.isLoadingMessages).toBe(false)
    expect(result.current.isLatestConversationRead('main', sendRead)).toBe(true)

    getMessages.mockResolvedValueOnce(transcript('main', [1, 2]))
    let refresh: Promise<void> = Promise.resolve()
    act(() => { refresh = result.current.loadConversation('attack', 'main') })
    expect(result.current.isLatestConversationRead('main', sendRead)).toBe(false)
    await act(async () => { await refresh })
    expect(contents(result.current.messages)).toEqual(['main:1', 'main:2'])
  })

  it('should invalidate an in-flight load and clear its loading state', async () => {
    const load = deferred<ConversationMessagesResponse>()
    getMessages.mockReturnValueOnce(load.promise)
    const { result } = renderMessages()
    const sendRead = result.current.claimConversationRead('other')

    act(() => { result.current.invalidateConversationLoads('main') })
    expect(result.current.isLoadingMessages).toBe(false)

    await act(async () => { load.resolve(transcript('main')) })
    expect(result.current.messages).toEqual([])
    expect(result.current.isLatestConversationRead('other', sendRead)).toBe(true)
  })

  it('should report an empty or missing evidence transcript as unavailable', async () => {
    getMessages.mockResolvedValueOnce(transcript('main', []))
    const { result } = renderMessages({ findingEvidenceId: 'evidence' })

    await waitFor(() => expect(result.current.isEvidenceUnavailable).toBe(true))
    expect(result.current.conversationLoadIssue).toEqual({
      conversationId: 'main', unavailable: true, detail: 'Evidence unavailable',
    })

    getMessages.mockRejectedValueOnce({ isAxiosError: true, response: { status: 404, data: {} } })
    await act(async () => { await result.current.loadConversation('attack', 'main') })
    expect(result.current.isEvidenceUnavailable).toBe(true)

    getMessages.mockRejectedValueOnce({ isAxiosError: true, response: { status: 500, data: { detail: 'Boom' } } })
    await act(async () => { await result.current.loadConversation('attack', 'main') })
    expect(result.current.isEvidenceUnavailable).toBe(false)
    expect(result.current.conversationLoadIssue).toEqual({
      conversationId: 'main', unavailable: false, detail: 'Could not load conversation: Boom',
    })
  })

  it('should show the transcript returned by the loaded-conversation callback', async () => {
    const response = transcript('main')
    getMessages.mockResolvedValueOnce(response)
    const onConversationLoaded = jest.fn((_id: string, _response: ConversationMessagesResponse, messages: Message[]) => (
      [...messages, { role: 'assistant', content: '...', timestamp: '', isLoading: true }]
    ))
    const { result } = renderMessages({ onConversationLoaded })

    await waitFor(() => expect(result.current.loadedConversationId).toBe('main'))
    expect(onConversationLoaded).toHaveBeenCalledTimes(1)
    expect(onConversationLoaded).toHaveBeenCalledWith('main', response, [{ role: 'user', content: 'main:1', timestamp: '' }])
    expect(contents(result.current.messages)).toEqual(['main:1', '...'])
  })

  it('should clear the transcript on reset without reloading', async () => {
    getMessages.mockResolvedValueOnce(transcript('main'))
    const { result, rerender } = renderMessages()
    await waitFor(() => expect(result.current.loadedConversationId).toBe('main'))

    rerender({ attackResultId: null, conversationId: null, activeConversationId: null })
    act(() => { result.current.resetConversationMessages() })

    expect(result.current.messages).toEqual([])
    expect(result.current.loadedConversationId).toBeNull()
    expect(getMessages).toHaveBeenCalledTimes(1)
  })

  it('should let a send mark its conversation as viewed and loaded', () => {
    const { result } = renderMessages({ attackResultId: null, conversationId: null, activeConversationId: null })
    expect(result.current.getViewedConversationId()).toBeNull()

    act(() => {
      result.current.setViewedConversationId('created')
      result.current.markConversationLoaded('created')
    })

    expect(result.current.getViewedConversationId()).toBe('created')
    expect(result.current.loadedConversationId).toBe('created')
  })

  describe('bounded saved transcripts', () => {
    const pageSize = 100
    function page(
      conversationId: string, turns: Array<{ sequence: number; ids: string[]; role?: string }>,
      nextCursor: string | null, partialSequences: number[] = [],
    ): ConversationMessagesResponse {
      return {
        conversation_id: conversationId,
        messages: turns.map(({ sequence, ids, role }) => ({
          turn_number: sequence, role: role ?? 'assistant', created_at: '2026-01-01T00:00:00Z',
          message_pieces: ids.map((id: string) => ({
            id, timestamp: '2026-01-01T00:00:00Z', original_value: id, converted_value: id,
            original_value_data_type: 'text', converted_value_data_type: 'text', scores: [], response_error: 'none',
          })),
        })),
        pagination: { limit: pageSize, has_more: nextCursor !== null, next_cursor: nextCursor },
        partial_message_sequences: partialSequences,
        target_response_status: null,
      }
    }
    const first = (): ConversationMessagesResponse => page('main', [{ sequence: -1, ids: ['a', 'b'] }], 'pieces-1', [-1])
    const renderSaved = (props: Partial<UseConversationMessagesOptions> = {}) => renderMessages({ messagePageSize: pageSize, ...props })
    const continueLoading = async (result: ReturnType<typeof renderMessages>['result']): Promise<void> => {
      await act(async () => { await result.current.loadMoreMessages() })
    }
    const longPieceIds = Array.from({ length: 150 }, (_value: unknown, index: number) => `piece-${String(index).padStart(3, '0')}`)
    const humanScore: BackendScore = {
      id: 'human', message_piece_id: longPieceIds[149], scorer_type: 'ManualScorer',
      score_type: 'true_false', score_value: 'True', timestamp: '2026-01-01T00:01:00Z', is_objective_score: true,
    }
    function longPage(start: number, end: number, nextCursor: string | null, scores: BackendScore[] = []): ConversationMessagesResponse {
      const response = page('main', [{ sequence: 1, ids: longPieceIds.slice(start, end) }], nextCursor, [1])
      for (const piece of response.messages[0].message_pieces) {
        piece.scores = scores.filter((score: BackendScore) => score.message_piece_id === piece.id)
      }
      return response
    }
    function displayedScores(messages: Message[]): BackendScore[] {
      return messages.flatMap(
        (message: Message) => message.displayPieces?.flatMap((piece: MessageDisplayPiece) => piece.scores ?? []) ?? [],
      )
    }

    beforeEach(() => {
      jest.mocked(backendMessagesToFrontend).mockImplementation(
        jest.requireActual<typeof import('@/utils/messageMapper')>('@/utils/messageMapper').backendMessagesToFrontend,
      )
    })

    it('should bound the first request and avoid treating an incomplete response as final', async () => {
      const response = { ...first(), target_response_status: {
        response_error: 'processing' as const, request_turn_number: -2, response_turn_number: -1,
      } }
      const onConversationLoaded = jest.fn((_id: string, _response: ConversationMessagesResponse, messages: Message[]) => messages)
      getMessages.mockResolvedValueOnce(response)
      const { result } = renderSaved({ onConversationLoaded })
      await waitFor(() => expect(result.current.loadedConversationId).toBe('main'))

      expect(getMessages).toHaveBeenCalledTimes(1)
      expect(getMessages).toHaveBeenCalledWith('attack', 'main', { limit: 100 })
      expect(result.current.loadedPieceCount).toBe(2)
      expect(result.current.isTranscriptIncomplete).toBe(true)
      expect(result.current.messages[0]).toMatchObject({ sequence: -1, isPartial: true })
      expect(onConversationLoaded.mock.calls[0][1].target_response_status).toBeNull()
      expect(result.current.messagePagination).toEqual(response.pagination)
    })

    it('should merge duplicate/split pieces through multiple pages and resolve leading partial sequences', async () => {
      const onConversationLoaded = jest.fn((_id: string, _response: ConversationMessagesResponse, messages: Message[]) => messages)
      getMessages.mockResolvedValueOnce(first())
        .mockResolvedValueOnce(page('main', [{ sequence: -1, ids: ['b', 'c'] }, { sequence: 0, ids: ['d'] }], 'pieces-2', [-1, 0]))
        .mockResolvedValueOnce(page('main', [{ sequence: 0, ids: ['e'] }], null, [0]))
      const { result } = renderSaved({ onConversationLoaded })
      await waitFor(() => expect(result.current.loadedConversationId).toBe('main'))
      await continueLoading(result)
      expect(contents(result.current.messages)).toEqual(['a\nb\nc', 'd'])
      expect(result.current.messages.map((message) => message.isPartial)).toEqual([false, true])
      await continueLoading(result)

      expect(contents(result.current.messages)).toEqual(['a\nb\nc', 'd\ne'])
      expect(result.current.messages.map((message) => message.isPartial)).toEqual([false, false])
      expect(result.current.loadedPieceCount).toBe(5)
      expect(result.current.isTranscriptIncomplete).toBe(false)
      expect(onConversationLoaded.mock.calls.at(-1)?.[1].messages).toHaveLength(2)
      expect(getMessages.mock.calls.map((call) => call[2])).toEqual([
        { limit: 100 }, { limit: 100, cursor: 'pieces-1' }, { limit: 100, cursor: 'pieces-2' },
      ])
    })

    it('should add and remove scores from all 150 accumulated pieces without replacing evidence or pagination', async () => {
      const onConversationLoaded = jest.fn((_id: string, _response: ConversationMessagesResponse, messages: Message[]) => messages)
      getMessages.mockResolvedValueOnce(longPage(0, 100, 'original-cursor'))
        .mockResolvedValueOnce(longPage(100, 150, null))
        .mockResolvedValueOnce(longPage(0, 100, 'fresh-add-cursor'))
        .mockResolvedValueOnce(longPage(100, 150, null, [humanScore]))
        .mockResolvedValueOnce(longPage(0, 100, 'fresh-remove-cursor'))
        .mockResolvedValueOnce(longPage(100, 150, null))
      const { result } = renderSaved({ onConversationLoaded })
      await waitFor(() => expect(result.current.loadedPieceCount).toBe(100))
      await continueLoading(result)
      const pagination = result.current.messagePagination
      const content = contents(result.current.messages)
      expect(result.current.messages[0].isPartial).toBe(false)

      await act(async () => { await result.current.refreshConversationScores('attack', 'main') })
      expect(displayedScores(result.current.messages)).toEqual([expect.objectContaining(humanScore)])
      expect(contents(result.current.messages)).toEqual(content)
      expect(result.current.loadedPieceCount).toBe(150)
      expect(result.current.messagePagination).toBe(pagination)
      expect(result.current.isTranscriptIncomplete).toBe(false)
      expect(result.current.isLoadingMessages).toBe(false)
      expect(result.current.isLoadingMoreMessages).toBe(false)

      await act(async () => { await result.current.refreshConversationScores('attack', 'main') })
      expect(displayedScores(result.current.messages)).toEqual([])
      expect(contents(result.current.messages)).toEqual(content)
      expect(result.current.loadedPieceCount).toBe(150)
      expect(result.current.messagePagination).toBe(pagination)
      expect(result.current.messages[0].isPartial).toBe(false)
      expect(result.current.isTranscriptIncomplete).toBe(false)
      expect(result.current.messageContinuationIssue).toBeNull()
      expect(onConversationLoaded).toHaveBeenCalledTimes(2)
      expect(getMessages.mock.calls).toEqual([
        ['attack', 'main', { limit: 100 }], ['attack', 'main', { limit: 100, cursor: 'original-cursor' }],
        ['attack', 'main', { limit: 100 }], ['attack', 'main', { limit: 100, cursor: 'fresh-add-cursor' }],
        ['attack', 'main', { limit: 100 }], ['attack', 'main', { limit: 100, cursor: 'fresh-remove-cursor' }],
      ])
    })

    it('should replace canonical scores but keep an incomplete transcript and its incumbent continuation cursor', async () => {
      const priorScore = { ...humanScore, message_piece_id: longPieceIds[0] }
      const refreshed = longPage(0, 100, 'unused-refresh-cursor')
      refreshed.messages[0].message_pieces[0].converted_value = 'Do not replace saved evidence'
      getMessages.mockResolvedValueOnce(longPage(0, 100, 'original-cursor', [priorScore]))
        .mockResolvedValueOnce(refreshed).mockResolvedValueOnce(longPage(100, 150, null))
      const { result } = renderSaved()
      await waitFor(() => expect(result.current.loadedPieceCount).toBe(100))
      const pagination = result.current.messagePagination
      const content = contents(result.current.messages)

      await act(async () => { await result.current.refreshConversationScores('attack', 'main') })

      expect(displayedScores(result.current.messages)).toEqual([])
      expect(contents(result.current.messages)).toEqual(content)
      expect(result.current.messagePagination).toBe(pagination)
      expect(result.current.loadedPieceCount).toBe(100)
      expect(result.current.isTranscriptIncomplete).toBe(true)
      expect(result.current.messages[0].isPartial).toBe(true)
      await continueLoading(result)
      expect(getMessages).toHaveBeenLastCalledWith('attack', 'main', { limit: 100, cursor: 'original-cursor' })
      expect(displayedScores(result.current.messages)).toEqual([])
      expect(result.current.loadedPieceCount).toBe(150)
      expect(result.current.isTranscriptIncomplete).toBe(false)
    })

    it('should retain all accumulated scores and paging state if a later refresh page fails, then allow retry', async () => {
      const changedScore = { ...humanScore, score_value: 'False' }
      getMessages.mockResolvedValueOnce(longPage(0, 100, 'original-cursor'))
        .mockResolvedValueOnce(longPage(100, 150, null, [humanScore]))
        .mockResolvedValueOnce(longPage(0, 100, 'failed-refresh-cursor'))
        .mockRejectedValueOnce(new Error('Score service busy'))
        .mockResolvedValueOnce(longPage(0, 100, 'retry-cursor'))
        .mockResolvedValueOnce(longPage(100, 150, null, [changedScore]))
      const { result } = renderSaved()
      await waitFor(() => expect(result.current.loadedPieceCount).toBe(100))
      await continueLoading(result)
      const messages = result.current.messages
      const pagination = result.current.messagePagination

      await act(async () => {
        await expect(result.current.refreshConversationScores('attack', 'main')).rejects.toThrow('Score service busy')
      })
      expect(result.current.messages).toBe(messages)
      expect(displayedScores(result.current.messages)).toEqual([expect.objectContaining(humanScore)])
      expect(result.current.messagePagination).toBe(pagination)
      expect(result.current.loadedPieceCount).toBe(150)
      expect(result.current.isTranscriptIncomplete).toBe(false)
      expect(result.current.conversationLoadIssue).toBeNull()
      expect(result.current.messageContinuationIssue).toBeNull()
      await act(async () => { await result.current.refreshConversationScores('attack', 'main') })
      expect(displayedScores(result.current.messages)).toEqual([expect.objectContaining(changedScore)])
      expect(result.current.messagePagination).toBe(pagination)
      expect(getMessages.mock.calls.slice(2).every((call: Parameters<typeof attacksApi.getMessages>) => call[2]?.limit === pageSize)).toBe(true)
    })

    it('should refresh a retained historical score when the backend removes only its objective-override marker', async () => {
      const historicalScore = { ...humanScore, is_objective_score: false }
      getMessages.mockResolvedValueOnce(longPage(0, 100, 'original-cursor'))
        .mockResolvedValueOnce(longPage(100, 150, null, [humanScore]))
        .mockResolvedValueOnce(longPage(0, 100, 'refresh-cursor'))
        .mockResolvedValueOnce(longPage(100, 150, null, [historicalScore]))
      const { result } = renderSaved()
      await waitFor(() => expect(result.current.loadedPieceCount).toBe(100))
      await continueLoading(result)
      await act(async () => { await result.current.refreshConversationScores('attack', 'main') })

      expect(displayedScores(result.current.messages)).toEqual([expect.objectContaining(historicalScore)])
      expect(result.current.loadedPieceCount).toBe(150)
      expect(result.current.isTranscriptIncomplete).toBe(false)
    })

    it('should stop at the already-loaded prefix and fail atomically if a loaded piece is missing', async () => {
      const changedEnd = longPage(100, 149, 'must-not-follow')
      getMessages.mockResolvedValueOnce(longPage(0, 100, 'original-cursor'))
        .mockResolvedValueOnce(longPage(100, 150, null, [humanScore]))
        .mockResolvedValueOnce(longPage(0, 100, 'refresh-cursor'))
        .mockResolvedValueOnce(changedEnd)
      const { result } = renderSaved()
      await waitFor(() => expect(result.current.loadedPieceCount).toBe(100))
      await continueLoading(result)
      const messages = result.current.messages
      const pagination = result.current.messagePagination

      await act(async () => {
        await expect(result.current.refreshConversationScores('attack', 'main')).rejects.toThrow('loaded evidence changed')
      })
      expect(getMessages).toHaveBeenCalledTimes(4)
      expect(result.current.messages).toBe(messages)
      expect(result.current.messagePagination).toBe(pagination)
      expect(result.current.loadedPieceCount).toBe(150)
      expect(result.current.isTranscriptIncomplete).toBe(false)
    })

    it.each(['conversation', 'attack', 'return', 'unmount'])(
      'should discard a score refresh after a %s ownership change', async (change: string) => {
        const pending = deferred<ConversationMessagesResponse>()
        getMessages.mockResolvedValueOnce(longPage(0, 100, 'original-cursor'))
          .mockResolvedValueOnce(longPage(100, 150, null))
          .mockReturnValueOnce(pending.promise)
        const { result, rerender, unmount } = renderSaved()
        await waitFor(() => expect(result.current.loadedPieceCount).toBe(100))
        await continueLoading(result)
        let refresh: Promise<void> = Promise.resolve()
        act(() => { refresh = result.current.refreshConversationScores('attack', 'main') })
        if (change === 'unmount') {
          unmount()
        } else {
          const nextConversationId = change === 'attack' ? 'main' : 'branch'
          getMessages.mockResolvedValueOnce(page(nextConversationId, [{ sequence: 2, ids: ['selected'] }], null))
          rerender({ messagePageSize: pageSize, activeConversationId: nextConversationId,
            attackResultId: change === 'attack' ? 'other-attack' : 'attack' })
          await waitFor(() => expect(contents(result.current.messages)).toEqual(['selected']))
          if (change === 'return') {
            getMessages.mockResolvedValueOnce(page('main', [{ sequence: 2, ids: ['returned'] }], null))
            rerender({ messagePageSize: pageSize })
            await waitFor(() => expect(contents(result.current.messages)).toEqual(['returned']))
          }
        }
        const mapperCalls = jest.mocked(backendMessagesToFrontend).mock.calls.length
        const fetchCalls = getMessages.mock.calls.length
        await act(async () => { pending.resolve(longPage(0, 100, 'stale-cursor', [humanScore])); await refresh })

        expect(backendMessagesToFrontend).toHaveBeenCalledTimes(mapperCalls)
        expect(getMessages).toHaveBeenCalledTimes(fetchCalls)
        if (change !== 'unmount') {
          expect(contents(result.current.messages)).toEqual([change === 'return' ? 'returned' : 'selected'])
          expect(result.current.loadedPieceCount).toBe(1)
          expect(result.current.isTranscriptIncomplete).toBe(false)
        }
      },
    )

    it.each(['resolve', 'reject'])('should let only the latest score refresh apply despite an older %s', async (settlement: string) => {
      const older = deferred<ConversationMessagesResponse>()
      getMessages.mockResolvedValueOnce(longPage(0, 100, 'original-cursor'))
        .mockResolvedValueOnce(longPage(100, 150, null))
        .mockReturnValueOnce(older.promise)
        .mockResolvedValueOnce(longPage(0, 100, 'newer-cursor'))
        .mockResolvedValueOnce(longPage(100, 150, null, [humanScore]))
      const { result } = renderSaved()
      await waitFor(() => expect(result.current.loadedPieceCount).toBe(100))
      await continueLoading(result)
      let refresh: Promise<void> = Promise.resolve()
      act(() => { refresh = result.current.refreshConversationScores('attack', 'main') })
      await act(async () => { await result.current.refreshConversationScores('attack', 'main') })
      const messages = result.current.messages
      await act(async () => {
        if (settlement === 'reject') older.reject(new Error('Superseded score refresh failed'))
        else older.resolve(longPage(0, 100, 'older-cursor'))
        await refresh
      })
      expect(result.current.messages).toBe(messages)
      expect(displayedScores(result.current.messages)).toEqual([expect.objectContaining(humanScore)])
      expect(result.current.loadedPieceCount).toBe(150)
      expect(result.current.isTranscriptIncomplete).toBe(false)
      expect(getMessages).toHaveBeenCalledTimes(5)
    })

    it('should not refresh another attack or conversation through the currently loaded reader', async () => {
      getMessages.mockResolvedValueOnce(first())
      const { result } = renderSaved()
      await waitFor(() => expect(result.current.loadedPieceCount).toBe(2))
      await act(async () => {
        await result.current.refreshConversationScores('other-attack', 'main')
        await result.current.refreshConversationScores('attack', 'branch')
      })
      expect(getMessages).toHaveBeenCalledTimes(1)
      expect(contents(result.current.messages)).toEqual(['a\nb'])
    })

    it('should not let a score refresh supersede an explicit transcript restart already in flight', async () => {
      const restart = deferred<ConversationMessagesResponse>()
      getMessages.mockResolvedValueOnce(longPage(0, 100, 'original-cursor'))
        .mockResolvedValueOnce(longPage(100, 150, null))
        .mockReturnValueOnce(restart.promise)
      const { result } = renderSaved()
      await waitFor(() => expect(result.current.loadedPieceCount).toBe(100))
      await continueLoading(result)
      let pending: Promise<void> = Promise.resolve()
      act(() => { pending = result.current.loadConversation('attack', 'main') })
      await act(async () => {
        await expect(result.current.refreshConversationScores('attack', 'main')).rejects.toThrow('Wait for transcript loading')
      })
      await act(async () => { restart.resolve(longPage(0, 100, 'restarted')); await pending })

      expect(getMessages).toHaveBeenCalledTimes(3)
      expect(result.current.loadedPieceCount).toBe(100)
      expect(result.current.messagePagination?.next_cursor).toBe('restarted')
      expect(result.current.isTranscriptIncomplete).toBe(true)
      expect(result.current.isLoadingMessages).toBe(false)
    })

    it('should prevent duplicate continuations while keeping loaded evidence visible', async () => {
      const continuation = deferred<ConversationMessagesResponse>()
      getMessages.mockResolvedValueOnce(first()).mockReturnValueOnce(continuation.promise)
      const { result } = renderSaved()
      await waitFor(() => expect(result.current.loadedPieceCount).toBe(2))
      let load: Promise<void> = Promise.resolve()
      act(() => {
        load = result.current.loadMoreMessages()
        void result.current.loadMoreMessages()
      })
      expect(getMessages).toHaveBeenCalledTimes(2)
      expect(result.current.isLoadingMoreMessages).toBe(true)
      expect(result.current.isLoadingMessages).toBe(false)
      expect(contents(result.current.messages)).toEqual(['a\nb'])
      await act(async () => { continuation.resolve(page('main', [{ sequence: -1, ids: ['c'] }], null, [-1])); await load })
      expect(result.current.isLoadingMoreMessages).toBe(false)
      await continueLoading(result)
      expect(getMessages).toHaveBeenCalledTimes(2)
    })

    it('should retain pieces and the cursor after a continuation failure and retry the same bounded page', async () => {
      getMessages.mockResolvedValueOnce(first()).mockRejectedValueOnce({
        isAxiosError: true, response: { status: 503, data: { detail: 'Saved evidence is busy' } },
      }).mockResolvedValueOnce(page('main', [{ sequence: -1, ids: ['c'] }], null, [-1]))
      const { result } = renderSaved()
      await waitFor(() => expect(result.current.loadedPieceCount).toBe(2))
      await continueLoading(result)

      expect(contents(result.current.messages)).toEqual(['a\nb'])
      expect(result.current.messagePagination?.next_cursor).toBe('pieces-1')
      expect(result.current.messageContinuationIssue).toEqual({
        detail: 'Could not load more messages: Saved evidence is busy', restartRequired: false,
      })
      expect(result.current.isLoadingMoreMessages).toBe(false)
      expect(result.current.isTranscriptIncomplete).toBe(true)
      await continueLoading(result)
      expect(contents(result.current.messages)).toEqual(['a\nb\nc'])
      expect(result.current.messageContinuationIssue).toBeNull()
      expect(getMessages.mock.calls.slice(1).map((call) => call[2])).toEqual([
        { limit: 100, cursor: 'pieces-1' }, { limit: 100, cursor: 'pieces-1' },
      ])
    })

    it('should require a bounded restart for an invalid continuation and preserve evidence until it succeeds', async () => {
      getMessages.mockResolvedValueOnce(first()).mockRejectedValueOnce({
        isAxiosError: true, response: { status: 400, data: { detail: 'Invalid saved-message cursor' } },
      }).mockRejectedValueOnce(new Error('Restart failed')).mockResolvedValueOnce(
        page('main', [{ sequence: 0, ids: ['fresh'] }], 'new-cursor'),
      )
      const { result } = renderSaved()
      await waitFor(() => expect(result.current.loadedPieceCount).toBe(2))
      await continueLoading(result)
      expect(result.current.messageContinuationIssue?.restartRequired).toBe(true)
      await continueLoading(result)
      expect(getMessages).toHaveBeenCalledTimes(2)
      await act(async () => { await result.current.loadConversation('attack', 'main') })
      expect(contents(result.current.messages)).toEqual(['a\nb'])
      expect(result.current.conversationLoadIssue?.detail).toContain('Restart failed')
      await act(async () => { await result.current.loadConversation('attack', 'main') })
      expect(contents(result.current.messages)).toEqual(['fresh'])
      expect(result.current.messagePagination?.next_cursor).toBe('new-cursor')
      expect(result.current.conversationLoadIssue).toBeNull()
      expect(getMessages.mock.calls.slice(2).map((call) => call[2])).toEqual([{ limit: 100 }, { limit: 100 }])
    })

    it.each(['conversation', 'attack'])('should discard late continuation evidence across a %s change', async (change: string) => {
      const continuation = deferred<ConversationMessagesResponse>()
      const nextConversationId = change === 'conversation' ? 'branch' : 'main'
      getMessages.mockResolvedValueOnce(first()).mockReturnValueOnce(continuation.promise).mockResolvedValueOnce(
        page(nextConversationId, [{ sequence: 0, ids: ['selected'] }], 'selected-cursor'),
      )
      const { result, rerender } = renderSaved()
      await waitFor(() => expect(result.current.loadedPieceCount).toBe(2))
      let load: Promise<void> = Promise.resolve()
      act(() => { load = result.current.loadMoreMessages() })
      rerender({ messagePageSize: pageSize, activeConversationId: nextConversationId,
        attackResultId: change === 'attack' ? 'other-attack' : 'attack' })
      await waitFor(() => expect(contents(result.current.messages)).toEqual(['selected']))
      await act(async () => { continuation.resolve(page('main', [{ sequence: -1, ids: ['stale'] }], null)); await load })

      expect(contents(result.current.messages)).toEqual(['selected'])
      expect(result.current.messagePagination?.next_cursor).toBe('selected-cursor')
      expect(result.current.loadedPieceCount).toBe(1)
      expect(result.current.isLoadingMoreMessages).toBe(false)
      expect(getMessages.mock.calls[2]).toEqual([change === 'attack' ? 'other-attack' : 'attack', nextConversationId, { limit: 100 }])
    })

    it('should discard a late continuation error after selection changes', async () => {
      const continuation = deferred<ConversationMessagesResponse>()
      getMessages.mockResolvedValueOnce(first()).mockReturnValueOnce(continuation.promise)
        .mockResolvedValueOnce(page('branch', [{ sequence: 0, ids: ['branch'] }], null))
      const { result, rerender } = renderSaved()
      await waitFor(() => expect(result.current.loadedPieceCount).toBe(2))
      act(() => { void result.current.loadMoreMessages() })
      rerender({ messagePageSize: pageSize, activeConversationId: 'branch' })
      await waitFor(() => expect(contents(result.current.messages)).toEqual(['branch']))
      await act(async () => { continuation.reject(new Error('Late failure')) })
      expect(result.current.messageContinuationIssue).toBeNull()
      expect(result.current.conversationLoadIssue).toBeNull()
    })

    it.each(['initial', 'continuation'])('should ignore a late %s response after unmount', async (phase: string) => {
      const pending = deferred<ConversationMessagesResponse>()
      const onConversationLoaded = jest.fn((_id: string, _response: ConversationMessagesResponse, messages: Message[]) => messages)
      if (phase === 'continuation') getMessages.mockResolvedValueOnce(first())
      getMessages.mockReturnValueOnce(pending.promise)
      const { result, unmount } = renderSaved({ onConversationLoaded })
      if (phase === 'continuation') {
        await waitFor(() => expect(result.current.loadedPieceCount).toBe(2))
        act(() => { void result.current.loadMoreMessages() })
      }
      const loadedCalls = onConversationLoaded.mock.calls.length
      unmount()
      await act(async () => { pending.resolve(page('main', [{ sequence: 0, ids: ['late'] }], null)) })
      expect(onConversationLoaded).toHaveBeenCalledTimes(loadedCalls)
    })

    it('should yield a continuation to the incumbent reader ownership claim', async () => {
      const pending = deferred<ConversationMessagesResponse>()
      getMessages.mockResolvedValueOnce(first()).mockReturnValueOnce(pending.promise)
      const { result } = renderSaved()
      await waitFor(() => expect(result.current.loadedPieceCount).toBe(2))
      act(() => { void result.current.loadMoreMessages() })
      const newerRead = result.current.claimConversationRead('main')
      await act(async () => { pending.resolve(page('main', [{ sequence: 0, ids: ['superseded'] }], null)) })
      expect(contents(result.current.messages)).toEqual(['a\nb'])
      expect(result.current.isLatestConversationRead('main', newerRead)).toBe(true)
      expect(result.current.isLoadingMoreMessages).toBe(false)
    })

    it.each(['missing', 'missing-cursor', 'wrong-conversation', 'missing-has-more', 'missing-limit', 'invalid-limit'])(
      'should fail closed for %s initial paging metadata', async (shape: string) => {
      const response = first()
      if (shape === 'missing') response.pagination = null
      if (shape === 'missing-cursor' && response.pagination) response.pagination.next_cursor = null
      if (shape === 'wrong-conversation') response.conversation_id = 'other'
      if (shape === 'missing-has-more' && response.pagination) Reflect.deleteProperty(response.pagination, 'has_more')
      if (shape === 'missing-limit' && response.pagination) Reflect.deleteProperty(response.pagination, 'limit')
      if (shape === 'invalid-limit' && response.pagination) response.pagination.limit = 501
      getMessages.mockResolvedValueOnce(response)
      const { result } = renderSaved()
      await waitFor(() => expect(result.current.conversationLoadIssue?.detail).toContain('invalid transcript pagination'))
      expect(result.current.messages).toEqual([])
      expect(result.current.isTranscriptIncomplete).toBe(true)
      expect(result.current.awaitingConversationLoad).toBe(false)
      expect(getMessages).toHaveBeenCalledTimes(1)
      expect(getMessages).toHaveBeenCalledWith('attack', 'main', { limit: 100 })
    })

    it('should not mistake missing continuation metadata for the end of the transcript', async () => {
      const incompleteEnd = page('main', [{ sequence: 0, ids: ['missing-end'] }], null)
      if (incompleteEnd.pagination) Reflect.deleteProperty(incompleteEnd.pagination, 'has_more')
      getMessages.mockResolvedValueOnce(first()).mockResolvedValueOnce(incompleteEnd)
      const { result } = renderSaved()
      await waitFor(() => expect(result.current.loadedPieceCount).toBe(2))
      await continueLoading(result)
      expect(result.current.isTranscriptIncomplete).toBe(true)
      expect(result.current.loadedPieceCount).toBe(2)
      expect(result.current.messageContinuationIssue?.restartRequired).toBe(true)
    })

    it.each(['initial', 'continuation'])('should keep a selected %s read valid when only the main-conversation metadata changes', async (phase: string) => {
      const pending = deferred<ConversationMessagesResponse>()
      if (phase === 'continuation') getMessages.mockResolvedValueOnce(first())
      getMessages.mockReturnValueOnce(pending.promise)
      const { result, rerender } = renderSaved()
      if (phase === 'continuation') {
        await waitFor(() => expect(result.current.loadedPieceCount).toBe(2))
        act(() => { void result.current.loadMoreMessages() })
      }
      rerender({ messagePageSize: pageSize, conversationId: 'new-main', activeConversationId: 'main' })
      await act(async () => { pending.resolve(page('main', [{ sequence: -1, ids: ['c'] }], null, [-1])) })
      expect(contents(result.current.messages)).toEqual([phase === 'initial' ? 'c' : 'a\nb\nc'])
      expect(result.current.isTranscriptIncomplete).toBe(false)
      expect(getMessages).toHaveBeenCalledTimes(phase === 'initial' ? 1 : 2)
    })

    it('should keep a repeated cursor from silently ending or extending a transcript', async () => {
      getMessages.mockResolvedValueOnce(first()).mockResolvedValueOnce(
        page('main', [{ sequence: -1, ids: ['c'] }], 'pieces-1', [-1]),
      )
      const { result } = renderSaved()
      await waitFor(() => expect(result.current.loadedPieceCount).toBe(2))
      await continueLoading(result)
      expect(contents(result.current.messages)).toEqual(['a\nb'])
      expect(result.current.messageContinuationIssue?.restartRequired).toBe(true)
      expect(result.current.isTranscriptIncomplete).toBe(true)
    })

    it('should reject a cursor cycle before requesting an already consumed page', async () => {
      getMessages.mockResolvedValueOnce(first()).mockResolvedValueOnce(
        page('main', [{ sequence: -1, ids: ['c'] }], 'pieces-2', [-1]),
      ).mockResolvedValueOnce(page('main', [{ sequence: 0, ids: ['d'] }], 'pieces-1'))
      const { result } = renderSaved()
      await waitFor(() => expect(result.current.loadedPieceCount).toBe(2))
      await continueLoading(result)
      await continueLoading(result)
      expect(contents(result.current.messages)).toEqual(['a\nb\nc'])
      expect(result.current.messageContinuationIssue?.restartRequired).toBe(true)
      await continueLoading(result)
      expect(getMessages).toHaveBeenCalledTimes(3)
    })

    it('should clear another attack transcript on an initial failure even when both reference the same conversation', async () => {
      getMessages.mockResolvedValueOnce(first()).mockRejectedValueOnce(new Error('Other attack unavailable'))
      const { result, rerender } = renderSaved()
      await waitFor(() => expect(result.current.loadedPieceCount).toBe(2))
      rerender({ messagePageSize: pageSize, attackResultId: 'other-attack' })
      await waitFor(() => expect(result.current.conversationLoadIssue?.detail).toContain('Other attack unavailable'))
      expect(result.current.messages).toEqual([])
      expect(result.current.loadedPieceCount).toBe(0)
      expect(result.current.messagePagination).toBeNull()
    })

    it('should distinguish an empty complete transcript from incomplete evidence', async () => {
      getMessages.mockResolvedValueOnce(page('main', [], 'more')).mockResolvedValueOnce(page('main', [], null))
      const { result } = renderSaved({ findingEvidenceId: 'finding' })
      await waitFor(() => expect(result.current.loadedConversationId).toBe('main'))
      expect(result.current.isEvidenceUnavailable).toBe(false)
      expect(result.current.isTranscriptIncomplete).toBe(true)
      await act(async () => { await result.current.loadConversation('attack', 'main') })
      expect(result.current.isEvidenceUnavailable).toBe(true)
      expect(result.current.isTranscriptIncomplete).toBe(false)
      expect(result.current.loadedPieceCount).toBe(0)
    })
  })

  describe('send ownership handoff', () => {
    function savedTranscript(conversationId: string): ConversationMessagesResponse {
      return {
        conversation_id: conversationId,
        messages: (['user', 'assistant'] as const).map((role, turn) => ({
          role,
          turn_number: turn,
          created_at: '2026-01-01T00:00:00Z',
          message_pieces: [{
            id: `${conversationId}-${role}`,
            original_value_data_type: 'text',
            converted_value_data_type: 'text',
            original_value: `${conversationId} ${role} text`,
            converted_value: `${conversationId} ${role} text`,
            scores: [],
            response_error: 'none',
          }],
        })),
        target_response_status: {
          response_error: 'none', request_turn_number: 0, response_turn_number: 1,
        },
      }
    }

    beforeEach(() => {
      const mapper = jest.requireActual<typeof import('@/utils/messageMapper')>('@/utils/messageMapper')
      jest.mocked(backendMessagesToFrontend).mockImplementation(mapper.backendMessagesToFrontend)
    })

    it('loads the exact saved transcript once when selected send ownership ends with unchanged IDs', async () => {
      let sending = true
      const isSendInFlight = (): boolean => sending
      getMessages.mockResolvedValue(savedTranscript('main'))
      const { result, rerender } = renderMessages({
        sendingConversations: new Set(['main']), isSendInFlight,
      })
      expect(getMessages).not.toHaveBeenCalled()
      expect(result.current.awaitingConversationLoad).toBe(false)

      sending = false
      rerender({ sendingConversations: notSending, isSendInFlight })

      await waitFor(() => expect(result.current.loadedConversationId).toBe('main'))
      expect(getMessages).toHaveBeenCalledTimes(1)
      expect(getMessages).toHaveBeenCalledWith('attack', 'main')
      expect(contents(result.current.messages)).toEqual(['main user text', 'main assistant text'])
      expect(result.current.messages.map((message) => message.role)).toEqual(['user', 'assistant'])
      expect(result.current.getLoadedConversationId()).toBe('main')
      expect(result.current.awaitingConversationLoad).toBe(false)
      expect(result.current.isLoadingMessages).toBe(false)
    })

    it('does not reload an already-loaded successful send when ownership ends', () => {
      let sending = true
      const isSendInFlight = (): boolean => sending
      const { result, rerender } = renderMessages({
        sendingConversations: new Set(['main']), isSendInFlight,
      })
      act(() => {
        result.current.setMessages(backendMessagesToFrontend(savedTranscript('main').messages))
        result.current.markConversationLoaded('main')
      })

      sending = false
      rerender({ sendingConversations: notSending, isSendInFlight })

      expect(getMessages).not.toHaveBeenCalled()
      expect(contents(result.current.messages)).toEqual(['main user text', 'main assistant text'])
      expect(result.current.loadedConversationId).toBe('main')
      expect(result.current.awaitingConversationLoad).toBe(false)
    })

    it('does not supersede a pending selected read when another conversation starts or stops sending', async () => {
      const load = deferred<ConversationMessagesResponse>()
      getMessages.mockReturnValueOnce(load.promise)
      const isSendInFlight = (id: string): boolean => id === 'other'
      const { result, rerender } = renderMessages({ isSendInFlight })

      rerender({ isSendInFlight, sendingConversations: new Set(['other']) })
      rerender({ isSendInFlight, sendingConversations: notSending })

      expect(getMessages).toHaveBeenCalledTimes(1)
      expect(getMessages).toHaveBeenCalledWith('attack', 'main')
      await act(async () => { load.resolve(savedTranscript('main')) })
      expect(contents(result.current.messages)).toEqual(['main user text', 'main assistant text'])
      expect(result.current.loadedConversationId).toBe('main')
      expect(result.current.awaitingConversationLoad).toBe(false)
    })

    it('does not apply a completed handoff read after navigating to another conversation', async () => {
      const mainLoad = deferred<ConversationMessagesResponse>()
      const branchLoad = deferred<ConversationMessagesResponse>()
      getMessages.mockReturnValueOnce(mainLoad.promise).mockReturnValueOnce(branchLoad.promise)
      let sending = true
      const isSendInFlight = (id: string): boolean => id === 'main' && sending
      const { result, rerender } = renderMessages({
        isSendInFlight, sendingConversations: new Set(['main']),
      })
      sending = false
      rerender({ isSendInFlight, sendingConversations: notSending })
      expect(getMessages).toHaveBeenCalledTimes(1)
      expect(getMessages).toHaveBeenLastCalledWith('attack', 'main')

      rerender({ activeConversationId: 'branch', isSendInFlight, sendingConversations: notSending })
      expect(getMessages).toHaveBeenCalledTimes(2)
      expect(getMessages).toHaveBeenLastCalledWith('attack', 'branch')
      await act(async () => { mainLoad.resolve(savedTranscript('main')) })
      expect(result.current.messages).toEqual([])
      expect(result.current.loadedConversationId).toBeNull()
      expect(result.current.isLoadingMessages).toBe(true)

      await act(async () => { branchLoad.resolve(savedTranscript('branch')) })
      expect(contents(result.current.messages)).toEqual(['branch user text', 'branch assistant text'])
      expect(result.current.loadedConversationId).toBe('branch')
      expect(result.current.awaitingConversationLoad).toBe(false)
      expect(result.current.isLoadingMessages).toBe(false)
    })
  })
})
