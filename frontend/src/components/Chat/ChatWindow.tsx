import { useState, useRef, useEffect, useLayoutEffect, useCallback, useMemo } from 'react'
import type { ChangeEvent } from 'react'
import { createPortal } from 'react-dom'
import {
  Button,
  Breadcrumb,
  BreadcrumbDivider,
  BreadcrumbItem,
  Drawer,
  Dialog,
  DialogSurface,
  DialogBody,
  DialogTitle,
  DialogContent,
  DialogActions,
  Menu,
  MenuItem,
  MenuList,
  MenuPopover,
  MenuTrigger,
  mergeClasses,
  MessageBar,
  MessageBarActions,
  MessageBarBody,
  Spinner,
  Switch,
  Text,
  Tooltip,
  useRestoreFocusSource,
  useRestoreFocusTarget,
} from '@fluentui/react-components'
import type { SwitchOnChangeData } from '@fluentui/react-components'
import { AddRegular, ArrowDownloadRegular, PanelRightRegular } from '@fluentui/react-icons'
import { Link } from 'react-router'
import MessageList from './MessageList'
import SystemPromptBanner from './SystemPromptBanner'
import ChatInputArea from './ChatInputArea'
import ConversationPanel from './ConversationPanel'
import ConverterPanel from './ConverterPanel'
import TargetBadge from './TargetBadge'
import ChatTargetPicker from './ChatTargetPicker'
import { sameTarget } from '@/utils/targetIdentity'
import { generateClientId } from '@/utils/clientId'
import ObjectiveHeader from './ObjectiveHeader'
import type { PieceConversion } from './converterTypes'
import { useChatConverters } from '@/hooks/useChatConverters'
import { useRuntime } from '@/hooks/useRuntime'
import { useUserPreferences } from '@/hooks/useUserPreferences'
import TargetSelect from '@/components/Config/TargetSelect'
import {
  basenameFromValue,
  applyConvertedValues,
  buildMediaUrl,
  buildDraftPieceIds,
  dataTypeToAttachmentKind,
  isPathDataType,
  withDraftIdentity,
} from './converterTypes'
import type { ChatInputAreaHandle } from './ChatInputArea'
import { attacksApi, scoresApi } from '../../services/api'
import { toApiError } from '../../services/errors'
import {
  buildMessagePieces,
  backendMessageToOriginalDraft,
  backendMessagesToFrontend,
} from '../../utils/messageMapper'
import { exportConversation } from '../../utils/conversationExport'
import type { ExportFormat } from '../../utils/conversationExport'
import type {
  AddMessageResponse,
  AttackOutcome,
  AttackSummary,
  AttackTargetResolutionStatus,
  BackendMessage,
  BackendScore,
  ChatSendOutcome,
  ConversationMessagesResponse,
  CreateAttackRequest,
  CreateConversationRequest,
  Message,
  MessageAttachment,
  MessageSendRequest,
  MessageSendStatus,
  TargetInstance,
  TargetInfo,
} from '../../types'
import { isTargetResolutionBlocking, targetInfoMatchesTarget } from '../../utils/targetIdentity'
import { scenarioRunRoutePath } from '../../utils/routeParams'
import type { ViewName } from '../Sidebar/Navigation'
import { useChatWindowStyles } from './ChatWindow.styles'

const NARROW_SCREEN_QUERY = '(max-width: 600px)'
const RETRYABLE_TARGET_RESPONSE_ERROR = 'processing'
const CLEAN_CONVERSATION_MESSAGE =
  'Continue in a clean conversation so the stored error is not sent back to the target.'

interface RecoverableSendDraft {
  conversationId: string
  failedRequestTurnNumber: number
  failedResponseTurnNumber: number
  historyCutoffIndex: number
  errorMessageIndex: number
  originalValue: string
  attachments: MessageAttachment[]
  conversions: Record<string, PieceConversion>
  source: 'live' | 'persisted'
  missingConverterSelections: boolean
  converterGeneration?: string
}

interface ConversationLoadRequest {
  conversationId: string
  requestId: number
}

interface PendingSend {
  readonly submissionId: string
  readonly controller: AbortController
  readonly draftRevision: number | undefined
  readonly originalValue: string
  readonly attachments: MessageAttachment[]
  readonly conversions: Record<string, PieceConversion>
  readonly priorUserPieceIds: Set<string>
  readonly initialMessages: Message[]
  readonly navigationRevision: number
  readonly converterGeneration: string
  attackResultId: string | null
  conversationId: string
  needsRefresh: boolean
  responseReadId?: number
  progress?: MessageSendStatus
}

interface SendIssue {
  description: string
  blocking: boolean
}

function isSendFinished(progress: MessageSendStatus): boolean {
  return ['completed', 'failed', 'interrupted'].includes(progress.state)
}

function userPieceIds(response: ConversationMessagesResponse): Set<string> {
  return new Set(response.messages.filter((message) => message.role === 'user')
    .flatMap((message) => message.message_pieces.map((piece) => piece.id)))
}

function getRecoveryDescription(draft: RecoverableSendDraft): string {
  const historyNotice = draft.historyCutoffIndex < draft.failedRequestTurnNumber - 1
    ? ' History from the first failed prompt onward will be left out.'
    : ''
  const recoveryMessage = `${CLEAN_CONVERSATION_MESSAGE}${historyNotice}`
  if (draft.source === 'live') {
    return `${recoveryMessage} Your prompt, attachments, and converter choices are preserved for editing.`
  }

  const restored = 'Your prompt and attachments were restored from conversation history.'

  if (draft.missingConverterSelections) {
    return `${recoveryMessage} ${restored} Converter choices could not be restored, so review them before sending.`
  }

  return `${recoveryMessage} ${restored} Review them before sending.`
}

function getRecoveryHistoryCutoff(messages: BackendMessage[], failedRequestTurnNumber: number): number {
  let precedingUserTurnNumber: number | undefined
  for (const message of messages) {
    if (message.turn_number >= failedRequestTurnNumber) {
      break
    }
    if (message.role === 'user') {
      precedingUserTurnNumber = message.turn_number
    }
    for (const piece of message.message_pieces) {
      if (piece.response_error === RETRYABLE_TARGET_RESPONSE_ERROR) {
        // Later replies can depend on the failed turn, so retain only its preceding history.
        return (precedingUserTurnNumber ?? message.turn_number) - 1
      }
    }
  }
  return failedRequestTurnNumber - 1
}

function getPersistedProcessingRecovery(
  conversationId: string,
  response: ConversationMessagesResponse,
): RecoverableSendDraft | undefined {
  const responseStatus = response.target_response_status
  if (responseStatus?.response_error !== RETRYABLE_TARGET_RESPONSE_ERROR) {
    return undefined
  }

  const failedRequest = response.messages.find(
    (message) => (
      message.role === 'user'
      && message.turn_number === responseStatus.request_turn_number
    ),
  )
  const errorMessageIndex = response.messages.findIndex(
    (message) => (
      message.role === 'assistant'
      && message.turn_number === responseStatus.response_turn_number
    ),
  )
  if (!failedRequest || errorMessageIndex < 0) {
    return undefined
  }

  const originalDraft = backendMessageToOriginalDraft(failedRequest)
  return {
    conversationId,
    failedRequestTurnNumber: responseStatus.request_turn_number,
    failedResponseTurnNumber: responseStatus.response_turn_number,
    historyCutoffIndex: getRecoveryHistoryCutoff(response.messages, responseStatus.request_turn_number),
    errorMessageIndex,
    originalValue: originalDraft.content,
    attachments: (originalDraft.attachments ?? []).map((attachment) => ({ ...attachment })),
    conversions: {},
    source: 'persisted',
    missingConverterSelections: failedRequest.message_pieces.some(
      (piece) => Boolean(piece.converter_identifiers?.length),
    ),
  }
}

function matchesNarrowScreen(): boolean {
  return typeof window !== 'undefined'
    && typeof window.matchMedia === 'function'
    && window.matchMedia(NARROW_SCREEN_QUERY).matches
}

interface ChatWindowProps {
  /** Shared layout slot; standalone chat renders its toolbar inline. */
  toolbarContainer?: HTMLElement | null
  onNewAttack: () => void
  activeTarget: TargetInstance | null
  availableTargets: TargetInstance[]
  targetsLoading: boolean
  targetsError: string | null
  onRefreshTargets: () => void
  onSelectTarget: (target: TargetInstance | null) => void
  defaultBranchTarget: TargetInstance | null
  attackResultId: string | null
  conversationId: string | null
  activeConversationId: string | null
  onConversationCreated: (
    attackResultId: string,
    conversationId: string,
    objective?: string,
    target?: TargetInstance,
  ) => void
  onSelectConversation: (conversationId: string) => void
  onObjectiveChange?: (objective: string) => void
  onHumanScoreChange?: (score: BackendScore | null, outcome: AttackOutcome) => void
  onAttackChange?: (attack: AttackSummary) => void
  labels?: Record<string, string>
  onNavigate?: (view: ViewName) => void
  /** Operator from the loaded attack (for operator locking). Null for new attacks. */
  attackOperator?: string | null
  /** Target info that the current attack was started with (for cross-target guard). */
  attackTarget?: TargetInfo | null
  /** Result of resolving the persisted attack target against the current registry. */
  targetResolutionStatus?: AttackTargetResolutionStatus
  /** Re-run target registry resolution after a transient or unavailable result. */
  onRetryTargetResolution?: () => void
  /** True while a historical attack is being loaded from the history view. */
  isLoadingAttack?: boolean
  /** Number of related (non-main) conversations in the loaded attack. */
  relatedConversationCount?: number
  /** The loaded attack's objective (empty for new/manual attacks). */
  objective?: string
  /** The loaded attack's current outcome. */
  outcome?: AttackOutcome
  automatedScore?: BackendScore | null
  humanScore?: BackendScore | null
  lastResponseMessagePieceId?: string | null
  /** Validated scenario-run provenance for attacks opened from a run dashboard. */
  scenarioResultId?: string | null
}

export default function ChatWindow({
  toolbarContainer,
  onNewAttack,
  activeTarget,
  availableTargets,
  targetsLoading,
  targetsError,
  onRefreshTargets,
  onSelectTarget,
  defaultBranchTarget,
  attackResultId,
  conversationId,
  activeConversationId,
  onConversationCreated,
  onSelectConversation,
  onObjectiveChange,
  onHumanScoreChange,
  onAttackChange,
  labels,
  onNavigate,
  attackOperator,
  attackTarget,
  targetResolutionStatus = 'idle',
  onRetryTargetResolution,
  isLoadingAttack,
  relatedConversationCount,
  objective = '',
  outcome,
  automatedScore,
  humanScore,
  lastResponseMessagePieceId,
  scenarioResultId,
}: ChatWindowProps) {
  const styles = useChatWindowStyles()
  const restoreFocusTargetAttributes = useRestoreFocusTarget()
  const restoreFocusSourceAttributes = useRestoreFocusSource()
  const [messages, setMessages] = useState<Message[]>([])
  const [pendingObjective, setPendingObjective] = useState('')
  const [branchRequest, setBranchRequest] = useState<{ conversationId: string; cutoff: number } | null>(null)
  const [branchTarget, setBranchTarget] = useState<TargetInstance | null>(null)
  const [branchError, setBranchError] = useState<string | null>(null)
  const [isBranching, setIsBranching] = useState(false)
  const branchingRef = useRef(false)
  const isBranchTargetAvailable = availableTargets.some((target: TargetInstance) => sameTarget(target, branchTarget))
  // Track sending state per conversation so parallel conversations can send independently
  const [sendingConversations, setSendingConversations] = useState<Set<string>>(new Set())
  /** True while an async message fetch is in-flight */
  const [isLoadingMessages, setIsLoadingMessages] = useState(false)
  /** Which conversation's messages are currently loaded (set after fetch completes) */
  const [loadedConversationId, setLoadedConversationId] = useState<string | null>(null)
  const loadedConversationIdRef = useRef<string | null>(null)
  const nextConversationLoadRequestIdRef = useRef(0)
  const latestConversationLoadRequestIdsRef = useRef<Map<string, number>>(new Map())
  const activeConversationLoadRequestRef = useRef<ConversationLoadRequest | null>(null)
  const isSending = activeConversationId ? sendingConversations.has(activeConversationId) : Boolean(sendingConversations.size)
  const [isPanelOpen, setIsPanelOpen] = useState(false)
  const [isExporting, setIsExporting] = useState(false)
  const isExportingRef = useRef(false)
  const [isNarrowScreen, setIsNarrowScreen] = useState(matchesNarrowScreen)
  const [isConverterPanelOpen, setIsConverterPanelOpen] = useState(false)
  const runtime = useRuntime()
  // Conversation-wide preference for rendering message text as Markdown.
  const { preferences, updatePreferences } = useUserPreferences()
  const globalMarkdown = preferences.chatMarkdown
  const [chatInputText, setChatInputText] = useState('')
  const [systemPrompt, setSystemPrompt] = useState('')
  const [draftAttachments, setDraftAttachments] = useState<MessageAttachment[]>([])
  const converters = useChatConverters(chatInputText, draftAttachments)
  const { applied: activePieceConversions, restore: restoreConversions } = converters
  const [recoverableSends, setRecoverableSends] = useState<Record<string, RecoverableSendDraft>>({})
  const [isRecoveringProcessingError, setIsRecoveringProcessingError] = useState(false)
  const [panelRefreshKey, setPanelRefreshKey] = useState(0)
  const inputBoxRef = useRef<ChatInputAreaHandle>(null)
  const recoveryInFlightRef = useRef(false)
  const viewedConversationId = activeConversationId ?? conversationId
  const savedRecovery = viewedConversationId
    ? recoverableSends[viewedConversationId]
    : undefined
  const recoverableSend = useMemo<RecoverableSendDraft | undefined>(() => (
    savedRecovery?.converterGeneration !== undefined && savedRecovery.converterGeneration !== runtime.generation
      ? { ...savedRecovery, conversions: {}, source: 'persisted', missingConverterSelections: true }
      : savedRecovery
  ), [savedRecovery, runtime.generation])
  const [sendIssues, setSendIssues] = useState<Record<string, SendIssue>>({})
  const sendIssueConversationId = attackResultId ? viewedConversationId : loadedConversationId ?? viewedConversationId
  const sendIssue = sendIssues[sendIssueConversationId ?? '__pending__']
  const pendingSendsRef = useRef<Map<string, PendingSend>>(new Map())
  const latestSendRef = useRef<string | null>(null)
  const loadedUserPieceIdsRef = useRef<Map<string, Set<string>>>(new Map())
  const viewedAttackRef = useRef(attackResultId)
  const navigationRevisionRef = useRef(0)

  useLayoutEffect(() => {
    viewedAttackRef.current = attackResultId
    navigationRevisionRef.current += 1
  }, [attackResultId, activeConversationId, conversationId])

  useEffect(() => {
    const pendingSends = pendingSendsRef.current
    return () => {
      for (const operation of pendingSends.values()) {
        operation.controller.abort()
      }
      pendingSends.clear()
    }
  }, [])

  const markConversationLoaded = useCallback((loadedId: string | null): void => {
    loadedConversationIdRef.current = loadedId
    setLoadedConversationId(loadedId)
  }, [])

  const invalidateConversationLoads = useCallback((conversationIdToInvalidate: string): void => {
    latestConversationLoadRequestIdsRef.current.delete(conversationIdToInvalidate)
    if (activeConversationLoadRequestRef.current?.conversationId === conversationIdToInvalidate) {
      activeConversationLoadRequestRef.current = null
      setIsLoadingMessages(false)
    }
  }, [])

  useLayoutEffect(() => {
    loadedConversationIdRef.current = loadedConversationId
  }, [loadedConversationId])

  const handleMarkdownChange = useCallback((
    _event: ChangeEvent<HTMLInputElement>,
    data: SwitchOnChangeData,
  ): void => {
    updatePreferences((current) => ({ ...current, chatMarkdown: data.checked }))
  }, [updatePreferences])

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
      return
    }

    const mediaQuery = window.matchMedia(NARROW_SCREEN_QUERY)
    const handleChange = (event: MediaQueryListEvent) => {
      setIsNarrowScreen(event.matches)
    }
    mediaQuery.addEventListener('change', handleChange)
    return () => mediaQuery.removeEventListener('change', handleChange)
  }, [])

  const conversionRevisionKey = useMemo(
    () => JSON.stringify({
      applied: activePieceConversions, pipelines: converters.pipelines, editRevision: converters.editRevision,
    }),
    [activePieceConversions, converters.pipelines, converters.editRevision],
  )

  // Auto-open conversation sidebar when loading a historical attack with multiple
  // conversations. Uses the "adjust state during render" pattern to avoid
  // react-hooks/set-state-in-effect.
  const [autoOpenedForAttack, setAutoOpenedForAttack] = useState<string | null>(null)
  if (
    attackResultId
    && attackResultId !== autoOpenedForAttack
    && relatedConversationCount
    && relatedConversationCount > 0
  ) {
    setAutoOpenedForAttack(attackResultId)
    if (!isNarrowScreen) {
      setIsPanelOpen(true)
    }
  }
  // Set by panel click to bypass the in-flight guard on the next useEffect cycle.
  // This lets users switch to a sending conversation while still protecting
  // optimistic messages when handleSend internally updates activeConversationId.
  const forceLoadRef = useRef(false)
  // Always-current ref of the conversation being viewed so async callbacks can
  // check whether the user navigated away while a request was in-flight.
  const viewedConvRef = useRef(activeConversationId ?? conversationId)
  useLayoutEffect(() => {
    viewedConvRef.current = activeConversationId ?? conversationId
    return () => { viewedConvRef.current = null }
  }, [activeConversationId, conversationId])
  // Synchronous ref tracking which conversations have an in-flight send.
  const sendingConvIdsRef = useRef<Set<string>>(new Set())
  // Pending user messages per conversation that may not be stored server-side yet.
  // Used to restore the user's input when switching back to an in-flight conversation.
  const pendingUserMessagesRef = useRef<Map<string, Message[]>>(new Map())

  const supportsSystemPrompt = activeTarget?.capabilities?.supports_system_prompt === true
  const isTargetResolutionLocked = Boolean(
    attackResultId
    && isTargetResolutionBlocking(targetResolutionStatus),
  )
  const currentOperator = labels?.operator
  // Existing attacks are operator-locked when their operator differs from the current one.
  const isOperatorLocked = Boolean(
    attackResultId && attackOperator && currentOperator && attackOperator !== currentOperator,
  )
  // They are cross-target locked when the selected target's canonical hash differs from the persisted target.
  const isCrossTargetLocked = Boolean(
    attackResultId
    && attackTarget
    && activeTarget
    && !targetInfoMatchesTarget(attackTarget, activeTarget),
  )
  // Any failed invariant keeps all mutation controls and handlers read-only.
  const isMutationLocked = isOperatorLocked || isCrossTargetLocked || isTargetResolutionLocked

  // Clear internal messages when attack state is reset (e.g. New Attack).
  // Uses the "adjust state during render" pattern (see React docs:
  // https://react.dev/reference/react/useState#storing-information-from-previous-renders)
  // instead of a useEffect so we don't trigger react-hooks/set-state-in-effect.
  const [prevAttackResultId, setPrevAttackResultId] = useState<string | null>(attackResultId)
  if (attackResultId !== prevAttackResultId) {
    setPrevAttackResultId(attackResultId)
    if (!attackResultId) {
      setRecoverableSends({})
      setMessages([])
      setLoadedConversationId(null)
      setSystemPrompt('')
      setPendingObjective('')
    }
  }

  // Clear a retained system prompt when switching to a target that can't use it,
  // so it isn't silently dropped on send. Preserved across supporting targets to
  // keep the A/B-testing workflow intact.
  if (activeTarget && !supportsSystemPrompt && systemPrompt) {
    setSystemPrompt('')
  }

  // Load messages for a given conversation
  const loadConversation = useCallback(async (arId: string, convId: string) => {
    nextConversationLoadRequestIdRef.current += 1
    const requestId = nextConversationLoadRequestIdRef.current
    latestConversationLoadRequestIdsRef.current.set(convId, requestId)
    activeConversationLoadRequestRef.current = { conversationId: convId, requestId }
    setIsLoadingMessages(true)
    const isCurrentLoad = (): boolean => (
      latestConversationLoadRequestIdsRef.current.get(convId) === requestId
    )

    try {
      const response = await attacksApi.getMessages(arId, convId)
      // Discard superseded loads and responses invalidated by a send.
      if (!isCurrentLoad() || viewedConvRef.current !== convId) { return }
      const frontendMessages = backendMessagesToFrontend(response.messages)
      const savedUserIds = userPieceIds(response)
      loadedUserPieceIdsRef.current.set(convId, savedUserIds)
      const persistedRecovery = getPersistedProcessingRecovery(convId, response)
      setRecoverableSends((currentRecoveries) => {
        const currentRecovery = currentRecoveries[convId]
        if (persistedRecovery) {
          if (
            currentRecovery?.source === 'live'
            && currentRecovery.failedRequestTurnNumber === persistedRecovery.failedRequestTurnNumber
            && currentRecovery.failedResponseTurnNumber === persistedRecovery.failedResponseTurnNumber
          ) {
            return {
              ...currentRecoveries,
              [convId]: {
                ...currentRecovery,
                errorMessageIndex: persistedRecovery.errorMessageIndex,
                historyCutoffIndex: persistedRecovery.historyCutoffIndex,
              },
            }
          }
          return { ...currentRecoveries, [convId]: persistedRecovery }
        }
        if (!currentRecovery) {
          return currentRecoveries
        }
        const nextRecoveries = { ...currentRecoveries }
        delete nextRecoveries[convId]
        return nextRecoveries
      })
      // If this conversation has an in-flight send, append any pending user
      // messages (that the server may not have stored yet) and a loading indicator.
      if (sendingConvIdsRef.current.has(convId)) {
        const operation = pendingSendsRef.current.get(convId)
        const pending = pendingUserMessagesRef.current.get(convId) ?? []
        const requestStored = operation && [...savedUserIds].some((id) => !operation.priorUserPieceIds.has(id))
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
      setMessages(frontendMessages)
      markConversationLoaded(convId)
    } catch {
      if (!isCurrentLoad() || viewedConvRef.current !== convId) { return }
      // Initial-load failures must not show another conversation's transcript.
      // Refresh failures keep the already-loaded transcript and recovery aligned.
      if (loadedConversationIdRef.current !== convId) {
        setMessages([])
        markConversationLoaded(convId)
      }
    } finally {
      if (latestConversationLoadRequestIdsRef.current.get(convId) === requestId) {
        latestConversationLoadRequestIdsRef.current.delete(convId)
      }
      if (activeConversationLoadRequestRef.current?.requestId === requestId) {
        activeConversationLoadRequestRef.current = null
        setIsLoadingMessages(false)
      }
    }
  }, [markConversationLoaded])

  // Reload messages when activeConversationId changes
  useEffect(() => {
    if (!attackResultId || !activeConversationId) { return }
    // A created-attack route can commit after its first send completes.
    // Preserve that local result unless the user explicitly requests a refresh.
    const force = forceLoadRef.current
    forceLoadRef.current = false
    if (!force && (
      sendingConvIdsRef.current.has(activeConversationId)
      || (
        loadedConversationIdRef.current === activeConversationId
        && activeConversationLoadRequestRef.current === null
      )
    )) { return }
    loadConversation(attackResultId, activeConversationId)
  }, [activeConversationId, attackResultId, loadConversation])

  // Synchronous loading derivation: if activeConversationId differs from the
  // conversation whose messages we've loaded, we're in a transition gap.
  // This avoids the 1-frame flash between useEffect fire and render.
  // Reads `sendingConversations` (state) rather than `sendingConvIdsRef` so the
  // computation stays render-safe (the ref is for handlers/effects only).
  const awaitingConversationLoad = Boolean(
    activeConversationId && activeConversationId !== loadedConversationId
    && !sendingConversations.has(activeConversationId)
  )
  const isScoreLocked = isOperatorLocked || Boolean(isLoadingAttack) || isLoadingMessages || awaitingConversationLoad

  // Handle conversation selection from the panel
  // For a different ID the useEffect handles loading; for same ID force a refresh
  const handlePanelSelectConversation = useCallback((convId: string) => {
    forceLoadRef.current = true
    onSelectConversation(convId)
    if (isNarrowScreen) {
      setIsPanelOpen(false)
    }
    if (convId === activeConversationId && attackResultId) {
      loadConversation(attackResultId, convId)
    }
  }, [attackResultId, activeConversationId, isNarrowScreen, onSelectConversation, loadConversation])

  const isCurrentSend = (operation: PendingSend): boolean => (
    !operation.controller.signal.aborted
    && pendingSendsRef.current.get(operation.conversationId) === operation
  )
  const isViewingSend = (operation: PendingSend): boolean => (
    viewedAttackRef.current === operation.attackResultId
    && (
      viewedConvRef.current === operation.conversationId
      || (viewedConvRef.current === null && operation.conversationId === '__pending__'
        && navigationRevisionRef.current === operation.navigationRevision)
    )
  )
  const setSendIssue = (conversation: string, issue?: SendIssue): void => {
    setSendIssues((previous) => {
      const next = { ...previous }
      if (issue) { next[conversation] = issue } else { delete next[conversation] }
      return next
    })
  }
  const finishTracking = (operation: PendingSend): void => {
    if (!isCurrentSend(operation) || !sendingConvIdsRef.current.has(operation.conversationId)) { return }
    if (operation.responseReadId === undefined
      || latestConversationLoadRequestIdsRef.current.get(operation.conversationId) === operation.responseReadId) {
      invalidateConversationLoads(operation.conversationId)
    }
    if (isViewingSend(operation)) {
      setMessages((previous) => previous.filter((message) => !message.isLoading))
    }
    sendingConvIdsRef.current.delete(operation.conversationId)
    pendingUserMessagesRef.current.delete(operation.conversationId)
    setSendingConversations((previous) => {
      const next = new Set(previous)
      next.delete(operation.conversationId)
      return next
    })
    setPanelRefreshKey((key) => key + 1)
  }
  const retireSend = (operation: PendingSend): void => {
    if (isCurrentSend(operation) && !operation.needsRefresh) {
      pendingSendsRef.current.delete(operation.conversationId)
    }
  }
  const stopSendSpinner = (operation: PendingSend): void => {
    if (!isViewingSend(operation)) { return }
    const hasMatchingTranscript = loadedConversationIdRef.current === operation.conversationId
    const pending = pendingUserMessagesRef.current.get(operation.conversationId) ?? []
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
      attachments: operation.attachments,
      conversions: operation.conversions,
      converterGeneration: operation.converterGeneration,
      source: 'live',
      missingConverterSelections: false,
    } : recovery
    if (isLatestRead) {
      loadedUserPieceIdsRef.current.set(effectiveConvId, userPieceIds(response.messages))
      setRecoverableSends((currentRecoveries) => {
        const next = { ...currentRecoveries }
        if (recoveredDraft) { next[effectiveConvId] = recoveredDraft } else { delete next[effectiveConvId] }
        return next
      })
    }
    if (isLatestRead && isViewingSend(operation)) {
      invalidateConversationLoads(effectiveConvId)
      setMessages(backendMessages)
      markConversationLoaded(effectiveConvId)
      onAttackChange?.(response.attack)
    }
    return {
      status,
      clearDraft: status !== 'retryable_failure' && latestSendRef.current === operation.submissionId,
    }
  }

  const trackSend = async (operation: PendingSend): Promise<ChatSendOutcome> => {
    try {
      if (!operation.attackResultId) { throw new Error('The send has no attack ID.') }
      while (operation.progress && !isSendFinished(operation.progress)) {
        let progress: MessageSendStatus | undefined
        try {
          progress = await attacksApi.getMessageSend(
            operation.attackResultId, operation.progress.send_id, operation.controller.signal,
          )
        } catch (err) {
          if (toApiError(err).status !== 404) { throw err }
          // Lost handles permit evidence reads, never a new submission or a claim of delivery.
        }
        if (!isCurrentSend(operation)) { return { status: 'non_retryable_failure', clearDraft: false } }
        operation.progress = progress
      }
      nextConversationLoadRequestIdRef.current += 1
      operation.responseReadId = nextConversationLoadRequestIdRef.current
      latestConversationLoadRequestIdsRef.current.set(operation.conversationId, operation.responseReadId)
      const [attack, conversation] = await Promise.all([
        attacksApi.getAttack(operation.attackResultId),
        attacksApi.getMessages(operation.attackResultId, operation.conversationId),
      ])
      if (!isCurrentSend(operation)) { return { status: 'non_retryable_failure', clearDraft: false } }
      const isLatestRead = latestConversationLoadRequestIdsRef.current.get(operation.conversationId)
        === operation.responseReadId
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
        ? { description: progress.error ?? 'Message preparation failed before target dispatch.', blocking: false }
        : undefined)
      operation.needsRefresh = progress.failure_stage === 'preparation'
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
    }
  }

  const refreshSend = async (): Promise<void> => {
    const operation = pendingSendsRef.current.get(sendIssueConversationId ?? '__pending__')
    if (!operation || sendingConvIdsRef.current.has(operation.conversationId)) { return }
    sendingConvIdsRef.current.add(operation.conversationId)
    setSendingConversations((previous) => new Set(previous).add(operation.conversationId))
    const outcome = await trackSend(operation)
    if (isCurrentSend(operation) && isViewingSend(operation) && outcome.clearDraft
      && inputBoxRef.current
      && inputBoxRef.current?.getDraftRevision() === operation.draftRevision) {
      inputBoxRef.current.restoreDraft('', [])
      setChatInputText('')
      setDraftAttachments([])
      converters.clearAll()
    }
    retireSend(operation)
  }

  const handleSend = async (
    originalValue: string,
    convertedValue: string | undefined,
    attachments: MessageAttachment[],
  ): Promise<ChatSendOutcome> => {
    if (
      !runtime.ready
      || !activeTarget
      || isLoadingAttack
      || isLoadingMessages
      || awaitingConversationLoad
      || isMutationLocked
      || sendIssue?.blocking
    ) {
      return { status: 'retryable_failure', clearDraft: false }
    }

    const initialSendConvId = activeConversationId ?? conversationId ?? '__pending__'
    if (sendingConvIdsRef.current.has(initialSendConvId)) {
      return { status: 'retryable_failure', clearDraft: false }
    }

    invalidateConversationLoads(initialSendConvId)
    setRecoverableSends((currentRecoveries) => {
      if (!currentRecoveries[initialSendConvId]) {
        return currentRecoveries
      }
      const nextRecoveries = { ...currentRecoveries }
      delete nextRecoveries[initialSendConvId]
      return nextRecoveries
    })

    // Capture all piece conversions upfront before any async work or state clears
    const conversions = { ...activePieceConversions }
    const operation: PendingSend = {
      submissionId: generateClientId(),
      controller: new AbortController(),
      draftRevision: inputBoxRef.current?.getDraftRevision(),
      originalValue,
      attachments: attachments.map((attachment) => ({ ...attachment })),
      conversions,
      priorUserPieceIds: new Set(loadedUserPieceIdsRef.current.get(initialSendConvId)),
      initialMessages: [...messages],
      navigationRevision: navigationRevisionRef.current,
      converterGeneration: runtime.generation,
      attackResultId,
      conversationId: initialSendConvId,
      needsRefresh: false,
    }
    pendingSendsRef.current.set(initialSendConvId, operation)
    latestSendRef.current = operation.submissionId
    setSendIssue(initialSendConvId)
    const submittedNavigationRevision = navigationRevisionRef.current
    let submissionAttempted = false
    const textConversion = conversions['text']
    const isTextTextConversion = textConversion?.convertedDataType === 'text'
    const isTextFileConversion = Boolean(textConversion) && !isTextTextConversion

    // Track which conversation this send belongs to (may be updated after attack creation)
    let sendConvId = initialSendConvId
    // Mark synchronously so the useEffect guard sees it immediately
    sendingConvIdsRef.current.add(sendConvId)

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
    const pending = pendingUserMessagesRef.current.get(sendConvId) ?? []
    pending.push(userMessage)
    pendingUserMessagesRef.current.set(sendConvId, pending)

    // Show loading indicator
    setSendingConversations(prev => new Set(prev).add(sendConvId))
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
      const originalPieces = await buildMessagePieces(originalValue, attachments)
      if (textConversion && !originalValue.trim()) {
        originalPieces.unshift({ data_type: 'text', original_value: originalValue })
      }
      const pieces = applyConvertedValues(
        originalPieces,
        pieceIds,
        conversions,
      )
      if (!isCurrentSend(operation)) { return { status: 'non_retryable_failure', clearDraft: false } }

      // Create attack lazily on first message
      let currentAttackResultId = attackResultId
      let currentConversationId = conversationId
      let currentActiveConversationId = activeConversationId
      if (!currentAttackResultId) {
        const createRequest: CreateAttackRequest = {
          target_registry_name: activeTarget.target_registry_name,
          name: pendingObjective || undefined,
          // TODO(PyRIT 1.4): Pass only dedicated attribution after legacy label aliases are removed.
          // The create-attack API normalizes these aliases through _AttackAttributionInput.
          labels,
          system_prompt: supportsSystemPrompt ? systemPrompt.trim() || undefined : undefined,
        }
        const createResponse = await attacksApi.createAttack(createRequest)
        if (!isCurrentSend(operation)) { return { status: 'non_retryable_failure', clearDraft: false } }
        currentAttackResultId = createResponse.attack_result_id
        currentConversationId = createResponse.conversation_id
        currentActiveConversationId = currentConversationId
        // Mark new ID in synchronous ref *before* triggering the state
        // update that changes activeConversationId (and fires the useEffect)
        sendingConvIdsRef.current.delete('__pending__')
        sendingConvIdsRef.current.add(currentConversationId!)
        // Move pending messages to the real conversation ID
        const pendingMsgs = pendingUserMessagesRef.current.get('__pending__')
        if (pendingMsgs) {
          pendingUserMessagesRef.current.delete('__pending__')
          pendingUserMessagesRef.current.set(currentConversationId!, pendingMsgs)
        }
        pendingSendsRef.current.delete('__pending__')
        operation.attackResultId = currentAttackResultId
        operation.conversationId = currentConversationId
        pendingSendsRef.current.set(currentConversationId, operation)
        if (navigationRevisionRef.current === submittedNavigationRevision) {
          onConversationCreated(currentAttackResultId, currentConversationId, pendingObjective || undefined)
          viewedAttackRef.current = currentAttackResultId
          viewedConvRef.current = currentConversationId
        }
        // Update sending tracker to use real ID instead of __pending__
        setSendingConversations(prev => {
          const next = new Set(prev)
          next.delete('__pending__')
          next.add(currentConversationId!)
          return next
        })
        sendConvId = currentConversationId!
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
      }
      submissionAttempted = true
      operation.progress = await attacksApi.submitMessageSend(currentAttackResultId, addMessageRequest)
      if (!isCurrentSend(operation)) { return { status: 'non_retryable_failure', clearDraft: false } }
      return await trackSend(operation)
    } catch (err) {
      if (!isCurrentSend(operation)) { return { status: 'non_retryable_failure', clearDraft: false } }
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
      const viewedConversationId = viewedConvRef.current
      const isViewingFailedConversation = isViewingSend(operation)

      // Only show error in UI if user is still on this conversation
      if (isViewingFailedConversation) {
        const hasMatchingTranscript = loadedConversationIdRef.current === sendConvId
        // Mark the viewed conversation as loaded so first-send failures do not
        // get stuck behind the "Loading conversation..." placeholder.
        if (viewedConversationId) {
          markConversationLoaded(viewedConversationId)
        } else if (sendConvId !== '__pending__') {
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
          if (failedMessages.length > 0 && failedMessages[failedMessages.length - 1].isLoading) {
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

  const appendConversationCreationError = useCallback((error: unknown): void => {
    const apiError = toApiError(error)
    setMessages((previousMessages) => [
      ...previousMessages,
      {
        role: 'assistant',
        content: '',
        timestamp: new Date().toISOString(),
        error: {
          type: 'unknown',
          description: `Could not create a new conversation. ${apiError.detail}`,
        },
      },
    ])
  }, [])

  const createAndSelectConversation = useCallback(async (
    request: CreateConversationRequest,
  ): Promise<boolean> => {
    if (!attackResultId || isMutationLocked) { return false }

    try {
      const response = await attacksApi.createConversation(attackResultId, request)
      onSelectConversation(response.conversation_id)
      setIsPanelOpen(!isNarrowScreen)
      return true
    } catch (err) {
      appendConversationCreationError(err)
      return false
    }
  }, [
    appendConversationCreationError,
    attackResultId,
    isNarrowScreen,
    isMutationLocked,
    onSelectConversation,
  ])

  const handleNewConversation = useCallback(
    (): Promise<boolean> => createAndSelectConversation({}),
    [createAndSelectConversation],
  )

  const restoreRecoverableDraft = useCallback((): void => {
    if (!recoverableSend) { return }
    const attachments = recoverableSend.attachments.map(withDraftIdentity)
    setChatInputText(recoverableSend.originalValue)
    setDraftAttachments(attachments)
    restoreConversions(recoverableSend.originalValue, attachments, recoverableSend.conversions)
    inputBoxRef.current?.restoreDraft(
      recoverableSend.originalValue,
      attachments,
    )
    inputBoxRef.current?.focus()
  }, [restoreConversions, recoverableSend])

  const handleRecoverProcessingError = useCallback(async (): Promise<void> => {
    if (
      !attackResultId
      || !recoverableSend
      || isMutationLocked
      || sendIssue?.blocking
      || isSending
      || recoveryInFlightRef.current
    ) {
      return
    }

    const supportsMultiTurn = Boolean(
      activeTarget && activeTarget.capabilities?.supports_multi_turn !== false,
    )
    const cutoffIndex = recoverableSend.historyCutoffIndex
    const recoveryRequest: CreateConversationRequest = supportsMultiTurn && cutoffIndex >= 0
      ? {
          source_conversation_id: recoverableSend.conversationId,
          cutoff_index: cutoffIndex,
        }
      : {}
    const sourceConversationId = recoverableSend.conversationId
    const draftRevision = inputBoxRef.current?.getDraftRevision()

    recoveryInFlightRef.current = true
    setIsRecoveringProcessingError(true)
    try {
      const response = await attacksApi.createConversation(attackResultId, recoveryRequest)
      setPanelRefreshKey((currentKey) => currentKey + 1)

      const isStillViewingSource = viewedConvRef.current === sourceConversationId
      const isDraftUnchanged = inputBoxRef.current?.getDraftRevision() === draftRevision
      if (!isStillViewingSource || !isDraftUnchanged) {
        return
      }

      onSelectConversation(response.conversation_id)
      setIsPanelOpen(!isNarrowScreen)
      restoreRecoverableDraft()
    } catch (err) {
      if (viewedConvRef.current === sourceConversationId) {
        appendConversationCreationError(err)
      }
    } finally {
      recoveryInFlightRef.current = false
      setIsRecoveringProcessingError(false)
    }
  }, [
    activeTarget,
    appendConversationCreationError,
    attackResultId,
    isMutationLocked,
    isSending,
    isNarrowScreen,
    onSelectConversation,
    recoverableSend,
    sendIssue?.blocking,
    restoreRecoverableDraft,
  ])

  // -------------------------------------------------------------------
  // Message action handlers (4 buttons on each assistant message)
  // -------------------------------------------------------------------

  const copyMessageToInput = useCallback((message: Message): void => {
    const inputBox = inputBoxRef.current
    if (!inputBox) { return }

    if (message.content) {
      inputBox.setText(message.content)
    }
    for (const attachment of message.attachments ?? []) {
      if (attachment.type !== 'file') {
        inputBox.addAttachment(attachment)
      }
    }
  }, [])

  /** 1. Copy the clicked message's content/attachments into the current conversation's input box */
  const handleCopyToInput = useCallback((messageIndex: number) => {
    const msg = messages[messageIndex]
    if (!msg) { return }
    copyMessageToInput(msg)
  }, [copyMessageToInput, messages])

  /** 2. Create a new conversation in the same attack and copy ONLY this message to its input box */
  const handleCopyToNewConversation = useCallback(async (messageIndex: number) => {
    if (!attackResultId || isMutationLocked) { return }
    const msg = messages[messageIndex]
    if (!msg) { return }

    try {
      const response = await attacksApi.createConversation(attackResultId, {})
      onSelectConversation(response.conversation_id)
      setIsPanelOpen(!isNarrowScreen)
      // Small delay so the panel/messages update first
      setTimeout(() => {
        copyMessageToInput(msg)
      }, 100)
    } catch {
      // If creating fails, fall back to current conversation
      if (msg.content) inputBoxRef.current?.setText(msg.content)
    }
  }, [
    attackResultId,
    copyMessageToInput,
    isNarrowScreen,
    isMutationLocked,
    messages,
    onSelectConversation,
  ])

  /** 3. Branch into a new conversation within the same attack (clone up to clicked message) */
  const handleBranchConversation = useCallback(async (messageIndex: number) => {
    if (
      !attackResultId
      || !activeConversationId
      || isMutationLocked
    ) {
      return
    }

    try {
      const response = await attacksApi.createConversation(attackResultId, {
        source_conversation_id: activeConversationId,
        cutoff_index: messageIndex,
      })
      onSelectConversation(response.conversation_id)
      setIsPanelOpen(!isNarrowScreen)
      // Load the cloned messages
      const messagesResp = await attacksApi.getMessages(attackResultId, response.conversation_id)
      const frontendMessages = backendMessagesToFrontend(messagesResp.messages)
      setMessages(frontendMessages)
    } catch (err) {
      console.error('Failed to branch into new conversation:', err)
    }
  }, [
    attackResultId,
    activeConversationId,
    isNarrowScreen,
    isMutationLocked,
    onSelectConversation,
  ])

  /** 4. Branch into a brand-new attack (clone up to clicked message with new labels) */
  const handleBranchAttack = useCallback((messageIndex: number): void => {
    if (!activeConversationId || isLoadingAttack || isLoadingMessages || awaitingConversationLoad) return
    setBranchRequest({ conversationId: activeConversationId, cutoff: messageIndex })
    setBranchTarget(defaultBranchTarget ?? activeTarget)
    setBranchError(null)
    onRefreshTargets()
  }, [
    activeConversationId, activeTarget, awaitingConversationLoad, defaultBranchTarget,
    isLoadingAttack, isLoadingMessages, onRefreshTargets,
  ])

  const confirmBranch = async (): Promise<void> => {
    if (!branchRequest || !branchTarget || branchingRef.current || targetsLoading || targetsError) return
    if (!isBranchTargetAvailable) {
      setBranchError('The destination target changed or is no longer registered. Select a target again.')
      return
    }
    branchingRef.current = true
    setIsBranching(true)
    setBranchError(null)
    try {
      const createResponse = await attacksApi.createAttack({
        target_registry_name: branchTarget.target_registry_name,
        labels,
        source_conversation_id: branchRequest.conversationId,
        cutoff_index: branchRequest.cutoff,
      })
      setBranchRequest(null)
      if (viewedConvRef.current !== branchRequest.conversationId) return
      onConversationCreated(createResponse.attack_result_id, createResponse.conversation_id, undefined, branchTarget)
      const messagesResp = await attacksApi.getMessages(createResponse.attack_result_id, createResponse.conversation_id)
      if (
        viewedConvRef.current !== branchRequest.conversationId
        && viewedConvRef.current !== createResponse.conversation_id
      ) return
      const frontendMessages = backendMessagesToFrontend(messagesResp.messages)
      setMessages(frontendMessages)
      markConversationLoaded(createResponse.conversation_id)
    } catch (err) {
      setBranchError(toApiError(err).detail)
    } finally {
      branchingRef.current = false
      setIsBranching(false)
    }
  }

  const handleChangeMainConversation = useCallback(async (convId: string) => {
    if (
      !attackResultId
      || isMutationLocked
    ) {
      return
    }

    try {
      await attacksApi.changeMainConversation(attackResultId, convId)
      setPanelRefreshKey(k => k + 1)
    } catch (err) {
      console.error('Failed to change main conversation:', err)
    }
  }, [
    attackResultId,
    isMutationLocked,
  ])

  const handleHumanScoreUpdate = useCallback(async (value: boolean, rationale: string): Promise<void> => {
    if (
      !attackResultId
      || !lastResponseMessagePieceId
      || !(objective || pendingObjective).trim()
      || isScoreLocked
    ) {
      return
    }

    const score = await scoresApi.createManualScore({
      attack_result_id: attackResultId,
      message_id: lastResponseMessagePieceId,
      value,
      rationale,
      update_attack: true,
    })
    onHumanScoreChange?.(score, value ? 'success' : 'failure')
    if (activeConversationId && viewedConvRef.current === activeConversationId) {
      await loadConversation(attackResultId, activeConversationId)
    }
  }, [
    activeConversationId,
    attackResultId,
    isScoreLocked,
    lastResponseMessagePieceId,
    loadConversation,
    objective,
    onHumanScoreChange,
    pendingObjective,
  ])

  const handleHumanScoreRemove = useCallback(async (): Promise<void> => {
    if (!attackResultId || !humanScore || isScoreLocked) return

    const attack = await attacksApi.removeHumanScore(attackResultId)
    onHumanScoreChange?.(null, attack.outcome ?? 'undetermined')
    if (activeConversationId && viewedConvRef.current === activeConversationId) {
      await loadConversation(attackResultId, activeConversationId)
    }
  }, [
    activeConversationId,
    attackResultId,
    humanScore,
    isScoreLocked,
    loadConversation,
    onHumanScoreChange,
  ])

  const handleAddObjective = useCallback(async (newObjective: string): Promise<void> => {
    if (!attackResultId) {
      setPendingObjective(newObjective)
      return
    }

    const updatedAttack = await attacksApi.updateAttack(attackResultId, { objective: newObjective })
    onObjectiveChange?.(updatedAttack.objective)
  }, [attackResultId, onObjectiveChange])

  const singleTurnLimitReached = activeTarget?.capabilities?.supports_multi_turn === false && messages.some(m => m.role === 'user')
  const recoverableProcessingErrorIndex = recoverableSend?.conversationId === viewedConversationId
    && recoverableSend.errorMessageIndex >= 0
    ? recoverableSend.errorMessageIndex
    : undefined
  const processingRecoveryDescription = recoverableSend
    ? getRecoveryDescription(recoverableSend)
    : undefined

  // "Continue with your target" — clone the current conversation into a new attack
  const handleUseAsTemplate = useCallback(() => {
    if (!attackResultId || !activeConversationId) { return }
    const lastIndex = messages.reduce(
      (acc, m, i) => (m.isLoading ? acc : i),
      -1
    )
    if (lastIndex < 0) { return }

    handleBranchAttack(lastIndex)
  }, [
    activeConversationId,
    attackResultId,
    handleBranchAttack,
    messages,
  ])

  const systemMessage = messages.find(message => message.role === 'system')

  // Export is available whenever there is a stable, viewable conversation:
  // not while empty, loading, or mid-send. A lone system prompt (rendered only
  // in the banner, not the chat body) does not count as an exportable message.
  // Read-only / operator-lock / cross-target states do not block export.
  const canExportConversation =
    messages.some((message) => !message.isLoading && message.role !== 'system') &&
    !isSending &&
    !isLoadingAttack &&
    !isLoadingMessages &&
    !awaitingConversationLoad

  const handleExport = async (format: ExportFormat) => {
    // A ref, not the state flag: two clicks in the same tick would both read
    // the pre-render value and start duplicate exports.
    if (isExportingRef.current) {
      return
    }
    isExportingRef.current = true
    setIsExporting(true)
    try {
      await exportConversation({ messages, conversationId: activeConversationId ?? conversationId, format })
    } catch (err) {
      console.error('Failed to export conversation:', err)
    } finally {
      isExportingRef.current = false
      setIsExporting(false)
    }
  }

  // Chat owns these handlers and states even when the layout hosts the controls.
  const toolbar = (
    <div
      className={toolbarContainer ? styles.sharedToolbar : styles.ribbon}
      role="group"
      aria-label="Chat controls"
    >
      <div className={mergeClasses(styles.conversationInfo, toolbarContainer ? styles.sharedTarget : undefined)}>
        {!attackResultId && !isLoadingAttack ? (
          <ChatTargetPicker
            target={activeTarget}
            targets={availableTargets}
            loading={targetsLoading}
            error={targetsError}
            disabled={isSending}
            onSelect={onSelectTarget}
          />
        ) : activeTarget ? (
          <TargetBadge target={activeTarget} />
        ) : (
          <Text size={200} className={styles.noTarget}>
            No target selected
          </Text>
        )}
      </div>
      <div className={mergeClasses(styles.ribbonActions, toolbarContainer ? styles.sharedActions : undefined)}>
        <Tooltip content="Render all messages as Markdown by default" relationship="label">
          <Switch
            checked={globalMarkdown}
            onChange={handleMarkdownChange}
            label="Markdown"
            data-testid="global-markdown-toggle"
          />
        </Tooltip>
        <Menu>
          <MenuTrigger disableButtonEnhancement>
            <Tooltip content="Export conversation" relationship="label">
              <Button
                appearance="subtle"
                className={styles.ribbonAction}
                icon={isExporting ? <Spinner size="tiny" /> : <ArrowDownloadRegular />}
                disabled={!canExportConversation}
                aria-label="Export conversation"
                data-testid="export-conversation-btn"
              />
            </Tooltip>
          </MenuTrigger>
          <MenuPopover>
            <MenuList>
              <MenuItem
                onClick={() => handleExport('markdown')}
                disabled={isExporting}
                data-testid="export-markdown-item"
              >
                Export as Markdown (.md)
              </MenuItem>
              <MenuItem onClick={() => handleExport('json')} disabled={isExporting} data-testid="export-json-item">
                Export as JSON (.json)
              </MenuItem>
              <MenuItem onClick={() => handleExport('html')} disabled={isExporting} data-testid="export-html-item">
                Export as HTML (.html)
              </MenuItem>
            </MenuList>
          </MenuPopover>
        </Menu>
        <Tooltip content="Toggle conversations panel" relationship="label">
          <Button
            {...restoreFocusTargetAttributes}
            appearance="subtle"
            className={styles.ribbonAction}
            icon={<PanelRightRegular />}
            onClick={() => setIsPanelOpen((open) => !open)}
            disabled={!attackResultId}
            data-testid="toggle-panel-btn"
            aria-label="Toggle conversations panel"
            aria-expanded={isPanelOpen}
            aria-controls="conversation-panel"
          />
        </Tooltip>
        <Tooltip content="New Attack" relationship="label">
          <Button
            appearance="primary"
            icon={<AddRegular />}
            onClick={() => {
              navigationRevisionRef.current += 1
              setIsPanelOpen(false)
              onNewAttack()
            }}
            disabled={!attackResultId}
            data-testid="new-attack-btn"
            aria-label="New Attack"
            className={styles.newAttackButton}
          >
            <span className={styles.newAttackLabel}>New Attack</span>
          </Button>
        </Tooltip>
      </div>
    </div>
  )

  return (
    <div className={styles.root}>
      <h1 className={styles.pageHeading}>Chat</h1>
      <Dialog
        open={branchRequest !== null && branchRequest.conversationId === activeConversationId}
        onOpenChange={(_event, data) => { if (!data.open && !isBranching) setBranchRequest(null) }}
      >
        <DialogSurface>
          <DialogBody>
            <DialogTitle>Continue in a new attack</DialogTitle>
            <DialogContent>
              <TargetSelect
                label="Destination target"
                targets={availableTargets}
                value={branchTarget?.target_registry_name ?? ''}
                onChange={setBranchTarget}
                disabled={targetsLoading || isBranching}
              />
              {(branchError || targetsError) && (
                <MessageBar intent="error"><MessageBarBody>{branchError || targetsError}</MessageBarBody></MessageBar>
              )}
              {!targetsLoading && availableTargets.length === 0 && (
                <Text>No targets are registered. Add a target in the registry.</Text>
              )}
            </DialogContent>
            <DialogActions>
              <Button onClick={() => setBranchRequest(null)} disabled={isBranching}>Cancel</Button>
              <Button
                appearance="primary"
                onClick={confirmBranch}
                disabled={!branchTarget || targetsLoading || Boolean(targetsError) || isBranching
                  || !isBranchTargetAvailable}
              >
                Create attack
              </Button>
            </DialogActions>
          </DialogBody>
        </DialogSurface>
      </Dialog>
      {isConverterPanelOpen && (
        <ConverterPanel
          onClose={() => setIsConverterPanelOpen(false)}
          controller={converters}
        />
      )}
      <div className={styles.chatArea} data-testid="chat-area">
        {scenarioResultId && (
          <div className={styles.breadcrumbBar}>
            <Breadcrumb aria-label="Attack provenance" size="small">
              <BreadcrumbItem>
                <Text size={200}>Scanner History</Text>
              </BreadcrumbItem>
              <BreadcrumbDivider />
              <BreadcrumbItem>
                <Link
                  className={styles.breadcrumbLink}
                  to={scenarioRunRoutePath(scenarioResultId)}
                  aria-label={`Return to scenario run ${scenarioResultId}`}
                >
                  Scenario run {scenarioResultId.slice(0, 8)}
                </Link>
              </BreadcrumbItem>
            </Breadcrumb>
          </div>
        )}
        {toolbarContainer ? createPortal(toolbar, toolbarContainer) : toolbar}
        <ObjectiveHeader
          key={`${attackResultId ?? 'new'}-${objective}-${pendingObjective}`}
          objective={objective || pendingObjective}
          outcome={outcome}
          automatedScore={automatedScore}
          humanScore={humanScore}
          canUpdateOutcome={
            Boolean(attackResultId)
            && Boolean(lastResponseMessagePieceId)
            && Boolean((objective || pendingObjective).trim())
            && !isScoreLocked
          }
          canRemoveHumanScore={
            Boolean(attackResultId)
            && Boolean(humanScore)
            && !isScoreLocked
          }
          onUpdateHumanScore={handleHumanScoreUpdate}
          onRemoveHumanScore={handleHumanScoreRemove}
          canAdd={
            Boolean(activeTarget)
            && !isLoadingAttack
            && !isLoadingMessages
            && !awaitingConversationLoad
            && !isMutationLocked
          }
          onAdd={handleAddObjective}
        />
        {systemMessage && <SystemPromptBanner content={systemMessage.content} />}
        <MessageList
          messages={messages}
          onCopyToInput={handleCopyToInput}
          onCopyToNewConversation={attackResultId ? handleCopyToNewConversation : undefined}
          onBranchConversation={attackResultId && activeConversationId ? handleBranchConversation : undefined}
          onBranchAttack={activeConversationId ? handleBranchAttack : undefined}
          isLoading={isLoadingAttack || isLoadingMessages || awaitingConversationLoad}
          isSingleTurn={activeTarget?.capabilities?.supports_multi_turn === false}
          isOperatorLocked={isOperatorLocked}
          isCrossTarget={isCrossTargetLocked || isTargetResolutionLocked}
          noTargetSelected={!activeTarget}
          globalMarkdown={globalMarkdown}
          processingErrorRecovery={recoverableProcessingErrorIndex === undefined
            || processingRecoveryDescription === undefined
            ? undefined
            : {
                messageIndex: recoverableProcessingErrorIndex,
                actionLabel: activeTarget?.capabilities?.supports_multi_turn === false
                  ? 'Edit in new conversation'
                  : 'Edit in clean conversation',
                description: processingRecoveryDescription,
                disabled: isRecoveringProcessingError || isMutationLocked || isSending || Boolean(sendIssue?.blocking),
                onRecover: handleRecoverProcessingError,
              }}
        />
        {sendIssue && (
          <MessageBar intent="error">
            <MessageBarBody>{sendIssue.description}</MessageBarBody>
            <MessageBarActions>
              <Button className={styles.ribbonAction} disabled={isSending} onClick={() => { void refreshSend() }}>
                Refresh saved messages
              </Button>
            </MessageBarActions>
          </MessageBar>
        )}
        <ChatInputArea
          ref={inputBoxRef}
          onSend={handleSend}
          sendDisabled={isLoadingMessages || awaitingConversationLoad || sendIssue?.blocking}
          conversionRevisionKey={conversionRevisionKey}
          showSystemPrompt={!attackResultId}
          supportsSystemPrompt={supportsSystemPrompt}
          systemPrompt={systemPrompt}
          onSystemPromptChange={setSystemPrompt}
          disabled={
            !runtime.ready
            || isSending
            || !activeTarget
            || isLoadingAttack
            || singleTurnLimitReached
            || isMutationLocked
            || recoverableProcessingErrorIndex !== undefined
          }
          activeTarget={activeTarget}
          singleTurnLimitReached={singleTurnLimitReached}
          onNewConversation={handleNewConversation}
          operatorLocked={isOperatorLocked}
          crossTargetLocked={isCrossTargetLocked}
          targetResolutionStatus={targetResolutionStatus}
          onRetryTargetResolution={onRetryTargetResolution}
          onUseAsTemplate={handleUseAsTemplate}
          attackOperator={isOperatorLocked ? attackOperator ?? undefined : undefined}
          onConfigureTarget={() => onNavigate?.('registry')}
          onToggleConverterPanel={() => setIsConverterPanelOpen(prev => !prev)}
          isConverterPanelOpen={isConverterPanelOpen}
          onInputChange={setChatInputText}
          onAttachmentsChange={setDraftAttachments}
          convertedValue={activePieceConversions['text']?.convertedDataType === 'text' ? (activePieceConversions['text']?.convertedValue ?? null) : null}
          originalValue={activePieceConversions['text']?.originalValue ?? null}
          onClearConversion={() => converters.clear('text')}
          onClearAllConversions={converters.clearAll}
          onConvertedValueChange={(val: string) => converters.editConvertedValue('text', val)}
          convertedFileChip={(() => {
            const tc = activePieceConversions['text']
            if (!tc || tc.convertedDataType === 'text') return null
            if (!isPathDataType(tc.convertedDataType)) return null
            return {
              name: basenameFromValue(tc.convertedValue, 'output'),
              url: buildMediaUrl(tc.convertedValue),
              iconKind: dataTypeToAttachmentKind(tc.convertedDataType),
            }
          })()}
          onClearConvertedFileChip={() => converters.clear('text')}
          converterOutputDataTypes={Object.values(activePieceConversions).map((c) => c.convertedDataType)}
          mediaConversions={Object.entries(activePieceConversions)
            .filter(([k]) => k !== 'text')
            .map(([, conversion]) => conversion)}
          onClearMediaConversion={converters.clear}
        />
      </div>
      <Drawer
        as="aside"
        {...restoreFocusSourceAttributes}
        type={isNarrowScreen ? 'overlay' : 'inline'}
        position="end"
        separator
        open={isPanelOpen}
        onOpenChange={(_, { open }) => setIsPanelOpen(open)}
        className={mergeClasses(
          styles.conversationDrawer,
          isNarrowScreen && styles.narrowConversationDrawer,
        )}
        aria-label="Attack Conversations"
      >
        <ConversationPanel
          attackResultId={attackResultId}
          activeConversationId={activeConversationId}
          onSelectConversation={handlePanelSelectConversation}
          onNewConversation={handleNewConversation}
          onChangeMainConversation={handleChangeMainConversation}
          onClose={() => setIsPanelOpen(false)}
          lockedReason={
            !activeTarget ? 'Configure a target to enable this action.'
            : isOperatorLocked ? 'Cannot modify — attack belongs to a different operator.'
            : isCrossTargetLocked ? 'Cannot modify — attack was created with a different target.'
            : isTargetResolutionLocked ? 'Cannot modify — the attack target could not be safely resolved.'
            : undefined
          }
          refreshKey={panelRefreshKey}
        />
      </Drawer>
    </div>
  )
}
