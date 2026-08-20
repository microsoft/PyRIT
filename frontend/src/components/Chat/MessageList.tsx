import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import {
  Text,
  Avatar,
  tokens,
  MessageBar,
  MessageBarBody,
  Button,
  Badge,
  Menu,
  MenuItem,
  MenuList,
  MenuPopover,
  MenuTrigger,
  Popover,
  PopoverSurface,
  PopoverTrigger,
  Tab,
  TabList,
  Tooltip,
  Spinner,
  mergeClasses,
} from '@fluentui/react-components'
import {
  ArrowDownloadRegular,
  ArrowForwardRegular,
  ArrowReplyRegular,
  BranchForkRegular,
  ChatAddRegular,
  MoreHorizontalRegular,
  OpenRegular,
} from '@fluentui/react-icons'
import type { DisplayScore, Message, MessageAttachment } from '../../types'
import MarkdownContent from './MarkdownContent'
import { useMessageListStyles } from './MessageList.styles'

interface MessageListProps {
  messages: Message[]
  /** Copy this message to the input box of the current conversation */
  onCopyToInput?: (messageIndex: number) => void
  /** Copy this message to the input box of a brand-new conversation (same attack) */
  onCopyToNewConversation?: (messageIndex: number) => void
  /** Branch conversation up to this point into a new conversation (same attack) */
  onBranchConversation?: (messageIndex: number) => void
  /** Branch conversation up to this point into a new attack */
  onBranchAttack?: (messageIndex: number) => void
  /** True while loading a historical attack's messages */
  isLoading?: boolean
  /** True when the target is single-turn (disables copy-to-input) */
  isSingleTurn?: boolean
  /** True when the current operator doesn't own this attack (disables same-attack actions) */
  isOperatorLocked?: boolean
  /** True when the historical conversation uses a different target (disables current-conv actions) */
  isCrossTarget?: boolean
  /** True when no target is currently selected */
  noTargetSelected?: boolean
  /** Conversation-wide default: render message text as Markdown. */
  globalMarkdown?: boolean
}

/** Image that shows a spinner while loading. */
function ImageWithSpinner({ src, alt, className, hiddenClassName, containerClassName, spinnerClassName }: {
  src: string
  alt: string
  className: string
  hiddenClassName: string
  containerClassName: string
  spinnerClassName: string
}) {
  const [loaded, setLoaded] = useState(false)
  const [error, setError] = useState(false)
  const onLoad = useCallback(() => setLoaded(true), [])
  const onError = useCallback(() => { setError(true); setLoaded(true) }, [])

  return (
    <div className={containerClassName}>
      {!loaded && <Spinner size="small" className={spinnerClassName} />}
      {error
        ? <Text size={200} italic>Image failed to load</Text>
        : <img
            src={src}
            alt={alt}
            className={loaded ? className : hiddenClassName}
            onLoad={onLoad}
            onError={onError}
          />
      }
    </div>
  )
}

function MediaWithFallback({ type, src, className }: { type: 'video' | 'audio'; src: string; className?: string }) {
  const [error, setError] = useState(false)
  const handleError = useCallback(() => setError(true), [])

  if (error) {
    return <Text size={200} italic data-testid={`${type}-error`}>{type === 'video' ? 'Video' : 'Audio'} failed to load</Text>
  }

  if (type === 'video') {
    return <video src={src} controls className={className} onError={handleError} data-testid="video-player" />
  }
  return <audio src={src} controls className={className} onError={handleError} data-testid="audio-player" />
}

function ScoreDetails({ score, testId }: { score: DisplayScore; testId: string }) {
  const styles = useMessageListStyles()
  const categories = score.score_category?.filter(Boolean) ?? []

  return (
    <div className={styles.scoreSurface} data-testid={testId}>
      <Text weight="semibold">Score details</Text>
      <div className={styles.scoreRow}>
        <Text size={200} weight="semibold" className={styles.scoreLabel}>Value</Text>
        <Badge appearance="tint" color="brand" size="small">{score.score_value}</Badge>
      </div>
      <div className={styles.scoreRow}>
        <Text size={200} weight="semibold" className={styles.scoreLabel}>Type</Text>
        <Text size={200}>{score.score_type}</Text>
      </div>
      <div className={styles.scoreRow}>
        <Text size={200} weight="semibold" className={styles.scoreLabel}>Scorer</Text>
        <Text size={200}>{score.scorer_type}</Text>
      </div>
      <div className={styles.scoreRow}>
        <Text size={200} weight="semibold" className={styles.scoreLabel}>Objective</Text>
        <Text size={200}>{score.is_objective_score ? 'Yes' : 'No'}</Text>
      </div>
      {score.sourceLabel && (
        <div className={styles.scoreRow}>
          <Text size={200} weight="semibold" className={styles.scoreLabel}>Piece</Text>
          <Text size={200}>{score.sourceLabel}</Text>
        </div>
      )}
      {categories.length > 0 && (
        <div className={styles.scoreRow}>
          <Text size={200} weight="semibold" className={styles.scoreLabel}>Category</Text>
          <Text size={200}>{categories.join(', ')}</Text>
        </div>
      )}
      {score.score_rationale && (
        <div className={styles.scoreRationale}>
          <Text size={200} weight="semibold">Rationale</Text>
          <Text size={200} className={styles.scoreRationaleText}>{score.score_rationale}</Text>
        </div>
      )}
    </div>
  )
}

function MessageScore({ score, groupId, scoreIndex }: { score: DisplayScore; groupId: string | number; scoreIndex: number }) {
  const styles = useMessageListStyles()

  return (
    <Popover withArrow>
      <PopoverTrigger disableButtonEnhancement>
        <Button
          appearance="subtle"
          size="small"
          className={styles.scoreChip}
          aria-label={`Score ${score.score_value} from ${score.scorer_type}${score.is_objective_score ? ', objective score' : ''}${score.sourceLabel ? `, ${score.sourceLabel}` : ''}`}
          data-testid={`message-score-${groupId}-${scoreIndex}`}
        >
          <Badge appearance="tint" color="brand" size="medium">
            {score.score_value}
          </Badge>
        </Button>
      </PopoverTrigger>
      <PopoverSurface>
        <ScoreDetails score={score} testId={`message-score-details-${groupId}-${scoreIndex}`} />
      </PopoverSurface>
    </Popover>
  )
}

interface ScoreOverflowMenuItemProps {
  score: DisplayScore
  onSelect: (scoreId: string) => void
}

function ScoreOverflowMenuItem({ score, onSelect }: ScoreOverflowMenuItemProps) {
  const styles = useMessageListStyles()

  return (
    <MenuItem
      className={styles.scoreMenuItem}
      onClick={() => onSelect(score.id)}
    >
      {score.score_value} · {score.scorer_type}{score.is_objective_score ? ' (Objective)' : ''}
    </MenuItem>
  )
}

interface ScoreOverflowMenuProps {
  scores: DisplayScore[]
  onSelect: (scoreId: string) => void
}

const SCORE_TAB_WIDTH_PX = 72
const SCORE_TAB_GAP_PX = 4
const SCORE_OVERFLOW_BUTTON_WIDTH_PX = 112

function ScoreOverflowMenu({ scores, onSelect }: ScoreOverflowMenuProps) {
  const styles = useMessageListStyles()

  return (
    <Menu>
      <MenuTrigger disableButtonEnhancement>
        <Button
          appearance="subtle"
          size="small"
          icon={<MoreHorizontalRegular />}
          className={styles.scoreOverflowButton}
          aria-label={`More scores, ${scores.length} hidden`}
          onPointerDown={(event) => event.stopPropagation()}
          onClick={(event) => event.stopPropagation()}
        >
          More scores
        </Button>
      </MenuTrigger>
      <MenuPopover>
        <MenuList>
          {scores.map((score) => (
            <ScoreOverflowMenuItem
              key={score.id}
              score={score}
              onSelect={onSelect}
            />
          ))}
        </MenuList>
      </MenuPopover>
    </Menu>
  )
}

function getVisibleScores(
  orderedScores: DisplayScore[],
  selectedScoreId: string,
  visibleCount: number
): DisplayScore[] {
  const initialVisibleScores = orderedScores.slice(0, visibleCount)
  if (
    initialVisibleScores.some((score) => score.id === selectedScoreId)
    || initialVisibleScores.length === orderedScores.length
  ) {
    return initialVisibleScores
  }

  const selectedScore = orderedScores.find((score) => score.id === selectedScoreId)
  return selectedScore
    ? [...initialVisibleScores.slice(0, -1), selectedScore]
    : initialVisibleScores
}

function getDisplayedScore(scores: DisplayScore[]): DisplayScore {
  const objectiveScores = scores.filter((score) => score.is_objective_score)
  const displayCandidates = objectiveScores.length > 0 ? objectiveScores : scores

  return displayCandidates.reduce((latestScore, score) => (
    new Date(score.timestamp).getTime() > new Date(latestScore.timestamp).getTime()
      ? score
      : latestScore
  ))
}

/**
 * Renders a single score chip or, for multiple scores, a stacked trigger whose
 * popover uses tabs to switch the visible score details.
 */
function MessageScores({ scores, groupId }: { scores: DisplayScore[]; groupId: string | number }) {
  const styles = useMessageListStyles()
  const displayedScore = getDisplayedScore(scores)
  const [selectedScoreId, setSelectedScoreId] = useState(displayedScore.id)
  const [visibleCount, setVisibleCount] = useState(Infinity)
  const [isScorePopoverOpen, setIsScorePopoverOpen] = useState(false)
  const tabBarRef = useRef<HTMLDivElement>(null)
  const selectedScore = scores.find((score) => score.id === selectedScoreId) ?? displayedScore
  const selectedIndex = scores.indexOf(selectedScore)
  const orderedScores = useMemo(
    () => [
      ...scores.filter((score) => score.is_objective_score),
      ...scores.filter((score) => !score.is_objective_score),
    ],
    [scores]
  )
  const visibleScores = getVisibleScores(orderedScores, selectedScore.id, visibleCount)
  const overflowScores = orderedScores.filter(
    (score) => !visibleScores.some((visibleScore) => visibleScore.id === score.id)
  )

  useLayoutEffect(() => {
    const tabBar = tabBarRef.current
    if (!tabBar) return

    const measure = () => {
      if (tabBar.clientWidth === 0) {
        setVisibleCount(Infinity)
        return
      }

      const totalTabWidth = (
        orderedScores.length * SCORE_TAB_WIDTH_PX
        + Math.max(orderedScores.length - 1, 0) * SCORE_TAB_GAP_PX
      )
      if (totalTabWidth <= tabBar.clientWidth) {
        setVisibleCount(orderedScores.length)
        return
      }

      const availableWidth = (
        tabBar.clientWidth
        - SCORE_OVERFLOW_BUTTON_WIDTH_PX
        - SCORE_TAB_GAP_PX
      )
      const fittingCount = Math.max(
        Math.min(2, orderedScores.length),
        Math.floor(
          (availableWidth + SCORE_TAB_GAP_PX)
          / (SCORE_TAB_WIDTH_PX + SCORE_TAB_GAP_PX)
        )
      )
      setVisibleCount(fittingCount)
    }

    const observer = new ResizeObserver(measure)
    observer.observe(tabBar)
    measure()
    return () => observer.disconnect()
  }, [isScorePopoverOpen, orderedScores, selectedScore.id])

  if (scores.length === 1) {
    return (
      <div className={styles.scoreList}>
        <MessageScore score={selectedScore} groupId={groupId} scoreIndex={selectedIndex} />
      </div>
    )
  }

  return (
    <div className={styles.scoreList}>
      <Popover
        withArrow
        open={isScorePopoverOpen}
        onOpenChange={(_event: unknown, data: { open: boolean }) => setIsScorePopoverOpen(data.open)}
      >
        <PopoverTrigger disableButtonEnhancement>
          <Button
            appearance="subtle"
            size="small"
            className={styles.stackedScoreButton}
            aria-label={`View ${scores.length} scores, displayed score ${displayedScore.score_value} from ${displayedScore.scorer_type}${displayedScore.is_objective_score ? ', objective score' : ''}${displayedScore.sourceLabel ? `, ${displayedScore.sourceLabel}` : ''}`}
            data-testid={`message-score-stack-${groupId}`}
          >
            <span className={styles.scoreStack} aria-hidden="true">
              <span className={styles.scoreStackOvalBack} />
              <span className={styles.scoreStackOvalMiddle} />
              <span className={styles.scoreStackOvalFront}>
                {displayedScore.score_value}
              </span>
            </span>
          </Button>
        </PopoverTrigger>
        <PopoverSurface className={styles.multiScorePopover}>
          <div ref={tabBarRef} className={styles.scoreTabBar} data-score-tab-bar>
            <TabList
              selectedValue={selectedScore.id}
              onTabSelect={(_event: unknown, data: { value: unknown }) => setSelectedScoreId(String(data.value))}
              selectTabOnFocus={false}
              size="small"
              className={styles.scoreTabs}
              aria-label="Scores"
            >
              {visibleScores.map((score) => {
                const scoreIndex = scores.indexOf(score)
                const scoreContext = `Score ${score.score_value} from ${score.scorer_type}${score.is_objective_score ? ', objective score' : ''}${score.sourceLabel ? `, ${score.sourceLabel}` : ''}`
                return (
                  <Tooltip
                    key={score.id}
                    content={scoreContext}
                    relationship="description"
                    withArrow
                  >
                    <Tab
                      value={score.id}
                      id={`message-score-tab-${groupId}-${scoreIndex}`}
                      aria-label={scoreContext}
                      aria-controls={`message-score-panel-${groupId}`}
                      className={mergeClasses(
                        styles.scoreTab,
                        score.is_objective_score && styles.objectiveScoreTab
                      )}
                      data-testid={`message-score-tab-${groupId}-${scoreIndex}`}
                    >
                      <span className={styles.scoreTabValue}>
                        {score.score_value}
                      </span>
                    </Tab>
                  </Tooltip>
                )
              })}
            </TabList>
            {overflowScores.length > 0 && (
              <ScoreOverflowMenu
                scores={overflowScores}
                onSelect={setSelectedScoreId}
              />
            )}
          </div>
          <div
            role="tabpanel"
            id={`message-score-panel-${groupId}`}
            aria-labelledby={`message-score-tab-${groupId}-${selectedIndex}`}
          >
            <ScoreDetails score={selectedScore} testId={`message-score-details-${groupId}-${selectedIndex}`} />
          </div>
        </PopoverSurface>
      </Popover>
    </div>
  )
}

/**
 * If the trimmed text is a JSON object or array, return a 2-space pretty-printed
 * version of it; otherwise return null. Used to render structured assistant
 * responses (e.g. PromptShield verdicts) as readable JSON instead of a single
 * line of compact text.
 */
function tryFormatJson(text: string): string | null {
  const trimmed = text.trim()
  if (!trimmed) return null
  const first = trimmed[0]
  const last = trimmed[trimmed.length - 1]
  // Cheap pre-check: only attempt parsing for object- or array-shaped content
  // so things like "1" or "true" (which are valid JSON) are still rendered as
  // plain text.
  if (!((first === '{' && last === '}') || (first === '[' && last === ']'))) {
    return null
  }
  try {
    return JSON.stringify(JSON.parse(trimmed), null, 2)
  } catch {
    return null
  }
}

export default function MessageList({ messages, onCopyToInput, onCopyToNewConversation, onBranchConversation, onBranchAttack, isLoading, isSingleTurn, isOperatorLocked, isCrossTarget, noTargetSelected, globalMarkdown = false }: MessageListProps) {
  const styles = useMessageListStyles()
  const messagesEndRef = useRef<HTMLDivElement>(null)

  const handleDownload = async (att: MessageAttachment) => {
    try {
      // Convert the URL (data URI or same-origin) to a Blob, then create
      // an object URL so the browser reliably triggers a file download.
      const resp = await fetch(att.url)
      const blob = await resp.blob()
      const objectUrl = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = objectUrl
      link.download = att.name
      document.body.appendChild(link)
      link.click()
      document.body.removeChild(link)
      URL.revokeObjectURL(objectUrl)
    } catch {
      // Fallback: open in a new tab rather than navigating away
      window.open(att.url, '_blank')
    }
  }

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  if (isLoading) {
    return (
      <div className={styles.emptyState} data-testid="loading-state">
        <Spinner size="medium" label="Loading conversation..." />
      </div>
    )
  }

  if (messages.length === 0) {
    return (
      <div className={styles.emptyState}>
        <Text size={300} style={{ color: tokens.colorNeutralForeground3 }}>
          There are no messages in this conversation yet.
        </Text>
      </div>
    )
  }

  return (
    <div className={styles.root} data-testid="message-list">
      {messages.map((message, index) => {
        if (message.role === 'system') return null
        const isUser = message.role === 'user'
        const isSimulated = message.role === 'simulated_assistant'
        const timestamp = new Date(message.timestamp).toLocaleTimeString()
        const avatarName = isUser ? 'User' : isSimulated ? 'Simulated' : 'Assistant'

        return (
          <div
            key={index}
            className={mergeClasses(styles.message, isUser && styles.userMessage)}
          >
            <Avatar
              name={avatarName}
              color={isUser ? 'colorful' : isSimulated ? 'steel' : 'brand'}
            />
            <div
              className={mergeClasses(styles.messageContent, isUser && styles.userMessageContent)}
              data-testid={`message-bubble-${index}`}
            >
              {/* Error rendering */}
              {message.error && (
                <div className={styles.errorContainer}>
                  <MessageBar intent="error">
                    <MessageBarBody>
                      <Text weight="semibold">{message.error.type}</Text>
                      {message.error.description && (
                        <Text>: {message.error.description}</Text>
                      )}
                    </MessageBarBody>
                  </MessageBar>
                </div>
              )}

              {/* Reasoning summaries (model thinking) */}
              {message.reasoningSummaries && message.reasoningSummaries.length > 0 && (
                <div className={styles.reasoningContainer} data-testid="reasoning-summary">
                  <div className={styles.reasoningLabel}>Reasoning</div>
                  {message.reasoningSummaries.map((summary, i) => (
                    <Text key={i} className={styles.reasoningText} block>
                      {summary}
                    </Text>
                  ))}
                </div>
              )}

              {/* Original value – shown only when it differs from converted */}
              {(message.originalContent || message.originalAttachments) && (
                <div className={styles.originalSection} data-testid="original-section">
                  <div className={styles.sectionLabel}>Original</div>
                  {message.originalContent && (
                    <Text className={styles.originalText}>{message.originalContent}</Text>
                  )}
                  {message.originalAttachments && message.originalAttachments.length > 0 && (
                    <div className={styles.attachmentsContainer}>
                      {message.originalAttachments.map((att, i) => (
                        <div key={i} className={styles.attachmentItem}>
                          {att.type === 'image' && <ImageWithSpinner src={att.url} alt={att.name} className={styles.attachmentPreview} hiddenClassName={styles.attachmentPreviewHidden} containerClassName={styles.imageContainer} spinnerClassName={styles.imageSpinner} />}
                          {att.type === 'video' && <MediaWithFallback type="video" src={att.url} className={styles.videoPreview} />}
                          {att.type === 'audio' && <MediaWithFallback type="audio" src={att.url} className={styles.audioPreview} />}
                          {att.type === 'file' && <div className={styles.attachmentFile}><Text size={200}>📄 {att.name}</Text></div>}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}

              {/* Divider + Converted label – only shown when there is an original section */}
              {(message.originalContent || message.originalAttachments) && (
                <>
                  <div className={styles.sectionDivider} />
                  <Tooltip content="Only the converted value was sent to the target" relationship="description">
                    <div className={styles.convertedLabel} data-testid="converted-label">Converted</div>
                  </Tooltip>
                </>
              )}

              {/* Text content (converted / primary), with its scores below it. */}
              {Boolean(message.content || message.scores?.length) && (
                <div className={styles.pieceRow}>
                  {(() => {
                    if (message.isLoading) {
                      return (
                        <Text className={styles.loadingEllipsis}>
                          {message.content}
                        </Text>
                      )
                    }
                    // When Markdown rendering is enabled, it takes precedence over
                    // the JSON auto-format below.
                    if (globalMarkdown) {
                      return (
                        <MarkdownContent
                          content={message.content}
                          testId={`message-markdown-${index}`}
                        />
                      )
                    }
                    // For assistant / simulated_assistant messages, detect
                    // structured JSON responses (e.g. PromptShield verdicts) and
                    // render them pretty-printed inside a <pre> so the user can
                    // actually read them. User-typed JSON is left as-is.
                    const formatted = !isUser ? tryFormatJson(message.content) : null
                    if (formatted !== null) {
                      return (
                        <pre className={styles.messageJsonBlock} data-testid={`message-json-${index}`}>
                          {formatted}
                        </pre>
                      )
                    }
                    return (
                      <Text className={styles.messageText}>
                        {message.content}
                      </Text>
                    )
                  })()}
                  {message.scores && message.scores.length > 0 && (
                    <MessageScores scores={message.scores} groupId={index} />
                  )}
                </div>
              )}

              {/* Attachments (images, audio, video, files) */}
              {message.attachments && message.attachments.length > 0 && (
                <div className={styles.attachmentsContainer}>
                  {message.attachments.map((att, attIndex) => (
                    <div key={attIndex} className={styles.attachmentItem}>
                      {att.type === 'image' && (
                        <ImageWithSpinner
                          src={att.url}
                          alt={att.name}
                          className={styles.attachmentPreview}
                          hiddenClassName={styles.attachmentPreviewHidden}
                          containerClassName={styles.imageContainer}
                          spinnerClassName={styles.imageSpinner}
                        />
                      )}
                      {att.type === 'video' && (
                        <MediaWithFallback type="video" src={att.url} className={styles.videoPreview} />
                      )}
                      {att.type === 'audio' && (
                        <MediaWithFallback type="audio" src={att.url} className={styles.audioPreview} />
                      )}
                      {att.type === 'file' && (
                        <div className={styles.attachmentFile}>
                          <Text size={200} className={styles.attachmentFileName}>📄 {att.name}</Text>
                          {att.url && (
                            <Tooltip content="Open in new tab" relationship="label">
                              <a
                                href={att.url}
                                target="_blank"
                                rel="noopener noreferrer"
                                className={styles.attachmentOpenLink}
                                data-testid={`attachment-open-${index}-${attIndex}`}
                              >
                                <OpenRegular fontSize={14} />
                                <span>Open</span>
                              </a>
                            </Tooltip>
                          )}
                        </div>
                      )}
                      {/* Scores for this attachment's piece — shown directly below it. */}
                      {att.scores && att.scores.length > 0 && (
                        <MessageScores scores={att.scores} groupId={`${index}-att-${attIndex}`} />
                      )}
                    </div>
                  ))}
                </div>
              )}

              {/* Unified action buttons – shown on all non-user, non-loading messages */}
              {!isUser && !message.isLoading && (
                <div className={styles.messageActions} data-testid={`message-actions-${index}`}>
                  {/* 1. Copy to input box in this conversation */}
                  {onCopyToInput && (() => {
                    const disabled = Boolean(noTargetSelected || isSingleTurn || isOperatorLocked || isCrossTarget)
                    const tip = noTargetSelected
                      ? 'Cannot copy to this conversation — no target selected'
                      : isSingleTurn
                        ? 'Cannot copy to this conversation — target is single-turn'
                        : isOperatorLocked
                          ? 'Cannot copy to this conversation — you are not the operator of this attack'
                          : isCrossTarget
                            ? 'Cannot copy to this conversation — it used a different target'
                            : 'Copy to input box in this conversation'
                    return (
                      <Tooltip content={tip} relationship="label">
                        <Button
                          appearance="subtle"
                          size="small"
                          icon={<ArrowReplyRegular />}
                          disabled={disabled}
                          onClick={() => onCopyToInput(index)}
                          data-testid={`copy-to-input-btn-${index}`}
                          className={styles.messageActionButton}
                        />
                      </Tooltip>
                    )
                  })()}

                  {/* 2. Copy to input box in a new conversation (same attack) */}
                  {onCopyToNewConversation && (() => {
                    const disabled = Boolean(noTargetSelected || isOperatorLocked || isCrossTarget)
                    const tip = noTargetSelected
                      ? 'Cannot copy to a new conversation — no target selected'
                      : isOperatorLocked
                        ? 'Cannot copy to a new conversation — you are not the operator of this attack'
                        : isCrossTarget
                          ? 'Cannot copy to a new conversation — this attack used a different target'
                          : 'Copy to input box in a new conversation'
                    return (
                      <Tooltip content={tip} relationship="label">
                        <Button
                          appearance="subtle"
                          size="small"
                          icon={<ArrowForwardRegular />}
                          disabled={disabled}
                          onClick={() => onCopyToNewConversation(index)}
                          data-testid={`copy-to-new-conv-btn-${index}`}
                          className={styles.messageActionButton}
                        />
                      </Tooltip>
                    )
                  })()}

                  {/* 3. Branch into new conversation (same attack) */}
                  {onBranchConversation && (() => {
                    const disabled = Boolean(noTargetSelected || isSingleTurn || isOperatorLocked || isCrossTarget)
                    const tip = noTargetSelected
                      ? 'Cannot branch into new conversation — no target selected'
                      : isSingleTurn
                        ? 'Cannot branch into new conversation — target is single-turn'
                        : isOperatorLocked
                          ? 'Cannot branch into new conversation — you are not the operator of this attack'
                          : isCrossTarget
                            ? 'Cannot branch into new conversation — this attack used a different target'
                            : 'Branch into new conversation'
                    return (
                      <Tooltip content={tip} relationship="label">
                        <Button
                          appearance="subtle"
                          size="small"
                          icon={<BranchForkRegular />}
                          disabled={disabled}
                          onClick={() => onBranchConversation(index)}
                          data-testid={`branch-conv-btn-${index}`}
                          className={styles.messageActionButton}
                        />
                      </Tooltip>
                    )
                  })()}

                  {/* 4. Branch into new attack */}
                  {(() => {
                    const singleTurnBlock = isSingleTurn && !noTargetSelected
                    if (onBranchAttack && !singleTurnBlock) {
                      return (
                        <Tooltip content="Branch into new attack" relationship="label">
                          <Button
                            appearance="subtle"
                            size="small"
                            icon={<ChatAddRegular />}
                            onClick={() => onBranchAttack(index)}
                            data-testid={`branch-attack-btn-${index}`}
                            className={styles.messageActionButton}
                          />
                        </Tooltip>
                      )
                    }
                    // Show disabled button with reason
                    const tip = noTargetSelected
                      ? 'Cannot branch into new attack — no target selected'
                      : singleTurnBlock
                        ? 'Cannot branch into new attack — target is single-turn'
                        : undefined
                    if (!tip) return null
                    return (
                      <Tooltip content={tip} relationship="label">
                        <Button
                          appearance="subtle"
                          size="small"
                          icon={<ChatAddRegular />}
                          disabled
                          data-testid={`branch-attack-btn-${index}`}
                          className={styles.messageActionButton}
                        />
                      </Tooltip>
                    )
                  })()}

                  {/* Download: non-text media only */}
                  {message.attachments && message.attachments.filter(a => a.type !== 'file').map((att, ai) => (
                    <Tooltip key={ai} content={`Download ${att.name}`} relationship="label">
                      <Button
                        appearance="subtle"
                        size="small"
                        icon={<ArrowDownloadRegular />}
                        onClick={() => handleDownload(att)}
                        data-testid={`download-btn-${index}-${ai}`}
                        className={styles.messageActionButton}
                      />
                    </Tooltip>
                  ))}
                </div>
              )}

              <div className={styles.messageFooter}>
                <Text className={styles.timestamp}>{timestamp}</Text>
                <div className={styles.footerDetails}>
                  <Text className={styles.role}>{message.role}</Text>
                </div>
              </div>
            </div>
          </div>
        )
      })}
      <div ref={messagesEndRef} />
    </div>
  )
}
