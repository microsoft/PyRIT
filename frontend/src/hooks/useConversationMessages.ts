import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import type { Dispatch, SetStateAction } from 'react'

import { attacksApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { BackendMessage, BackendMessagePiece, BackendScore, ConversationMessagesResponse, Message, PaginationInfo } from '@/types'
import { backendMessagesToFrontend, mergeBackendMessages } from '@/utils/messageMapper'

export interface ConversationLoadIssue {
  conversationId: string
  unavailable: boolean
  detail: string
}

interface ConversationLoadRequest {
  attackResultId: string
  conversationId: string
  requestId: number
}

interface SavedTranscriptPage {
  attackResultId: string
  conversationId: string
  pageSize: number
  response: ConversationMessagesResponse | null
  consumedCursors: ReadonlySet<string>
}

function mergeTranscriptPage(
  previous: ConversationMessagesResponse | null, incoming: ConversationMessagesResponse, conversationId: string,
  cursor?: string, consumedCursors?: ReadonlySet<string>,
): ConversationMessagesResponse {
  const pagination = incoming.pagination
  if (incoming.conversation_id !== conversationId || !pagination || typeof pagination.has_more !== 'boolean'
    || !Number.isInteger(pagination.limit) || pagination.limit < 1 || pagination.limit > 500
    || (pagination.has_more && (!pagination.next_cursor || pagination.next_cursor === cursor
      || consumedCursors?.has(pagination.next_cursor)))) {
    throw new Error('The server returned invalid transcript pagination. Restart transcript loading.')
  }
  const messages = mergeBackendMessages(previous?.messages ?? [], incoming.messages)
  const lastSequence = messages[messages.length - 1]?.turn_number
  // A continuation's leading partial turn is complete once our prefix reaches a later turn or the end.
  const partialSequences = pagination.has_more && lastSequence !== undefined
    && incoming.partial_message_sequences?.includes(lastSequence) ? [lastSequence] : []
  return {
    ...incoming,
    messages,
    partial_message_sequences: partialSequences,
    target_response_status: pagination.has_more || partialSequences.length > 0 ? null : incoming.target_response_status,
  }
}

export interface UseConversationMessagesOptions {
  attackResultId: string | null
  conversationId: string | null
  activeConversationId: string | null
  findingEvidenceId?: string | null
  /** Opts saved evidence into piece-bounded reads; live Chat keeps its existing full reads. */
  messagePageSize?: number
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
  messagePagination: PaginationInfo | null
  loadedPieceCount: number
  isTranscriptIncomplete: boolean
  isLoadingMoreMessages: boolean
  messageContinuationIssue: { detail: string; restartRequired: boolean } | null
  loadMoreMessages: () => Promise<void>
  loadConversation: (attackResultId: string, conversationId: string) => Promise<void>
  /** Refreshes scores without replacing saved evidence or its paging state. */
  refreshConversationScores: (attackResultId: string, conversationId: string) => Promise<void>
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
  messagePageSize,
  sendingConversations,
  isSendInFlight,
  onConversationLoaded,
}: UseConversationMessagesOptions): ConversationMessages {
  const [messages, setMessages] = useState<Message[]>([])
  const [conversationLoadIssue, setConversationLoadIssue] = useState<ConversationLoadIssue | null>(null)
  const [isLoadingMessages, setIsLoadingMessages] = useState(false)
  const [loadedConversationId, setLoadedConversationId] = useState<string | null>(null)
  const [loadedAttackResultId, setLoadedAttackResultId] = useState<string | null>(null)
  const [savedTranscriptPage, setSavedTranscriptPage] = useState<SavedTranscriptPage | null>(null)
  const [isLoadingMoreMessages, setIsLoadingMoreMessages] = useState(false)
  const [messageContinuationIssue, setMessageContinuationIssue] = useState<{ detail: string; restartRequired: boolean } | null>(null)
  const savedTranscriptPageRef = useRef<SavedTranscriptPage | null>(null)
  const loadedAttackResultIdRef = useRef<string | null>(null)
  const loadedConversationIdRef = useRef<string | null>(null)
  const viewedAttackRef = useRef(attackResultId)
  const viewRevisionRef = useRef(0)
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
    loadedAttackResultIdRef.current = viewedAttackRef.current
    setLoadedConversationId(loadedId)
    setLoadedAttackResultId(viewedAttackRef.current)
  }, [])

  const saveTranscriptPage = useCallback((page: SavedTranscriptPage): void => {
    savedTranscriptPageRef.current = page
    setSavedTranscriptPage(page)
  }, [])

  const invalidateConversationLoads = useCallback((conversationIdToInvalidate: string): void => {
    latestReadIdsRef.current.delete(conversationIdToInvalidate)
    if (activeLoadRef.current?.conversationId === conversationIdToInvalidate) {
      activeLoadRef.current = null
      setIsLoadingMessages(false)
      setIsLoadingMoreMessages(false)
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
    viewRevisionRef.current += 1
    viewedAttackRef.current = attackResultId
    viewedConvRef.current = viewedConversationId
    return () => {
      viewRevisionRef.current += 1
      viewedAttackRef.current = null
      viewedConvRef.current = null
    }
  }, [viewedConversationId, attackResultId, messagePageSize])

  const resetConversationMessages = useCallback((): void => {
    setMessages([])
    setLoadedConversationId(null)
    setLoadedAttackResultId(null)
    setSavedTranscriptPage(null)
    setConversationLoadIssue(null)
    setMessageContinuationIssue(null)
    setIsLoadingMessages(false)
    setIsLoadingMoreMessages(false)
  }, [])

  // Load messages for a given conversation
  const loadConversation = useCallback(async (arId: string, convId: string, rejectOnFailure: boolean = false): Promise<void> => {
    const requestId = claimConversationRead(convId)
    const viewRevision = viewRevisionRef.current
    activeLoadRef.current = { attackResultId: arId, conversationId: convId, requestId }
    setIsLoadingMessages(true)
    setIsLoadingMoreMessages(false)
    const isCurrentLoad = (): boolean => isLatestConversationRead(convId, requestId)
      && viewedAttackRef.current === arId && viewedConvRef.current === convId && viewRevisionRef.current === viewRevision

    try {
      const incoming = messagePageSize === undefined
        ? await attacksApi.getMessages(arId, convId)
        : await attacksApi.getMessages(arId, convId, { limit: messagePageSize })
      // Discard superseded loads and responses invalidated by a send.
      if (!isCurrentLoad()) { return }
      const response = messagePageSize === undefined ? incoming : mergeTranscriptPage(null, incoming, convId)
      setMessageContinuationIssue(null)
      if (messagePageSize !== undefined) {
        saveTranscriptPage({ attackResultId: arId, conversationId: convId, pageSize: messagePageSize, response, consumedCursors: new Set() })
      }
      setConversationLoadIssue(
        findingEvidenceId && response.messages.length === 0 && !response.pagination?.has_more
          ? { conversationId: convId, unavailable: true, detail: 'Evidence unavailable' }
          : null,
      )
      const frontendMessages = backendMessagesToFrontend(
        response.messages, messagePageSize === undefined ? undefined : response.partial_message_sequences,
      )
      const onLoaded = sendCallbacksRef.current.onConversationLoaded
      setMessages(onLoaded ? onLoaded(convId, response, frontendMessages) : frontendMessages)
      markConversationLoaded(convId)
    } catch (cause: unknown) {
      if (!isCurrentLoad()) { return }
      const error = toApiError(cause)
      setConversationLoadIssue({
        conversationId: convId, unavailable: error.status === 404,
        detail: error.status === 404 && findingEvidenceId
          ? 'Evidence unavailable' : `Could not load conversation: ${error.detail}`,
      })
      // Initial-load failures must not show another conversation's transcript.
      // Refresh failures keep the already-loaded transcript and recovery aligned.
      if (loadedConversationIdRef.current !== convId || loadedAttackResultIdRef.current !== arId) {
        setMessageContinuationIssue(null)
        setMessages([])
        markConversationLoaded(convId)
        if (messagePageSize !== undefined) {
          saveTranscriptPage({
            attackResultId: arId, conversationId: convId, pageSize: messagePageSize, response: null, consumedCursors: new Set(),
          })
        }
      }
      if (rejectOnFailure) {
        const refreshError = new Error(`Could not refresh conversation scores: ${error.detail}`)
        Object.defineProperty(refreshError, 'cause', { value: cause })
        throw refreshError
      }
    } finally {
      if (isLatestConversationRead(convId, requestId)) {
        latestReadIdsRef.current.delete(convId)
      }
      if (activeLoadRef.current?.requestId === requestId) {
        activeLoadRef.current = null
        if (viewedConvRef.current !== null) {
          setIsLoadingMessages(false)
        }
      }
    }
  }, [claimConversationRead, isLatestConversationRead, markConversationLoaded, findingEvidenceId, messagePageSize, saveTranscriptPage])

  const refreshConversationScores = useCallback(async (arId: string, convId: string): Promise<void> => {
    if (viewedAttackRef.current !== arId || viewedConvRef.current !== convId) return
    if (messagePageSize === undefined) {
      await loadConversation(arId, convId, true)
      return
    }
    if (activeLoadRef.current) throw new Error('Wait for transcript loading to finish before refreshing scores.')
    const page = savedTranscriptPageRef.current
    if (!page?.response || page.attackResultId !== arId || page.conversationId !== convId
      || page.pageSize !== messagePageSize) {
      throw new Error('Load the conversation before refreshing its scores.')
    }
    const response = page.response
    const pieceIds = new Set(response.messages.flatMap(
      (message: BackendMessage) => message.message_pieces.map((piece: BackendMessagePiece) => piece.id),
    ))
    if (pieceIds.size === 0) return
    const requestId = claimConversationRead(convId)
    const viewRevision = viewRevisionRef.current
    const isCurrentRefresh = (): boolean => isLatestConversationRead(convId, requestId)
      && viewedAttackRef.current === arId && viewedConvRef.current === convId
      && viewRevisionRef.current === viewRevision && savedTranscriptPageRef.current === page
    const scoresByPiece = new Map<string, BackendScore[]>()
    const consumedCursors = new Set<string>()
    let cursor: string | undefined
    try {
      // Re-read at most the prefix already loaded, using fresh cursors and only retaining scores.
      for (let remainingPages = page.consumedCursors.size + 1; remainingPages > 0; remainingPages -= 1) {
        const incoming = await attacksApi.getMessages(
          arId, convId, cursor === undefined ? { limit: page.pageSize } : { limit: page.pageSize, cursor },
        )
        if (!isCurrentRefresh()) return
        mergeTranscriptPage(null, incoming, convId, cursor, consumedCursors)
        for (const message of incoming.messages) {
          for (const piece of message.message_pieces) {
            if (pieceIds.has(piece.id)) scoresByPiece.set(piece.id, piece.scores)
          }
        }
        if (scoresByPiece.size === pieceIds.size || !incoming.pagination?.has_more) break
        if (cursor) consumedCursors.add(cursor)
        cursor = incoming.pagination.next_cursor ?? undefined
      }
      if (scoresByPiece.size !== pieceIds.size) {
        throw new Error('The loaded evidence changed. Restart transcript loading before refreshing scores.')
      }
      const refreshed: ConversationMessagesResponse = {
        ...response,
        messages: response.messages.map((message: BackendMessage) => ({
          ...message,
          message_pieces: message.message_pieces.map((piece: BackendMessagePiece) => ({
            ...piece, scores: scoresByPiece.get(piece.id) ?? piece.scores,
          })),
        })),
      }
      setMessages(backendMessagesToFrontend(refreshed.messages, refreshed.partial_message_sequences))
      saveTranscriptPage({ ...page, response: refreshed })
    } catch (cause: unknown) {
      if (!isCurrentRefresh()) return
      const refreshError = new Error(`Could not refresh conversation scores: ${toApiError(cause).detail}`)
      Object.defineProperty(refreshError, 'cause', { value: cause })
      throw refreshError
    } finally {
      if (isLatestConversationRead(convId, requestId)) latestReadIdsRef.current.delete(convId)
    }
  }, [claimConversationRead, isLatestConversationRead, loadConversation, messagePageSize, saveTranscriptPage])

  const loadMoreMessages = useCallback(async (): Promise<void> => {
    const page = savedTranscriptPageRef.current
    const cursor = page?.response?.pagination?.next_cursor
    if (activeLoadRef.current || messageContinuationIssue?.restartRequired || !page || !cursor || !page.response?.pagination?.has_more
      || page.attackResultId !== viewedAttackRef.current || page.conversationId !== viewedConvRef.current
      || page.pageSize !== messagePageSize) return
    const { attackResultId: arId, conversationId: convId } = page
    const requestId = claimConversationRead(convId)
    const viewRevision = viewRevisionRef.current
    activeLoadRef.current = { attackResultId: arId, conversationId: convId, requestId }
    setIsLoadingMoreMessages(true)
    setMessageContinuationIssue(null)
    const isCurrentLoad = (): boolean => isLatestConversationRead(convId, requestId)
      && viewedAttackRef.current === arId && viewedConvRef.current === convId
      && viewRevisionRef.current === viewRevision && savedTranscriptPageRef.current === page
    let restartRequired = false
    try {
      const incoming = await attacksApi.getMessages(arId, convId, { limit: page.pageSize, cursor })
      if (!isCurrentLoad()) return
      restartRequired = true
      const response = mergeTranscriptPage(page.response, incoming, convId, cursor, page.consumedCursors)
      const frontendMessages = backendMessagesToFrontend(response.messages, response.partial_message_sequences)
      setConversationLoadIssue(findingEvidenceId && response.messages.length === 0 && !response.pagination?.has_more
        ? { conversationId: convId, unavailable: true, detail: 'Evidence unavailable' } : null)
      const onLoaded = sendCallbacksRef.current.onConversationLoaded
      setMessages(onLoaded ? onLoaded(convId, response, frontendMessages) : frontendMessages)
      saveTranscriptPage({ ...page, response, consumedCursors: new Set([...page.consumedCursors, cursor]) })
    } catch (cause: unknown) {
      if (!isCurrentLoad()) return
      const error = toApiError(cause)
      setMessageContinuationIssue({
        detail: `Could not load more messages: ${error.detail}`, restartRequired: restartRequired || error.status === 400,
      })
    } finally {
      if (isLatestConversationRead(convId, requestId)) latestReadIdsRef.current.delete(convId)
      if (activeLoadRef.current?.requestId === requestId) {
        activeLoadRef.current = null
        if (viewedConvRef.current !== null) setIsLoadingMoreMessages(false)
      }
    }
  }, [claimConversationRead, isLatestConversationRead, messagePageSize, saveTranscriptPage, messageContinuationIssue, findingEvidenceId])

  const currentPage = savedTranscriptPage?.attackResultId === attackResultId
    && savedTranscriptPage.conversationId === viewedConversationId && savedTranscriptPage.pageSize === messagePageSize
    ? savedTranscriptPage : null
  const messagePagination = currentPage?.response?.pagination ?? null
  const isTranscriptIncomplete = messagePageSize !== undefined && (!messagePagination || messagePagination.has_more)
  const loadedPieceCount = currentPage?.response?.messages.reduce((count, message) => count + message.message_pieces.length, 0) ?? 0

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
        && (loadedAttackResultIdRef.current === null || loadedAttackResultIdRef.current === attackResultId)
        && (messagePageSize === undefined || (
          savedTranscriptPageRef.current?.attackResultId === attackResultId
          && savedTranscriptPageRef.current.conversationId === activeConversationId
          && savedTranscriptPageRef.current.pageSize === messagePageSize
        ))
        && activeLoadRef.current === null
      )
    )) { return }
    loadConversation(attackResultId, activeConversationId)
  }, [activeConversationId, attackResultId, loadConversation, messagePageSize])

  // Synchronous loading derivation: if activeConversationId differs from the
  // conversation whose messages we've loaded, we're in a transition gap.
  // This avoids the 1-frame flash between useEffect fire and render.
  // Reads `sendingConversations` (state) rather than a send ref so the
  // computation stays render-safe.
  const awaitingConversationLoad = Boolean(
    activeConversationId && (activeConversationId !== loadedConversationId
      || (loadedAttackResultId !== null && loadedAttackResultId !== attackResultId))
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
    messagePagination,
    loadedPieceCount,
    isTranscriptIncomplete,
    isLoadingMoreMessages,
    messageContinuationIssue: currentPage ? messageContinuationIssue : null,
    loadMoreMessages,
    loadConversation,
    refreshConversationScores,
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
