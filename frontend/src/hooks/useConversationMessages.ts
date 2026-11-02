import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import type { Dispatch, SetStateAction } from 'react'

import { attacksApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { ConversationMessagesResponse, Message } from '@/types'
import { backendMessagesToFrontend } from '@/utils/messageMapper'

export interface ConversationLoadIssue {
  conversationId: string
  unavailable: boolean
  detail: string
}

interface ConversationLoadRequest {
  conversationId: string
  requestId: number
}

export interface UseConversationMessagesOptions {
  attackResultId: string | null
  conversationId: string | null
  activeConversationId: string | null
  findingEvidenceId?: string | null
  /** Conversations with an in-flight send; render-safe state used for the loading derivation. */
  sendingConversations: ReadonlySet<string>
  /** Synchronous send ownership check used by the reload guard. */
  isSendInFlight: (conversationId: string) => boolean
  /** Records state from a current load and returns the transcript to show, e.g. with an in-flight send. */
  onConversationLoaded?: (
    conversationId: string, response: ConversationMessagesResponse, messages: Message[],
  ) => Message[]
}

export interface ConversationMessages {
  messages: Message[]
  setMessages: Dispatch<SetStateAction<Message[]>>
  /** Which conversation's messages are currently loaded (set after fetch completes). */
  loadedConversationId: string | null
  /** True while an async message fetch is in-flight. */
  isLoadingMessages: boolean
  /** True between a conversation change and the end of its load. */
  awaitingConversationLoad: boolean
  conversationLoadIssue: ConversationLoadIssue | null
  isEvidenceUnavailable: boolean
  loadConversation: (attackResultId: string, conversationId: string) => Promise<void>
  /** Lets the next conversation change load even while that conversation is sending. */
  requestForcedLoad: () => void
  markConversationLoaded: (conversationId: string | null) => void
  /** Clears the transcript when the attack is reset; safe to call while rendering. */
  resetConversationMessages: () => void
  invalidateConversationLoads: (conversationId: string) => void
  /** Claims the latest read of a conversation so older loads cannot replace it. */
  claimConversationRead: (conversationId: string) => number
  isLatestConversationRead: (conversationId: string, readId: number) => boolean
  getViewedConversationId: () => string | null
  setViewedConversationId: (conversationId: string | null) => void
  getLoadedConversationId: () => string | null
}

export function useConversationMessages({
  attackResultId,
  conversationId,
  activeConversationId,
  findingEvidenceId,
  sendingConversations,
  isSendInFlight,
  onConversationLoaded,
}: UseConversationMessagesOptions): ConversationMessages {
  const [messages, setMessages] = useState<Message[]>([])
  const [conversationLoadIssue, setConversationLoadIssue] = useState<ConversationLoadIssue | null>(null)
  const [isLoadingMessages, setIsLoadingMessages] = useState(false)
  const [loadedConversationId, setLoadedConversationId] = useState<string | null>(null)
  const loadedConversationIdRef = useRef<string | null>(null)
  const nextReadIdRef = useRef(0)
  const latestReadIdsRef = useRef<Map<string, number>>(new Map())
  const activeLoadRef = useRef<ConversationLoadRequest | null>(null)
  const sendCallbacksRef = useRef({ isSendInFlight, onConversationLoaded })
  useLayoutEffect(() => {
    sendCallbacksRef.current = { isSendInFlight, onConversationLoaded }
  }, [isSendInFlight, onConversationLoaded])

  const viewedConversationId = activeConversationId ?? conversationId
  const isEvidenceUnavailable = Boolean(
    findingEvidenceId && conversationLoadIssue?.conversationId === viewedConversationId
    && conversationLoadIssue?.unavailable,
  )

  const markConversationLoaded = useCallback((loadedId: string | null): void => {
    loadedConversationIdRef.current = loadedId
    setLoadedConversationId(loadedId)
  }, [])

  const invalidateConversationLoads = useCallback((conversationIdToInvalidate: string): void => {
    latestReadIdsRef.current.delete(conversationIdToInvalidate)
    if (activeLoadRef.current?.conversationId === conversationIdToInvalidate) {
      activeLoadRef.current = null
      setIsLoadingMessages(false)
    }
  }, [])

  const claimConversationRead = useCallback((conversationIdToRead: string): number => {
    nextReadIdRef.current += 1
    latestReadIdsRef.current.set(conversationIdToRead, nextReadIdRef.current)
    return nextReadIdRef.current
  }, [])

  const isLatestConversationRead = useCallback((conversationIdToRead: string, readId: number): boolean => (
    latestReadIdsRef.current.get(conversationIdToRead) === readId
  ), [])

  useLayoutEffect(() => {
    loadedConversationIdRef.current = loadedConversationId
  }, [loadedConversationId])

  // Set by panel click to bypass the in-flight guard on the next useEffect cycle.
  // This lets users switch to a sending conversation while still protecting
  // optimistic messages when a send internally updates activeConversationId.
  const forceLoadRef = useRef(false)
  // Always-current ref of the conversation being viewed so async callbacks can
  // check whether the user navigated away while a request was in-flight.
  const viewedConvRef = useRef(activeConversationId ?? conversationId)
  useLayoutEffect(() => {
    viewedConvRef.current = activeConversationId ?? conversationId
    return () => { viewedConvRef.current = null }
  }, [activeConversationId, conversationId])

  const resetConversationMessages = useCallback((): void => {
    setMessages([])
    setLoadedConversationId(null)
  }, [])

  // Load messages for a given conversation
  const loadConversation = useCallback(async (arId: string, convId: string) => {
    const requestId = claimConversationRead(convId)
    activeLoadRef.current = { conversationId: convId, requestId }
    setIsLoadingMessages(true)
    const isCurrentLoad = (): boolean => isLatestConversationRead(convId, requestId)

    try {
      const response = await attacksApi.getMessages(arId, convId)
      // Discard superseded loads and responses invalidated by a send.
      if (!isCurrentLoad() || viewedConvRef.current !== convId) { return }
      setConversationLoadIssue(
        findingEvidenceId && response.messages.length === 0
          ? { conversationId: convId, unavailable: true, detail: 'Evidence unavailable' }
          : null,
      )
      const frontendMessages = backendMessagesToFrontend(response.messages)
      const onLoaded = sendCallbacksRef.current.onConversationLoaded
      setMessages(onLoaded ? onLoaded(convId, response, frontendMessages) : frontendMessages)
      markConversationLoaded(convId)
    } catch (cause: unknown) {
      if (!isCurrentLoad() || viewedConvRef.current !== convId) { return }
      const error = toApiError(cause)
      setConversationLoadIssue({
        conversationId: convId, unavailable: error.status === 404,
        detail: error.status === 404 && findingEvidenceId
          ? 'Evidence unavailable' : `Could not load conversation: ${error.detail}`,
      })
      // Initial-load failures must not show another conversation's transcript.
      // Refresh failures keep the already-loaded transcript and recovery aligned.
      if (loadedConversationIdRef.current !== convId) {
        setMessages([])
        markConversationLoaded(convId)
      }
    } finally {
      if (isCurrentLoad()) {
        latestReadIdsRef.current.delete(convId)
      }
      if (activeLoadRef.current?.requestId === requestId) {
        activeLoadRef.current = null
        setIsLoadingMessages(false)
      }
    }
  }, [claimConversationRead, isLatestConversationRead, markConversationLoaded, findingEvidenceId])

  // Reload messages when activeConversationId changes
  useEffect(() => {
    if (!attackResultId || !activeConversationId) { return }
    // A created-attack route can commit after its first send completes.
    // Preserve that local result unless the user explicitly requests a refresh.
    const force = forceLoadRef.current
    forceLoadRef.current = false
    if (!force && (
      sendCallbacksRef.current.isSendInFlight(activeConversationId)
      || (
        loadedConversationIdRef.current === activeConversationId
        && activeLoadRef.current === null
      )
    )) { return }
    loadConversation(attackResultId, activeConversationId)
  }, [activeConversationId, attackResultId, loadConversation])

  // Synchronous loading derivation: if activeConversationId differs from the
  // conversation whose messages we've loaded, we're in a transition gap.
  // This avoids the 1-frame flash between useEffect fire and render.
  // Reads `sendingConversations` (state) rather than a send ref so the
  // computation stays render-safe.
  const awaitingConversationLoad = Boolean(
    activeConversationId && activeConversationId !== loadedConversationId
    && !sendingConversations.has(activeConversationId)
  )

  const requestForcedLoad = useCallback((): void => { forceLoadRef.current = true }, [])
  const getViewedConversationId = useCallback((): string | null => viewedConvRef.current, [])
  const setViewedConversationId = useCallback((id: string | null): void => { viewedConvRef.current = id }, [])
  const getLoadedConversationId = useCallback((): string | null => loadedConversationIdRef.current, [])

  return {
    messages,
    setMessages,
    loadedConversationId,
    isLoadingMessages,
    awaitingConversationLoad,
    conversationLoadIssue,
    isEvidenceUnavailable,
    loadConversation,
    requestForcedLoad,
    markConversationLoaded,
    resetConversationMessages,
    invalidateConversationLoads,
    claimConversationRead,
    isLatestConversationRead,
    getViewedConversationId,
    setViewedConversationId,
    getLoadedConversationId,
  }
}
