import { useEffect, useState, useCallback } from 'react'
import {
  tokens,
  Button,
  Text,
  Tooltip,
  Badge,
  Spinner,
  MessageBar,
  MessageBarBody,
} from '@fluentui/react-components'
import {
  AddRegular,
  ArrowSyncRegular,
  ChatRegular,
  ChatMultipleRegular,
  DismissRegular,
  StarRegular,
  StarFilled,
} from '@fluentui/react-icons'
import { attacksApi } from '../../services/api'
import { toApiError } from '../../services/errors'
import type { ConversationSummary } from '../../types'
import { useConversationPanelStyles } from './ConversationPanel.styles'

interface ConversationPanelProps {
  attackResultId: string | null
  activeConversationId: string | null
  onSelectConversation: (conversationId: string) => void
  onNewConversation: () => void
  onChangeMainConversation: (conversationId: string) => void
  onClose: () => void
  /** When true, disable mutating actions (new conversation, promote to main) */
  locked?: boolean
  /** Increment to trigger a conversation list refresh (e.g. after sending a message) */
  refreshKey?: number
}

export default function ConversationPanel({
  attackResultId,
  activeConversationId,
  onSelectConversation,
  onNewConversation,
  onChangeMainConversation,
  onClose,
  locked,
  refreshKey,
}: ConversationPanelProps) {
  const styles = useConversationPanelStyles()
  const [conversations, setConversations] = useState<ConversationSummary[]>([])
  const [mainConversationId, setMainConversationId] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const fetchConversations = useCallback(async () => {
    if (!attackResultId) {
      setConversations([])
      setMainConversationId(null)
      return
    }

    setIsLoading(true)
    setError(null)
    try {
      const response = await attacksApi.getConversations(attackResultId)
      setConversations(response.conversations)
      setMainConversationId(response.main_conversation_id)
    } catch (err) {
      setConversations([])
      setMainConversationId(null)
      setError(toApiError(err).detail)
    } finally {
      setIsLoading(false)
    }
  }, [attackResultId])

  useEffect(() => {
    fetchConversations()
  }, [fetchConversations, activeConversationId, refreshKey])

  // Expose refresh via a data attribute on the root element so parent can call it
  // Actually, we'll handle refresh via the attackConversationId dependency

  return (
    <div className={styles.root} data-testid="conversation-panel">
      <div className={styles.header}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: tokens.spacingVerticalXXS, overflow: 'hidden' }}>
          <div className={styles.headerTitle}>
            <ChatMultipleRegular />
            <Text weight="semibold" size={300}>Attack Conversations</Text>
          </div>
          {attackResultId && (
            <Text
              size={100}
              style={{
                color: tokens.colorNeutralForeground3,
                fontFamily: 'monospace',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
                paddingLeft: '20px',
              }}
              title={attackResultId}
            >
              {attackResultId}
            </Text>
          )}
        </div>
        <div style={{ display: 'flex', gap: tokens.spacingHorizontalXXS }}>
          <Tooltip content={locked ? 'Cannot modify — attack is locked' : 'New Conversation'} relationship="label">
            <Button
              appearance="subtle"
              size="small"
              icon={<AddRegular />}
              onClick={onNewConversation}
              disabled={!attackResultId || locked}
              data-testid="new-conversation-btn"
            />
          </Tooltip>
          <Tooltip content="Close panel" relationship="label">
            <Button
              appearance="subtle"
              size="small"
              icon={<DismissRegular />}
              onClick={onClose}
              data-testid="close-panel-btn"
            />
          </Tooltip>
        </div>
      </div>

      <div className={styles.conversationList}>
        {isLoading && (
          <div className={styles.loading}>
            <Spinner size="tiny" />
          </div>
        )}

        {!isLoading && error && (
          <div className={styles.emptyState} data-testid="conversation-error">
            <MessageBar intent="error">
              <MessageBarBody>{error}</MessageBarBody>
            </MessageBar>
            <Button
              appearance="primary"
              size="small"
              icon={<ArrowSyncRegular />}
              onClick={fetchConversations}
              data-testid="conversation-retry-btn"
            >
              Retry
            </Button>
          </div>
        )}

        {!isLoading && !error && conversations.length === 0 && (
          <div className={styles.emptyState}>
            <ChatRegular fontSize={24} />
            <Text size={200}>
              {attackResultId
                ? 'No related conversations'
                : 'Start an attack to see conversations'}
            </Text>
          </div>
        )}

        {!isLoading && !error && conversations.map((conv) => {
          const isActive = conv.conversation_id === activeConversationId
          return (
            <div
              key={conv.conversation_id}
              className={`${styles.conversationItem} ${isActive ? styles.conversationItemActive : ''}`}
              onClick={() => onSelectConversation(conv.conversation_id)}
              data-testid={`conversation-item-${conv.conversation_id}`}
            >
              <div className={styles.conversationHeader}>
                <div className={styles.conversationTitle}>
                  <ChatRegular fontSize={16} />
                  <Text size={200} weight={isActive ? 'semibold' : 'regular'} truncate>
                    {conv.conversation_id}
                  </Text>
                </div>
                <div style={{ display: 'flex', gap: tokens.spacingHorizontalXXS, alignItems: 'center' }}>
                  <Tooltip
                    content={conv.conversation_id === mainConversationId
                      ? 'This is the main conversation.'
                      : 'Promote to main conversation.'}
                    relationship="description"
                  >
                    <Button
                      appearance="subtle"
                      size="small"
                      icon={conv.conversation_id === mainConversationId ? <StarFilled /> : <StarRegular />}
                      disabled={conv.conversation_id === mainConversationId || locked}
                      onClick={(e) => {
                        e.stopPropagation()
                        if (conv.conversation_id !== mainConversationId) {
                          onChangeMainConversation(conv.conversation_id)
                        }
                      }}
                      data-testid={`star-btn-${conv.conversation_id}`}
                      style={{ minWidth: 'auto', padding: '2px' }}
                    />
                  </Tooltip>
                  <Badge appearance="tint" size="small">
                    {conv.message_count}
                  </Badge>
                </div>
              </div>
              {conv.last_message_preview && (
                <Text size={200} className={styles.preview}>
                  {conv.last_message_preview}
                </Text>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

export { type ConversationPanelProps }
