import { act, renderHook, waitFor } from '@testing-library/react'

import { attacksApi } from '@/services/api'
import { deferred } from '@/test-utils/deferred'
import type { BackendMessage, ConversationMessagesResponse, Message } from '@/types'
import { backendMessagesToFrontend } from '@/utils/messageMapper'

import { useConversationMessages } from './useConversationMessages'
import type { UseConversationMessagesOptions } from './useConversationMessages'

jest.mock('@/services/api', () => ({ attacksApi: { getMessages: jest.fn() } }))
jest.mock('@/utils/messageMapper', () => ({ backendMessagesToFrontend: jest.fn() }))

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

  it('should not load or report a load gap for a conversation that is sending', () => {
    const { result } = renderMessages({ sendingConversations: new Set(['main']), isSendInFlight: () => true })

    expect(getMessages).not.toHaveBeenCalled()
    expect(result.current.awaitingConversationLoad).toBe(false)
    expect(result.current.isLoadingMessages).toBe(false)
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

  it('should skip loaded or sending conversations unless a forced load is requested', async () => {
    getMessages.mockResolvedValue(transcript('main'))
    const isSendInFlight = (conversationId: string): boolean => conversationId === 'branch'
    const sendingConversations = new Set(['branch'])
    const { result, rerender } = renderMessages({ isSendInFlight, sendingConversations })
    await waitFor(() => expect(result.current.loadedConversationId).toBe('main'))
    expect(getMessages).toHaveBeenCalledTimes(1)

    rerender({ activeConversationId: 'branch', isSendInFlight, sendingConversations })
    rerender({ activeConversationId: 'main', isSendInFlight, sendingConversations })
    expect(getMessages).toHaveBeenCalledTimes(1)

    getMessages.mockResolvedValueOnce(transcript('branch'))
    act(() => { result.current.requestForcedLoad() })
    rerender({ activeConversationId: 'branch', isSendInFlight, sendingConversations })

    expect(getMessages).toHaveBeenCalledTimes(2)
    expect(getMessages).toHaveBeenLastCalledWith('attack', 'branch')
    await waitFor(() => expect(result.current.loadedConversationId).toBe('branch'))
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
