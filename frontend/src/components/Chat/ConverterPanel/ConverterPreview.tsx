import { Button, MessageBar, MessageBarBody, Spinner, Text } from '@fluentui/react-components'
import { PlayRegular } from '@fluentui/react-icons'
import type { PieceConversion } from '../converterTypes'
import { useConverterPanelStyles } from './ConverterPanel.styles'

export interface ConverterPreviewProps {
  activeTab: string
  previewText: string
  attachmentData: Record<string, string>
  selectedConverterType: string
  isPreviewing: boolean
  previewError: string | null
  previewOutput: string
  previewConverterInstanceId: string | null
  onPreview: () => void
  onUseConvertedValue?: (conversion: PieceConversion) => void
}

export default function ConverterPreview({ activeTab, previewText, attachmentData, selectedConverterType, isPreviewing, previewError, previewOutput, previewConverterInstanceId, onPreview, onUseConvertedValue }: ConverterPreviewProps) {
  const styles = useConverterPanelStyles()

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
          {previewOutput ? (
            previewOutput.match(/\.(png|jpg|jpeg|gif|bmp|webp)$/i) ? (
              <img
                src={`/api/media?path=${encodeURIComponent(previewOutput)}`}
                alt="Converter output"
                className={styles.previewImage}
                data-testid="converter-preview-result"
              />
            ) : previewOutput.match(/\.(wav|mp3|ogg|flac)$/i) ? (
              <audio
                controls
                src={`/api/media?path=${encodeURIComponent(previewOutput)}`}
                className={styles.previewAudio}
                data-testid="converter-preview-result"
              />
            ) : previewOutput.match(/\.(mp4|webm|mov)$/i) ? (
              <video
                controls
                src={`/api/media?path=${encodeURIComponent(previewOutput)}`}
                className={styles.previewVideo}
                data-testid="converter-preview-result"
              />
            ) : (
              <pre className={styles.previewPre} data-testid="converter-preview-result">{previewOutput}</pre>
            )
          ) : (
            <Text size={200} className={styles.hintText}>
              Converted output will appear here.
            </Text>
          )}
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
