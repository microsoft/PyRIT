import { useState, useRef, useEffect, useCallback, useMemo } from 'react'
import {
  Button,
  Drawer,
  mergeClasses,
  Text,
  Tooltip,
  useRestoreFocusSource,
  useRestoreFocusTarget,
} from '@fluentui/react-components'
import { AddRegular, PanelRightRegular } from '@fluentui/react-icons'
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
import { buildMessagePieces, backendMessagesToFrontend } from '../../utils/messageMapper'
import type { Message, MessageAttachment, TargetInstance, TargetInfo } from '../../types'
import { targetInfoMatchesTarget } from '../../utils/targetIdentity'
import type { ViewName } from '../Sidebar/Navigation'
import { useChatWindowStyles } from './ChatWindow.styles'

const NARROW_SCREEN_QUERY = '(max-width: 600px)'

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
  const [chatInputText, setChatInputText] = useState('')
  const [systemPrompt, setSystemPrompt] = useState('')
  const [attachmentTypes, setAttachmentTypes] = useState<string[]>([])
  const [attachmentData, setAttachmentData] = useState<Record<string, string>>({})
  const [pieceConversions, setPieceConversions] = useState<Record<string, PieceConversion>>({})
  const [panelRefreshKey, setPanelRefreshKey] = useState(0)
  const inputBoxRef = useRef<ChatInputAreaHandle>(null)

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
  useEffect(() => { viewedConvRef.current = activeConversationId ?? conversationId }, [activeConversationId, conversationId])
  // Synchronous ref tracking which conversations have an in-flight send.
  const sendingConvIdsRef = useRef<Set<string>>(new Set())
  // Pending user messages per conversation that may not be stored server-side yet.
  // Used to restore the user's input when switching back to an in-flight conversation.
  const pendingUserMessagesRef = useRef<Map<string, Message[]>>(new Map())

  const supportsSystemPrompt = activeTarget?.capabilities?.supports_system_prompt === true

  // Clear internal messages when attack state is reset (e.g. New Attack).
  // Uses the "adjust state during render" pattern (see React docs:
  // https://react.dev/reference/react/useState#storing-information-from-previous-renders)
  // instead of a useEffect so we don't trigger react-hooks/set-state-in-effect.
  const [prevAttackResultId, setPrevAttackResultId] = useState<string | null>(attackResultId)
  if (attackResultId !== prevAttackResultId) {
    setPrevAttackResultId(attackResultId)
    if (!attackResultId) {
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

  const handleSend = async (originalValue: string, convertedValue: string | undefined, attachments: MessageAttachment[]) => {
    if (!activeTarget) { return }

    // Capture all piece conversions upfront before any async work or state clears
    const conversions = { ...activePieceConversions }
    const textConversion = conversions['text']
    const isTextTextConversion = textConversion?.convertedDataType === 'text'
    const isTextFileConversion = Boolean(textConversion) && !isTextTextConversion

    // Track which conversation this send belongs to (may be updated after attack creation)
    let sendConvId = activeConversationId || '__pending__'
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
      const response = await attacksApi.addMessage(currentAttackResultId!, {
        role: 'user',
        pieces,
        send: true,
        target_registry_name: activeTarget.target_registry_name,
        target_conversation_id: effectiveConvId!,
        labels: labels ?? undefined,
        converter_ids: converterIds,
      })

      // Clear converter state after successful send
      setPieceConversions({})

      // Only update displayed messages if the user is still viewing this conversation.
      // If they switched away the response is persisted server-side and will appear
      // when they navigate back.
      if (viewedConvRef.current === effectiveConvId) {
        // Replace the entire message list with authoritative server data.
        // This correctly handles the case where the user switched away and
        // back during the request — the full conversation is restored.
        const backendMessages = backendMessagesToFrontend(response.messages.messages)
        setMessages(backendMessages)
        setLoadedConversationId(effectiveConvId!)
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

        // Preserve the failed message text in the input box for easy re-send
        if (originalValue && inputBoxRef.current) {
          inputBoxRef.current.setText(originalValue)
        }
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

  const handleNewConversation = useCallback(async () => {
    if (!attackResultId) { return }

    try {
      const response = await attacksApi.createConversation(attackResultId, {})
      onSelectConversation(response.conversation_id)
      setIsPanelOpen(!isNarrowScreen)
    } catch {
      // Silently fail
    }
  }, [attackResultId, isNarrowScreen, onSelectConversation])

  // -------------------------------------------------------------------
  // Message action handlers (4 buttons on each assistant message)
  // -------------------------------------------------------------------

  /** 1. Copy the clicked message's content/attachments into the current conversation's input box */
  const handleCopyToInput = useCallback((messageIndex: number) => {
    const msg = messages[messageIndex]
    if (!msg) { return }
    if (msg.content) { inputBoxRef.current?.setText(msg.content) }
    if (msg.attachments) {
      msg.attachments.filter(a => a.type !== 'file').forEach(att => {
        inputBoxRef.current?.addAttachment(att)
      })
    }
  }, [messages])

  /** 2. Create a new conversation in the same attack and copy ONLY this message to its input box */
  const handleCopyToNewConversation = useCallback(async (messageIndex: number) => {
    if (!attackResultId) { return }
    const msg = messages[messageIndex]
    if (!msg) { return }

    try {
      const response = await attacksApi.createConversation(attackResultId, {})
      onSelectConversation(response.conversation_id)
      setIsPanelOpen(!isNarrowScreen)
      // Small delay so the panel/messages update first
      setTimeout(() => {
        if (msg.content) inputBoxRef.current?.setText(msg.content)
        if (msg.attachments) {
          msg.attachments.filter(a => a.type !== 'file').forEach(att => {
            inputBoxRef.current?.addAttachment(att)
          })
        }
      }, 100)
    } catch {
      // If creating fails, fall back to current conversation
      if (msg.content) inputBoxRef.current?.setText(msg.content)
    }
  }, [attackResultId, isNarrowScreen, messages, onSelectConversation])

  /** 3. Branch into a new conversation within the same attack (clone up to clicked message) */
  const handleBranchConversation = useCallback(async (messageIndex: number) => {
    if (!attackResultId || !activeConversationId) { return }

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
  }, [attackResultId, activeConversationId, isNarrowScreen, onSelectConversation])

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
    if (!attackResultId) { return }

    try {
      await attacksApi.changeMainConversation(attackResultId, convId)
      setPanelRefreshKey(k => k + 1)
    } catch (err) {
      console.error('Failed to change main conversation:', err)
    }
  }, [attackResultId])

  const singleTurnLimitReached = activeTarget?.capabilities?.supports_multi_turn === false && messages.some(m => m.role === 'user')

  // Operator locking: if the loaded attack's operator differs from the current
  // user's operator label, the conversation should be read-only.
  const currentOperator = labels?.operator
  const attackOperator = attackLabels?.operator
  const isOperatorLocked = Boolean(
    attackResultId && attackLabels && attackOperator && currentOperator && attackOperator !== currentOperator
  )

  // Cross-target guard: if viewing a historical attack whose target differs
  // from the currently configured target, prevent sending new messages.
  // The user can "Continue with your target" to branch into a new attack with their target.
  const isCrossTargetLocked = Boolean(
    attackResultId &&
    attackTarget &&
    activeTarget &&
    !targetInfoMatchesTarget(attackTarget, activeTarget)
  )

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
        <div className={styles.ribbon} data-tour="chat-area">
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
            <Tooltip content="Toggle conversations panel" relationship="label">
              <Button
                {...restoreFocusTargetAttributes}
                appearance="subtle"
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
          isCrossTarget={isCrossTargetLocked}
          noTargetSelected={!activeTarget}
        />
        <ChatInputArea
          ref={inputBoxRef}
          onSend={handleSend}
          showSystemPrompt={!attackResultId}
          supportsSystemPrompt={supportsSystemPrompt}
          systemPrompt={systemPrompt}
          onSystemPromptChange={setSystemPrompt}
          disabled={isSending || !activeTarget || singleTurnLimitReached || isOperatorLocked || isCrossTargetLocked}
          activeTarget={activeTarget}
          singleTurnLimitReached={singleTurnLimitReached}
          onNewConversation={handleNewConversation}
          operatorLocked={isOperatorLocked}
          crossTargetLocked={isCrossTargetLocked}
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
            : undefined
          }
          refreshKey={panelRefreshKey}
        />
      </Drawer>
    </div>
  )
}
