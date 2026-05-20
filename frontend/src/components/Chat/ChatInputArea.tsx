import { useState, useEffect, useLayoutEffect, useRef, forwardRef, useImperativeHandle, KeyboardEvent, Ref } from 'react'
import {
  Button,
  Caption1,
  Tooltip,
  Text,
  tokens,
  mergeClasses,
} from '@fluentui/react-components'
import { SendRegular, AttachRegular, DismissRegular, InfoRegular, AddRegular, CopyRegular, WarningRegular, SettingsRegular, ArrowShuffleRegular, OpenRegular } from '@fluentui/react-icons'
import { MessageAttachment, TargetInstance } from '../../types'
import { useChatInputAreaStyles } from './ChatInputArea.styles'
import { PIECE_TYPE_TO_DATA_TYPE } from './converterTypes'

// ---------------------------------------------------------------------------
// Reusable status banner
// ---------------------------------------------------------------------------

export interface ConvertedFileChip {
  name: string
  url: string
  iconKind: 'image' | 'audio' | 'video' | 'file'
}

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
  mediaConversions: Array<{ pieceType: string; convertedValue: string; convertedDataType: string }>
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
  convertedFileChip?: ConvertedFileChip | null
  disabled: boolean
  textareaRef: Ref<HTMLTextAreaElement>
  convertedRef: Ref<HTMLTextAreaElement>
  onInput: (e: React.ChangeEvent<HTMLTextAreaElement>) => void
  onKeyDown: (e: KeyboardEvent<HTMLTextAreaElement>) => void
  onConvertedValueChange: (value: string) => void
  onClearConvertedFileChip?: () => void
  styles: ReturnType<typeof useChatInputAreaStyles>
  textInputClassName: string
}

function TextInputRows({ input, convertedValue, convertedFileChip, disabled, textareaRef, convertedRef, onInput, onKeyDown, onConvertedValueChange, onClearConvertedFileChip, styles, textInputClassName }: TextInputRowsProps) {
  const hasConversion = Boolean(convertedValue) || Boolean(convertedFileChip)
  return (
    <>
      <div className={styles.textRow}>
        {hasConversion && (
          <span className={styles.originalBadge} data-testid="original-banner">Original</span>
        )}
        <textarea
          ref={textareaRef}
          className={textInputClassName}
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
            ref={convertedRef}
            className={styles.convertedTextarea}
            value={convertedValue}
            onChange={(e) => onConvertedValueChange(e.target.value)}
            rows={1}
            data-testid="converted-value-input"
          />
        </div>
      )}
      {!convertedValue && convertedFileChip && (
        <div className={styles.convertedFileBlock} data-testid="converted-file-chip">
          <div className={styles.convertedRow}>
            <span className={styles.convertedBadge}>Converted</span>
            <span aria-hidden="true">
              {convertedFileChip.iconKind === 'image' && '🖼️'}
              {convertedFileChip.iconKind === 'audio' && '🎵'}
              {convertedFileChip.iconKind === 'video' && '🎥'}
              {convertedFileChip.iconKind === 'file' && '📄'}
            </span>
            <Caption1 className={styles.convertedFilename} title={convertedFileChip.name}>
              {convertedFileChip.name}
            </Caption1>
            <Tooltip content="Open in new tab" relationship="label">
              <a
                href={convertedFileChip.url}
                target="_blank"
                rel="noopener noreferrer"
                className={styles.openLink}
                data-testid="converted-file-open"
              >
                <OpenRegular fontSize={14} />
                <span>Open</span>
              </a>
            </Tooltip>
            <Button
              appearance="transparent"
              size="small"
              className={styles.dismissBtn}
              icon={<DismissRegular />}
              onClick={onClearConvertedFileChip}
              data-testid="clear-converted-file-chip"
            />
          </div>
          {convertedFileChip.iconKind === 'image' && (
            <img
              src={convertedFileChip.url}
              alt={convertedFileChip.name}
              className={styles.convertedImagePreview}
              data-testid="converted-file-preview-image"
            />
          )}
          {convertedFileChip.iconKind === 'audio' && (
            <audio
              controls
              src={convertedFileChip.url}
              className={styles.convertedAudioPreview}
              data-testid="converted-file-preview-audio"
            />
          )}
          {convertedFileChip.iconKind === 'video' && (
            <video
              controls
              src={convertedFileChip.url}
              className={styles.convertedVideoPreview}
              data-testid="converted-file-preview-video"
            />
          )}
        </div>
      )}
    </>
  )
}

// ---------------------------------------------------------------------------
// Target modality validation
// ---------------------------------------------------------------------------

/**
 * Returns the attachment UI types (e.g. `'image'`, `'audio'`, `'file'`) whose
 * underlying `PromptDataType` the active target does not accept.
 *
 * Returns an empty list if no target is selected, if the target advertises no
 * capabilities, or if every attachment is supported. Deduplicated by UI type.
 */
function getUnsupportedAttachmentTypes(
  attachments: MessageAttachment[],
  activeTarget: TargetInstance | null | undefined,
): string[] {
  if (!activeTarget?.capabilities?.supported_input_modalities) return []
  const supported = new Set(activeTarget.capabilities.supported_input_modalities)
  const unsupported: string[] = []
  const seen = new Set<string>()
  for (const att of attachments) {
    const dataType = PIECE_TYPE_TO_DATA_TYPE[att.type]
    if (dataType && !seen.has(att.type) && !supported.has(dataType)) {
      seen.add(att.type)
      unsupported.push(att.type)
    }
  }
  return unsupported
}

/**
 * Returns the converter output `PromptDataType` strings (e.g. `'image_path'`)
 * the active target does not accept. Surfaced separately from attachment
 * checks because converters can produce data types that don't match any
 * existing attachment (e.g. a text-to-image converter on text input).
 *
 * Returns an empty list if no target is selected, if the target advertises no
 * capabilities, or if every converter output is supported.
 */
function getUnsupportedConverterOutputTypes(
  converterOutputDataTypes: string[],
  activeTarget: TargetInstance | null | undefined,
): string[] {
  if (!activeTarget?.capabilities?.supported_input_modalities) return []
  const supported = new Set(activeTarget.capabilities.supported_input_modalities)
  const unsupported: string[] = []
  const seen = new Set<string>()
  for (const dataType of converterOutputDataTypes) {
    if (!seen.has(dataType) && !supported.has(dataType)) {
      seen.add(dataType)
      unsupported.push(dataType)
    }
  }
  return unsupported
}

// Strip the `_path` suffix used internally for media `PromptDataType` strings
// so the UI shows e.g. "image" instead of "image_path", matching ConverterPanel badges.
const formatModalityLabel = (modality: string): string => modality.replace('_path', '')

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
  converterOutputDataTypes?: string[]
  mediaConversions?: Array<{ pieceType: string; convertedValue: string; convertedDataType: string }>
  onClearMediaConversion: (pieceType: string) => void
  /** Chip describing a text→file conversion (e.g. PDFConverter output). */
  convertedFileChip?: ConvertedFileChip | null
  onClearConvertedFileChip?: () => void
}

const ChatInputArea = forwardRef<ChatInputAreaHandle, ChatInputAreaProps>(function ChatInputArea({ onSend, disabled = false, activeTarget, singleTurnLimitReached = false, onNewConversation, operatorLocked = false, crossTargetLocked = false, onUseAsTemplate, attackOperator, noTargetSelected = false, onConfigureTarget, onToggleConverterPanel, isConverterPanelOpen = false, onInputChange, onAttachmentsChange, convertedValue, originalValue: _originalValue, onClearConversion, onConvertedValueChange, converterOutputDataTypes = [], mediaConversions = [], onClearMediaConversion, convertedFileChip, onClearConvertedFileChip }, ref) {
  const styles = useChatInputAreaStyles()
  const [input, setInput] = useState('')
  const [attachments, setAttachments] = useState<MessageAttachment[]>([])
  const fileInputRef = useRef<HTMLInputElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const convertedRef = useRef<HTMLTextAreaElement>(null)

  // Derive unsupported types from attachments AND converter outputs
  const unsupportedAttachmentTypes = getUnsupportedAttachmentTypes(attachments, activeTarget)
  const unsupportedConverterOutputTypes = getUnsupportedConverterOutputTypes(converterOutputDataTypes, activeTarget)
  const hasUnsupportedModalities =
    unsupportedAttachmentTypes.length > 0 || unsupportedConverterOutputTypes.length > 0

  const hasConversion = convertedValue != null && convertedValue !== ''
  const textInputClassName = hasConversion
    ? mergeClasses(styles.textInput, styles.textInputShared)
    : styles.textInput

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
    if ((input || attachments.length > 0) && !disabled && !hasUnsupportedModalities) {
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

  // Auto-resize textareas whenever content changes.
  // useLayoutEffect fires before paint, avoiding visible flicker on resize.
  // CSS max-height (60vh solo / 30vh shared) caps the growth; overflowY: auto scrolls beyond.
  useLayoutEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto'
      textareaRef.current.style.height = textareaRef.current.scrollHeight + 'px'
    }
    onInputChange(input)
  }, [input, onInputChange])

  useLayoutEffect(() => {
    if (convertedRef.current) {
      convertedRef.current.style.height = 'auto'
      convertedRef.current.style.height = convertedRef.current.scrollHeight + 'px'
    }
  }, [convertedValue])

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
              <Tooltip content="Attach files" relationship="label">
                <Button
                  className={styles.iconButton}
                  appearance="subtle"
                  icon={<AttachRegular />}
                  onClick={() => fileInputRef.current?.click()}
                  disabled={disabled}
                  aria-label="Attach files"
                />
              </Tooltip>
              <Tooltip content="Toggle converter panel" relationship="label">
                <Button
                  className={styles.iconButton}
                  appearance={isConverterPanelOpen ? 'primary' : 'subtle'}
                  icon={<ArrowShuffleRegular />}
                  onClick={onToggleConverterPanel}
                  disabled={disabled}
                  data-testid="toggle-converter-panel-btn"
                  aria-label="Toggle converter panel"
                />
              </Tooltip>
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
              {hasUnsupportedModalities && (
                <div className={styles.unsupportedWarning} data-testid="unsupported-modality-warning">
                  <WarningRegular fontSize={14} />
                  <Caption1>
                    {unsupportedAttachmentTypes.length > 0 && (
                      <>
                        This target does not support {unsupportedAttachmentTypes.join(', ')} attachments.
                        Remove them to send.
                      </>
                    )}
                    {unsupportedAttachmentTypes.length > 0 && unsupportedConverterOutputTypes.length > 0 && ' '}
                    {unsupportedConverterOutputTypes.length > 0 && (
                      <>
                        The selected converter produces{' '}
                        {unsupportedConverterOutputTypes.map(formatModalityLabel).join(', ')} output, which this target
                        does not support.
                      </>
                    )}
                  </Caption1>
                </div>
              )}
              <TextInputRows
                input={input}
                convertedValue={convertedValue}
                convertedFileChip={convertedFileChip}
                disabled={disabled}
                textareaRef={textareaRef}
                convertedRef={convertedRef}
                onInput={handleInput}
                onKeyDown={handleKeyDown}
                onConvertedValueChange={onConvertedValueChange}
                onClearConvertedFileChip={onClearConvertedFileChip}
                styles={styles}
                textInputClassName={textInputClassName}
              />
            </div>
            <div className={styles.columnRight}>
              {activeTarget && activeTarget.capabilities?.supports_multi_turn === false && (
                <Tooltip
                  content="This target does not track conversation history — each turn is sent independently."
                  relationship="description"
                >
                  <span className={styles.singleTurnWarning}>
                    <InfoRegular fontSize={18} />
                  </span>
                </Tooltip>
              )}
              <Tooltip content="Send message" relationship="label">
                <Button
                  className={styles.sendButton}
                  appearance="primary"
                  icon={<SendRegular />}
                  onClick={handleSend}
                  disabled={disabled || (!input && attachments.length === 0) || hasUnsupportedModalities}
                  aria-label="Send message"
                  data-testid="send-message-btn"
                />
              </Tooltip>
              {convertedValue && (
                <Tooltip content="Clear conversion" relationship="label">
                  <Button
                    appearance="subtle"
                    className={styles.clearConversionButton}
                    icon={<DismissRegular />}
                    onClick={onClearConversion}
                    data-testid="clear-conversion-btn"
                  />
                </Tooltip>
              )}
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
