import { useState, useRef, useEffect, useCallback } from 'react'
import {
  Button,
  Text,
  Badge,
  Tooltip,
} from '@fluentui/react-components'
import { AddRegular, PanelRightRegular } from '@fluentui/react-icons'
import MessageList from './MessageList'
import ChatInputArea from './ChatInputArea'
import ConversationPanel from './ConversationPanel'
import LabelsBar from '../Labels/LabelsBar'
import type { ChatInputAreaHandle } from './ChatInputArea'
import { attacksApi } from '../../services/api'
import { toApiError } from '../../services/errors'
import { buildMessagePieces, backendMessagesToFrontend } from '../../utils/messageMapper'
import type { Message, MessageAttachment, TargetInstance, TargetInfo } from '../../types'
import type { ViewName } from '../Sidebar/Navigation'
import { useChatWindowStyles } from './ChatWindow.styles'

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
  const [messages, setMessages] = useState<Message[]>([])
  // Track sending state per conversation so parallel conversations can send independently
  const [sendingConversations, setSendingConversations] = useState<Set<string>>(new Set())
  /** True while an async message fetch is in-flight */
  const [isLoadingMessages, setIsLoadingMessages] = useState(false)
  /** Which conversation's messages are currently loaded (set after fetch completes) */
  const [loadedConversationId, setLoadedConversationId] = useState<string | null>(null)
  const isSending = activeConversationId ? sendingConversations.has(activeConversationId) : Boolean(sendingConversations.size)
  const [isPanelOpen, setIsPanelOpen] = useState(false)
  const [panelRefreshKey, setPanelRefreshKey] = useState(0)
  const inputBoxRef = useRef<ChatInputAreaHandle>(null)

  // Auto-open conversation sidebar when loading a historical attack with multiple conversations
  useEffect(() => {
    if (relatedConversationCount && relatedConversationCount > 0) {
      setIsPanelOpen(true)
    }
  }, [attackResultId, relatedConversationCount])
  // Always-current ref of the conversation being viewed so async callbacks can
  // check whether the user navigated away while a request was in-flight.
  const viewedConvRef = useRef(activeConversationId ?? conversationId)
  useEffect(() => { viewedConvRef.current = activeConversationId ?? conversationId }, [activeConversationId, conversationId])
  // Synchronous ref tracking which conversations have an in-flight send.
  const sendingConvIdsRef = useRef<Set<string>>(new Set())
  // Pending user messages per conversation that may not be stored server-side yet.
  // Used to restore the user's input when switching back to an in-flight conversation.
  const pendingUserMessagesRef = useRef<Map<string, Message[]>>(new Map())

  // Clear internal messages when attack state is reset (e.g. New Attack)
  useEffect(() => {
    if (!attackResultId) {
      setMessages([])
      setLoadedConversationId(null)
    }
  }, [attackResultId])

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
    // Skip loading if a send is already in-flight for this conversation —
    // the send handler will update messages when it completes.
    if (sendingConvIdsRef.current.has(activeConversationId)) { return }
    loadConversation(attackResultId, activeConversationId)
  }, [activeConversationId, attackResultId, loadConversation])

  // Synchronous loading derivation: if activeConversationId differs from the
  // conversation whose messages we've loaded, we're in a transition gap.
  // This avoids the 1-frame flash between useEffect fire and render.
  const awaitingConversationLoad = Boolean(
    activeConversationId && activeConversationId !== loadedConversationId
    && !sendingConvIdsRef.current.has(activeConversationId)
  )

  // Handle conversation selection from the panel
  // For a different ID the useEffect handles loading; for same ID force a refresh
  const handlePanelSelectConversation = useCallback((convId: string) => {
    onSelectConversation(convId)
    if (convId === activeConversationId && attackResultId) {
      loadConversation(attackResultId, convId)
    }
  }, [attackResultId, activeConversationId, onSelectConversation, loadConversation])

  const handleSend = async (originalValue: string, _convertedValue: string | undefined, attachments: MessageAttachment[]) => {
    if (!activeTarget) { return }

    // Track which conversation this send belongs to (may be updated after attack creation)
    let sendConvId = activeConversationId || '__pending__'
    // Mark synchronously so the useEffect guard sees it immediately
    sendingConvIdsRef.current.add(sendConvId)

    // Add user message with attachments for display
    const userMessage: Message = {
      role: 'user',
      content: originalValue,
      timestamp: new Date().toISOString(),
      attachments: attachments.length > 0 ? attachments : undefined,
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
      // Build message pieces from text + attachments
      const pieces = await buildMessagePieces(originalValue, attachments)

      // Create attack lazily on first message
      let currentAttackResultId = attackResultId
      let currentConversationId = conversationId
      let currentActiveConversationId = activeConversationId
      if (!currentAttackResultId) {
        const createResponse = await attacksApi.createAttack({
          target_registry_name: activeTarget.target_registry_name,
          labels: labels,
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
      const response = await attacksApi.addMessage(currentAttackResultId!, {
        role: 'user',
        pieces,
        send: true,
        target_registry_name: activeTarget.target_registry_name,
        target_conversation_id: effectiveConvId!,
        labels: labels ?? undefined,
      })

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
      // Only show error in UI if user is still on this conversation
      if (viewedConvRef.current === sendConvId || viewedConvRef.current === (activeConversationId ?? conversationId)) {
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
      setIsPanelOpen(true)
    } catch {
      // Silently fail
    }
  }, [attackResultId, onSelectConversation])

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
      setIsPanelOpen(true)
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
  }, [attackResultId, messages, onSelectConversation])

  /** 3. Branch into a new conversation within the same attack (clone up to clicked message) */
  const handleBranchConversation = useCallback(async (messageIndex: number) => {
    if (!attackResultId || !activeConversationId) { return }

    try {
      const response = await attacksApi.createConversation(attackResultId, {
        source_conversation_id: activeConversationId,
        cutoff_index: messageIndex,
      })
      onSelectConversation(response.conversation_id)
      setIsPanelOpen(true)
      // Load the cloned messages
      const messagesResp = await attacksApi.getMessages(attackResultId, response.conversation_id)
      const frontendMessages = backendMessagesToFrontend(messagesResp.messages)
      setMessages(frontendMessages)
    } catch (err) {
      console.error('Failed to branch into new conversation:', err)
    }
  }, [attackResultId, activeConversationId, onSelectConversation])

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

  const singleTurnLimitReached = activeTarget?.supports_multi_turn === false && messages.some(m => m.role === 'user')

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
    attackResultId && attackTarget && activeTarget && (
      attackTarget.target_type !== activeTarget.target_type ||
      (attackTarget.endpoint ?? '') !== (activeTarget.endpoint ?? '') ||
      (attackTarget.model_name ?? '') !== (activeTarget.model_name ?? '')
    )
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

  return (
    <div className={styles.root}>
      <div className={styles.chatArea}>
        <div className={styles.ribbon}>
          <div className={styles.conversationInfo}>
            <Text>PyRIT Attack</Text>
            {activeTarget ? (
              <div className={styles.targetInfo}>
                <Text size={200}>→</Text>
                <Tooltip content={activeTarget.target_registry_name} relationship="label">
                  <Badge appearance="outline" size="medium">
                    {activeTarget.target_type}
                    {activeTarget.model_name ? ` (${activeTarget.model_name})` : ''}
                  </Badge>
                </Tooltip>
              </div>
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
                appearance="subtle"
                icon={<PanelRightRegular />}
                onClick={() => setIsPanelOpen(!isPanelOpen)}
                disabled={!attackResultId}
                data-testid="toggle-panel-btn"
              />
            </Tooltip>
            <Button
              appearance="primary"
              icon={<AddRegular />}
              onClick={() => { setIsPanelOpen(false); onNewAttack() }}
              disabled={!attackResultId}
              data-testid="new-attack-btn"
            >
              New Attack
            </Button>
          </div>
        </div>
        <MessageList
          messages={messages}
          onCopyToInput={handleCopyToInput}
          onCopyToNewConversation={attackResultId ? handleCopyToNewConversation : undefined}
          onBranchConversation={attackResultId && activeConversationId ? handleBranchConversation : undefined}
          onBranchAttack={activeTarget && activeConversationId ? handleBranchAttack : undefined}
          isLoading={isLoadingAttack || isLoadingMessages || awaitingConversationLoad}
          isSingleTurn={activeTarget?.supports_multi_turn === false}
          isOperatorLocked={isOperatorLocked}
          isCrossTarget={isCrossTargetLocked}
          noTargetSelected={!activeTarget}
        />
        <ChatInputArea
          ref={inputBoxRef}
          onSend={handleSend}
          disabled={isSending || !activeTarget || singleTurnLimitReached || isOperatorLocked || isCrossTargetLocked}
          activeTarget={activeTarget}
          singleTurnLimitReached={singleTurnLimitReached}
          onNewConversation={attackResultId ? handleNewConversation : undefined}
          operatorLocked={isOperatorLocked}
          crossTargetLocked={isCrossTargetLocked}
          onUseAsTemplate={(isOperatorLocked || isCrossTargetLocked) ? handleUseAsTemplate : undefined}
          attackOperator={isOperatorLocked ? attackOperator ?? undefined : undefined}
          noTargetSelected={!activeTarget}
          onConfigureTarget={!activeTarget ? () => onNavigate?.('config') : undefined}
        />
      </div>
      {isPanelOpen && (
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
      )}
    </div>
  )
}
