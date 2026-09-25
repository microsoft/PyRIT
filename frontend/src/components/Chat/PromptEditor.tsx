import { useId, useLayoutEffect, useRef, type KeyboardEvent, type ReactNode, type RefObject } from 'react'

import { Button, Caption1, Menu, MenuItem, MenuList, MenuPopover, MenuTrigger, Tooltip, mergeClasses } from '@fluentui/react-components'
import { ArrowShuffleRegular, AttachRegular, DismissRegular, OpenRegular } from '@fluentui/react-icons'

import type { ConvertedFileChip, MessageAttachment, PieceConversion } from '@/types'

import { withDraftIdentity } from './converterTypes'
import { usePromptEditorStyles } from './PromptEditor.styles'

interface PromptEditorProps {
  text: string
  attachments: MessageAttachment[]
  onInputChange: (value: string) => void
  onAttachmentsChange: (attachments: MessageAttachment[]) => void
  disabled?: boolean
  header?: ReactNode
  setup?: ReactNode
  warning?: ReactNode
  actions?: ReactNode
  attachmentMenuItems?: ReactNode
  showTextInput?: boolean
  textareaRef?: RefObject<HTMLTextAreaElement | null>
  onKeyDown?: (event: KeyboardEvent<HTMLTextAreaElement>) => void
  onToggleConverterPanel: () => void
  isConverterPanelOpen: boolean
  convertedValue?: string | null
  onConvertedValueChange: (value: string) => void
  mediaConversions?: Array<Pick<PieceConversion, 'pieceId' | 'convertedValue' | 'convertedDataType'>>
  onClearMediaConversion: (pieceId: string) => void
  convertedFileChip?: ConvertedFileChip | null
  onClearConvertedFileChip?: () => void
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function attachmentIcon(kind: MessageAttachment['type']): string {
  return { image: '🖼️', audio: '🎵', video: '🎥', file: '📄' }[kind]
}

export default function PromptEditor({
  text, attachments, onInputChange, onAttachmentsChange, disabled = false, header, setup, warning, actions,
  attachmentMenuItems, showTextInput = true, textareaRef: externalTextareaRef, onKeyDown,
  onToggleConverterPanel, isConverterPanelOpen, convertedValue, onConvertedValueChange,
  mediaConversions = [], onClearMediaConversion, convertedFileChip, onClearConvertedFileChip,
}: PromptEditorProps) {
  const styles = usePromptEditorStyles()
  const localTextareaRef = useRef<HTMLTextAreaElement>(null)
  const textareaRef = externalTextareaRef ?? localTextareaRef
  const convertedRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const convertedId = useId()
  const hasConversion = convertedValue != null || Boolean(convertedFileChip)

  useLayoutEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto'
      textareaRef.current.style.height = `${textareaRef.current.scrollHeight}px`
    }
  }, [text, textareaRef])
  useLayoutEffect(() => {
    if (convertedRef.current) {
      convertedRef.current.style.height = 'auto'
      convertedRef.current.style.height = `${convertedRef.current.scrollHeight}px`
    }
  }, [convertedValue])

  const selectFiles = (event: React.ChangeEvent<HTMLInputElement>): void => {
    const files = event.target.files
    if (!files) return
    const additions = Array.from(files, (file: File): MessageAttachment => withDraftIdentity({
      type: file.type.startsWith('image/') ? 'image' : file.type.startsWith('audio/') ? 'audio'
        : file.type.startsWith('video/') ? 'video' : 'file',
      name: file.name, url: URL.createObjectURL(file), mimeType: file.type, size: file.size, file,
    }))
    onAttachmentsChange([...attachments, ...additions])
    event.target.value = ''
  }

  return <div className={styles.inputWrapper}>
    {header && <div className={styles.editHeader}>{header}</div>}
    {setup}
    <input ref={fileInputRef} type="file" data-testid="file-input" multiple
      accept="image/*,audio/*,video/*,.pdf,.doc,.docx,.txt"
      className={styles.hiddenInput} disabled={disabled} onChange={selectFiles} />
    <div className={styles.inputColumns}>
      <div className={styles.columnLeft}>
        {attachmentMenuItems ? <Menu>
          <MenuTrigger disableButtonEnhancement>
            <Button className={styles.iconButton} appearance="subtle" icon={<AttachRegular />}
              disabled={disabled} aria-label="Add content" />
          </MenuTrigger>
          <MenuPopover><MenuList>
            <MenuItem icon={<AttachRegular />} onClick={() => fileInputRef.current?.click()}>Attach files</MenuItem>
            {attachmentMenuItems}
          </MenuList></MenuPopover>
        </Menu> : <Tooltip content="Attach files" relationship="label">
          <Button className={styles.iconButton} appearance="subtle" icon={<AttachRegular />}
            onClick={() => fileInputRef.current?.click()} disabled={disabled} aria-label="Attach files" />
        </Tooltip>}
        <Tooltip content="Toggle converter panel" relationship="label">
          <Button className={styles.iconButton} appearance={isConverterPanelOpen ? 'primary' : 'subtle'}
            icon={<ArrowShuffleRegular />} onClick={onToggleConverterPanel} disabled={disabled}
            data-testid="toggle-converter-panel-btn" data-tour="converter-toggle" aria-label="Toggle converter panel" />
        </Tooltip>
      </div>
      <div className={styles.columnCenter}>
        {attachments.length > 0 && <div className={styles.attachmentsContainer}>
          {attachments.map((attachment: MessageAttachment, index: number) => {
            const conversion = mediaConversions.find(
              (item: Pick<PieceConversion, 'pieceId' | 'convertedValue' | 'convertedDataType'>) => item.pieceId === attachment.draftId,
            )
            return <div key={attachment.draftId} className={styles.attachmentGroup}>
              <div className={styles.attachmentRow}>
                <span className={styles.attachmentContent}>
                  {conversion && <span className={styles.originalBadge}>Original</span>}
                  <Caption1>{attachmentIcon(attachment.type)} {attachment.name}
                    {attachment.size != null ? ` (${formatFileSize(attachment.size)})` : ''}</Caption1>
                </span>
                <Button appearance="transparent" size="small" className={styles.dismissBtn}
                  icon={<DismissRegular />} disabled={disabled} aria-label={`Remove ${attachment.name}`}
                  onClick={() => onAttachmentsChange(attachments.filter((item: MessageAttachment) => item !== attachment))}
                  data-testid={`remove-attachment-${index}`} />
              </div>
              {conversion && <div className={styles.attachmentRow}>
                <span className={styles.attachmentContent}>
                  <span className={styles.convertedBadge}>Converted</span>
                  <Caption1 className={styles.convertedFilename}>{conversion.convertedValue.split('/').pop()}</Caption1>
                </span>
                <Button appearance="transparent" size="small" className={styles.dismissBtn} icon={<DismissRegular />}
                  disabled={disabled} aria-label={`Clear conversion for ${attachment.name}`}
                  onClick={() => onClearMediaConversion(conversion.pieceId)} data-testid={`clear-media-conversion-${attachment.type}`} />
              </div>}
            </div>
          })}
        </div>}
        {warning}
        {showTextInput && <div className={styles.textRow}>
          {hasConversion && <span className={styles.originalBadge} data-testid="original-banner">Original</span>}
          <textarea ref={textareaRef} className={mergeClasses(styles.textInput, convertedValue != null && styles.textInputShared)}
            placeholder="Type prompt here" value={text}
            onChange={(event: React.ChangeEvent<HTMLTextAreaElement>) => onInputChange(event.target.value)}
            onKeyDown={onKeyDown} disabled={disabled} rows={1} data-testid="chat-input" />
        </div>}
        {convertedValue != null && <div className={styles.convertedRow} data-testid="converted-indicator">
          <label htmlFor={convertedId} className={styles.convertedBadge}>Converted prompt</label>
          <textarea id={convertedId} ref={convertedRef} className={styles.convertedTextarea}
            value={convertedValue} disabled={disabled}
            onChange={(event: React.ChangeEvent<HTMLTextAreaElement>) => onConvertedValueChange(event.target.value)}
            rows={1} data-testid="converted-value-input" />
        </div>}
        {convertedValue == null && convertedFileChip && <div className={styles.convertedFileBlock} data-testid="converted-file-chip">
          <div className={styles.convertedRow}>
            <span className={styles.convertedBadge}>Converted</span>
            <span aria-hidden="true">{attachmentIcon(convertedFileChip.iconKind)}</span>
            <Caption1 className={styles.convertedFilename} title={convertedFileChip.name}>{convertedFileChip.name}</Caption1>
            <Tooltip content="Open in new tab" relationship="label">
              <a href={convertedFileChip.url} target="_blank" rel="noopener noreferrer" className={styles.openLink}
                data-testid="converted-file-open"><OpenRegular fontSize={14} /><span>Open</span></a>
            </Tooltip>
            <Button appearance="transparent" size="small" className={styles.dismissBtn} icon={<DismissRegular />}
              disabled={disabled} onClick={onClearConvertedFileChip} data-testid="clear-converted-file-chip"
              aria-label="Clear converted file" />
          </div>
          {convertedFileChip.iconKind === 'image' && <img src={convertedFileChip.url} alt={convertedFileChip.name}
            className={styles.convertedImagePreview} data-testid="converted-file-preview-image" />}
          {convertedFileChip.iconKind === 'audio' && <audio controls src={convertedFileChip.url}
            className={styles.convertedAudioPreview} data-testid="converted-file-preview-audio" />}
          {convertedFileChip.iconKind === 'video' && <video controls src={convertedFileChip.url}
            className={styles.convertedVideoPreview} data-testid="converted-file-preview-video" />}
        </div>}
      </div>
      {actions && <div className={styles.columnRight}>{actions}</div>}
    </div>
  </div>
}
