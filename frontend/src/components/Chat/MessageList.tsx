import { useEffect, useRef, useState, useCallback } from 'react'
import {
  Text,
  Avatar,
  tokens,
  MessageBar,
  MessageBarBody,
  Button,
  Tooltip,
  Spinner,
} from '@fluentui/react-components'
import { ArrowDownloadRegular, ArrowReplyRegular, ArrowForwardRegular, ChatAddRegular, BranchForkRegular } from '@fluentui/react-icons'
import { Message, MessageAttachment } from '../../types'
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
  return <audio src={src} controls onError={handleError} data-testid="audio-player" />
}

export default function MessageList({ messages, onCopyToInput, onCopyToNewConversation, onBranchConversation, onBranchAttack, isLoading, isSingleTurn, isOperatorLocked, isCrossTarget, noTargetSelected }: MessageListProps) {
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
        <Text size={500} weight="semibold">Welcome to PyRIT</Text>
        <Text size={300} style={{ color: tokens.colorNeutralForeground3 }}>
          Start a conversation to test AI safety and robustness
        </Text>
      </div>
    )
  }

  return (
    <div className={styles.root}>
      {messages.map((message, index) => {
        const isUser = message.role === 'user'
        const isSimulated = message.role === 'simulated_assistant'
        const timestamp = new Date(message.timestamp).toLocaleTimeString()
        const avatarName = isUser ? 'User' : isSimulated ? 'Simulated' : 'Assistant'

        return (
          <div
            key={index}
            className={`${styles.message} ${isUser ? styles.userMessage : ''}`}
          >
            <Avatar
              name={avatarName}
              color={isUser ? 'colorful' : isSimulated ? 'steel' : 'brand'}
            />
            <div className={`${styles.messageContent} ${isUser ? styles.userMessageContent : ''}`}>
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
                        <div key={i}>
                          {att.type === 'image' && <ImageWithSpinner src={att.url} alt={att.name} className={styles.attachmentPreview} hiddenClassName={styles.attachmentPreviewHidden} containerClassName={styles.imageContainer} spinnerClassName={styles.imageSpinner} />}
                          {att.type === 'video' && <MediaWithFallback type="video" src={att.url} className={styles.videoPreview} />}
                          {att.type === 'audio' && <MediaWithFallback type="audio" src={att.url} />}
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

              {/* Text content (converted / primary) */}
              {message.content && (
                <Text className={message.isLoading ? styles.loadingEllipsis : styles.messageText}>
                  {message.content}
                </Text>
              )}

              {/* Attachments (images, audio, video, files) */}
              {message.attachments && message.attachments.length > 0 && (
                <div className={styles.attachmentsContainer}>
                  {message.attachments.map((att, attIndex) => (
                    <div key={attIndex}>
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
                        <MediaWithFallback type="audio" src={att.url} />
                      )}
                      {att.type === 'file' && (
                        <div className={styles.attachmentFile}>
                          <Text size={200}>📄 {att.name}</Text>
                        </div>
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
                      ? 'Cannot copy — no target selected'
                      : isSingleTurn
                        ? 'Cannot copy — target is single-turn'
                        : isOperatorLocked
                          ? 'Cannot copy — you are not the operator of this attack'
                          : isCrossTarget
                            ? 'Cannot copy — conversation used a different target'
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
                          style={{ minWidth: 'auto', padding: '2px' }}
                        />
                      </Tooltip>
                    )
                  })()}

                  {/* 2. Copy to input box in a new conversation (same attack) */}
                  {onCopyToNewConversation && (() => {
                    const disabled = Boolean(noTargetSelected || isOperatorLocked || isCrossTarget)
                    const tip = noTargetSelected
                      ? 'Cannot copy — no target selected'
                      : isOperatorLocked
                        ? 'Cannot add to this attack — you are not the operator'
                        : isCrossTarget
                          ? 'Cannot add to this attack — conversation used a different target'
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
                          style={{ minWidth: 'auto', padding: '2px' }}
                        />
                      </Tooltip>
                    )
                  })()}

                  {/* 3. Branch into new conversation (same attack) */}
                  {onBranchConversation && (() => {
                    const disabled = Boolean(noTargetSelected || isSingleTurn || isOperatorLocked || isCrossTarget)
                    const tip = noTargetSelected
                      ? 'Cannot branch — no target selected'
                      : isSingleTurn
                        ? 'Cannot branch — target is single-turn'
                        : isOperatorLocked
                          ? 'Cannot add to this attack — you are not the operator'
                          : isCrossTarget
                            ? 'Cannot add to this attack — conversation used a different target'
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
                          style={{ minWidth: 'auto', padding: '2px' }}
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
                            style={{ minWidth: 'auto', padding: '2px' }}
                          />
                        </Tooltip>
                      )
                    }
                    // Show disabled button with reason
                    const tip = noTargetSelected
                      ? 'Cannot branch — no target selected'
                      : singleTurnBlock
                        ? 'Cannot branch — target is single-turn'
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
                          style={{ minWidth: 'auto', padding: '2px' }}
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
                        style={{ minWidth: 'auto', padding: '2px' }}
                      />
                    </Tooltip>
                  ))}
                </div>
              )}

              <div className={styles.messageFooter}>
                <Text className={styles.timestamp}>{timestamp}</Text>
                <Text className={styles.role}>{message.role}</Text>
              </div>
            </div>
          </div>
        )
      })}
      <div ref={messagesEndRef} />
    </div>
  )
}
