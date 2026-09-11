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
import MarkdownContent from '@/components/Markdown/MarkdownContent'

import type { DisplayScore, Message, MessageAttachment, MessageDisplayPiece } from '../../types'
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

function scoreDisplayValue(score: DisplayScore): string {
  // An undetermined score has no value, so name its status instead.
  return score.score_value ?? score.status ?? ''
}

function scoreDisplayLabel(score: DisplayScore): string {
  return `Score: ${scoreDisplayValue(score)}`
}

function ScoreDetails({ score, testId }: { score: DisplayScore; testId: string }) {
  const styles = useMessageListStyles()
  const categories = score.score_category?.filter(Boolean) ?? []

  return (
    <div className={styles.scoreSurface} data-testid={testId}>
      <Text weight="semibold">Score details</Text>
      <div className={styles.scoreRow}>
        <Text size={200} weight="semibold" className={styles.scoreLabel}>Score</Text>
        <Badge appearance="tint" color="brand" size="small" className={styles.scoreValue}>{scoreDisplayValue(score)}</Badge>
      </div>
      <div className={styles.scoreRow}>
        <Text size={200} weight="semibold" className={styles.scoreLabel}>Type</Text>
        <Text size={200} className={styles.scoreValue}>{score.score_type}</Text>
      </div>
      <div className={styles.scoreRow}>
        <Text size={200} weight="semibold" className={styles.scoreLabel}>Scorer</Text>
        <Text size={200} className={styles.scoreValue}>{score.scorer_type}</Text>
      </div>
      <div className={styles.scoreRow}>
        <Text size={200} weight="semibold" className={styles.scoreLabel}>Objective</Text>
        <Text size={200} className={styles.scoreValue}>{score.is_objective_score ? 'Yes' : 'No'}</Text>
      </div>
      {score.sourceLabel && (
        <div className={styles.scoreRow}>
          <Text size={200} weight="semibold" className={styles.scoreLabel}>Piece</Text>
          <Text size={200} className={styles.scoreValue}>{score.sourceLabel}</Text>
        </div>
      )}
      {categories.length > 0 && (
        <div className={styles.scoreRow}>
          <Text size={200} weight="semibold" className={styles.scoreLabel}>Category</Text>
          <Text size={200} className={styles.scoreValue}>{categories.join(', ')}</Text>
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
  const displayValue = scoreDisplayValue(score)
  const displayLabel = scoreDisplayLabel(score)

  return (
    <Popover withArrow>
      <Tooltip content={displayLabel} relationship="description" withArrow>
        <PopoverTrigger disableButtonEnhancement>
          <Button
            appearance="subtle"
            size="small"
            className={styles.scoreChip}
            aria-label={`Score ${displayValue} from ${score.scorer_type}${score.is_objective_score ? ', objective score' : ''}${score.sourceLabel ? `, ${score.sourceLabel}` : ''}`}
            data-testid={`message-score-${groupId}-${scoreIndex}`}
          >
            <Badge appearance="tint" color="brand" size="medium" className={styles.scoreChipValue}>
              {displayLabel}
            </Badge>
          </Button>
        </PopoverTrigger>
      </Tooltip>
      <PopoverSurface>
        <ScoreDetails score={score} testId={`message-score-details-${groupId}-${scoreIndex}`} />
      </PopoverSurface>
    </Popover>
  )
}

interface ScoreOverflowMenuItemProps {
  score: DisplayScore
  label: string
  onSelect: (scoreId: string) => void
}

function ScoreOverflowMenuItem({ score, label, onSelect }: ScoreOverflowMenuItemProps) {
  const styles = useMessageListStyles()

  return (
    <MenuItem
      className={styles.scoreMenuItem}
      aria-label={label}
      onClick={() => onSelect(score.id)}
    >
      {label}
    </MenuItem>
  )
}

interface ScoreOverflowMenuProps {
  scores: DisplayScore[]
  onSelect: (scoreId: string) => void
}

// Keep these measurements synchronized with scoreTab, scoreTabs.columnGap,
// and scoreOverflowButton in MessageList.styles.ts.
const SCORE_TAB_WIDTH_PX = 72
const SCORE_TAB_GAP_PX = 4
const SCORE_OVERFLOW_BUTTON_WIDTH_PX = 112

function getScoreOverflowLabels(scores: DisplayScore[]): string[] {
  const baseLabels = scores.map((score) => {
    const categories = score.score_category?.filter(Boolean) ?? []
    return [
      scoreDisplayValue(score),
      score.scorer_type,
      score.is_objective_score ? 'Objective' : '',
      score.sourceLabel,
      categories.length > 0 ? `Categories: ${categories.join(', ')}` : '',
    ].filter(Boolean).join(' · ')
  })
  const labelCounts = new Map<string, number>()
  baseLabels.forEach((label) => labelCounts.set(label, (labelCounts.get(label) ?? 0) + 1))

  const labelOrdinals = new Map<string, number>()
  return baseLabels.map((label) => {
    const count = labelCounts.get(label) ?? 1
    if (count === 1) return label
    const ordinal = (labelOrdinals.get(label) ?? 0) + 1
    labelOrdinals.set(label, ordinal)
    return `${label} · ${ordinal} of ${count}`
  })
}

function ScoreOverflowMenu({ scores, onSelect }: ScoreOverflowMenuProps) {
  const styles = useMessageListStyles()
  const labels = getScoreOverflowLabels(scores)

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
          {scores.map((score, index) => (
            <ScoreOverflowMenuItem
              key={score.id}
              score={score}
              label={labels[index]}
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
  const selectedTabRef = useRef<HTMLButtonElement>(null)
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

  useEffect(() => {
    if (isScorePopoverOpen) {
      selectedTabRef.current?.focus()
    }
  }, [isScorePopoverOpen, selectedScore.id])

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
        unstable_disableAutoFocus
        onOpenChange={(_event: unknown, data: { open: boolean }) => setIsScorePopoverOpen(data.open)}
      >
        <Tooltip content={scoreDisplayLabel(displayedScore)} relationship="description" withArrow>
          <PopoverTrigger disableButtonEnhancement>
            <Button
              appearance="subtle"
              size="small"
              className={styles.stackedScoreButton}
              aria-label={`View ${scores.length} scores, displayed score ${scoreDisplayValue(displayedScore)} from ${displayedScore.scorer_type}${displayedScore.is_objective_score ? ', objective score' : ''}${displayedScore.sourceLabel ? `, ${displayedScore.sourceLabel}` : ''}`}
              data-testid={`message-score-stack-${groupId}`}
            >
              <span className={styles.scoreStack} aria-hidden="true">
                <span className={styles.scoreStackOvalBack} />
                <span className={styles.scoreStackOvalMiddle} />
                <span className={styles.scoreStackOvalFront}>
                  {scoreDisplayLabel(displayedScore)}
                </span>
              </span>
            </Button>
          </PopoverTrigger>
        </Tooltip>
        <PopoverSurface className={styles.multiScorePopover}>
          <Text size={200} weight="semibold">Score:</Text>
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
                const scoreContext = `Score ${scoreDisplayValue(score)} from ${score.scorer_type}${score.is_objective_score ? ', objective score' : ''}${score.sourceLabel ? `, ${score.sourceLabel}` : ''}`
                return (
                  <Tooltip
                    key={score.id}
                    content={scoreContext}
                    relationship="description"
                    withArrow
                  >
                    <Tab
                      ref={score.id === selectedScore.id ? selectedTabRef : undefined}
                      value={score.id}
                      id={`message-score-tab-${groupId}-${scoreIndex}`}
                      aria-label={scoreContext}
                      aria-controls={`message-score-panel-${groupId}`}
                      className={styles.scoreTab}
                      data-testid={`message-score-tab-${groupId}-${scoreIndex}`}
                    >
                      <span className={styles.scoreTabValue}>
                        {scoreDisplayValue(score)}
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

interface RenderMessagePiece {
  piece: MessageDisplayPiece
  groupId: string | number
  markdownTestId: string
}

function getRenderMessagePieces(message: Message, messageIndex: number): RenderMessagePiece[] {
  if (message.displayPieces) {
    const hasMultiplePieces = message.displayPieces.length > 1
    return message.displayPieces.map((piece) => ({
      piece,
      groupId: hasMultiplePieces ? `${messageIndex}-piece-${piece.pieceIndex}` : messageIndex,
      markdownTestId: `message-markdown-${messageIndex}-${piece.pieceIndex}`,
    }))
  }

  const pieces: RenderMessagePiece[] = []
  if (message.content || message.scores?.length) {
    pieces.push({
      piece: {
        type: 'text',
        pieceId: `message-${messageIndex}-text`,
        pieceIndex: 0,
        content: message.content,
        scores: message.scores,
      },
      groupId: messageIndex,
      markdownTestId: `message-markdown-${messageIndex}`,
    })
  }
  message.attachments?.forEach((attachment, attachmentIndex) => {
    pieces.push({
      piece: {
        type: 'media',
        pieceId: attachment.pieceId ?? `message-${messageIndex}-attachment-${attachmentIndex}`,
        pieceIndex: attachmentIndex,
        attachment,
      },
      groupId: `${messageIndex}-att-${attachmentIndex}`,
      markdownTestId: `message-markdown-${messageIndex}-att-${attachmentIndex}`,
    })
  })
  return pieces
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
        const renderPieces = getRenderMessagePieces(message, index)

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

              {/* Converted pieces in backend order, each with only its own scores. */}
              {renderPieces.length > 0 && (
                <div className={styles.displayPiecesContainer}>
                  {renderPieces.map(({ piece, groupId, markdownTestId }) => {
                    if (piece.type === 'text') {
                      const formatted = !message.isLoading && !globalMarkdown && !isUser
                        ? tryFormatJson(piece.content)
                        : null
                      return (
                        <div
                          key={piece.pieceId}
                          className={styles.pieceRow}
                          data-testid={`message-piece-${index}-${piece.pieceIndex}`}
                        >
                          {message.isLoading ? (
                            <Text className={styles.loadingEllipsis}>{piece.content}</Text>
                          ) : globalMarkdown ? (
                            <MarkdownContent content={piece.content} testId={markdownTestId} />
                          ) : formatted !== null ? (
                            <pre
                              className={styles.messageJsonBlock}
                              data-testid={message.displayPieces
                                ? `message-json-${index}-${piece.pieceIndex}`
                                : `message-json-${index}`}
                            >
                              {formatted}
                            </pre>
                          ) : (
                            <Text className={styles.messageText}>{piece.content}</Text>
                          )}
                          {piece.scores && piece.scores.length > 0 && (
                            <MessageScores scores={piece.scores} groupId={groupId} />
                          )}
                        </div>
                      )
                    }

                    const att = piece.attachment
                    return (
                      <div
                        key={piece.pieceId}
                        className={styles.attachmentItem}
                        data-testid={`message-piece-${index}-${piece.pieceIndex}`}
                      >
                        {att?.type === 'image' && att.url && (
                          <ImageWithSpinner
                            src={att.url}
                            alt={att.name}
                            className={styles.attachmentPreview}
                            hiddenClassName={styles.attachmentPreviewHidden}
                            containerClassName={styles.imageContainer}
                            spinnerClassName={styles.imageSpinner}
                          />
                        )}
                        {att?.type === 'video' && att.url && (
                          <MediaWithFallback type="video" src={att.url} className={styles.videoPreview} />
                        )}
                        {att?.type === 'audio' && att.url && (
                          <MediaWithFallback type="audio" src={att.url} className={styles.audioPreview} />
                        )}
                        {att?.type === 'file' && (
                          <div className={styles.attachmentFile}>
                            <Text size={200} className={styles.attachmentFileName}>📄 {att.name}</Text>
                            {att.url && (
                              <Tooltip content="Open in new tab" relationship="label">
                                <a
                                  href={att.url}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  className={styles.attachmentOpenLink}
                                  data-testid={`attachment-open-${index}-${piece.pieceIndex}`}
                                >
                                  <OpenRegular fontSize={14} />
                                  <span>Open</span>
                                </a>
                              </Tooltip>
                            )}
                          </div>
                        )}
                        {piece.scores && piece.scores.length > 0 && (
                          <MessageScores scores={piece.scores} groupId={groupId} />
                        )}
                      </div>
                    )
                  })}
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
                  {message.attachments && message.attachments.filter(a => a.type !== 'file' && a.url).map((att, ai) => (
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
