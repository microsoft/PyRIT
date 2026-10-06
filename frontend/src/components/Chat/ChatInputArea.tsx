import { useState, useEffect, useLayoutEffect, useRef, forwardRef, useImperativeHandle, type KeyboardEvent } from 'react'

import { Button, Caption1, Tooltip, Text } from '@fluentui/react-components'
import { SendRegular, DismissRegular, InfoRegular, AddRegular, CopyRegular, WarningRegular, SettingsRegular, ArrowSyncRegular } from '@fluentui/react-icons'

import type { AttackTargetResolutionStatus, ChatSendOutcome, ConvertedFileChip, MessageAttachment, PieceConversion, TargetInstance } from '@/types'
import { isTargetResolutionBlocking } from '@/utils/targetIdentity'

import { usePromptEditorStyles } from './PromptEditor.styles'
import PromptEditor from './PromptEditor'
import SystemPromptSetup from './SystemPromptSetup'
import { PIECE_TYPE_TO_DATA_TYPE, withDraftIdentity } from './converterTypes'

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
  buttonClassName?: string
}

function StatusBanner({ icon, text, buttonText, buttonIcon, onButtonClick, testId, className, textClassName, buttonTestId, buttonClassName }: StatusBannerProps) {
  return (
    <div className={className} data-testid={testId}>
      {icon}
      <Text className={textClassName} size={300}>{text}</Text>
      {onButtonClick && buttonText && (
        <Button className={buttonClassName} appearance="primary" icon={buttonIcon}
          onClick={onButtonClick} data-testid={buttonTestId}>{buttonText}</Button>
      )}
    </div>
  )
}

interface TargetResolutionBannerProps {
  status: AttackTargetResolutionStatus
  activeTarget?: TargetInstance | null
  onRetry?: () => void
  onConfigureTarget?: () => void
  onUseAsTemplate?: () => void
  styles: ReturnType<typeof usePromptEditorStyles>
}

function TargetResolutionBanner({ status, activeTarget, onRetry, onConfigureTarget, onUseAsTemplate, styles }: TargetResolutionBannerProps) {
  if (status === 'loading') {
    return <StatusBanner className={styles.statusBanner} textClassName={styles.statusBannerText}
      icon={<ArrowSyncRegular fontSize={18} />} text="Verifying this attack's target before sending messages..."
      testId="target-resolution-loading-banner" />
  }
  if (status === 'error' || status === 'unavailable' || status === 'ambiguous') {
    const text = status === 'error'
      ? 'Target verification failed. Sending is disabled; human scores can still be changed by the same operator.'
      : status === 'unavailable'
        ? 'The target used by this attack is not currently registered. Sending is disabled; human scores can still be changed by the same operator.'
        : "Multiple registered targets have this attack's identity. Remove duplicate registrations, then retry."
    return <StatusBanner className={styles.statusBanner} textClassName={styles.statusBannerText}
      icon={<WarningRegular fontSize={18} />} text={text} buttonText="Retry" buttonIcon={<ArrowSyncRegular />}
      onButtonClick={onRetry} testId={`target-resolution-${status}-banner`}
      buttonTestId="retry-target-resolution-btn" buttonClassName={styles.touchTarget} />
  }
  if (status === 'legacy') {
    const canUseAsTemplate = Boolean(activeTarget)
    return <StatusBanner className={styles.statusBanner} textClassName={styles.statusBannerText}
      icon={<WarningRegular fontSize={18} />}
      text="This attack does not contain a complete target identity. Sending is disabled; human scores can still be changed by the same operator."
      buttonText={canUseAsTemplate ? 'Continue with your target' : 'Configure Target'}
      buttonIcon={canUseAsTemplate ? <CopyRegular /> : <SettingsRegular />}
      onButtonClick={canUseAsTemplate ? onUseAsTemplate : onConfigureTarget}
      testId="target-resolution-legacy-banner"
      buttonTestId={canUseAsTemplate ? 'use-as-template-btn' : 'configure-target-input-btn'}
      buttonClassName={styles.touchTarget} />
  }
  return null
}

function getUnsupportedAttachmentTypes(attachments: MessageAttachment[], activeTarget: TargetInstance | null | undefined): string[] {
  if (!activeTarget?.capabilities?.supported_input_modalities) return []
  const supported = new Set(activeTarget.capabilities.supported_input_modalities)
  return [...new Set(attachments.filter((attachment: MessageAttachment) => {
    const type = PIECE_TYPE_TO_DATA_TYPE[attachment.type]
    return type && !supported.has(type)
  }).map((attachment: MessageAttachment) => attachment.type))]
}

function getUnsupportedConverterOutputTypes(converterOutputDataTypes: string[], activeTarget: TargetInstance | null | undefined): string[] {
  if (!activeTarget?.capabilities?.supported_input_modalities) return []
  const supported = new Set(activeTarget.capabilities.supported_input_modalities)
  return [...new Set(converterOutputDataTypes.filter((type: string) => !supported.has(type)))]
}

export interface ChatInputAreaHandle {
  addAttachment: (attachment: MessageAttachment) => void
  setText: (text: string) => void
  restoreDraft: (text: string, attachments: MessageAttachment[]) => void
  focus: () => void
  getDraftRevision: () => number
}

interface ChatInputAreaProps {
  onSend: (
    originalValue: string, convertedValue: string | undefined, attachments: MessageAttachment[],
  ) => Promise<ChatSendOutcome>
  conversionRevisionKey?: string
  disabled?: boolean
  sendDisabled?: boolean
  activeTarget?: TargetInstance | null
  singleTurnLimitReached?: boolean
  onNewConversation?: () => void
  operatorLocked?: boolean
  crossTargetLocked?: boolean
  targetResolutionStatus?: AttackTargetResolutionStatus
  onRetryTargetResolution?: () => void
  onUseAsTemplate?: () => void
  attackOperator?: string
  onConfigureTarget?: () => void
  onToggleConverterPanel: () => void
  isConverterPanelOpen: boolean
  onInputChange: (value: string) => void
  onAttachmentsChange: (attachments: MessageAttachment[]) => void
  convertedValue?: string | null
  originalValue?: string | null
  onClearConversion: () => void
  onClearAllConversions?: () => void
  onConvertedValueChange: (value: string) => void
  converterOutputDataTypes?: string[]
  mediaConversions?: Array<Pick<PieceConversion, 'pieceId' | 'convertedValue' | 'convertedDataType'>>
  onClearMediaConversion: (pieceId: string) => void
  convertedFileChip?: ConvertedFileChip | null
  onClearConvertedFileChip?: () => void
  showSystemPrompt?: boolean
  supportsSystemPrompt?: boolean
  systemPrompt?: string
  onSystemPromptChange?: (value: string) => void
}

const ChatInputArea = forwardRef<ChatInputAreaHandle, ChatInputAreaProps>(function ChatInputArea({
  onSend, conversionRevisionKey = '', disabled = false, sendDisabled = false, activeTarget,
  singleTurnLimitReached = false, onNewConversation, operatorLocked = false, crossTargetLocked = false,
  targetResolutionStatus = 'idle', onRetryTargetResolution, onUseAsTemplate, attackOperator, onConfigureTarget,
  onToggleConverterPanel, isConverterPanelOpen = false, onInputChange, onAttachmentsChange,
  convertedValue, onClearConversion, onClearAllConversions, onConvertedValueChange,
  converterOutputDataTypes = [], mediaConversions = [], onClearMediaConversion, convertedFileChip,
  onClearConvertedFileChip, showSystemPrompt = false, supportsSystemPrompt = false, systemPrompt = '', onSystemPromptChange,
}, ref) {
  const styles = usePromptEditorStyles()
  const [input, setInput] = useState('')
  const [attachments, setAttachments] = useState<MessageAttachment[]>([])
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const inputRef = useRef(input)
  const attachmentsRef = useRef(attachments)
  const draftRevisionRef = useRef(0)
  const previousConversionRevisionKeyRef = useRef(conversionRevisionKey)

  useLayoutEffect(() => {
    if (previousConversionRevisionKeyRef.current !== conversionRevisionKey) {
      previousConversionRevisionKeyRef.current = conversionRevisionKey
      draftRevisionRef.current += 1
    }
  }, [conversionRevisionKey])
  useLayoutEffect(() => { inputRef.current = input; onInputChange(input) }, [input, onInputChange])
  useLayoutEffect(() => { attachmentsRef.current = attachments; onAttachmentsChange(attachments) }, [attachments, onAttachmentsChange])

  const changeText = (text: string): void => {
    inputRef.current = text
    draftRevisionRef.current += 1
    setInput(text)
  }
  const changeAttachments = (next: MessageAttachment[]): void => {
    for (const attachment of attachmentsRef.current) {
      if (!next.includes(attachment) && attachment.url.startsWith('blob:')) URL.revokeObjectURL(attachment.url)
    }
    attachmentsRef.current = next
    draftRevisionRef.current += 1
    setAttachments(next)
  }
  useImperativeHandle(ref, () => ({
    addAttachment: (attachment: MessageAttachment): void => {
      changeAttachments([...attachmentsRef.current, withDraftIdentity({ ...attachment, draftId: undefined })])
    },
    setText: changeText,
    restoreDraft: (text: string, draftAttachments: MessageAttachment[]): void => {
      changeText(text)
      changeAttachments(draftAttachments.map(withDraftIdentity))
    },
    focus: (): void => { textareaRef.current?.focus() },
    getDraftRevision: (): number => draftRevisionRef.current,
  }))

  const unsupportedAttachmentTypes = getUnsupportedAttachmentTypes(attachments, activeTarget)
  const unsupportedConverterOutputTypes = getUnsupportedConverterOutputTypes(converterOutputDataTypes, activeTarget)
  const hasUnsupportedModalities = unsupportedAttachmentTypes.length > 0 || unsupportedConverterOutputTypes.length > 0
  const canSend = !disabled && !sendDisabled && !hasUnsupportedModalities
    && Boolean(input || convertedValue != null || convertedFileChip || attachments.length)
  const handleSend = async (): Promise<void> => {
    if (!canSend) return
    const submittedRevision = draftRevisionRef.current
    const outcome = await onSend(inputRef.current, convertedValue ?? undefined, attachmentsRef.current)
    if (outcome.clearDraft && draftRevisionRef.current === submittedRevision) {
      changeText('')
      // Recovery may still own submitted attachments; keep their URLs alive.
      attachmentsRef.current = []
      setAttachments([])
      onClearAllConversions?.()
    }
  }
  useEffect(() => { if (!disabled) textareaRef.current?.focus() }, [disabled])
  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>): void => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      void handleSend()
    }
  }

  return <div className={styles.root}>
    <div className={styles.inputContainer}>
      {isTargetResolutionBlocking(targetResolutionStatus) ? (
        <TargetResolutionBanner status={targetResolutionStatus} activeTarget={activeTarget} onRetry={onRetryTargetResolution}
          onConfigureTarget={onConfigureTarget} onUseAsTemplate={onUseAsTemplate} styles={styles} />
      ) : operatorLocked ? (
        <StatusBanner className={styles.statusBanner} textClassName={styles.statusBannerText} icon={<InfoRegular fontSize={18} />}
          text={`This conversation belongs to operator: ${attackOperator}.`} buttonText="Continue with your target"
          buttonIcon={<CopyRegular />} onButtonClick={onUseAsTemplate} testId="operator-locked-banner"
          buttonTestId="use-as-template-btn" buttonClassName={styles.touchTarget} />
      ) : crossTargetLocked ? (
        <StatusBanner className={styles.statusBanner} textClassName={styles.statusBannerText} icon={<InfoRegular fontSize={18} />}
          text="This attack uses a different target. Continue with your target to keep the conversation."
          buttonText="Continue with your target" buttonIcon={<CopyRegular />} onButtonClick={onUseAsTemplate}
          testId="cross-target-banner" buttonTestId="use-as-template-btn" buttonClassName={styles.touchTarget} />
      ) : singleTurnLimitReached ? (
        <StatusBanner className={styles.statusBanner} textClassName={styles.statusBannerText} icon={<InfoRegular fontSize={18} />}
          text="This target only supports single-turn conversations." buttonText="New Conversation" buttonIcon={<AddRegular />}
          onButtonClick={onNewConversation} testId="single-turn-banner"
          buttonTestId="new-conversation-btn" buttonClassName={styles.touchTarget} />
      ) : (
        <PromptEditor text={input} attachments={attachments} onInputChange={changeText} onAttachmentsChange={changeAttachments}
          disabled={disabled} textareaRef={textareaRef} onKeyDown={handleKeyDown}
          onToggleConverterPanel={onToggleConverterPanel} isConverterPanelOpen={isConverterPanelOpen}
          convertedValue={convertedValue} onConvertedValueChange={onConvertedValueChange}
          mediaConversions={mediaConversions} onClearMediaConversion={onClearMediaConversion}
          convertedFileChip={convertedFileChip} onClearConvertedFileChip={onClearConvertedFileChip}
          setup={showSystemPrompt && onSystemPromptChange && <SystemPromptSetup value={systemPrompt}
            onChange={onSystemPromptChange} disabled={!!activeTarget && !supportsSystemPrompt} />}
          warning={hasUnsupportedModalities && <div className={styles.unsupportedWarning} data-testid="unsupported-modality-warning">
            <WarningRegular fontSize={14} /><Caption1>
              {unsupportedAttachmentTypes.length > 0 && <>This target does not support {unsupportedAttachmentTypes.join(', ')} attachments. Remove them to send.</>}
              {unsupportedAttachmentTypes.length > 0 && unsupportedConverterOutputTypes.length > 0 && ' '}
              {unsupportedConverterOutputTypes.length > 0 && <>The selected converter produces{' '}
                {unsupportedConverterOutputTypes.map((type: string) => type.replace('_path', '')).join(', ')} output, which this target does not support.</>}
            </Caption1>
          </div>}
          actions={<>
            {activeTarget?.capabilities?.supports_multi_turn === false && <Tooltip
              content="This target does not track conversation history — each turn is sent independently." relationship="description">
              <span className={styles.singleTurnWarning}><InfoRegular fontSize={18} /></span>
            </Tooltip>}
            <Tooltip content="Send message" relationship="label">
              <Button className={styles.sendButton} appearance="primary" icon={<SendRegular />}
                onClick={() => { void handleSend() }} disabled={!canSend} aria-label="Send message" data-testid="send-message-btn" />
            </Tooltip>
            {convertedValue != null && <Tooltip content="Clear conversion" relationship="label">
              <Button appearance="subtle" className={styles.clearConversionButton} icon={<DismissRegular />}
                onClick={onClearConversion} data-testid="clear-conversion-btn" />
            </Tooltip>}
          </>}
        />
      )}
    </div>
  </div>
})

export default ChatInputArea
