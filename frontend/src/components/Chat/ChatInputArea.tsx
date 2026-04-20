import { useState, useEffect, useLayoutEffect, useRef, forwardRef, useImperativeHandle, KeyboardEvent, Ref } from 'react'
import {
  Button,
  Caption1,
  Tooltip,
  Text,
  tokens,
} from '@fluentui/react-components'
import { SendRegular, AttachRegular, DismissRegular, InfoRegular, AddRegular, CopyRegular, WarningRegular, SettingsRegular, ArrowShuffleRegular } from '@fluentui/react-icons'
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
// Attachment list
// ---------------------------------------------------------------------------

interface AttachmentListProps {
  attachments: MessageAttachment[]
  mediaConversions: Array<{ pieceType: string; convertedValue: string }>
  onRemove: (index: number) => void
  onClearMediaConversion: (pieceType: string) => void
  formatFileSize: (bytes: number) => string
  styles: ReturnType<typeof useChatInputAreaStyles>
}

function AttachmentList({ attachments, mediaConversions, onRemove, onClearMediaConversion, formatFileSize, styles }: AttachmentListProps) {
  if (attachments.length === 0) return null
  return (
    <div className={styles.attachmentsContainer}>
      {attachments.map((att, index) => {
        const conversion = mediaConversions.find((mc) => mc.pieceType === att.type)
        return (
          <div key={index} className={styles.attachmentGroup}>
            <div className={styles.attachmentRow}>
              <span className={styles.attachmentContent}>
                {conversion && <span className={styles.originalBadge}>Original</span>}
                <Caption1>
                  {att.type === 'image' && '🖼️'}
                  {att.type === 'audio' && '🎵'}
                  {att.type === 'video' && '🎥'}
                  {att.type === 'file' && '📄'}
                  {' '}{att.name} ({formatFileSize(att.size)})
                </Caption1>
              </span>
              <Button
                appearance="transparent"
                size="small"
                className={styles.dismissBtn}
                icon={<DismissRegular />}
                onClick={() => onRemove(index)}
                data-testid={`remove-attachment-${index}`}
              />
            </div>
            {conversion && (
              <div className={styles.attachmentRow}>
                <span className={styles.attachmentContent}>
                  <span className={styles.convertedBadge}>Converted</span>
                  <Caption1 className={styles.convertedFilename}>{conversion.convertedValue.split('/').pop()}</Caption1>
                </span>
                <Button
                  appearance="transparent"
                  size="small"
                  className={styles.dismissBtn}
                  icon={<DismissRegular />}
                  onClick={() => onClearMediaConversion(att.type)}
                  data-testid={`clear-media-conversion-${att.type}`}
                />
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Text input rows (original + converted)
// ---------------------------------------------------------------------------

interface TextInputRowsProps {
  input: string
  convertedValue?: string | null
  disabled: boolean
  textareaRef: Ref<HTMLTextAreaElement>
  onInput: (e: React.ChangeEvent<HTMLTextAreaElement>) => void
  onKeyDown: (e: KeyboardEvent<HTMLTextAreaElement>) => void
  onConvertedValueChange: (value: string) => void
  onClearConversion: () => void
  styles: ReturnType<typeof useChatInputAreaStyles>
}

function TextInputRows({ input, convertedValue, disabled, textareaRef, onInput, onKeyDown, onConvertedValueChange, onClearConversion, styles }: TextInputRowsProps) {
  return (
    <>
      <div className={styles.textRow}>
        {convertedValue && (
          <span className={styles.originalBadge} data-testid="original-banner">Original</span>
        )}
        <textarea
          ref={textareaRef}
          className={styles.textInput}
          placeholder="Type prompt here"
          value={input}
          onChange={onInput}
          onKeyDown={onKeyDown}
          disabled={disabled}
          rows={1}
          data-testid="chat-input"
        />
      </div>
      {convertedValue && (
        <div className={styles.convertedRow} data-testid="converted-indicator">
          <span className={styles.convertedBadge}>Converted</span>
          <textarea
            className={styles.convertedTextarea}
            value={convertedValue}
            onChange={(e) => onConvertedValueChange(e.target.value)}
            rows={1}
            data-testid="converted-value-input"
          />
          <Button
            appearance="transparent"
            size="small"
            className={styles.dismissBtn}
            icon={<DismissRegular />}
            onClick={onClearConversion}
            data-testid="clear-conversion-btn"
          />
        </div>
      )}
    </>
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
  onNewConversation: () => void
  operatorLocked?: boolean
  crossTargetLocked?: boolean
  onUseAsTemplate: () => void
  attackOperator?: string
  noTargetSelected?: boolean
  onConfigureTarget: () => void
  onToggleConverterPanel: () => void
  isConverterPanelOpen: boolean
  onInputChange: (value: string) => void
  onAttachmentsChange: (types: string[], data: Record<string, string>) => void
  convertedValue?: string | null
  originalValue?: string | null
  onClearConversion: () => void
  onConvertedValueChange: (value: string) => void
  mediaConversions?: Array<{ pieceType: string; convertedValue: string }>
  onClearMediaConversion: (pieceType: string) => void
}

const ChatInputArea = forwardRef<ChatInputAreaHandle, ChatInputAreaProps>(function ChatInputArea({ onSend, disabled = false, activeTarget, singleTurnLimitReached = false, onNewConversation, operatorLocked = false, crossTargetLocked = false, onUseAsTemplate, attackOperator, noTargetSelected = false, onConfigureTarget, onToggleConverterPanel, isConverterPanelOpen = false, onInputChange, onAttachmentsChange, convertedValue, originalValue: _originalValue, onClearConversion, onConvertedValueChange, mediaConversions = [], onClearMediaConversion }, ref) {
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
      onSend(input, convertedValue ?? undefined, attachments)
      setInput('')
      setAttachments([])
      onClearConversion()
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
    onInputChange(input)
  }, [input, onInputChange])

  useEffect(() => {
    const types = [...new Set(attachments.map((a) => a.type))]

    // Convert the first attachment per media type to a base64 data URI for the
    // converter panel. Only one attachment per type is supported because the
    // converter panel operates on a single value per piece type.
    let cancelled = false
    const buildData = async () => {
      const data: Record<string, string> = {}
      for (const att of attachments) {
        if (cancelled) return
        if (!data[att.type] && att.file) {
          const reader = new FileReader()
          const base64 = await new Promise<string>((resolve, reject) => {
            reader.onload = () => resolve(reader.result as string)
            reader.onerror = () => reject(reader.error)
            reader.readAsDataURL(att.file!)
          })
          if (cancelled) return
          data[att.type] = base64
        }
      }
      if (!cancelled) {
        onAttachmentsChange(types, data)
      }
    }

    void buildData()

    return () => { cancelled = true }
  }, [attachments, onAttachmentsChange])

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
            buttonText="Configure Target"
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
            buttonText="Continue with your target"
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
            buttonText="Continue with your target"
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
            buttonText="New Conversation"
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
          <div className={styles.inputColumns}>
            <div className={styles.columnLeft}>
              <Button
                className={styles.iconButton}
                appearance="subtle"
                icon={<AttachRegular />}
                onClick={() => fileInputRef.current?.click()}
                disabled={disabled}
                title="Attach files"
              />
              <Button
                className={styles.iconButton}
                appearance={isConverterPanelOpen ? 'primary' : 'subtle'}
                icon={<ArrowShuffleRegular />}
                onClick={onToggleConverterPanel}
                disabled={disabled}
                data-testid="toggle-converter-panel-btn"
                title="Toggle converter panel"
              />
            </div>
            <div className={styles.columnCenter}>
              <AttachmentList
                attachments={attachments}
                mediaConversions={mediaConversions}
                onRemove={removeAttachment}
                onClearMediaConversion={onClearMediaConversion}
                formatFileSize={formatFileSize}
                styles={styles}
              />
              <TextInputRows
                input={input}
                convertedValue={convertedValue}
                disabled={disabled}
                textareaRef={textareaRef}
                onInput={handleInput}
                onKeyDown={handleKeyDown}
                onConvertedValueChange={onConvertedValueChange}
                onClearConversion={onClearConversion}
                styles={styles}
              />
            </div>
            <div className={styles.columnRight}>
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
                data-testid="send-message-btn"
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
