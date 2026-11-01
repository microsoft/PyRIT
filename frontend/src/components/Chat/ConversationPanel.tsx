import { useEffect, useLayoutEffect, useState, useCallback, useRef } from 'react'
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
import { attacksApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { AttackConversationsResponse, ConversationSummary } from '@/types'
import { useConversationPanelStyles } from './ConversationPanel.styles'

interface SavedReferencePage {
  attackResultId: string
  pageSize: number
  refreshKey?: number
  response: AttackConversationsResponse
  consumedCursors: ReadonlySet<string>
}

function mergeReferencePage(
  previous: AttackConversationsResponse | null, incoming: AttackConversationsResponse, attackResultId: string,
  cursor?: string, consumedCursors?: ReadonlySet<string>,
): AttackConversationsResponse {
  const pagination = incoming.pagination
  if (incoming.attack_result_id !== attackResultId || !pagination || typeof pagination.has_more !== 'boolean'
    || !Number.isInteger(pagination.limit) || pagination.limit < 1 || pagination.limit > 200
    || !Number.isInteger(incoming.total_count) || (incoming.total_count ?? -1) < 0
    || (pagination.has_more && (!pagination.next_cursor || pagination.next_cursor === cursor
      || consumedCursors?.has(pagination.next_cursor)))
    || (previous && (previous.main_conversation_id !== incoming.main_conversation_id || previous.total_count !== incoming.total_count))) {
    throw new Error('The server returned changed or invalid conversation references. Restart reference loading.')
  }
  const conversations = new Map<string, ConversationSummary>()
  for (const conversation of [...(previous?.conversations ?? []), ...incoming.conversations]) {
    conversations.set(conversation.conversation_id, conversation)
  }
  return { ...incoming, conversations: [...conversations.values()] }
}

interface ConversationPanelProps {
  attackResultId: string | null
  activeConversationId: string | null
  onSelectConversation: (conversationId: string) => void
  onNewConversation: () => void
  onChangeMainConversation: (conversationId: string) => void
  onClose: () => void
  /** When set, disables mutating actions (new conversation, promote to main) and explains why. */
  lockedReason?: string
  /** Increment to trigger a conversation list refresh (e.g. after sending a message) */
  refreshKey?: number
  /** Saved evidence pages references independently from the selected transcript. */
  referencePageSize?: number
}

export default function ConversationPanel({
  attackResultId,
  activeConversationId,
  onSelectConversation,
  onNewConversation,
  onChangeMainConversation,
  onClose,
  lockedReason,
  refreshKey,
  referencePageSize,
}: ConversationPanelProps) {
  const styles = useConversationPanelStyles()
  const [unpagedConversations, setUnpagedConversations] = useState<ConversationSummary[]>([])
  const [unpagedMainConversationId, setUnpagedMainConversationId] = useState<string | null>(null)
  const [savedReferencePage, setSavedReferencePage] = useState<SavedReferencePage | null>(null)
  const savedReferencePageRef = useRef<SavedReferencePage | null>(null)
  const activeReadRef = useRef<number | null>(null)
  const nextReadIdRef = useRef(0)
  const [isLoadingMore, setIsLoadingMore] = useState(false)
  const [continuationIssue, setContinuationIssue] = useState<{ detail: string; restartRequired: boolean } | null>(null)
  // Initialize to `true` only when we will actually fetch on mount so the spinner
  // shows during the initial fetch without needing a synchronous setState inside
  // the effect body (which `react-hooks/set-state-in-effect` would flag).
  const [isLoading, setIsLoading] = useState(() => attackResultId != null)
  const [error, setError] = useState<string | null>(null)

  // Counter used by the retry button to re-trigger the fetch effect without
  // calling setState synchronously in an effect body.
  const [retryCount, setRetryCount] = useState(0)
  const selectionRefreshId = referencePageSize === undefined ? activeConversationId : null

  const saveReferencePage = useCallback((page: SavedReferencePage): void => {
    savedReferencePageRef.current = page
    setSavedReferencePage(page)
  }, [])

  useLayoutEffect(() => {
    const page = savedReferencePageRef.current
    if (page?.attackResultId !== attackResultId || page?.pageSize !== referencePageSize || page?.refreshKey !== refreshKey) {
      savedReferencePageRef.current = null
    }
    return () => { activeReadRef.current = null }
  }, [attackResultId, referencePageSize, refreshKey, retryCount, selectionRefreshId])

  useEffect(() => {
    if (!attackResultId) {
      return
    }

    let cancelled = false
    const readId = ++nextReadIdRef.current
    activeReadRef.current = readId
    const request = referencePageSize === undefined
      ? attacksApi.getConversations(attackResultId)
      : attacksApi.getConversations(attackResultId, { limit: referencePageSize })
    request
      .then((response) => {
        if (cancelled || activeReadRef.current !== readId) return
        if (referencePageSize === undefined) {
          setUnpagedConversations(response.conversations)
          setUnpagedMainConversationId(response.main_conversation_id)
        } else {
          saveReferencePage({
            attackResultId, pageSize: referencePageSize, refreshKey,
            response: mergeReferencePage(null, response, attackResultId), consumedCursors: new Set(),
          })
        }
        setContinuationIssue(null)
        setError(null)
      })
      .catch((err: unknown) => {
        if (cancelled || activeReadRef.current !== readId) return
        if (referencePageSize === undefined) {
          setUnpagedConversations([])
          setUnpagedMainConversationId(null)
        }
        setError(toApiError(err).detail)
      })
      .finally(() => {
        if (cancelled || activeReadRef.current !== readId) return
        activeReadRef.current = null
        setIsLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [attackResultId, referencePageSize, selectionRefreshId, refreshKey, retryCount, saveReferencePage])

  // Reset local state when the attack is unloaded so stale data from a prior
  // attack doesn't briefly appear when the user opens a new one. Uses the
  // "adjust state during render" pattern instead of a useEffect to satisfy
  // react-hooks/set-state-in-effect.
  const [previousSource, setPreviousSource] = useState({ attackResultId, referencePageSize, refreshKey })
  if (attackResultId !== previousSource.attackResultId || referencePageSize !== previousSource.referencePageSize
    || refreshKey !== previousSource.refreshKey) {
    setPreviousSource({ attackResultId, referencePageSize, refreshKey })
    setUnpagedConversations([])
    setUnpagedMainConversationId(null)
    setSavedReferencePage(null)
    setError(null)
    setContinuationIssue(null)
    setIsLoadingMore(false)
    setIsLoading(Boolean(attackResultId))
  }

  const handleRetry = useCallback(() => {
    if (activeReadRef.current !== null) return
    setIsLoading(true)
    setError(null)
    setRetryCount((c) => c + 1)
  }, [])

  const loadMore = async (): Promise<void> => {
    const page = savedReferencePageRef.current
    const cursor = page?.response.pagination?.next_cursor
    if (activeReadRef.current !== null || continuationIssue?.restartRequired || !page || !cursor || !page.response.pagination?.has_more
      || page.attackResultId !== attackResultId || page.pageSize !== referencePageSize || page.refreshKey !== refreshKey) return
    const readId = ++nextReadIdRef.current
    activeReadRef.current = readId
    setIsLoadingMore(true)
    setContinuationIssue(null)
    let restartRequired = false
    const isCurrentRead = (): boolean => activeReadRef.current === readId && savedReferencePageRef.current === page
    try {
      const incoming = await attacksApi.getConversations(page.attackResultId, { limit: page.pageSize, cursor })
      if (!isCurrentRead()) return
      restartRequired = true
      const response = mergeReferencePage(page.response, incoming, page.attackResultId, cursor, page.consumedCursors)
      saveReferencePage({ ...page, response, consumedCursors: new Set([...page.consumedCursors, cursor]) })
      setError(null)
    } catch (cause: unknown) {
      if (!isCurrentRead()) return
      const error = toApiError(cause)
      setContinuationIssue({
        detail: `Could not load more conversations: ${error.detail}`, restartRequired: restartRequired || error.status === 400,
      })
    } finally {
      if (activeReadRef.current === readId) {
        activeReadRef.current = null
        setIsLoadingMore(false)
      }
    }
  }

  const currentPage = savedReferencePage?.attackResultId === attackResultId
    && savedReferencePage.pageSize === referencePageSize && savedReferencePage.refreshKey === refreshKey
    ? savedReferencePage : null
  const conversations = referencePageSize === undefined ? unpagedConversations : currentPage?.response.conversations ?? []
  const mainConversationId = referencePageSize === undefined ? unpagedMainConversationId : currentPage?.response.main_conversation_id ?? null
  const showLoadedReferences = currentPage !== null
  const showRows = (!isLoading || showLoadedReferences) && (!error || showLoadedReferences)
  const selectionOutsidePage = currentPage && activeConversationId
    && !conversations.some((conversation: ConversationSummary) => conversation.conversation_id === activeConversationId)

  return (
    <div id="conversation-panel" className={styles.root} data-testid="conversation-panel">
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
          <Tooltip content={lockedReason ?? 'New Conversation'} relationship="label">
            <Button
              appearance="subtle"
              size="small"
              className={styles.headerButton}
              icon={<AddRegular />}
              onClick={onNewConversation}
              disabled={!attackResultId || !!lockedReason}
              data-testid="new-conversation-btn"
            />
          </Tooltip>
          <Tooltip content="Close panel" relationship="label">
            <Button
              appearance="subtle"
              size="small"
              className={styles.headerButton}
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
            <Spinner size="tiny" label={referencePageSize === undefined ? undefined : 'Loading saved conversation references...'} />
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
              className={styles.retryButton}
              icon={<ArrowSyncRegular />}
              onClick={handleRetry}
              disabled={isLoadingMore}
              data-testid="conversation-retry-btn"
            >
              Retry
            </Button>
          </div>
        )}

        {!isLoading && !error && conversations.length === 0 && !currentPage?.response.pagination?.has_more && (
          <div className={styles.emptyState}>
            <ChatRegular fontSize={24} />
            <Text size={200}>
              {attackResultId
                ? 'No related conversations'
                : 'Start an attack to see conversations'}
            </Text>
          </div>
        )}

        {selectionOutsidePage && (
          <Text size={200} role="status" className={styles.offPageSelection}>
            Selected conversation {activeConversationId} is outside the loaded references. Its transcript remains selected.
          </Text>
        )}

        {showRows && conversations.map((conv: ConversationSummary) => {
          const isActive = conv.conversation_id === activeConversationId
          const selectConversation = () => onSelectConversation(conv.conversation_id)
          return (
            <div
              key={conv.conversation_id}
              className={`${styles.conversationItem} ${isActive ? styles.conversationItemActive : ''}`}
              onClick={selectConversation}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault()
                  selectConversation()
                }
              }}
              role="button"
              tabIndex={0}
              aria-label={`Select conversation ${conv.conversation_id}`}
              aria-current={isActive ? 'true' : undefined}
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
                      : lockedReason ?? 'Promote to main conversation.'}
                    relationship="description"
                  >
                    <Button
                      appearance="subtle"
                      size="small"
                      icon={conv.conversation_id === mainConversationId ? <StarFilled /> : <StarRegular />}
                      disabled={conv.conversation_id === mainConversationId || !!lockedReason}
                      onClick={(e) => {
                        e.stopPropagation()
                        if (conv.conversation_id !== mainConversationId) {
                          onChangeMainConversation(conv.conversation_id)
                        }
                      }}
                      data-testid={`star-btn-${conv.conversation_id}`}
                      className={styles.starButton}
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
      {currentPage && (
        <section className={styles.pagingControls} aria-label="Saved conversation references">
          {continuationIssue && (
            <MessageBar intent="error"><MessageBarBody>
              {continuationIssue.detail} Loaded references are still available.
            </MessageBarBody></MessageBar>
          )}
          <Text size={200} role="status" aria-live="polite">
            {conversations.length} of {currentPage.response.total_count} saved conversations loaded.{' '}
            {currentPage.response.pagination?.has_more ? 'More references are available.' : 'End of saved conversation references.'}
          </Text>
          {currentPage.response.pagination?.has_more && (
            <Button className={styles.retryButton} disabled={isLoading || isLoadingMore}
              icon={isLoadingMore ? <Spinner size="tiny" /> : undefined}
              onClick={() => { if (continuationIssue?.restartRequired) handleRetry(); else void loadMore() }}>
              {isLoadingMore ? 'Loading more conversations...'
                : continuationIssue?.restartRequired ? 'Restart conversation references'
                : continuationIssue ? 'Retry more conversations' : 'Load more conversations'}
            </Button>
          )}
        </section>
      )}
    </div>
  )
}

export { type ConversationPanelProps }
