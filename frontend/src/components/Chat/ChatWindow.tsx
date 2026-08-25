import { useState, useRef, useEffect, useLayoutEffect, useCallback, useMemo } from 'react'
import type { ChangeEvent } from 'react'
import {
  Button,
  Drawer,
  Menu,
  MenuItem,
  MenuList,
  MenuPopover,
  MenuTrigger,
  mergeClasses,
  Switch,
  Text,
  Tooltip,
  useRestoreFocusSource,
  useRestoreFocusTarget,
} from '@fluentui/react-components'
import type { SwitchOnChangeData } from '@fluentui/react-components'
import { AddRegular, ArrowDownloadRegular, PanelRightRegular } from '@fluentui/react-icons'
import MessageList from './MessageList'
import SystemPromptBanner from './SystemPromptBanner'
import ChatInputArea from './ChatInputArea'
import ConversationPanel from './ConversationPanel'
import ConverterPanel from './ConverterPanel'
import TargetBadge from './TargetBadge'
import type { PieceConversion } from './converterTypes'
import { PIECE_TYPE_TO_DATA_TYPE, basenameFromValue, buildMediaUrl, dataTypeToAttachmentKind, isPathDataType } from './converterTypes'
import LabelsBar from '../Labels/LabelsBar'
import type { ChatInputAreaHandle } from './ChatInputArea'
import { attacksApi } from '../../services/api'
import { toApiError } from '../../services/errors'
import {
  buildMessagePieces,
  backendMessageToOriginalDraft,
  backendMessagesToFrontend,
} from '../../utils/messageMapper'
import { exportConversation } from '../../utils/conversationExport'
import type { ExportFormat } from '../../utils/conversationExport'
import type {
  AttackTargetResolutionStatus,
  BackendMessage,
  ChatSendOutcome,
  CreateConversationRequest,
  Message,
  MessageAttachment,
  TargetInstance,
  TargetInfo,
} from '../../types'
import { isTargetResolutionBlocking, targetInfoMatchesTarget } from '../../utils/targetIdentity'
import type { ViewName } from '../Sidebar/Navigation'
import { useChatWindowStyles } from './ChatWindow.styles'

const NARROW_SCREEN_QUERY = '(max-width: 600px)'
const MARKDOWN_PREFERENCE_STORAGE_KEY = 'pyrit.chatMarkdownMode'
const RETRYABLE_TARGET_RESPONSE_ERROR = 'processing'
const CLEAN_CONVERSATION_MESSAGE =
  'Continue in a clean conversation so the stored error is not sent back to the target.'

interface RecoverableSendDraft {
  conversationId: string
  failedRequestTurnNumber: number
  originalValue: string
  attachments: MessageAttachment[]
  conversions: Record<string, PieceConversion>
  source: 'live' | 'persisted'
  missingConverterSelections: boolean
}

interface TargetResponseFailure {
  type: string
  errorTurnNumber: number
  failedRequestTurnNumber?: number
}

function getRecoveryDescription(draft: RecoverableSendDraft): string {
  if (draft.source === 'live') {
    return `${CLEAN_CONVERSATION_MESSAGE} Your prompt, attachments, and converter choices are preserved for editing.`
  }

  const restored = 'Your prompt and attachments were restored from conversation history.'

  if (draft.missingConverterSelections) {
    return `${CLEAN_CONVERSATION_MESSAGE} ${restored} Converter choices could not be restored, so review them before sending.`
  }

  return `${CLEAN_CONVERSATION_MESSAGE} ${restored} Review them before sending.`
}

function findPrecedingUserMessage(
  messages: BackendMessage[],
  beforeIndex: number,
): BackendMessage | undefined {
  for (let index = beforeIndex - 1; index >= 0; index -= 1) {
    if (messages[index].role === 'user') {
      return messages[index]
    }
  }
  return undefined
}

function getLatestTargetResponseFailure(messages: BackendMessage[]): TargetResponseFailure | undefined {
  const latestMessageIndex = messages.length - 1
  const latestMessage = messages[messages.length - 1]
  if (
    !latestMessage
    || (latestMessage.role !== 'assistant' && latestMessage.role !== 'simulated_assistant')
  ) {
    return undefined
  }

  const errorType = latestMessage.message_pieces.find(
    (piece) => piece.response_error && piece.response_error !== 'none',
  )?.response_error
  if (!errorType) {
    return undefined
  }

  return {
    type: errorType,
    errorTurnNumber: latestMessage.turn_number,
    failedRequestTurnNumber: findPrecedingUserMessage(messages, latestMessageIndex)?.turn_number,
  }
}

function getPersistedProcessingRecovery(
  conversationId: string,
  messages: BackendMessage[],
): RecoverableSendDraft | undefined {
  for (let errorIndex = messages.length - 1; errorIndex >= 0; errorIndex -= 1) {
    const errorMessage = messages[errorIndex]
    const isProcessingError = (
      errorMessage.role === 'assistant'
      || errorMessage.role === 'simulated_assistant'
    ) && errorMessage.message_pieces.some(
      (piece) => piece.response_error === RETRYABLE_TARGET_RESPONSE_ERROR,
    )
    if (!isProcessingError) {
      continue
    }

    const failedRequest = findPrecedingUserMessage(messages, errorIndex)
    if (!failedRequest) {
      return undefined
    }

    const originalDraft = backendMessageToOriginalDraft(failedRequest)
    return {
      conversationId,
      failedRequestTurnNumber: failedRequest.turn_number,
      originalValue: originalDraft.content,
      attachments: (originalDraft.attachments ?? []).map((attachment) => ({ ...attachment })),
      conversions: {},
      source: 'persisted',
      missingConverterSelections: failedRequest.message_pieces.some(
        (piece) => Boolean(piece.converter_identifiers?.length),
      ),
    }
  }
  return undefined
}

function findLastProcessingErrorIndex(messages: Message[]): number | undefined {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    if (messages[index].error?.type === RETRYABLE_TARGET_RESPONSE_ERROR) {
      return index
    }
  }
  return undefined
}

function readStoredMarkdownPreference(): boolean {
  if (typeof window === 'undefined') return false
  try {
    const storedPreference = window.localStorage.getItem(MARKDOWN_PREFERENCE_STORAGE_KEY)
    return storedPreference === 'markdown'
  } catch {
    return false
  }
}

function persistMarkdownPreference(enabled: boolean): void {
  if (typeof window === 'undefined') return
  try {
    window.localStorage.setItem(MARKDOWN_PREFERENCE_STORAGE_KEY, enabled ? 'markdown' : 'raw')
  } catch {
    /* localStorage may be unavailable (private mode, quota, sandboxed iframe). */
  }
}

function matchesNarrowScreen(): boolean {
  return typeof window !== 'undefined'
    && typeof window.matchMedia === 'function'
    && window.matchMedia(NARROW_SCREEN_QUERY).matches
}

interface ChatWindowProps {
  onNewAttack: () => void
  activeTarget: TargetInstance | null
  attackResultId: string | null
  conversationId: string | null
  activeConversationId: string | null
  onConversationCreated: (attackResultId: string, conversationId: string) => void
  onSelectConversation: (conversationId: string) => void
  labels?: Record<string, string>
  onLabelsChange?: (labels: Record<string, string>) => void
  onNavigate?: (view: ViewName) => void
  /** Labels from the loaded attack (for operator locking). Null for new attacks. */
  attackLabels?: Record<string, string> | null
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
}

export default function ChatWindow({
  onNewAttack,
  activeTarget,
  attackResultId,
  conversationId,
  activeConversationId,
  onConversationCreated,
  onSelectConversation,
  labels,
  onLabelsChange,
  onNavigate,
  attackLabels,
  attackTarget,
  targetResolutionStatus = 'idle',
  onRetryTargetResolution,
  isLoadingAttack,
  relatedConversationCount,
}: ChatWindowProps) {
  const styles = useChatWindowStyles()
  const restoreFocusTargetAttributes = useRestoreFocusTarget()
  const restoreFocusSourceAttributes = useRestoreFocusSource()
  const [messages, setMessages] = useState<Message[]>([])
  // Track sending state per conversation so parallel conversations can send independently
  const [sendingConversations, setSendingConversations] = useState<Set<string>>(new Set())
  /** True while an async message fetch is in-flight */
  const [isLoadingMessages, setIsLoadingMessages] = useState(false)
  /** Which conversation's messages are currently loaded (set after fetch completes) */
  const [loadedConversationId, setLoadedConversationId] = useState<string | null>(null)
  const isSending = activeConversationId ? sendingConversations.has(activeConversationId) : Boolean(sendingConversations.size)
  const [isPanelOpen, setIsPanelOpen] = useState(false)
  const [isNarrowScreen, setIsNarrowScreen] = useState(matchesNarrowScreen)
  const [isConverterPanelOpen, setIsConverterPanelOpen] = useState(false)
  // Conversation-wide preference for rendering message text as Markdown.
  const [globalMarkdown, setGlobalMarkdown] = useState(() => readStoredMarkdownPreference())
  const [chatInputText, setChatInputText] = useState('')
  const [systemPrompt, setSystemPrompt] = useState('')
  const [attachmentTypes, setAttachmentTypes] = useState<string[]>([])
  const [attachmentData, setAttachmentData] = useState<Record<string, string>>({})
  const [pieceConversions, setPieceConversions] = useState<Record<string, PieceConversion>>({})
  const [recoverableSends, setRecoverableSends] = useState<Record<string, RecoverableSendDraft>>({})
  const [isRecoveringProcessingError, setIsRecoveringProcessingError] = useState(false)
  const [panelRefreshKey, setPanelRefreshKey] = useState(0)
  const inputBoxRef = useRef<ChatInputAreaHandle>(null)
  const recoveryInFlightRef = useRef(false)
  const viewedConversationId = activeConversationId ?? conversationId
  const recoverableSend = viewedConversationId
    ? recoverableSends[viewedConversationId]
    : undefined

  const handleMarkdownChange = useCallback((
    _event: ChangeEvent<HTMLInputElement>,
    data: SwitchOnChangeData,
  ): void => {
    setGlobalMarkdown(data.checked)
    persistMarkdownPreference(data.checked)
  }, [])

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

  const handleAttachmentsChange = useCallback((types: string[], data: Record<string, string>) => {
    setAttachmentTypes(types)
    setAttachmentData(data)
  }, [])

  // Auto-prune stale conversions whose original input no longer matches.
  // For text: the typed text differs from the captured originalValue.
  // For media: the uploaded base64 changed (or was removed).
  // Deriving this rather than syncing via an effect avoids triggering
  // react-hooks/set-state-in-effect and is the pattern recommended by React
  // (see frontend-style-guide → "Prefer Derived Values Over Effects").
  const activePieceConversions = useMemo(() => {
    const entries = Object.entries(pieceConversions)
    if (entries.length === 0) return pieceConversions
    const next: Record<string, PieceConversion> = {}
    let hasStale = false
    for (const [key, conv] of entries) {
      const stillValid = key === 'text'
        ? conv.originalValue === chatInputText
        : attachmentData[key] === conv.originalValue
      if (stillValid) {
        next[key] = conv
      } else {
        hasStale = true
      }
    }
    return hasStale ? next : pieceConversions
  }, [pieceConversions, chatInputText, attachmentData])
  const conversionRevisionKey = useMemo(
    () => JSON.stringify(
      Object.entries(activePieceConversions)
        .sort(([left], [right]) => left.localeCompare(right)),
    ),
    [activePieceConversions],
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
  const attackOperator = attackLabels?.operator
  // Existing attacks are operator-locked when their operator differs from the current one.
  const isOperatorLocked = Boolean(
    attackResultId && attackLabels && attackOperator && currentOperator && attackOperator !== currentOperator,
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
    }
  }

  // Clear a retained system prompt when switching to a target that can't use it,
  // so it isn't silently dropped on send. Preserved across supporting targets to
  // keep the A/B-testing workflow intact.
  const [prevTargetName, setPrevTargetName] = useState(activeTarget?.target_registry_name)
  if (activeTarget?.target_registry_name !== prevTargetName) {
    setPrevTargetName(activeTarget?.target_registry_name)
    if (!supportsSystemPrompt) {
      setSystemPrompt('')
    }
  }

  // Load messages for a given conversation
  const loadConversation = useCallback(async (arId: string, convId: string) => {
    setIsLoadingMessages(true)
    try {
      const response = await attacksApi.getMessages(arId, convId)
      // Discard stale response if user navigated away while loading
      if (viewedConvRef.current !== convId) { return }
      const frontendMessages = backendMessagesToFrontend(response.messages)
      const persistedRecovery = getPersistedProcessingRecovery(convId, response.messages)
      setRecoverableSends((currentRecoveries) => {
        const currentRecovery = currentRecoveries[convId]
        if (persistedRecovery) {
          if (currentRecovery?.source === 'live') {
            return currentRecoveries
          }
          return { ...currentRecoveries, [convId]: persistedRecovery }
        }
        if (!currentRecovery || currentRecovery.source === 'live') {
          return currentRecoveries
        }
        const nextRecoveries = { ...currentRecoveries }
        delete nextRecoveries[convId]
        return nextRecoveries
      })
      // If this conversation has an in-flight send, append any pending user
      // messages (that the server may not have stored yet) and a loading indicator.
      if (sendingConvIdsRef.current.has(convId)) {
        const pending = pendingUserMessagesRef.current.get(convId) ?? []
        frontendMessages.push(...pending)
        frontendMessages.push({
          role: 'assistant',
          content: '...',
          timestamp: new Date().toISOString(),
          isLoading: true,
        })
      }
      setMessages(frontendMessages)
      setLoadedConversationId(convId)
    } catch {
      if (viewedConvRef.current !== convId) { return }
      setMessages([])
      setLoadedConversationId(convId)
    } finally {
      setIsLoadingMessages(false)
    }
  }, [])

  // Reload messages when activeConversationId changes
  useEffect(() => {
    if (!attackResultId || !activeConversationId) { return }
    // Allow user-initiated switches (forceLoadRef), but skip re-loading when
    // handleSend internally updated activeConversationId during an in-flight
    // send — the optimistic messages are already displayed.
    const force = forceLoadRef.current
    forceLoadRef.current = false
    if (!force && sendingConvIdsRef.current.has(activeConversationId)) { return }
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

  const handleSend = async (
    originalValue: string,
    convertedValue: string | undefined,
    attachments: MessageAttachment[],
  ): Promise<ChatSendOutcome> => {
    if (
      !activeTarget
      || isLoadingAttack
      || isMutationLocked
    ) {
      return { status: 'retryable_failure', clearDraft: false }
    }

    const initialSendConvId = activeConversationId ?? conversationId ?? '__pending__'
    if (sendingConvIdsRef.current.has(initialSendConvId)) {
      return { status: 'retryable_failure', clearDraft: false }
    }

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
      const pieces = await buildMessagePieces(originalValue, attachments)

      // Send converter selections to the backend and let it apply conversions per piece.
      // Avoid setting converted_value client-side because one preview value does not
      // necessarily correspond to every piece of the same data type, and any locally
      // preconverted piece may cause the backend to skip converter_ids entirely.
      const allConverterIds: string[] = []
      for (const [pieceType, conv] of Object.entries(conversions)) {
        const dataType = PIECE_TYPE_TO_DATA_TYPE[pieceType]
        if (!dataType) continue
        const hasMatchingPiece = pieces.some(piece => piece.data_type === dataType)
        if (hasMatchingPiece) {
          allConverterIds.push(conv.converterInstanceId)
        }
      }

      // Create attack lazily on first message
      let currentAttackResultId = attackResultId
      let currentConversationId = conversationId
      let currentActiveConversationId = activeConversationId
      if (!currentAttackResultId) {
        const createResponse = await attacksApi.createAttack({
          target_registry_name: activeTarget.target_registry_name,
          labels: labels,
          system_prompt: supportsSystemPrompt ? systemPrompt.trim() || undefined : undefined,
        })
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
        onConversationCreated(currentAttackResultId, currentConversationId)
        // Update the viewed-conversation ref so the success/error guards
        // below recognise this as the active conversation.
        viewedConvRef.current = currentConversationId!
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
      const converterIds = allConverterIds.length > 0 ? allConverterIds : undefined
      if (!currentAttackResultId || !effectiveConvId) {
        throw new Error('Message send is missing an attack or conversation ID.')
      }
      const response = await attacksApi.addMessage(currentAttackResultId, {
        role: 'user',
        pieces,
        send: true,
        target_registry_name: activeTarget.target_registry_name,
        target_conversation_id: effectiveConvId,
        labels: labels ?? undefined,
        converter_ids: converterIds,
      })

      const targetResponseFailure = getLatestTargetResponseFailure(response.messages.messages)
      const status: ChatSendOutcome['status'] = targetResponseFailure?.type === RETRYABLE_TARGET_RESPONSE_ERROR
        ? 'retryable_failure'
        : targetResponseFailure
          ? 'non_retryable_failure'
          : 'sent'
      const backendMessages = backendMessagesToFrontend(response.messages.messages)

      if (targetResponseFailure?.type === RETRYABLE_TARGET_RESPONSE_ERROR) {
        setRecoverableSends((currentRecoveries) => ({
          ...currentRecoveries,
          [effectiveConvId]: {
            conversationId: effectiveConvId,
            failedRequestTurnNumber: targetResponseFailure.failedRequestTurnNumber
              ?? targetResponseFailure.errorTurnNumber - 1,
            originalValue,
            attachments: attachments.map((attachment) => ({ ...attachment })),
            conversions,
            source: 'live',
            missingConverterSelections: false,
          },
        }))
      }

      // Only update displayed messages if the user is still viewing this conversation.
      // If they switched away the response is persisted server-side and will appear
      // when they navigate back.
      if (viewedConvRef.current === effectiveConvId) {
        // Replace the entire message list with authoritative server data.
        // This correctly handles the case where the user switched away and
        // back during the request — the full conversation is restored.
        setMessages(backendMessages)
        setLoadedConversationId(effectiveConvId)
      }
      return {
        status,
        clearDraft: status !== 'retryable_failure' || viewedConvRef.current !== effectiveConvId,
      }
    } catch (err) {
      const viewedConversationId = viewedConvRef.current
      const isViewingFailedConversation = viewedConversationId === sendConvId
        || viewedConversationId === (activeConversationId ?? conversationId)
        || (viewedConversationId == null && sendConvId !== '__pending__')

      // Only show error in UI if user is still on this conversation
      if (isViewingFailedConversation) {
        // Mark the viewed conversation as loaded so first-send failures do not
        // get stuck behind the "Loading conversation..." placeholder.
        if (viewedConversationId) {
          setLoadedConversationId(viewedConversationId)
        } else if (sendConvId !== '__pending__') {
          setLoadedConversationId(sendConvId)
        }

        const apiError = toApiError(err)
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
          if (prev.length > 0 && prev[prev.length - 1].isLoading) {
            return [...prev.slice(0, -1), errorMessage]
          }
          return [...prev, errorMessage]
        })

      }
      return {
        status: 'retryable_failure',
        clearDraft: viewedConversationId != null && viewedConversationId !== sendConvId,
      }
    } finally {
      sendingConvIdsRef.current.delete(sendConvId)
      pendingUserMessagesRef.current.delete(sendConvId)
      setSendingConversations(prev => {
        const next = new Set(prev)
        next.delete(sendConvId)
        return next
      })
      setPanelRefreshKey(k => k + 1)
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
    setPieceConversions(recoverableSend.conversions)
    inputBoxRef.current?.restoreDraft(
      recoverableSend.originalValue,
      recoverableSend.attachments,
    )
    inputBoxRef.current?.focus()
  }, [recoverableSend])

  const handleRecoverProcessingError = useCallback(async (): Promise<void> => {
    if (
      !attackResultId
      || !recoverableSend
      || isMutationLocked
      || recoveryInFlightRef.current
    ) {
      return
    }

    const supportsMultiTurn = Boolean(
      activeTarget && activeTarget.capabilities?.supports_multi_turn !== false,
    )
    const cutoffIndex = recoverableSend.failedRequestTurnNumber - 1
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
    isNarrowScreen,
    onSelectConversation,
    recoverableSend,
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
  const handleBranchAttack = useCallback(async (messageIndex: number) => {
    if (!activeTarget || !activeConversationId) { return }

    try {
      const createResponse = await attacksApi.createAttack({
        target_registry_name: activeTarget.target_registry_name,
        labels: labels,
        source_conversation_id: activeConversationId,
        cutoff_index: messageIndex,
      })
      onConversationCreated(createResponse.attack_result_id, createResponse.conversation_id)
      // Load the cloned messages into the UI
      const messagesResp = await attacksApi.getMessages(createResponse.attack_result_id, createResponse.conversation_id)
      const frontendMessages = backendMessagesToFrontend(messagesResp.messages)
      setMessages(frontendMessages)
      setLoadedConversationId(createResponse.conversation_id)
    } catch (err) {
      console.error('Failed to branch into new attack:', err)
    }
  }, [activeTarget, activeConversationId, labels, onConversationCreated])

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

  const singleTurnLimitReached = activeTarget?.capabilities?.supports_multi_turn === false && messages.some(m => m.role === 'user')
  const recoverableProcessingErrorIndex = recoverableSend?.conversationId === viewedConversationId
    ? findLastProcessingErrorIndex(messages)
    : undefined
  const processingRecoveryDescription = recoverableSend
    ? getRecoveryDescription(recoverableSend)
    : undefined

  // "Continue with your target" — clone the current conversation into a new attack
  const handleUseAsTemplate = useCallback(async () => {
    if (!attackResultId || !activeTarget || !activeConversationId) { return }

    // Find the last non-loading message index to use as cutoff
    const lastIndex = messages.reduce(
      (acc, m, i) => (m.isLoading ? acc : i),
      -1
    )
    if (lastIndex < 0) { return }

    try {
      // Let the backend clone the conversation with new labels
      const createResponse = await attacksApi.createAttack({
        target_registry_name: activeTarget.target_registry_name,
        labels: labels,
        source_conversation_id: activeConversationId,
        cutoff_index: lastIndex,
      })
      onConversationCreated(createResponse.attack_result_id, createResponse.conversation_id)
      // Load the cloned messages into the UI
      const messagesResp = await attacksApi.getMessages(createResponse.attack_result_id, createResponse.conversation_id)
      const frontendMessages = backendMessagesToFrontend(messagesResp.messages)
      setMessages(frontendMessages)
      setLoadedConversationId(createResponse.conversation_id)
    } catch (err) {
      console.error('Failed to use as template:', err)
    }
  }, [attackResultId, activeTarget, activeConversationId, messages, labels, onConversationCreated])

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

  const handleExport = (format: ExportFormat) => {
    exportConversation({ messages, conversationId: activeConversationId ?? conversationId, format })
  }

  return (
    <div className={styles.root}>
      <h1 className={styles.pageHeading}>Chat</h1>
      {isConverterPanelOpen && (
        <ConverterPanel
          onClose={() => setIsConverterPanelOpen(false)}
          previewText={chatInputText}
          attachmentData={attachmentData}
          activeInputTypes={chatInputText.trim() ? ['text', ...attachmentTypes] : attachmentTypes}
          onUseConvertedValue={(conversion) => {
            setPieceConversions((prev) => ({ ...prev, [conversion.pieceType]: conversion }))
          }}
        />
      )}
      <div className={styles.chatArea} data-testid="chat-area">
        <div className={styles.ribbon}>
          <div className={styles.conversationInfo}>
            {activeTarget ? (
              <TargetBadge target={activeTarget} />
            ) : (
              <Text size={200} className={styles.noTarget}>
                No target selected
              </Text>
            )}
            {labels && onLabelsChange && (
              <LabelsBar labels={labels} onLabelsChange={onLabelsChange} />
            )}
          </div>
          <div className={styles.ribbonActions}>
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
                    icon={<ArrowDownloadRegular />}
                    disabled={!canExportConversation}
                    aria-label="Export conversation"
                    data-testid="export-conversation-btn"
                  />
                </Tooltip>
              </MenuTrigger>
              <MenuPopover>
                <MenuList>
                  <MenuItem onClick={() => handleExport('markdown')} data-testid="export-markdown-item">
                    Export as Markdown (.md)
                  </MenuItem>
                  <MenuItem onClick={() => handleExport('json')} data-testid="export-json-item">
                    Export as JSON (.json)
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
                onClick={() => { setIsPanelOpen(false); onNewAttack() }}
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
        {systemMessage && <SystemPromptBanner content={systemMessage.content} />}
        <MessageList
          messages={messages}
          onCopyToInput={handleCopyToInput}
          onCopyToNewConversation={attackResultId ? handleCopyToNewConversation : undefined}
          onBranchConversation={attackResultId && activeConversationId ? handleBranchConversation : undefined}
          onBranchAttack={activeTarget && activeConversationId ? handleBranchAttack : undefined}
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
                disabled: isRecoveringProcessingError || isMutationLocked,
                onRecover: handleRecoverProcessingError,
              }}
        />
        <ChatInputArea
          ref={inputBoxRef}
          onSend={handleSend}
          conversionRevisionKey={conversionRevisionKey}
          showSystemPrompt={!attackResultId}
          supportsSystemPrompt={supportsSystemPrompt}
          systemPrompt={systemPrompt}
          onSystemPromptChange={setSystemPrompt}
          disabled={
            isSending
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
          noTargetSelected={!activeTarget}
          onConfigureTarget={() => onNavigate?.('config')}
          onToggleConverterPanel={() => setIsConverterPanelOpen(prev => !prev)}
          isConverterPanelOpen={isConverterPanelOpen}
          onInputChange={setChatInputText}
          onAttachmentsChange={handleAttachmentsChange}
          convertedValue={activePieceConversions['text']?.convertedDataType === 'text' ? (activePieceConversions['text']?.convertedValue ?? null) : null}
          originalValue={activePieceConversions['text']?.originalValue ?? null}
          onClearConversion={() => setPieceConversions((prev) => { const next = { ...prev }; delete next['text']; return next })}
          onClearAllConversions={() => setPieceConversions((current) => (
            current === pieceConversions ? {} : current
          ))}
          onConvertedValueChange={(val) => setPieceConversions((prev) => {
            const existing = prev['text']
            if (!existing) return prev
            return { ...prev, text: { ...existing, convertedValue: val } }
          })}
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
          onClearConvertedFileChip={() => setPieceConversions((prev) => { const next = { ...prev }; delete next['text']; return next })}
          converterOutputDataTypes={Object.values(activePieceConversions).map((c) => c.convertedDataType)}
          mediaConversions={Object.entries(activePieceConversions)
            .filter(([k]) => k !== 'text')
            .map(([k, v]) => ({ pieceType: k, convertedValue: v.convertedValue, convertedDataType: v.convertedDataType }))}
          onClearMediaConversion={(pieceType) => setPieceConversions((prev) => {
            const next = { ...prev }
            delete next[pieceType]
            return next
          })}
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
