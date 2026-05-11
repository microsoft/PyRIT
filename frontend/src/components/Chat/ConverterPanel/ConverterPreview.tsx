import { Button, MessageBar, MessageBarBody, Spinner, Text } from '@fluentui/react-components'
import { OpenRegular, PlayRegular } from '@fluentui/react-icons'
import type { PieceConversion } from '../converterTypes'
import { basenameFromValue, buildMediaUrl, dataTypeToAttachmentKind, isPathDataType } from '../converterTypes'
import { useConverterPanelStyles } from './ConverterPanel.styles'

export interface ConverterPreviewProps {
  activeTab: string
  previewText: string
  attachmentData: Record<string, string>
  selectedConverterType: string
  isPreviewing: boolean
  previewError: string | null
  previewOutput: string
  /** Backend data type of the preview output (e.g. 'text', 'image_path', 'binary_path'). */
  previewOutputType: string
  previewConverterInstanceId: string | null
  onPreview: () => void
  onUseConvertedValue?: (conversion: PieceConversion) => void
}

const FILE_ICON_BY_KIND: Record<string, string> = {
  image: '🖼️',
  audio: '🎵',
  video: '🎥',
  file: '📄',
}

export default function ConverterPreview({ activeTab, previewText, attachmentData, selectedConverterType, isPreviewing, previewError, previewOutput, previewOutputType, previewConverterInstanceId, onPreview, onUseConvertedValue }: ConverterPreviewProps) {
  const styles = useConverterPanelStyles()

  const renderOutput = () => {
    if (!previewOutput) {
      return (
        <Text size={200} className={styles.hintText}>
          Converted output will appear here.
        </Text>
      )
    }

    // Path-based outputs (image/audio/video/binary) → media renderer or file chip.
    if (isPathDataType(previewOutputType)) {
      const kind = dataTypeToAttachmentKind(previewOutputType)
      const url = buildMediaUrl(previewOutput)
      const filename = basenameFromValue(previewOutput, `output.${kind}`)
      if (kind === 'image') {
        return (
          <img
            src={url}
            alt="Converter output"
            className={styles.previewImage}
            data-testid="converter-preview-result"
          />
        )
      }
      if (kind === 'audio') {
        return (
          <audio
            controls
            src={url}
            className={styles.previewAudio}
            data-testid="converter-preview-result"
          />
        )
      }
      if (kind === 'video') {
        return (
          <video
            controls
            src={url}
            className={styles.previewVideo}
            data-testid="converter-preview-result"
          />
        )
      }
      // Generic binary file (e.g. PDF, DOCX): show a chip with an Open action.
      return (
        <div className={styles.fileChip} data-testid="converter-preview-result">
          <span aria-hidden="true">{FILE_ICON_BY_KIND.file}</span>
          <Text size={200} className={styles.fileChipName} title={filename}>
            {filename}
          </Text>
          <a
            href={url}
            target="_blank"
            rel="noopener noreferrer"
            className={styles.fileChipOpen}
            data-testid="converter-preview-open"
          >
            <OpenRegular fontSize={14} />
            <span>Open</span>
          </a>
        </div>
      )
    }

    // Text output — fallback to a preformatted block.
    return <pre className={styles.previewPre} data-testid="converter-preview-result">{previewOutput}</pre>
  }

  return (
    <div className={styles.outputSection} data-testid="converter-preview-section">
      <Button
        appearance="primary"
        size="small"
        icon={isPreviewing ? <Spinner size="tiny" /> : <PlayRegular />}
        onClick={onPreview}
        disabled={isPreviewing || !(activeTab === 'text' ? previewText.trim() : attachmentData[activeTab]) || !selectedConverterType}
        data-testid="converter-preview-btn"
      >
        {isPreviewing ? 'Converting...' : 'Preview'}
      </Button>

      {activeTab === 'text' && !previewText.trim() && (
        <Text size={200} className={styles.hintText}>
          Type in the chat input box to preview a conversion.
        </Text>
      )}

      {activeTab !== 'text' && !attachmentData[activeTab] && (
        <Text size={200} className={styles.hintText}>
          Attach a {activeTab} file to preview a conversion.
        </Text>
      )}

      {previewError && (
        <MessageBar intent="error" data-testid="converter-preview-error">
          <MessageBarBody className={styles.errorBody}>{previewError}</MessageBarBody>
        </MessageBar>
      )}

      <div data-testid="converter-output">
        <Text weight="semibold" size={300}>Output</Text>
        <div className={styles.outputBox}>
          {renderOutput()}
        </div>
      </div>

      {previewOutput && previewConverterInstanceId && (
        <Button
          appearance="primary"
          size="small"
          onClick={() => onUseConvertedValue?.({
            pieceType: activeTab,
            converterInstanceId: previewConverterInstanceId,
            convertedValue: previewOutput,
            originalValue: activeTab === 'text' ? previewText : (attachmentData[activeTab] ?? ''),
            convertedDataType: previewOutputType,
          })}
          disabled={!onUseConvertedValue}
          data-testid="use-converted-btn"
        >
          Use Converted Value
        </Button>
      )}
    </div>
  )
}
