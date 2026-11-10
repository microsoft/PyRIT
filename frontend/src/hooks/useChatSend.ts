import { useCallback, useEffect, useMemo, useReducer, useRef } from 'react'
import type { Dispatch, RefObject } from 'react'

import {
  applyConvertedValues,
  basenameFromValue,
  buildConverterInputs,
  buildDraftPieceIds,
  buildMediaUrl,
  buildRequestConverterConfigurations,
  dataTypeToAttachmentKind,
  withDraftIdentity,
} from '@/components/Chat/converterTypes'
import { attacksApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type {
  AddMessageResponse,
  AttackSummary,
  AttackTargetResolutionStatus,
  BackendMessage,
  ChatConverterController,
  ChatSendOutcome,
  ConversationMessagesResponse,
  ConverterPipelineStage,
  CreateAttackRequest,
  Message,
  MessageAttachment,
  MessageSendConversation,
  MessageSendObservation,
  MessageSendRequest,
  MessageSendStatus,
  MultiSendOptions,
  PieceConversion,
  RegisteredComponentReference,
  ScorerInstance,
  TargetInstance,
  TargetReference,
} from '@/types'
import {
  chatSendReducer,
  getPersistedProcessingRecovery,
  INITIAL_CHAT_SEND_STATE,
  isSendFinished,
  PENDING_SEND_CONVERSATION_ID,
  userPieceIds,
} from '@/utils/chatSendState'
import type {
  ChatSendAction,
  ChatSendState,
  PendingSend,
  RecoverableSendDraft,
  RepeatSendGroup,
  RepeatSendView,
  SendIssue,
} from '@/utils/chatSendState'
import type { ConversationMessages } from '@/hooks/useConversationMessages'
import { generateClientId } from '@/utils/clientId'
import { backendMessagesToFrontend, buildMessagePieces } from '@/utils/messageMapper'
import { conversationPieceSignature } from '@/utils/promptRestore'
import { buildTemperatureTarget } from '@/services/targetRegistry'
import { targetReference } from '@/utils/targetIdentity'

/** Synchronous send locks and transcript snapshots. Reads must not wait for a render. */
export interface ChatSendLocks {
  /** Conversations with an in-flight send; marked before any state update can trigger a load. */
  readonly sendingIds: Set<string>
  /** Optimistic user messages per conversation that the server may not have stored yet. */
  readonly pendingUserMessages: Map<string, Message[]>
  /** The current send per conversation; a send that is no longer here has lost ownership. */
  readonly pendingSends: Map<string, PendingSend>
  readonly loadedUserPieceIds: Map<string, Set<string>>
  readonly loadedBackendMessages: Map<string, BackendMessage[]>
}

export interface UseChatSendStateOptions {
  activeConversationId: string | null
  viewedConversationId: string | null
  runtimeGeneration: string
}

export interface ChatSendTracking {
  readonly state: ChatSendState
  readonly dispatch: Dispatch<ChatSendAction>
  readonly locksRef: RefObject<ChatSendLocks>
  /** True while the active conversation (or, before one exists, any conversation) is sending. */
  readonly isSending: boolean
  /** The viewed conversation's recovery, without converter state from another runtime generation. */
  readonly recoverableSend: RecoverableSendDraft | undefined
  readonly isSendInFlight: (conversationId: string) => boolean
  /** Records a loaded transcript and re-appends the optimistic turn of a send that still owns it. */
  readonly reconcileLoadedConversation: (
    conversationId: string, response: ConversationMessagesResponse, frontendMessages: Message[],
  ) => Message[]
  readonly getPendingSend: (conversationId: string) => PendingSend | undefined
  readonly getLoadedBackendMessages: (conversationId: string) => BackendMessage[] | undefined
  readonly rememberBackendMessages: (conversationId: string, messages: BackendMessage[]) => void
  readonly clearUndoableSend: () => void
  /** Drops every recovery when the attack is reset; safe to call while rendering. */
  readonly clearRecoveries: () => void
}

function createLocks(): ChatSendLocks {
  return {
    sendingIds: new Set(),
    pendingUserMessages: new Map(),
    pendingSends: new Map(),
    loadedUserPieceIds: new Map(),
    loadedBackendMessages: new Map(),
  }
}

/**
 * Owns the send workflow's state: a pure reducer for what renders, plus the synchronous
 * per-conversation locks. Conversation loading consumes this before `useChatSend` composes the commands.
 */
export function useChatSendState({
  activeConversationId,
  viewedConversationId,
  runtimeGeneration,
}: UseChatSendStateOptions): ChatSendTracking {
  const [state, dispatch] = useReducer(chatSendReducer, INITIAL_CHAT_SEND_STATE)
  const locksRef = useRef<ChatSendLocks>(createLocks())

  useEffect(() => {
    const { pendingSends } = locksRef.current
    return () => {
      for (const operation of pendingSends.values()) {
        operation.controller.abort()
      }
      pendingSends.clear()
    }
  }, [])

  const isSendInFlight = useCallback(
    (conversationId: string): boolean => locksRef.current.sendingIds.has(conversationId),
    [],
  )

  const reconcileLoadedConversation = useCallback((
    conversationId: string, response: ConversationMessagesResponse, frontendMessages: Message[],
  ): Message[] => {
    const locks = locksRef.current
    const savedUserIds = userPieceIds(response)
    locks.loadedUserPieceIds.set(conversationId, savedUserIds)
    if (response.messages) {
      locks.loadedBackendMessages.set(conversationId, response.messages)
    }
    dispatch({
      type: 'recovery-loaded', conversationId, recovery: getPersistedProcessingRecovery(conversationId, response),
    })
    // If this conversation has an in-flight send, append any pending user
    // messages (that the server may not have stored yet) and a loading indicator.
    if (locks.sendingIds.has(conversationId)) {
      const operation = locks.pendingSends.get(conversationId)
      const pending = locks.pendingUserMessages.get(conversationId) ?? []
      const requestStored = operation?.progress?.request_turn_number != null
        ? response.messages.some((message: BackendMessage) => (
          message.role === 'user' && message.turn_number === operation.progress?.request_turn_number
        ))
        : operation && [...savedUserIds].some((id) => !operation.priorUserPieceIds.has(id))
      if (!operation?.progress || !isSendFinished(operation.progress)) {
        if (!requestStored) { frontendMessages.push(...pending) }
        frontendMessages.push({
          role: 'assistant',
          content: '...',
          timestamp: new Date().toISOString(),
          isLoading: true,
        })
      }
    }
    return frontendMessages
  }, [])

  const getPendingSend = useCallback(
    (conversationId: string): PendingSend | undefined => locksRef.current.pendingSends.get(conversationId),
    [],
  )
  const getLoadedBackendMessages = useCallback(
    (conversationId: string): BackendMessage[] | undefined => locksRef.current.loadedBackendMessages.get(conversationId),
    [],
  )
  const rememberBackendMessages = useCallback((conversationId: string, messages: BackendMessage[]): void => {
    locksRef.current.loadedBackendMessages.set(conversationId, messages)
  }, [])
  const clearUndoableSend = useCallback((): void => { dispatch({ type: 'undo-cleared' }) }, [])
  const clearRecoveries = useCallback((): void => { dispatch({ type: 'recoveries-cleared' }) }, [])

  const isSending = activeConversationId ? state.sending.has(activeConversationId) : Boolean(state.sending.size)
  const savedRecovery = viewedConversationId ? state.recoveries[viewedConversationId] : undefined
  const recoverableSend = useMemo<RecoverableSendDraft | undefined>(() => (
    savedRecovery?.converterGeneration !== undefined && savedRecovery.converterGeneration !== runtimeGeneration
      ? {
          ...savedRecovery, conversions: {}, pipelines: undefined, source: 'persisted',
        }
      : savedRecovery
  ), [savedRecovery, runtimeGeneration])

  return {
    state,
    dispatch,
    locksRef,
    isSending,
    recoverableSend,
    isSendInFlight,
    reconcileLoadedConversation,
    getPendingSend,
    getLoadedBackendMessages,
    rememberBackendMessages,
    clearUndoableSend,
    clearRecoveries,
  }
}

export interface ChatSendLaunchState {
  generation: string
  ready: boolean
  defaultsReady: boolean
  scorerSelectionReady: boolean
}

/** Settings a first send uses to create its attack. */
export interface ChatSendNewAttack {
  objective: string
  temperature: string
  systemPrompt: string
  supportsSystemPrompt: boolean
  labels?: Record<string, string>
  operationId: string | null
  /** The operator's explicit draft scorer choice, or undefined to use the default. */
  draftScorer: RegisteredComponentReference | null | undefined
  defaultChatScorer?: RegisteredComponentReference | null
}

export interface ScoringNotice {
  id: string
  message: string
  conversationId: string
}

/** Which conversation a send belongs to, plus live reads that detect the user moving on mid-send. */
export interface ChatSendRoute {
  attackResultId: string | null
  conversationId: string | null
  activeConversationId: string | null
  viewedConversationId: string | null
  getViewedAttackId: () => string | null
  setViewedAttackId: (attackResultId: string) => void
  getNavigationRevision: () => number
}

/** Whether a send may start now, and a live read of the same launch state for in-flight checks. */
export interface ChatSendReadiness {
  runtimeReady: boolean
  runtimeGeneration: string
  defaultsReady: boolean
  scorerSelectionReady: boolean
  /** True when another workflow (loading, editing, locking, undo) currently forbids sending. */
  isSendBlocked: boolean
  getLaunchState: () => ChatSendLaunchState
}

/** What the submission targets and how a first send creates its attack. */
export interface ChatSendRequest {
  activeTarget: TargetInstance | null
  targetResolutionStatus: AttackTargetResolutionStatus
  newAttack: ChatSendNewAttack
  /** The loaded attack's scorer, recorded with the send for preference observation. */
  chatScorer: RegisteredComponentReference | null
  availableScorers: ScorerInstance[]
}

/** The draft a send captures, and how the composer is cleared or restored afterwards. */
export interface ChatSendComposer {
  converters: ChatConverterController
  getDraftRevision: () => number | undefined
  getTextMetadata: () => Record<string, unknown> | undefined
  /** Clears the composer after a refreshed send settles, when its draft revision is still shown. */
  clearSentDraft: (draftRevision: number | undefined) => void
  restoreSendDraft: (
    text: string, attachments: MessageAttachment[], textMetadata: Record<string, unknown> | undefined,
    conversions: Record<string, PieceConversion>, pipelines: Record<string, ConverterPipelineStage[]> | undefined,
  ) => void
  clearPreservedDraft: (sequence: number | undefined, attackId: string | null, conversationId: string | null) => void
}

/** The transcript and load ownership from `useConversationMessages`. */
export type ChatSendLoader = Pick<ConversationMessages,
  | 'messages' | 'setMessages' | 'loadedConversationId' | 'invalidateConversationLoads' | 'claimConversationRead'
  | 'isLatestConversationRead' | 'markConversationLoaded' | 'getViewedConversationId' | 'setViewedConversationId'
  | 'getLoadedConversationId'
>

/** Effects outside the send workflow: scoring display, notices, the panel and the parent route. */
export interface ChatSendCallbacks {
  observeChatSend: (
    attackId: string, acceptance: Promise<MessageSendStatus>, target: TargetReference,
    scorerChoice: RegisteredComponentReference | null | undefined, scorerExplicit: boolean, scorers: ScorerInstance[],
  ) => MessageSendObservation
  showConversationScoring: (conversationId: string, response: ConversationMessagesResponse) => void
  showScoringNotice: (notice: ScoringNotice) => void
  setEditorNotice: (notice: string | null) => void
  /** Shows the conversations panel for a repeated send, except on narrow screens. */
  revealRepeatConversations: () => void
  refreshPanel: () => void
  onConversationCreated: (
    attackResultId: string, conversationId: string, objective?: string, target?: TargetInstance | null,
  ) => void
  /** Keeps the composer when a first send's created conversation becomes the route. */
  onCreatedDestination: (attackResultId: string, conversationId: string) => void
  onAttackChange?: (attack: AttackSummary) => void
}

export interface UseChatSendOptions {
  tracking: ChatSendTracking
  route: ChatSendRoute
  readiness: ChatSendReadiness
  request: ChatSendRequest
  composer: ChatSendComposer
  loader: ChatSendLoader
  callbacks: ChatSendCallbacks
}

export interface ChatSend {
  readonly sendIssue: SendIssue | undefined
  /** Repeated sends started from the current attack. */
  readonly repeatSends: RepeatSendView[]
  readonly send: (
    originalValue: string, convertedValue: string | undefined, attachments: MessageAttachment[], options?: MultiSendOptions,
  ) => Promise<ChatSendOutcome>
  /** Re-reads the viewed conversation's send without resubmitting it. */
  readonly refreshSend: () => Promise<void>
  readonly refreshRepeatSend: (sendId: string) => void
  readonly dismissRepeatSend: (sendId: string) => void
  readonly restorePreparationDraft: () => void
}

/**
 * Message sending: optimistic messages, per-conversation locking, attachment and conversion capture,
 * progress tracking, and recovery after unknown delivery. State transitions go through `chatSendReducer`.
 */
export function useChatSend({
  tracking, route, readiness, request, composer, loader, callbacks,
}: UseChatSendOptions): ChatSend {
  const {
    attackResultId, conversationId, activeConversationId, viewedConversationId,
    getViewedAttackId, setViewedAttackId, getNavigationRevision,
  } = route
  const { runtimeReady, runtimeGeneration, defaultsReady, scorerSelectionReady, isSendBlocked, getLaunchState } = readiness
  const { activeTarget, targetResolutionStatus, newAttack, chatScorer, availableScorers } = request
  const { converters, getDraftRevision, getTextMetadata, clearSentDraft, restoreSendDraft, clearPreservedDraft } = composer
  const {
    messages, setMessages, loadedConversationId, invalidateConversationLoads, claimConversationRead,
    isLatestConversationRead, markConversationLoaded, getViewedConversationId, setViewedConversationId,
    getLoadedConversationId,
  } = loader
  const {
    observeChatSend, showConversationScoring, showScoringNotice, setEditorNotice, revealRepeatConversations,
    refreshPanel, onConversationCreated, onCreatedDestination, onAttackChange,
  } = callbacks
  const { state, dispatch, locksRef } = tracking
  const latestSendRef = useRef<string | null>(null)
  const sendIssueConversationId = attackResultId ? viewedConversationId : loadedConversationId ?? viewedConversationId
  const sendIssue = state.issues[sendIssueConversationId ?? PENDING_SEND_CONVERSATION_ID]

  const isCurrentSend = (operation: PendingSend): boolean => (
    !operation.controller.signal.aborted
    && locksRef.current.pendingSends.get(operation.conversationId) === operation
  )
  const checkSendPreparation = (operation: PendingSend, requireDefaults: boolean): void => {
    const current = getLaunchState()
    if (current.generation !== operation.converterGeneration || !current.ready
      || (requireDefaults && (!current.defaultsReady || !current.scorerSelectionReady))) {
      throw new Error('Runtime or default labels changed while preparing this message. Your draft is preserved. Retry after default labels finish loading.')
    }
  }
  const isViewingSend = (operation: PendingSend): boolean => (
    getViewedAttackId() === operation.attackResultId
    && (
      getViewedConversationId() === operation.conversationId
      || (getViewedConversationId() === null && operation.conversationId === PENDING_SEND_CONVERSATION_ID
        && getNavigationRevision() === operation.navigationRevision)
    )
  )
  const setSendIssue = (conversation: string, issue?: SendIssue): void => {
    dispatch({ type: 'issue-changed', conversationId: conversation, issue })
  }
  const finishTracking = (operation: PendingSend): void => {
    const locks = locksRef.current
    if (!isCurrentSend(operation) || !locks.sendingIds.has(operation.conversationId)) { return }
    if (operation.responseReadId === undefined
      || isLatestConversationRead(operation.conversationId, operation.responseReadId)) {
      invalidateConversationLoads(operation.conversationId)
    }
    if (isViewingSend(operation)) {
      setMessages((previous) => previous.filter((message) => !message.isLoading))
    }
    locks.sendingIds.delete(operation.conversationId)
    locks.pendingUserMessages.delete(operation.conversationId)
    dispatch({ type: 'tracking-finished', conversationId: operation.conversationId })
    refreshPanel()
  }
  const retireSend = (operation: PendingSend): void => {
    if (isCurrentSend(operation) && !operation.needsRefresh
      && operation.progress?.failure_stage !== 'preparation') {
      locksRef.current.pendingSends.delete(operation.conversationId)
    }
  }
  const stopSendSpinner = (operation: PendingSend): void => {
    if (!isViewingSend(operation)) { return }
    const hasMatchingTranscript = getLoadedConversationId() === operation.conversationId
    const pending = locksRef.current.pendingUserMessages.get(operation.conversationId) ?? []
    setMessages((previous) => (hasMatchingTranscript
      ? previous
      : [...operation.initialMessages, ...pending]
    ).filter((message) => !message.isLoading))
    markConversationLoaded(operation.conversationId)
  }

  const applySendResponse = (
    operation: PendingSend, response: AddMessageResponse, isLatestRead: boolean,
  ): ChatSendOutcome => {
    const effectiveConvId = operation.conversationId
    const targetResponseStatus = response.messages.target_response_status
    const recovery = operation.progress
      && !['interrupted', 'finalization'].includes(operation.progress.failure_stage ?? '')
      ? getPersistedProcessingRecovery(effectiveConvId, response.messages)
      : undefined
    const processingFailure = recovery?.failedRequestTurnNumber === operation.progress?.request_turn_number
      && recovery !== undefined
    const status: ChatSendOutcome['status'] = operation.progress?.failure_stage === 'preparation'
      || processingFailure
      ? 'retryable_failure'
      : operation.progress?.failure_stage
        || (targetResponseStatus?.response_error && targetResponseStatus.response_error !== 'none')
        ? 'non_retryable_failure'
        : 'sent'
    const backendMessages = backendMessagesToFrontend(response.messages.messages)
    const recoveredDraft: RecoverableSendDraft | undefined = processingFailure && recovery ? {
      ...recovery,
      originalValue: operation.originalValue,
      textMetadata: operation.textMetadata,
      attachments: operation.attachments,
      conversions: operation.conversions,
      converterGeneration: operation.converterGeneration,
      pipelines: operation.pipelines,
      source: 'live',
    } : recovery
    if (isLatestRead) {
      const locks = locksRef.current
      locks.loadedUserPieceIds.set(effectiveConvId, userPieceIds(response.messages))
      if (response.messages?.messages) {
        locks.loadedBackendMessages.set(effectiveConvId, response.messages.messages)
      }
      dispatch({ type: 'recovery-replaced', conversationId: effectiveConvId, recovery: recoveredDraft })
    }
    if (isLatestRead && isViewingSend(operation)) {
      invalidateConversationLoads(effectiveConvId)
      setMessages(backendMessages)
      showConversationScoring(effectiveConvId, response.messages)
      markConversationLoaded(effectiveConvId)
      onAttackChange?.(response.attack)
      const scoringError = operation.progress?.scoring_error
      const result = response.messages.scoring_failure
      if (scoringError && result && result.conversation_id === effectiveConvId) {
        showScoringNotice({
          id: operation.submissionId, conversationId: effectiveConvId,
          message: 'A scoring error occurred. Open the score above for details.',
        })
      }
      const lastMessage = response.messages.messages[response.messages.messages.length - 1]
      if (
        status === 'sent' && operation.progress?.state === 'completed'
        && latestSendRef.current === operation.submissionId
        && !operation.repeatGroup && operation.converterSnapshot && operation.attackResultId
        && typeof targetResponseStatus?.request_turn_number === 'number'
        && targetResponseStatus.request_turn_number === operation.progress.request_turn_number
        && lastMessage?.role === 'assistant' && lastMessage.message_pieces.length > 0
      ) {
        dispatch({
          type: 'undo-recorded',
          undoable: {
            attackResultId: operation.attackResultId,
            conversationId: effectiveConvId,
            historySignature: conversationPieceSignature(response.messages),
            cutoffIndex: targetResponseStatus.request_turn_number - 1,
            originalValue: operation.originalValue,
            textMetadata: operation.textMetadata,
            attachments: operation.attachments,
            converters: operation.converterSnapshot,
            history: response.messages.messages.filter(
              (message: BackendMessage) => message.turn_number < targetResponseStatus.request_turn_number,
            ).map((message: BackendMessage) => ({ role: message.role })),
          },
        })
      }
    }
    return {
      status,
      clearDraft: status !== 'retryable_failure' && latestSendRef.current === operation.submissionId
        && (!operation.repeatGroup || operation.repeatGroup.source === operation),
    }
  }

  const updateRepeatView = (group: RepeatSendGroup): void => {
    if (group.source.controller.signal.aborted) return
    const needsRefresh = [...group.conversations.values()].some(
      (operation: PendingSend) => isCurrentSend(operation) && operation.needsRefresh,
    )
    dispatch({ type: 'repeat-progressed', progress: group.progress, needsRefresh })
  }

  const receiveRepeatProgress = (group: RepeatSendGroup, progress: MessageSendStatus): void => {
    if (group.source.controller.signal.aborted) return
    const locks = locksRef.current
    group.progress = progress
    let addedConversations = false
    for (const conversation of progress.conversations ?? []) {
      if (group.conversations.has(conversation.conversation_id)) continue
      addedConversations = true
      const operation: PendingSend = {
        ...group.source,
        conversationId: conversation.conversation_id,
        draftRevision: undefined,
        responseReadId: undefined,
        needsRefresh: false,
        progress: { ...progress, ...conversation },
      }
      group.conversations.set(operation.conversationId, operation)
      // A finished sibling may already have a newer send by the time this snapshot arrives.
      if (locks.pendingSends.has(operation.conversationId)) continue
      locks.pendingSends.set(operation.conversationId, operation)
      locks.sendingIds.add(operation.conversationId)
      const pending = locks.pendingUserMessages.get(group.source.conversationId)
      if (pending) locks.pendingUserMessages.set(operation.conversationId, [...pending])
      dispatch({ type: 'tracking-started', conversationId: operation.conversationId })
      void trackSend(operation).then(() => { retireSend(operation) })
    }
    if (addedConversations) refreshPanel()
    updateRepeatView(group)
  }

  const readSendProgress = async (operation: PendingSend): Promise<MessageSendStatus> => {
    const { attackResultId: sendAttackId, progress: previousProgress } = operation
    if (!sendAttackId || !previousProgress) throw new Error('The send has no progress handle.')
    const group = operation.repeatGroup
    const read = (): Promise<MessageSendStatus> => operation.observation
      ? operation.observation.readProgress(operation.needsRefresh)
      : attacksApi.getMessageSend(sendAttackId, previousProgress.send_id, operation.controller.signal)
    if (!group) return read()
    // Every conversation uses the same in-flight GET, but settles its own evidence reads and ownership.
    group.read ??= read().then((progress: MessageSendStatus) => {
      receiveRepeatProgress(group, progress)
      return progress
    }).finally(() => { group.read = undefined })
    const progress = await group.read
    const conversation = progress.conversations?.find(
      (candidate: MessageSendConversation) => candidate.conversation_id === operation.conversationId,
    )
    return conversation ? { ...progress, ...conversation } : progress
  }

  const trackSend = async (operation: PendingSend): Promise<ChatSendOutcome> => {
    try {
      if (!operation.attackResultId) { throw new Error('The send has no attack ID.') }
      while (operation.progress) {
        const repeatProgress = operation.repeatGroup?.progress
        // The last conversation can release ownership before the operation publishes its final summary.
        const awaitingSummary = repeatProgress && !isSendFinished(repeatProgress)
          && repeatProgress.conversations?.length && repeatProgress.conversations.every(isSendFinished)
        if (isSendFinished(operation.progress) && !awaitingSummary) break
        let progress: MessageSendStatus | undefined
        try {
          progress = await readSendProgress(operation)
        } catch (err) {
          if (toApiError(err).status !== 404) { throw err }
          // Lost handles permit evidence reads, never a new submission or a claim of delivery.
        }
        if (!isCurrentSend(operation)) { return { status: 'non_retryable_failure', clearDraft: false } }
        operation.progress = progress
        dispatch({ type: 'scoring-observed', conversationId: operation.conversationId, scoring: progress?.is_scoring === true })
      }
      operation.responseReadId = claimConversationRead(operation.conversationId)
      const [attack, conversation] = await Promise.all([
        operation.observation?.readAttack(operation.needsRefresh) ?? attacksApi.getAttack(operation.attackResultId),
        attacksApi.getMessages(operation.attackResultId, operation.conversationId),
      ])
      if (!isCurrentSend(operation)) { return { status: 'non_retryable_failure', clearDraft: false } }
      const isLatestRead = isLatestConversationRead(operation.conversationId, operation.responseReadId)
      const outcome = applySendResponse(operation, { attack, messages: conversation }, isLatestRead)
      const progress = operation.progress
      const hasProcessingRecovery = conversation.target_response_status?.response_error === 'processing'
        && conversation.target_response_status.request_turn_number === progress?.request_turn_number
      if (!progress || progress.failure_stage === 'interrupted' || progress.failure_stage === 'finalization'
        || (progress.failure_stage === 'sending' && !hasProcessingRecovery)) {
        operation.needsRefresh = true
        setSendIssue(operation.conversationId, {
          description: `${progress?.error ?? 'Send acceptance is unknown.'} Refresh saved messages only; do not resend.`,
          blocking: true,
        })
        return { status: 'non_retryable_failure', clearDraft: false }
      }
      setSendIssue(operation.conversationId, progress.failure_stage === 'preparation'
        ? {
          description: progress.error ?? 'Message preparation failed before target dispatch.',
          blocking: false,
          draft: operation,
        }
        : undefined)
      operation.needsRefresh = false
      if (outcome.status !== 'retryable_failure' && operation.preservedDraft) {
        const preserved = operation.preservedDraft
        clearPreservedDraft(preserved.sequence, preserved.originatingAttackId, preserved.originatingConversationId)
        operation.preservedDraft = undefined
      }
      return outcome
    } catch (err) {
      if (isCurrentSend(operation)) {
        operation.needsRefresh = true
        const error = toApiError(err)
        const detail = error.isTimeout ? 'The read timed out.'
          : error.isNetworkError ? 'The backend could not be reached.' : error.detail
        const phase = operation.progress && isSendFinished(operation.progress)
          ? `${operation.progress.error ?? 'Sending finished.'} Saved messages or attack details could not be loaded.`
          : 'Send status is unavailable. Delivery may be unknown.'
        setSendIssue(operation.conversationId, {
          description: `${phase} ${detail} Refresh only; do not resend.`,
          blocking: true,
        })
        stopSendSpinner(operation)
      }
      return { status: 'non_retryable_failure', clearDraft: false }
    } finally {
      finishTracking(operation)
      if (operation.repeatGroup) updateRepeatView(operation.repeatGroup)
    }
  }

  const refreshConversationSend = async (conversationIdToRefresh: string): Promise<void> => {
    const locks = locksRef.current
    const operation = locks.pendingSends.get(conversationIdToRefresh)
    if (!operation || locks.sendingIds.has(operation.conversationId)) { return }
    locks.sendingIds.add(operation.conversationId)
    dispatch({ type: 'tracking-started', conversationId: operation.conversationId })
    const outcome = await trackSend(operation)
    if (isCurrentSend(operation) && isViewingSend(operation) && outcome.clearDraft) {
      clearSentDraft(operation.draftRevision)
    }
    retireSend(operation)
  }

  const refreshSend = (): Promise<void> => refreshConversationSend(sendIssueConversationId ?? PENDING_SEND_CONVERSATION_ID)

  const refreshRepeatSend = (sendId: string): void => {
    for (const operation of locksRef.current.pendingSends.values()) {
      if (operation.repeatGroup?.progress.send_id === sendId && operation.needsRefresh) {
        void refreshConversationSend(operation.conversationId)
      }
    }
  }

  const dismissRepeatSend = (sendId: string): void => { dispatch({ type: 'repeat-dismissed', sendId }) }

  const restorePreparationDraft = (): void => {
    const operation = sendIssue?.draft
    if (!operation || !isCurrentSend(operation) || !isViewingSend(operation)) return
    const sameGeneration = operation.converterGeneration === runtimeGeneration
    const attachments = operation.attachments.map(withDraftIdentity)
    restoreSendDraft(
      operation.originalValue, attachments, operation.textMetadata,
      sameGeneration ? operation.conversions : {}, sameGeneration ? operation.pipelines : undefined,
    )
    if (!sameGeneration) setSendIssue(operation.conversationId, {
      ...sendIssue,
      description: 'Prompt restored after the runtime changed. Review converters before sending.',
    })
  }

  const send = async (
    originalValue: string,
    convertedValue: string | undefined,
    attachments: MessageAttachment[],
    options?: MultiSendOptions,
  ): Promise<ChatSendOutcome> => {
    if (
      !runtimeReady
      || (!attackResultId && (!defaultsReady || !scorerSelectionReady))
      || !activeTarget
      || isSendBlocked
      || sendIssue?.blocking
    ) {
      return { status: 'retryable_failure', clearDraft: false }
    }

    const locks = locksRef.current
    const initialSendConvId = activeConversationId ?? conversationId ?? PENDING_SEND_CONVERSATION_ID
    if (locks.sendingIds.has(initialSendConvId)) {
      return { status: 'retryable_failure', clearDraft: false }
    }

    setEditorNotice(null)
    invalidateConversationLoads(initialSendConvId)

    // Capture all piece conversions upfront before any async work or state clears
    const conversions = { ...converters.applied }
    const count = options?.count ?? 1
    const converterMode = options?.requestConverterMode ?? 'shared'
    const pipelines = count > 1 ? { ...converters.pipelines } : undefined
    const { draftScorer, defaultChatScorer, objective: pendingObjective } = newAttack
    const operation: PendingSend = {
      submissionId: generateClientId(),
      controller: new AbortController(),
      draftRevision: getDraftRevision(),
      originalValue,
      textMetadata: getTextMetadata(),
      attachments: attachments.map((attachment) => ({ ...attachment })),
      conversions,
      priorUserPieceIds: new Set(locks.loadedUserPieceIds.get(initialSendConvId)),
      initialMessages: [...messages],
      navigationRevision: getNavigationRevision(),
      converterGeneration: runtimeGeneration,
      target: targetReference(activeTarget),
      chatScorerExplicit: draftScorer !== undefined,
      chatScorerChoice: attackResultId ? chatScorer : draftScorer === undefined ? defaultChatScorer : draftScorer,
      converterSnapshot: count === 1 ? converters.snapshot() : undefined,
      pipelines,
      attackResultId,
      conversationId: initialSendConvId,
      needsRefresh: false,
    }
    locks.pendingSends.set(initialSendConvId, operation)
    latestSendRef.current = operation.submissionId
    const submittedNavigationRevision = getNavigationRevision()
    let submissionAttempted = false
    const textConversion = conversions['text']
    const isTextTextConversion = textConversion?.convertedDataType === 'text'
    const isTextFileConversion = Boolean(textConversion) && !isTextTextConversion

    // Mark synchronously so the useEffect guard sees it immediately
    locks.sendingIds.add(initialSendConvId)

    // When a text→text converter is active, show the converted text as the bubble's
    // primary content. When a text→file converter is active, keep the typed text
    // as content and synthesize a file attachment so the bubble shows both.
    const displayContent = isTextTextConversion && convertedValue != null ? convertedValue : originalValue
    const optimisticAttachments: MessageAttachment[] = [...attachments]
    if (isTextFileConversion && textConversion) {
      const url = buildMediaUrl(textConversion.convertedValue)
      const kind = dataTypeToAttachmentKind(textConversion.convertedDataType)
      optimisticAttachments.push({
        type: kind,
        name: basenameFromValue(textConversion.convertedValue, `output.${kind}`),
        url,
        mimeType: 'application/octet-stream',
      })
    }

    // Add user message with attachments for display
    const userMessage: Message = {
      role: 'user',
      content: displayContent,
      timestamp: new Date().toISOString(),
      attachments: optimisticAttachments.length > 0 ? optimisticAttachments : undefined,
      originalContent: isTextTextConversion ? originalValue : undefined,
    }
    setMessages(prev => [...prev, userMessage])

    // Track as pending so switching back before the server stores it still shows it
    const pending = locks.pendingUserMessages.get(initialSendConvId) ?? []
    pending.push(userMessage)
    locks.pendingUserMessages.set(initialSendConvId, pending)

    // Show loading indicator
    dispatch({ type: 'send-started', conversationId: initialSendConvId })
    const loadingMessage: Message = {
      role: 'assistant',
      content: '...',
      timestamp: new Date().toISOString(),
      isLoading: true,
    }
    setMessages(prev => [...prev, loadingMessage])

    try {
      // Build message pieces from text + attachments — always use original text
      const pieceIds = buildDraftPieceIds(originalValue, attachments, conversions)
      const originalPieces = await buildMessagePieces(originalValue, attachments, operation.textMetadata)
      if (textConversion && !originalValue.trim()) {
        originalPieces.unshift({ data_type: 'text', original_value: originalValue })
      }
      const pieces = applyConvertedValues(
        originalPieces,
        pieceIds,
        conversions,
      )
      const requestConfigurations = count > 1 && converterMode === 'per_branch' ? buildRequestConverterConfigurations(
        buildConverterInputs(originalValue, attachments), pieceIds, pipelines ?? {}, conversions,
      ) : []
      if (!isCurrentSend(operation)) { return { status: 'non_retryable_failure', clearDraft: false } }

      // Create attack lazily on first message
      let currentAttackResultId = attackResultId
      let currentConversationId = conversationId
      let currentActiveConversationId = activeConversationId
      if (!currentAttackResultId) {
        checkSendPreparation(operation, true)
        let selectedTarget = activeTarget
        if (newAttack.temperature.trim()) {
          selectedTarget = await buildTemperatureTarget(activeTarget, Number(newAttack.temperature))
          if (!isCurrentSend(operation)) { return { status: 'non_retryable_failure', clearDraft: false } }
        }
        checkSendPreparation(operation, true)
        const createRequest: CreateAttackRequest = {
          target_registry_name: activeTarget.target_registry_name,
          ...(selectedTarget.binding ? { target_binding: selectedTarget.binding } : {}),
          objective: pendingObjective || undefined,
          // TODO(PyRIT 1.4): Pass only dedicated attribution after legacy label aliases are removed.
          // The create-attack API normalizes these aliases through _AttackAttributionInput.
          labels: newAttack.labels,
          operation_id: newAttack.operationId ?? undefined,
          system_prompt: newAttack.supportsSystemPrompt ? newAttack.systemPrompt.trim() || undefined : undefined,
          ...(draftScorer !== undefined ? { chat_scorer: draftScorer }
            : defaultChatScorer === null || pendingObjective.trim() && defaultChatScorer
              ? { chat_scorer: defaultChatScorer } : {}),
        }
        const createResponse = await attacksApi.createAttack(createRequest)
        operation.chatScorerChoice = createRequest.chat_scorer
        if (!isCurrentSend(operation)) { return { status: 'non_retryable_failure', clearDraft: false } }
        currentAttackResultId = createResponse.attack_result_id
        currentConversationId = createResponse.conversation_id
        currentActiveConversationId = currentConversationId
        // Mark new ID in synchronous ref *before* triggering the state
        // update that changes activeConversationId (and fires the useEffect)
        locks.sendingIds.delete(PENDING_SEND_CONVERSATION_ID)
        locks.sendingIds.add(currentConversationId)
        // Move pending messages to the real conversation ID
        const pendingMsgs = locks.pendingUserMessages.get(PENDING_SEND_CONVERSATION_ID)
        if (pendingMsgs) {
          locks.pendingUserMessages.delete(PENDING_SEND_CONVERSATION_ID)
          locks.pendingUserMessages.set(currentConversationId, pendingMsgs)
        }
        locks.pendingSends.delete(PENDING_SEND_CONVERSATION_ID)
        operation.attackResultId = currentAttackResultId
        operation.conversationId = currentConversationId
        locks.pendingSends.set(currentConversationId, operation)
        if (getNavigationRevision() === submittedNavigationRevision) {
          onCreatedDestination(currentAttackResultId, currentConversationId)
          if (selectedTarget.binding) {
            onConversationCreated(currentAttackResultId, currentConversationId, pendingObjective || undefined, selectedTarget)
          } else {
            onConversationCreated(currentAttackResultId, currentConversationId, pendingObjective || undefined)
          }
          setViewedAttackId(currentAttackResultId)
          setViewedConversationId(currentConversationId)
        }
        // Update sending tracker to use real ID instead of the pending key
        dispatch({ type: 'send-conversation-created', from: PENDING_SEND_CONVERSATION_ID, to: currentConversationId })
      }

      // The effective conversation we're sending for
      const effectiveConvId = currentActiveConversationId ?? currentConversationId

      // Send message to target
      if (!currentAttackResultId || !effectiveConvId) {
        throw new Error('Message send is missing an attack or conversation ID.')
      }
      const addMessageRequest: MessageSendRequest = {
        role: 'user',
        pieces,
        send: true,
        target_registry_name: activeTarget.target_registry_name,
        target_conversation_id: effectiveConvId,
        submission_id: operation.submissionId,
        ...(count > 1 ? {
          count,
          request_converter_mode: converterMode,
        } : {}),
        ...(requestConfigurations.length ? { request_converter_configurations: requestConfigurations } : {}),
      }
      if (targetResolutionStatus === 'unbound' && newAttack.temperature.trim()) {
        const selectedTarget = await buildTemperatureTarget(activeTarget, Number(newAttack.temperature))
        if (!isCurrentSend(operation)) { return { status: 'non_retryable_failure', clearDraft: false } }
        addMessageRequest.target_binding = selectedTarget.binding
      }
      checkSendPreparation(operation, false)
      submissionAttempted = true
      const acceptance = attacksApi.submitMessageSend(currentAttackResultId, addMessageRequest)
      operation.observation = observeChatSend(
        currentAttackResultId, acceptance, operation.target,
        operation.chatScorerChoice, operation.chatScorerExplicit, availableScorers,
      )
      operation.progress = await acceptance
      if (!isCurrentSend(operation)) { return { status: 'non_retryable_failure', clearDraft: false } }
      if (count > 1) {
        const group: RepeatSendGroup = {
          source: operation,
          progress: operation.progress,
          conversations: new Map([[operation.conversationId, operation]]),
        }
        operation.repeatGroup = group
        dispatch({ type: 'repeat-started', progress: group.progress })
        receiveRepeatProgress(group, group.progress)
        const sourceProgress = group.progress.conversations?.find(
          (conversation: MessageSendConversation) => conversation.conversation_id === operation.conversationId,
        )
        if (sourceProgress) operation.progress = { ...group.progress, ...sourceProgress }
        if (isViewingSend(operation)) revealRepeatConversations()
        refreshPanel()
      }
      return await trackSend(operation)
    } catch (err) {
      if (!isCurrentSend(operation)) { return { status: 'non_retryable_failure', clearDraft: false } }
      const sendConvId = operation.conversationId
      const apiError = toApiError(err)
      if (submissionAttempted && ![400, 401, 403, 404, 409, 422, 429].includes(apiError.status ?? 0)) {
        operation.needsRefresh = true
        const detail = apiError.isTimeout ? 'Request timed out.'
          : apiError.isNetworkError ? 'Network error. The backend could not be reached.' : apiError.detail
        setSendIssue(sendConvId, {
          description: `${detail} Send acceptance is unknown. Refresh saved messages only; do not resend.`,
          blocking: true,
        })
        stopSendSpinner(operation)
        return { status: 'non_retryable_failure', clearDraft: false }
      }
      const viewedId = getViewedConversationId()
      const isViewingFailedConversation = isViewingSend(operation)

      // Only show error in UI if user is still on this conversation
      if (isViewingFailedConversation) {
        const hasMatchingTranscript = getLoadedConversationId() === sendConvId
        // Mark the viewed conversation as loaded so first-send failures do not
        // get stuck behind the "Loading conversation..." placeholder.
        if (viewedId) {
          markConversationLoaded(viewedId)
        } else if (sendConvId !== PENDING_SEND_CONVERSATION_ID) {
          markConversationLoaded(sendConvId)
        }

        let description: string
        if (apiError.isNetworkError) {
          description = 'Network error — check that the backend is running and reachable.'
        } else if (apiError.isTimeout) {
          description = 'Request timed out. The server may be busy — please try again.'
        } else {
          description = apiError.detail
        }

        const errorMessage: Message = {
          role: 'assistant',
          content: '',
          timestamp: new Date().toISOString(),
          error: {
            type: apiError.isNetworkError ? 'network' : apiError.isTimeout ? 'timeout' : 'unknown',
            description,
          },
        }
        setMessages(prev => {
          // A pending navigation load may still leave a different conversation in state.
          const failedMessages = hasMatchingTranscript ? prev : [...messages, userMessage]
          const lastFailedMessage = failedMessages[failedMessages.length - 1]
          if (failedMessages.length > 0 && lastFailedMessage?.isLoading) {
            return [...failedMessages.slice(0, -1), errorMessage]
          }
          return [...failedMessages, errorMessage]
        })

      }
      return {
        status: 'retryable_failure',
        clearDraft: false,
      }
    } finally {
      finishTracking(operation)
      retireSend(operation)
    }
  }

  return {
    sendIssue,
    repeatSends: state.repeatSends.filter((view: RepeatSendView) => view.progress.attack_result_id === attackResultId),
    send,
    refreshSend,
    refreshRepeatSend,
    dismissRepeatSend,
    restorePreparationDraft,
  }
}
