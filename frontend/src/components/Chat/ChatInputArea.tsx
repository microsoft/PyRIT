import { useState, useEffect, useLayoutEffect, useRef, forwardRef, useImperativeHandle, KeyboardEvent } from 'react'
import {
  Button,
  tokens,
  Caption1,
  Tooltip,
  Text,
} from '@fluentui/react-components'
import { SendRegular, AttachRegular, DismissRegular, InfoRegular, AddRegular, CopyRegular, WarningRegular, SettingsRegular } from '@fluentui/react-icons'
import { MessageAttachment, TargetInstance } from '../../types'
import { useChatInputAreaStyles } from './ChatInputArea.styles'

// ---------------------------------------------------------------------------
// Reusable status banner
// ---------------------------------------------------------------------------

interface StatusBannerProps {
  icon: React.ReactElement
  text: string
  buttonText?: string
  buttonIcon?: React.ReactElement
  onButtonClick?: () => void
  testId: string
  className: string
  textClassName: string
  buttonTestId?: string
}

function StatusBanner({ icon, text, buttonText, buttonIcon, onButtonClick, testId, className, textClassName, buttonTestId }: StatusBannerProps) {
  return (
    <div className={className} data-testid={testId}>
      {icon}
      <Text className={textClassName} size={300}>
        {text}
      </Text>
      {onButtonClick && buttonText && (
        <Button
          appearance="primary"
          icon={buttonIcon}
          onClick={onButtonClick}
          data-testid={buttonTestId}
        >
          {buttonText}
        </Button>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export interface ChatInputAreaHandle {
  addAttachment: (att: MessageAttachment) => void
  setText: (text: string) => void
}

interface ChatInputAreaProps {
  onSend: (originalValue: string, convertedValue: string | undefined, attachments: MessageAttachment[]) => void
  disabled?: boolean
  activeTarget?: TargetInstance | null
  singleTurnLimitReached?: boolean
  onNewConversation?: () => void
  operatorLocked?: boolean
  crossTargetLocked?: boolean
  onUseAsTemplate?: () => void
  attackOperator?: string
  noTargetSelected?: boolean
  onConfigureTarget?: () => void
}

const ChatInputArea = forwardRef<ChatInputAreaHandle, ChatInputAreaProps>(function ChatInputArea({ onSend, disabled = false, activeTarget, singleTurnLimitReached = false, onNewConversation, operatorLocked = false, crossTargetLocked = false, onUseAsTemplate, attackOperator, noTargetSelected = false, onConfigureTarget }, ref) {
  const styles = useChatInputAreaStyles()
  const [input, setInput] = useState('')
  const [attachments, setAttachments] = useState<MessageAttachment[]>([])
  const fileInputRef = useRef<HTMLInputElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  useImperativeHandle(ref, () => ({
    addAttachment: (att: MessageAttachment) => {
      setAttachments(prev => [...prev, att])
    },
    setText: (text: string) => {
      setInput(text)
    },
  }))

  const handleFileSelect = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files
    if (!files) return

    const newAttachments: MessageAttachment[] = []

    for (let i = 0; i < files.length; i++) {
      const file = files[i]
      const url = URL.createObjectURL(file)

      let type: MessageAttachment['type'] = 'file'
      if (file.type.startsWith('image/')) type = 'image'
      else if (file.type.startsWith('audio/')) type = 'audio'
      else if (file.type.startsWith('video/')) type = 'video'

      newAttachments.push({
        type,
        name: file.name,
        url,
        mimeType: file.type,
        size: file.size,
        file,
      })
    }

    setAttachments([...attachments, ...newAttachments])
    if (fileInputRef.current) {
      fileInputRef.current.value = ''
    }
  }

  const removeAttachment = (index: number) => {
    const newAttachments = [...attachments]
    URL.revokeObjectURL(newAttachments[index].url)
    newAttachments.splice(index, 1)
    setAttachments(newAttachments)
  }

  const handleSend = () => {
    if ((input || attachments.length > 0) && !disabled) {
      onSend(input, undefined, attachments)
      setInput('')
      setAttachments([])
      if (textareaRef.current) {
        textareaRef.current.style.height = 'auto'
      }
    }
  }

  // Re-focus the textarea after sending completes (disabled goes false)
  useEffect(() => {
    if (!disabled && textareaRef.current) {
      textareaRef.current.focus()
    }
  }, [disabled])

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  // Auto-resize textarea whenever input changes (covers paste, setText, etc.)
  // useLayoutEffect fires before paint, avoiding visible flicker on resize.
  useLayoutEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto'
      textareaRef.current.style.height = Math.min(textareaRef.current.scrollHeight, 96) + 'px'
    }
  }, [input])

  const handleInput = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInput(e.target.value)
  }

  const formatFileSize = (bytes: number) => {
    if (bytes < 1024) return bytes + ' B'
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
  }

  return (
    <div className={styles.root}>
      <div className={styles.inputContainer}>
        {noTargetSelected ? (
          <StatusBanner
            className={styles.noTargetBanner}
            textClassName={styles.noTargetText}
            icon={<WarningRegular fontSize={18} style={{ color: tokens.colorPaletteRedForeground1 }} />}
            text="No target selected"
            buttonText={onConfigureTarget ? "Configure Target" : undefined}
            buttonIcon={<SettingsRegular />}
            onButtonClick={onConfigureTarget}
            testId="no-target-banner"
            buttonTestId="configure-target-input-btn"
          />
        ) : operatorLocked ? (
          <StatusBanner
            className={styles.statusBanner}
            textClassName={styles.statusBannerText}
            icon={<InfoRegular fontSize={18} />}
            text={`This conversation belongs to operator: ${attackOperator}.`}
            buttonText={onUseAsTemplate ? "Continue with your target" : undefined}
            buttonIcon={<CopyRegular />}
            onButtonClick={onUseAsTemplate}
            testId="operator-locked-banner"
            buttonTestId="use-as-template-btn"
          />
        ) : crossTargetLocked ? (
          <StatusBanner
            className={styles.statusBanner}
            textClassName={styles.statusBannerText}
            icon={<InfoRegular fontSize={18} />}
            text="This attack uses a different target. Continue with your target to keep the conversation."
            buttonText={onUseAsTemplate ? "Continue with your target" : undefined}
            buttonIcon={<CopyRegular />}
            onButtonClick={onUseAsTemplate}
            testId="cross-target-banner"
            buttonTestId="use-as-template-btn"
          />
        ) : singleTurnLimitReached ? (
          <StatusBanner
            className={styles.statusBanner}
            textClassName={styles.statusBannerText}
            icon={<InfoRegular fontSize={18} />}
            text="This target only supports single-turn conversations."
            buttonText={onNewConversation ? "New Conversation" : undefined}
            buttonIcon={<AddRegular />}
            onButtonClick={onNewConversation}
            testId="single-turn-banner"
            buttonTestId="new-conversation-btn"
          />
        ) : (
        <>
        <div className={styles.inputWrapper}>
          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept="image/*,audio/*,video/*,.pdf,.doc,.docx,.txt"
            style={{ display: 'none' }}
            onChange={handleFileSelect}
          />
          {attachments.length > 0 && (
            <div className={styles.attachmentsContainer}>
              {attachments.map((att, index) => (
                <div key={index} className={styles.attachmentChip}>
                  <Caption1>
                    {att.type === 'image' && '🖼️'}
                    {att.type === 'audio' && '🎵'}
                    {att.type === 'video' && '🎥'}
                    {att.type === 'file' && '📄'}
                    {' '}{att.name} ({formatFileSize(att.size)})
                  </Caption1>
                  <Button
                    appearance="transparent"
                    size="small"
                    icon={<DismissRegular />}
                    onClick={() => removeAttachment(index)}
                  />
                </div>
              ))}
            </div>
          )}
          <div className={styles.inputRow}>
            <div className={styles.iconButtonsLeft}>
            <Button
              className={styles.iconButton}
              appearance="subtle"
              icon={<AttachRegular />}
              onClick={() => fileInputRef.current?.click()}
              disabled={disabled}
              title="Attach files"
            />
            </div>
          <textarea
            ref={textareaRef}
            className={styles.textInput}
            placeholder="Type something here"
            value={input}
            onChange={handleInput}
            onKeyDown={handleKeyDown}
            disabled={disabled}
            rows={1}
          />
          <div className={styles.iconButtonsRight}>
            {activeTarget && activeTarget.supports_multi_turn === false && (
              <Tooltip
                content="This target does not track conversation history — each turn is sent independently."
                relationship="description"
              >
                <span className={styles.singleTurnWarning}>
                  <InfoRegular fontSize={18} />
                </span>
              </Tooltip>
            )}
            <Button
              className={styles.sendButton}
              appearance="primary"
              icon={<SendRegular />}
              onClick={handleSend}
              disabled={disabled || (!input && attachments.length === 0)}
              title="Send message"
            />
          </div>
          </div>
        </div>
        </>
        )}
      </div>
    </div>
  )
})

export default ChatInputArea
